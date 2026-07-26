import ipaddress
from datetime import date, datetime, timezone

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from tradingng_platform.assessments.contracts import AssessmentItem, SubmitAssessments
from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.assessments.service import AssessmentService
from tradingng_platform.auth.principal import Principal
from tradingng_platform.models import (
    AssessmentRun,
    AuditEvent,
    Webhook,
    WebhookDelivery,
)
from tradingng_platform.webhooks.contracts import CreateWebhook
from tradingng_platform.webhooks.service import WebhookService
from tradingng_platform.webhooks.signing import SecretCipher
from tradingng_platform.webhooks.worker import WebhookDeliveryWorker


def _admin() -> Principal:
    return Principal(
        "issuer",
        "webhook-admin",
        "user",
        frozenset({"assessments:admin", "assessments:submit", "assessments:read"}),
        display_name="Webhook Admin",
        roles=frozenset({"Admin"}),
    )


async def test_webhook_secret_idempotent_delivery_and_run_state_isolation(
    session_factory,
    instrument_classifier,
    monkeypatch,
):
    principal = _admin()
    run_view = (
        await AssessmentService(session_factory, instrument_classifier).submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="SPCX", analysis_date=date(2026, 7, 25))],
                idempotency_key="webhook-20260725",
            ),
            "request-submit",
        )
    )[0]
    async with session_factory() as session, session.begin():
        run = await session.get(AssessmentRun, run_view.id)
        run.status = "succeeded"
        run.finished_at = datetime.now(timezone.utc)
        event = await AssessmentRepository(session).append_event(
            run.id,
            "assessment.succeeded",
            {"ticker": "SPCX", "rating": "Hold"},
        )
        event_id = event.id

    async def validated(endpoint, _allowlist):
        return httpx.URL(endpoint)

    monkeypatch.setattr("tradingng_platform.webhooks.service.validate_endpoint", validated)
    encryption_key = Fernet.generate_key().decode()
    signing_secret = "webhook-signing-secret-value"
    service = WebhookService(session_factory, encryption_key)
    created = await service.create(
        principal,
        CreateWebhook(
            endpoint="https://hooks.example/events",
            event_types={"assessment.succeeded"},
            secret=signing_secret,
        ),
        "request-webhook",
    )

    async with session_factory() as session:
        stored = await session.get(Webhook, created.id)
        assert signing_secret not in stored.encrypted_secret
        decrypted = SecretCipher(encryption_key).decrypt(stored.encrypted_secret)
        assert decrypted == signing_secret.encode()
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "webhook.create")
        )
        assert signing_secret not in str(audit.metadata_json)

    async def resolver(_host, _port):
        return [ipaddress.ip_address("8.8.8.8")]

    async def unavailable(_request):
        return httpx.Response(503)

    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        worker = WebhookDeliveryWorker(
            session_factory,
            encryption_key,
            (),
            client,
            resolver=resolver,
        )
        assert await worker.run_once(now) is True
        assert await worker.enqueue_missing() == 0

    async with session_factory() as session:
        delivery = await session.scalar(
            select(WebhookDelivery).where(
                WebhookDelivery.webhook_id == created.id,
                WebhookDelivery.event_id == event_id,
            )
        )
        run = await session.get(AssessmentRun, run_view.id)
        count = await session.scalar(select(func.count()).select_from(WebhookDelivery))

    assert count == 1
    assert delivery.attempt == 1
    assert delivery.status == "pending"
    assert delivery.response_code == 503
    assert run.status == "succeeded"
