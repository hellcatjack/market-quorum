import hashlib
import hmac
import ipaddress
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet

from tradingng_platform.webhooks.signing import (
    SecretCipher,
    canonical_json,
    signature_headers,
)
from tradingng_platform.webhooks.worker import (
    DeliveryAttempt,
    EndpointRejected,
    deliver_webhook,
    next_retry_at,
    validate_endpoint,
)


def test_secret_cipher_round_trips_without_storing_plaintext():
    key = Fernet.generate_key().decode()
    cipher = SecretCipher(key)

    encrypted = cipher.encrypt("super-secret-signing-value")

    assert "super-secret-signing-value" not in encrypted
    assert cipher.decrypt(encrypted) == b"super-secret-signing-value"


def test_canonical_json_and_signature_are_deterministic():
    body = canonical_json({"z": 1, "a": {"two": 2, "one": 1}})
    event_id = "evt-123"
    timestamp = 1_722_000_000

    headers = signature_headers(b"secret", event_id, body, timestamp)
    expected = hmac.new(
        b"secret",
        f"{timestamp}.{event_id}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()

    assert body == b'{"a":{"one":1,"two":2},"z":1}'
    assert headers == {
        "X-TradingNG-Event-ID": event_id,
        "X-TradingNG-Timestamp": str(timestamp),
        "X-TradingNG-Signature": f"v1={expected}",
    }


@pytest.mark.parametrize(
    ("endpoint", "resolved"),
    [
        ("http://hooks.example/events", ["203.0.113.10"]),
        ("https://127.0.0.1/events", ["127.0.0.1"]),
        ("https://hooks.example/events", ["10.0.0.8"]),
        ("https://hooks.example/events", ["169.254.1.2"]),
        ("https://hooks.example/events", ["224.0.0.1"]),
    ],
)
async def test_endpoint_policy_rejects_non_https_and_unsafe_destinations(endpoint, resolved):
    async def resolver(_host: str, _port: int) -> list[ipaddress.IPv4Address]:
        return [ipaddress.ip_address(item) for item in resolved]

    with pytest.raises(EndpointRejected):
        await validate_endpoint(endpoint, frozenset(), resolver=resolver)


async def test_explicit_hostname_allowlist_permits_private_destination():
    calls = 0

    async def resolver(_host: str, _port: int) -> list[ipaddress.IPv4Address]:
        nonlocal calls
        calls += 1
        return [ipaddress.ip_address("10.20.30.40")]

    endpoint = await validate_endpoint(
        "https://internal-hooks.example/events",
        frozenset({"internal-hooks.example"}),
        resolver=resolver,
    )

    assert str(endpoint) == "https://internal-hooks.example/events"
    assert calls == 1


@pytest.mark.parametrize(
    ("attempt", "minutes"),
    [(1, 1), (2, 5), (3, 15), (4, 60)],
)
def test_retry_schedule(attempt, minutes):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    assert next_retry_at(attempt, now) == now + timedelta(minutes=minutes)


def test_fifth_attempt_is_terminal():
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    assert next_retry_at(5, now) is None


async def test_delivery_signs_canonical_payload_and_does_not_follow_redirects():
    observed_request: httpx.Request | None = None
    dns_calls = 0

    async def resolver(_host: str, _port: int) -> list[ipaddress.IPv4Address]:
        nonlocal dns_calls
        dns_calls += 1
        return [ipaddress.ip_address("8.8.8.8")]

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_request
        observed_request = request
        return httpx.Response(302, headers={"Location": "https://elsewhere.example"})

    payload = {"type": "assessment.completed", "data": {"ticker": "SPCX"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await deliver_webhook(
            endpoint="https://hooks.example/events",
            event_id="event-42",
            secret=b"sign-me",
            payload=payload,
            attempt=1,
            now=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
            allowlist=frozenset(),
            resolver=resolver,
            client=client,
        )

    assert dns_calls == 1
    assert observed_request is not None
    assert (
        observed_request.content
        == json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )
    assert observed_request.headers["X-TradingNG-Event-ID"] == "event-42"
    assert result == DeliveryAttempt(
        status="failed",
        response_code=302,
        next_attempt_at=None,
    )


@pytest.mark.parametrize("response_code", [429, 500, 503])
async def test_retryable_status_schedules_another_attempt(response_code):
    async def resolver(_host: str, _port: int) -> list[ipaddress.IPv4Address]:
        return [ipaddress.ip_address("8.8.8.8")]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(response_code)

    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await deliver_webhook(
            endpoint="https://hooks.example/events",
            event_id="event-42",
            secret=b"sign-me",
            payload={"ok": True},
            attempt=2,
            now=now,
            allowlist=frozenset(),
            resolver=resolver,
            client=client,
        )

    assert result.status == "pending"
    assert result.response_code == response_code
    assert result.next_attempt_at == now + timedelta(minutes=5)


async def test_network_failure_retries_but_never_mutates_assessment_state():
    assessment = {"status": "succeeded"}

    async def resolver(_host: str, _port: int) -> list[ipaddress.IPv4Address]:
        return [ipaddress.ip_address("8.8.8.8")]

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await deliver_webhook(
            endpoint="https://hooks.example/events",
            event_id="event-42",
            secret=b"sign-me",
            payload={"assessment": assessment.copy()},
            attempt=1,
            now=now,
            allowlist=frozenset(),
            resolver=resolver,
            client=client,
        )

    assert result.status == "pending"
    assert result.response_code is None
    assert assessment == {"status": "succeeded"}
