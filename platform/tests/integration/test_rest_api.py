import uuid
from datetime import date

import httpx
from cryptography.fernet import Fernet
from fastapi import Request
from sqlalchemy import select

from tradingng_platform.api.app import create_app
from tradingng_platform.api.auth import current_principal
from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.auth.principal import Principal
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.models import Artifact, AuditEvent
from tradingng_platform.scheduler.policy import SystemSnapshot


def _principal(subject: str, role: str, scopes: set[str]) -> Principal:
    return Principal(
        "test-issuer",
        subject,
        "user",
        frozenset(scopes),
        display_name=subject.title(),
        roles=frozenset({role}),
    )


VIEWER = _principal(
    "viewer",
    "Viewer",
    {"assessments:read", "artifacts:read", "system:read"},
)
ANALYST = _principal(
    "analyst",
    "Analyst",
    {
        "assessments:read",
        "assessments:submit",
        "assessments:cancel",
        "assessments:review",
        "artifacts:read",
        "system:read",
    },
)
OTHER_ANALYST = _principal(
    "other-analyst",
    "Analyst",
    {"assessments:read", "assessments:submit", "assessments:cancel"},
)
ADMIN = _principal(
    "admin",
    "Admin",
    {
        "assessments:read",
        "assessments:submit",
        "assessments:cancel",
        "assessments:review",
        "assessments:admin",
        "artifacts:read",
        "system:read",
    },
)
PRINCIPALS = {principal.subject: principal for principal in (VIEWER, ANALYST, OTHER_ANALYST, ADMIN)}


class _Gateway:
    async def status(self):
        return GatewaySnapshot(
            status="ok",
            active_completions=0,
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            snapshot_id="f" * 64,
            latency_ms=1,
        )


class _Probe:
    def sample(self):
        return SystemSnapshot(20, 32, 100, 50, False)


def _headers(subject: str) -> dict[str, str]:
    return {"X-Test-Principal": subject}


async def test_complete_rest_management_workflow(
    test_database_url,
    session_factory,
    tmp_path,
):
    settings = Settings(
        database_url=test_database_url,
        data_dir=tmp_path / "runtime",
        token_pepper="integration-token-pepper-value",
        webhook_encryption_key=Fernet.generate_key().decode(),
    )
    database = Database(settings)
    app = create_app(settings=settings, database=database)

    async def test_principal(request: Request) -> Principal:
        return PRINCIPALS[request.headers.get("X-Test-Principal", "viewer")]

    app.dependency_overrides[current_principal] = test_principal
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            app.state.system.gateway = _Gateway()
            app.state.system.system_probe = _Probe()
            submission = {
                "items": [{"ticker": "SPCX", "analysis_date": str(date(2026, 7, 25))}],
                "analysts": ["market", "social", "news", "fundamentals"],
                "depth": "deep",
                "language": "Chinese",
                "idempotency_key": "rest-workflow-20260725",
            }

            viewer_submit = await client.post(
                "/api/v1/assessment-batches",
                headers=_headers("viewer"),
                json=submission,
            )
            submitted = await client.post(
                "/api/v1/assessment-batches",
                headers=_headers("analyst"),
                json=submission,
            )
            run_id = submitted.json()["items"][0]["id"]

            listed = await client.get(
                "/api/v1/assessments?ticker=spcx&limit=10",
                headers=_headers("analyst"),
            )
            other_cancel = await client.post(
                f"/api/v1/assessments/{run_id}/cancel",
                headers=_headers("other-analyst"),
                json={},
            )
            viewer_cancel = await client.post(
                f"/api/v1/assessments/{run_id}/cancel",
                headers=_headers("viewer"),
                json={},
            )
            admin_cancel = await client.post(
                f"/api/v1/assessments/{run_id}/cancel",
                headers=_headers("admin"),
                json={},
            )
            stream = await client.get(
                f"/api/v1/assessments/{run_id}/events?after=0",
                headers={**_headers("analyst"), "Accept": "text/event-stream"},
            )
            retried = await client.post(
                f"/api/v1/assessments/{run_id}/retry",
                headers=_headers("analyst"),
                json={},
            )
            retry_id = retried.json()["id"]
            compared = await client.post(
                "/api/v1/assessment-comparisons",
                headers=_headers("analyst"),
                json={"run_ids": [run_id, retry_id]},
            )

            source = tmp_path / "final-report.md"
            source.write_text("hash verified report", encoding="utf-8")
            store = LocalArtifactStore(settings.artifact_dir)
            run_uuid = uuid.UUID(run_id)
            stored = store.put(run_uuid, "final_report", "text/markdown", source)
            async with session_factory() as session, session.begin():
                artifact = Artifact(
                    run_id=run_uuid,
                    kind=stored.kind,
                    media_type=stored.media_type,
                    size=stored.size,
                    sha256=stored.sha256,
                    storage_key=stored.storage_key,
                    redacted=True,
                )
                session.add(artifact)
                await session.flush()
                artifact_id = artifact.id

            downloaded = await client.get(
                f"/api/v1/artifacts/{artifact_id}",
                headers=_headers("analyst"),
            )
            comment = await client.post(
                f"/api/v1/assessments/{run_id}/comments",
                headers=_headers("analyst"),
                json={"body": "等待后续价格验证"},
            )
            review = await client.post(
                f"/api/v1/assessments/{run_id}/reviews",
                headers=_headers("analyst"),
                json={"verdict": "approved", "comment": "证据链完整"},
            )
            capacity = await client.get(
                "/api/v1/system/capacity",
                headers=_headers("admin"),
            )

            stored_path = store.resolve(stored.storage_key)
            stored_path.write_text("tampered", encoding="utf-8")
            tampered = await client.get(
                f"/api/v1/artifacts/{artifact_id}",
                headers=_headers("analyst"),
            )

        assert viewer_submit.status_code == 403
        assert viewer_cancel.status_code == 403
        assert submitted.status_code == 202
        assert submitted.headers["Location"].endswith(run_id)
        assert listed.status_code == 200
        assert [item["ticker"] for item in listed.json()["items"]] == ["SPCX"]
        assert other_cancel.status_code == 403
        assert other_cancel.json()["error"]["code"] == "assessment_forbidden"
        assert admin_cancel.status_code == 202
        assert admin_cancel.json()["status"] == "cancelled"
        assert stream.status_code == 200
        assert "id: 1\ndata:" in stream.text
        assert "assessment.cancelled" in stream.text
        assert retried.status_code == 202
        assert retried.headers["Location"].endswith(retry_id)
        assert compared.status_code == 200
        assert compared.json()["changed_sections"]["status"] == [run_id, retry_id]
        assert downloaded.status_code == 200
        assert downloaded.text == "hash verified report"
        assert downloaded.headers["ETag"] == f'"sha256:{stored.sha256}"'
        assert comment.status_code == 201
        assert review.status_code == 201
        assert capacity.status_code == 200
        assert capacity.json()["hard_max_running_total"] == 32
        assert capacity.json()["gateway_model"] == "gpt-5.6-sol"
        assert tampered.status_code == 409
        assert tampered.json()["error"]["code"] == "artifact_integrity_error"

        async with session_factory() as session:
            actions = set(await session.scalars(select(AuditEvent.action)))
        assert {
            "assessment.submit",
            "assessment.cancel",
            "assessment.retry",
            "assessment.comment",
            "assessment.review",
        } <= actions
    finally:
        await database.close()
