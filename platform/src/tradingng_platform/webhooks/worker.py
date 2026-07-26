from __future__ import annotations

import asyncio
import ipaddress
import socket
import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import case, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.models import RunEvent, Webhook, WebhookDelivery
from tradingng_platform.persistence.upsert import insert_ignore, session_dialect
from tradingng_platform.webhooks.signing import SecretCipher, canonical_json, signature_headers

Resolver = Callable[[str, int], Awaitable[list[ipaddress.IPv4Address | ipaddress.IPv6Address]]]

_RETRY_MINUTES = (1, 5, 15, 60, 240)
_MAX_ATTEMPTS = 5


class EndpointRejected(ValueError):
    """Raised when an endpoint violates the outbound webhook policy."""


@dataclass(frozen=True)
class DeliveryAttempt:
    status: str
    response_code: int | None
    next_attempt_at: datetime | None


async def _resolve_host(
    host: str,
    port: int,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    records = await asyncio.to_thread(
        socket.getaddrinfo,
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    if not addresses:
        raise EndpointRejected("webhook hostname did not resolve")
    return sorted(addresses, key=lambda address: (address.version, int(address)))


async def validate_endpoint(
    endpoint: str,
    private_host_allowlist: Collection[str],
    *,
    resolver: Resolver = _resolve_host,
) -> httpx.URL:
    try:
        url = httpx.URL(endpoint)
    except (TypeError, httpx.InvalidURL) as exc:
        raise EndpointRejected("webhook endpoint is invalid") from exc
    if url.scheme != "https":
        raise EndpointRejected("webhook endpoint must use HTTPS")
    if not url.host:
        raise EndpointRejected("webhook endpoint must include a hostname")
    if url.userinfo:
        raise EndpointRejected("webhook endpoint must not include credentials")
    if url.fragment:
        raise EndpointRejected("webhook endpoint must not include a fragment")

    host = url.host.lower().rstrip(".")
    allowlist = {item.lower().rstrip(".") for item in private_host_allowlist}
    try:
        addresses = await resolver(host, url.port or 443)
    except EndpointRejected:
        raise
    except (OSError, ValueError) as exc:
        raise EndpointRejected("webhook hostname resolution failed") from exc
    if not addresses:
        raise EndpointRejected("webhook hostname did not resolve")
    if host not in allowlist:
        for address in addresses:
            if (
                not address.is_global
                or address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
            ):
                raise EndpointRejected("webhook endpoint resolved to a non-public address")
    return url


def next_retry_at(attempt: int, now: datetime) -> datetime | None:
    if attempt < 1:
        raise ValueError("delivery attempt must be positive")
    if attempt >= _MAX_ATTEMPTS:
        return None
    return now + timedelta(minutes=_RETRY_MINUTES[attempt - 1])


async def deliver_webhook(
    *,
    endpoint: str,
    event_id: str,
    secret: bytes,
    payload: dict,
    attempt: int,
    now: datetime,
    allowlist: Collection[str],
    resolver: Resolver = _resolve_host,
    client: httpx.AsyncClient,
) -> DeliveryAttempt:
    try:
        url = await validate_endpoint(endpoint, allowlist, resolver=resolver)
    except EndpointRejected:
        return DeliveryAttempt("failed", None, None)

    body = canonical_json(payload)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "TradingNG-Webhook/1.0",
        **signature_headers(secret, event_id, body, int(now.timestamp())),
    }
    try:
        response = await client.post(
            url,
            content=body,
            headers=headers,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        retry_at = next_retry_at(attempt, now)
        return DeliveryAttempt("pending" if retry_at else "failed", None, retry_at)

    if 200 <= response.status_code < 300:
        return DeliveryAttempt("delivered", response.status_code, None)
    if response.status_code == 429 or response.status_code >= 500:
        retry_at = next_retry_at(attempt, now)
        return DeliveryAttempt(
            "pending" if retry_at else "failed",
            response.status_code,
            retry_at,
        )
    return DeliveryAttempt("failed", response.status_code, None)


@dataclass(frozen=True)
class ClaimedDelivery:
    delivery_id: uuid.UUID
    event_id: uuid.UUID
    attempt: int
    endpoint: str
    encrypted_secret: str
    payload: dict


def webhook_claim_statement(now: datetime):
    return (
        select(WebhookDelivery, Webhook, RunEvent)
        .join(Webhook, WebhookDelivery.webhook_id == Webhook.id)
        .join(RunEvent, WebhookDelivery.event_id == RunEvent.id)
        .where(
            WebhookDelivery.status == "pending",
            or_(
                WebhookDelivery.next_attempt_at.is_(None),
                WebhookDelivery.next_attempt_at <= now,
            ),
            Webhook.status == "active",
        )
        .order_by(
            case((WebhookDelivery.next_attempt_at.is_(None), 0), else_=1),
            WebhookDelivery.next_attempt_at.asc(),
            WebhookDelivery.created_at,
            WebhookDelivery.id,
        )
        .with_for_update(of=WebhookDelivery, skip_locked=True)
        .limit(1)
    )


class WebhookDeliveryWorker:
    """Persistently enqueue and deliver run events without touching run state."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        encryption_key: str,
        private_host_allowlist: tuple[str, ...],
        client: httpx.AsyncClient,
        resolver: Resolver = _resolve_host,
    ):
        self.sessions = sessions
        self.cipher = SecretCipher(encryption_key)
        self.private_host_allowlist = frozenset(private_host_allowlist)
        self.client = client
        self.resolver = resolver

    async def enqueue_missing(self, *, per_webhook_limit: int = 100) -> int:
        created = 0
        async with self.sessions() as session, session.begin():
            webhooks = list(
                await session.scalars(
                    select(Webhook).where(Webhook.status == "active").order_by(Webhook.id)
                )
            )
            for webhook in webhooks:
                if not webhook.event_types_json:
                    continue
                event_ids = list(
                    await session.scalars(
                        select(RunEvent.id)
                        .where(
                            RunEvent.event_type.in_(webhook.event_types_json),
                            ~exists(
                                select(WebhookDelivery.id).where(
                                    WebhookDelivery.webhook_id == webhook.id,
                                    WebhookDelivery.event_id == RunEvent.id,
                                )
                            ),
                        )
                        .order_by(RunEvent.created_at, RunEvent.id)
                        .limit(per_webhook_limit)
                    )
                )
                for event_id in event_ids:
                    result = await session.execute(
                        insert_ignore(
                            session_dialect(session),
                            WebhookDelivery,
                            {
                                "webhook_id": webhook.id,
                                "event_id": event_id,
                                "attempt": 0,
                                "status": "pending",
                                "response_code": None,
                                "next_attempt_at": None,
                            },
                            [WebhookDelivery.webhook_id, WebhookDelivery.event_id],
                        )
                    )
                    created += int(result.rowcount or 0)
        return created

    async def recover_expired_claims(self, now: datetime) -> int:
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(WebhookDelivery)
                .where(
                    WebhookDelivery.status == "delivering",
                    WebhookDelivery.next_attempt_at <= now,
                )
                .values(status="pending", next_attempt_at=now)
            )
            return int(result.rowcount or 0)

    async def claim(self, now: datetime) -> ClaimedDelivery | None:
        async with self.sessions() as session, session.begin():
            row = (await session.execute(webhook_claim_statement(now))).one_or_none()
            if row is None:
                return None
            delivery, webhook, event = row
            delivery.status = "delivering"
            delivery.attempt += 1
            delivery.next_attempt_at = now + timedelta(minutes=5)
            return ClaimedDelivery(
                delivery_id=delivery.id,
                event_id=event.id,
                attempt=delivery.attempt,
                endpoint=webhook.endpoint,
                encrypted_secret=webhook.encrypted_secret,
                payload={
                    "id": str(event.id),
                    "type": event.event_type,
                    "created_at": event.created_at.isoformat(),
                    "run_id": str(event.run_id),
                    "sequence": event.sequence,
                    "data": dict(event.payload_json),
                },
            )

    async def complete(
        self,
        claimed: ClaimedDelivery,
        result: DeliveryAttempt,
    ) -> None:
        async with self.sessions() as session, session.begin():
            delivery = await session.scalar(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.id == claimed.delivery_id,
                    WebhookDelivery.status == "delivering",
                    WebhookDelivery.attempt == claimed.attempt,
                )
                .with_for_update()
            )
            if delivery is None:
                return
            delivery.status = result.status
            delivery.response_code = result.response_code
            delivery.next_attempt_at = result.next_attempt_at

    async def run_once(self, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(timezone.utc)
        await self.recover_expired_claims(current_time)
        await self.enqueue_missing()
        claimed = await self.claim(current_time)
        if claimed is None:
            return False
        try:
            secret = self.cipher.decrypt(claimed.encrypted_secret)
        except ValueError:
            result = DeliveryAttempt("failed", None, None)
        else:
            result = await deliver_webhook(
                endpoint=claimed.endpoint,
                event_id=str(claimed.event_id),
                secret=secret,
                payload=claimed.payload,
                attempt=claimed.attempt,
                now=current_time,
                allowlist=self.private_host_allowlist,
                resolver=self.resolver,
                client=self.client,
            )
        await self.complete(claimed, result)
        return True

    async def run_forever(self, poll_seconds: float = 1.0) -> None:
        while True:
            handled = await self.run_once()
            if not handled:
                await asyncio.sleep(poll_seconds)


async def _main() -> None:
    from tradingng_platform.config import Settings
    from tradingng_platform.db import Database

    settings = Settings()
    database = Database(settings)
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(10.0),
        ) as client:
            worker = WebhookDeliveryWorker(
                database.sessions,
                settings.webhook_encryption_key.get_secret_value(),
                settings.webhook_private_host_allowlist,
                client,
            )
            await worker.run_forever()
    finally:
        await database.close()


def main() -> None:
    asyncio.run(_main())
