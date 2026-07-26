import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tradingng_platform.api.app import create_app
from tradingng_platform.api.auth import current_principal
from tradingng_platform.auth.principal import Principal
from tradingng_platform.auth.tokens import ApiCredentialView, CreatedApiCredentialView
from tradingng_platform.records.contracts import (
    ArtifactView,
    CommentView,
    DecisionView,
    EvidenceView,
    InstrumentSummaryView,
    OpenedArtifact,
    ReviewView,
)
from tradingng_platform.system.contracts import CapacityView, SchedulerPolicyView

RUN_ID = uuid.UUID("00000000-0000-0000-0000-000000000201")
ARTIFACT_ID = uuid.UUID("00000000-0000-0000-0000-000000000202")
NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _environment(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "TRADINGNG_DATABASE_URL",
        "postgresql+psycopg://tradingng:test@127.0.0.1:5432/tradingng",
    )
    monkeypatch.setenv("TRADINGNG_TOKEN_PEPPER", "unit-test-pepper-with-enough-entropy")
    monkeypatch.setenv("TRADINGNG_WEBHOOK_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TRADINGNG_DATA_DIR", str(tmp_path))


def _principal(scopes=None):
    return Principal(
        "issuer",
        "admin",
        "user",
        frozenset(
            scopes
            or {
                "assessments:read",
                "assessments:review",
                "artifacts:read",
                "system:read",
                "assessments:admin",
            }
        ),
        roles=frozenset({"Admin"}),
    )


class _Records:
    def __init__(self, artifact_path: Path):
        self.artifact_path = artifact_path

    async def decision(self, principal, run_id):
        return DecisionView(
            run_id=run_id,
            rating="Hold",
            executive_summary="Wait",
            investment_thesis="Balanced",
            price_target="100.0",
            time_horizon="5 days",
            structured={},
        )

    async def evidence(self, principal, run_id):
        return [
            EvidenceView(
                id=uuid.UUID(int=3),
                source="yfinance",
                tool_name="get_stock_data",
                arguments={"ticker": "NVDA"},
                collected_at=NOW,
                effective_at=None,
                freshness=None,
                content_hash="a" * 64,
            )
        ]

    async def list_artifacts(self, principal, run_id):
        return [
            ArtifactView(
                id=ARTIFACT_ID,
                run_id=run_id,
                kind="report_1_complete",
                media_type="text/markdown",
                size=6,
                sha256="b" * 64,
                created_at=NOW,
            )
        ]

    async def open_artifact(self, principal, artifact_id):
        return OpenedArtifact(
            id=artifact_id,
            path=self.artifact_path,
            media_type="text/markdown",
            filename="report_1_complete.md",
            sha256="b" * 64,
        )

    async def add_review(self, principal, run_id, verdict, comment, request_id):
        return ReviewView(
            id=uuid.UUID(int=4),
            run_id=run_id,
            reviewer="Admin",
            verdict=verdict,
            comment=comment,
            created_at=NOW,
        )

    async def list_reviews(self, principal, run_id):
        return []

    async def add_comment(self, principal, run_id, body, request_id):
        return CommentView(
            id=uuid.UUID(int=5),
            run_id=run_id,
            author="Admin",
            body=body,
            created_at=NOW,
        )

    async def list_comments(self, principal, run_id):
        return []

    async def instrument_summary(self, principal, ticker):
        return InstrumentSummaryView(
            ticker=ticker,
            asset_types=["stock"],
            assessment_count=2,
            latest_run_id=RUN_ID,
            latest_rating="Hold",
            latest_created_at=NOW,
        )

    async def instrument_history(self, principal, ticker, limit):
        return []


class _System:
    async def status(self, principal):
        return {"gateway": {"status": "ok"}, "workers": [], "circuits": []}

    async def capacity(self, principal):
        return CapacityView(
            admitted_or_running=1,
            max_running_total=2,
            hard_max_running_total=3,
            queued=4,
            oldest_queued_seconds=30,
            gateway_active_completions=1,
            gateway_model="gpt-5.6-sol",
            gateway_reasoning_effort="xhigh",
            open_circuits=[],
            admission_allowed=True,
            admission_reasons=[],
        )

    async def get_scheduler_policy(self, principal):
        return SchedulerPolicyView(
            max_running_total=2,
            hard_max_running_total=3,
            gateway_active_limit=3,
            cpu_limit_percent=85,
            minimum_memory_gib=8,
            minimum_disk_gib=10,
            minimum_disk_percent=10,
            version=1,
            updated_at=NOW,
        )

    async def update_scheduler_policy(self, principal, command, request_id):
        return SchedulerPolicyView(**command.model_dump(), version=2, updated_at=NOW)


class _Tokens:
    async def create(self, principal, scopes, expires_at=None, request_id=None):
        return CreatedApiCredentialView(
            id=uuid.UUID(int=6),
            token="tng_public_raw-once",
            scopes=scopes,
            expires_at=expires_at,
        )

    async def list(self, principal):
        return [
            ApiCredentialView(
                id=uuid.UUID(int=6),
                public_id="public",
                scopes={"assessments:read"},
                expires_at=None,
                last_used_at=None,
                revoked_at=None,
                created_at=NOW,
            )
        ]

    async def revoke(self, principal, credential_id, request_id):
        return None


def _app(monkeypatch, tmp_path, principal=None):
    _environment(monkeypatch, tmp_path)
    app = create_app()
    app.dependency_overrides[current_principal] = lambda: principal or _principal()
    return app


def test_records_collaboration_instruments_and_artifact_download(monkeypatch, tmp_path):
    artifact = tmp_path / "report.md"
    artifact.write_text("report", encoding="utf-8")
    app = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        app.state.records = _Records(artifact)
        decision = client.get(f"/api/v1/assessments/{RUN_ID}/decision")
        evidence = client.get(f"/api/v1/assessments/{RUN_ID}/evidence")
        artifacts = client.get(f"/api/v1/assessments/{RUN_ID}/artifacts")
        download = client.get(f"/api/v1/artifacts/{ARTIFACT_ID}")
        review = client.post(
            f"/api/v1/assessments/{RUN_ID}/reviews",
            json={"verdict": "approved", "comment": "Reviewed"},
        )
        comment = client.post(
            f"/api/v1/assessments/{RUN_ID}/comments",
            json={"body": "Watch valuation"},
        )
        instrument = client.get("/api/v1/instruments/NVDA")

    assert decision.json()["rating"] == "Hold"
    assert evidence.json()[0]["source"] == "yfinance"
    assert "storage_key" not in artifacts.text
    assert str(tmp_path) not in artifacts.text
    assert download.text == "report"
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in download.headers["Content-Disposition"]
    assert review.status_code == 201
    assert comment.status_code == 201
    assert instrument.json()["assessment_count"] == 2


def test_system_policy_and_api_credentials_never_list_raw_or_hash(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        app.state.system = _System()
        app.state.api_tokens = _Tokens()
        capacity = client.get("/api/v1/system/capacity")
        policy = client.put(
            "/api/v1/system/scheduler-policy",
            json={
                "max_running_total": 2,
                "hard_max_running_total": 3,
                "gateway_active_limit": 3,
                "cpu_limit_percent": 85,
                "minimum_memory_gib": 8,
                "minimum_disk_gib": 10,
                "minimum_disk_percent": 10,
            },
        )
        created = client.post(
            "/api/v1/api-credentials",
            json={"scopes": ["assessments:read"]},
        )
        listed = client.get("/api/v1/api-credentials")
        revoked = client.delete(f"/api/v1/api-credentials/{uuid.UUID(int=6)}")

    assert capacity.json()["hard_max_running_total"] == 3
    assert policy.json()["version"] == 2
    assert created.json()["token"] == "tng_public_raw-once"
    assert "token" not in listed.text
    assert "hash" not in listed.text
    assert revoked.status_code == 204


def test_artifact_download_requires_dedicated_scope(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, _principal({"assessments:read"}))
    with TestClient(app) as client:
        app.state.records = _Records(tmp_path / "unused")
        response = client.get(f"/api/v1/artifacts/{ARTIFACT_ID}")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"
