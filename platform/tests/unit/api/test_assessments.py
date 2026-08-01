import uuid
from datetime import date, datetime, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tradingng_platform.api.app import create_app
from tradingng_platform.api.auth import current_principal
from tradingng_platform.assessments.contracts import (
    ComparisonView,
    RunEventView,
    RunPage,
    RunStepView,
    RunView,
)
from tradingng_platform.assessments.service import (
    AssessmentAnalystsIncompatible,
    AssessmentAssetTypeConflict,
    AssessmentDeleteNotAllowed,
    AssessmentInstrumentIdentityConflict,
    DeletedAssessment,
)
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.instruments import AssetType
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.instruments.classification import (
    InstrumentClassificationNotFound,
    InstrumentClassificationUnavailable,
    InstrumentTypeUnsupported,
)
from tradingng_platform.integrity.contracts import IntegritySummaryView, IntegrityView
from tradingng_platform.integrity.service import (
    CleanReassessmentNotAllowed,
    IntegrityNotFound,
)

RUN_ID = uuid.UUID("00000000-0000-0000-0000-000000000101")
RETRY_ID = uuid.UUID("00000000-0000-0000-0000-000000000102")


def _environment(monkeypatch):
    monkeypatch.setenv(
        "TRADINGNG_DATABASE_URL",
        "postgresql+psycopg://tradingng:test@127.0.0.1:5432/tradingng",
    )
    monkeypatch.setenv("TRADINGNG_TOKEN_PEPPER", "unit-test-pepper-with-enough-entropy")
    monkeypatch.setenv("TRADINGNG_WEBHOOK_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _principal(scopes=None):
    return Principal(
        "issuer",
        "alice",
        "user",
        frozenset(
            scopes
            or {
                "assessments:submit",
                "assessments:read",
                "assessments:cancel",
            }
        ),
        roles=frozenset({"Analyst"}),
    )


def _admin_principal():
    return Principal(
        "issuer",
        "admin",
        "user",
        frozenset({"assessments:read", "assessments:admin"}),
        roles=frozenset({"Admin"}),
    )


def _run(run_id=RUN_ID, status=RunStatus.QUEUED):
    return RunView(
        id=run_id,
        request_id=uuid.UUID(int=2),
        ticker="NVDA",
        asset_type="stock",
        analysis_date=date(2026, 7, 25),
        status=status,
        attempt=1 if run_id == RUN_ID else 2,
        created_at=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
    )


class _Assessments:
    def __init__(self):
        self.submissions = {}
        self.deleted = []

    async def submit(self, principal, command, request_id):
        self.submissions.setdefault(command.idempotency_key, [_run()])
        return self.submissions[command.idempotency_key]

    async def get(self, principal, run_id):
        return _run() if run_id == RUN_ID else None

    async def list(self, principal, filters):
        return RunPage(items=[_run()], next_cursor=None)

    async def steps(self, principal, run_id):
        return [
            RunStepView(
                name="running_analysts",
                status="running",
                attempt=1,
                started_at=datetime(2026, 7, 25, 12, 1, tzinfo=timezone.utc),
                finished_at=None,
                error_code=None,
                summary=None,
            )
        ]

    async def events(self, principal, run_id, after=0, limit=200):
        return [
            RunEventView(
                sequence=1,
                event_type="assessment.succeeded",
                payload={},
                created_at=datetime(2026, 7, 25, 12, 2, tzinfo=timezone.utc),
            )
        ]

    async def cancel(self, principal, run_id, request_id):
        return _run(status=RunStatus.CANCEL_REQUESTED)

    async def retry(self, principal, run_id, request_id):
        return _run(RETRY_ID)

    async def delete(self, principal, run_id, request_id):
        self.deleted.append((principal, run_id, request_id))
        return DeletedAssessment(
            run_id=run_id,
            ticker="NVDA",
            analysis_date=date(2026, 7, 25),
            status=RunStatus.SUCCEEDED,
        )

    async def compare(self, principal, run_ids):
        return ComparisonView(
            runs=[_run(), _run(RETRY_ID)],
            ratings={RUN_ID: "Hold", RETRY_ID: "Buy"},
            changed_sections={"rating": [RUN_ID, RETRY_ID]},
        )


class _FailingAssessments(_Assessments):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def submit(self, principal, command, request_id):
        raise self.error


class _DeleteFailingAssessments(_Assessments):
    async def delete(self, principal, run_id, request_id):
        raise AssessmentDeleteNotAllowed(
            "dependent_runs_exist",
            status=RunStatus.SUCCEEDED,
            dependent_run_ids=(RETRY_ID,),
        )


class _Integrity:
    def __init__(self, error=None):
        self.error = error

    async def get(self, principal, run_id):
        if self.error is not None:
            raise self.error
        return IntegrityView(
            run_id=run_id,
            status="at_risk",
            audit_mode="retrospective",
            temporal_scope="historical_reconstruction",
            analysis_date=date(2026, 7, 25),
            checked_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
            reason_codes=("future_publication_exposed",),
            input_fingerprint="a" * 64,
        )

    async def summary(self, principal):
        return IntegritySummaryView(
            total=3,
            safe=1,
            at_risk=1,
            unknown=0,
            unassessed=1,
            eligible_count=1,
            excluded_at_risk_count=1,
            excluded_unknown_count=0,
        )

    async def clean_reassess(self, principal, run_id, request_id):
        if self.error is not None:
            raise self.error
        return _run(RETRY_ID)


def _client(monkeypatch, principal=None):
    _environment(monkeypatch)
    app = create_app()
    app.dependency_overrides[current_principal] = lambda: principal or _principal()
    return app


def test_submit_is_accepted_idempotent_and_location_is_stable(monkeypatch):
    app = _client(monkeypatch)
    service = _Assessments()
    body = {
        "items": [{"ticker": "NVDA", "analysis_date": "2026-07-25"}],
        "idempotency_key": "dispatch-20260725",
    }
    with TestClient(app) as client:
        app.state.assessments = service
        first = client.post("/api/v1/assessments", json=body)
        second = client.post("/api/v1/assessments", json=body)

    assert first.status_code == 202
    assert first.headers["Location"] == f"/api/v1/assessments/{RUN_ID}"
    assert first.json()["items"][0]["id"] == str(RUN_ID)
    assert second.json()["items"][0]["id"] == str(RUN_ID)


def test_user_can_read_only_sanitized_assessment_admission_summary(monkeypatch):
    user = Principal(
        issuer="https://issuer.example",
        subject="user-sub",
        actor_type="user",
        scopes=frozenset({"assessments:read"}),
        roles=frozenset({"User"}),
    )
    app = _client(monkeypatch, user)

    class System:
        async def admission_summary(self, principal):
            principal.require("assessments:read")
            return {
                "running": 2,
                "max_running": 4,
                "waiting_for_data": 5,
                "oldest_waiting_seconds": 45,
                "queued": 3,
                "oldest_queued_seconds": 18,
                "admission": "queued",
                "reason": "capacity_busy",
            }

    with TestClient(app) as client:
        app.state.system = System()
        response = client.get("/api/v1/assessments/admission-summary")
        diagnostics = client.get("/api/v1/system/capacity")

    assert response.status_code == 200
    assert response.json() == {
        "running": 2,
        "max_running": 4,
        "waiting_for_data": 5,
        "oldest_waiting_seconds": 45,
        "queued": 3,
        "oldest_queued_seconds": 18,
        "admission": "queued",
        "reason": "capacity_busy",
    }
    forbidden_fragments = {
        "gateway",
        "model",
        "reasoning",
        "circuit",
        "vendor",
        "cpu",
        "memory",
        "hard_max",
    }
    assert not any(fragment in response.text.casefold() for fragment in forbidden_fragments)
    assert diagnostics.status_code == 403


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (
            AssessmentAssetTypeConflict("GLD", AssetType.STOCK, AssetType.FUND),
            422,
            "asset_type_conflict",
        ),
        (
            AssessmentInstrumentIdentityConflict("GLD", "fund", AssetType.STOCK),
            409,
            "instrument_identity_conflict",
        ),
        (
            AssessmentAnalystsIncompatible("GLD", AssetType.FUND),
            422,
            "incompatible_analysts",
        ),
        (
            InstrumentClassificationNotFound("UNKNOWN"),
            422,
            "instrument_not_found",
        ),
        (
            InstrumentTypeUnsupported("ES=F", "FUTURE"),
            422,
            "instrument_type_unsupported",
        ),
        (
            InstrumentClassificationUnavailable("NVDA"),
            503,
            "instrument_classification_unavailable",
        ),
    ],
)
def test_submit_translates_classification_failures(
    monkeypatch,
    error,
    status_code,
    code,
):
    app = _client(monkeypatch)
    with TestClient(app) as client:
        app.state.assessments = _FailingAssessments(error)
        response = client.post(
            "/api/v1/assessments",
            json={
                "items": [{"ticker": "GLD", "analysis_date": "2026-07-25"}],
                "idempotency_key": "classification-error-20260725",
            },
        )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


def test_list_get_steps_cancel_retry_and_compare_contract(monkeypatch):
    app = _client(monkeypatch)
    with TestClient(app) as client:
        app.state.assessments = _Assessments()
        assert client.get("/api/v1/assessments").status_code == 200
        assert client.get(f"/api/v1/assessments/{RUN_ID}").status_code == 200
        assert (
            client.get(f"/api/v1/assessments/{RUN_ID}/steps").json()[0]["name"]
            == "running_analysts"
        )
        cancelled = client.post(f"/api/v1/assessments/{RUN_ID}/cancel", json={})
        retried = client.post(f"/api/v1/assessments/{RUN_ID}/retry", json={})
        compared = client.post(
            "/api/v1/assessment-comparisons",
            json={"run_ids": [str(RUN_ID), str(RETRY_ID)]},
        )

    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancel_requested"
    assert retried.status_code == 202
    assert retried.headers["Location"].endswith(str(RETRY_ID))
    assert compared.json()["ratings"][str(RETRY_ID)] == "Buy"


def test_unknown_run_uses_stable_not_found_error(monkeypatch):
    app = _client(monkeypatch)
    with TestClient(app) as client:
        app.state.assessments = _Assessments()
        response = client.get(f"/api/v1/assessments/{uuid.UUID(int=999)}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "assessment_not_found"


def test_scope_is_enforced_before_submission(monkeypatch):
    app = _client(monkeypatch, _principal({"assessments:read"}))
    with TestClient(app) as client:
        app.state.assessments = _Assessments()
        response = client.post(
            "/api/v1/assessments",
            json={
                "items": [{"ticker": "NVDA", "analysis_date": "2026-07-25"}],
                "idempotency_key": "dispatch-20260725",
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_admin_can_delete_terminal_assessment(monkeypatch):
    app = _client(monkeypatch, _admin_principal())
    service = _Assessments()
    with TestClient(app) as client:
        app.state.assessments = service
        response = client.delete(f"/api/v1/assessments/{RUN_ID}")

    assert response.status_code == 204
    assert response.content == b""
    assert len(service.deleted) == 1
    assert service.deleted[0][1] == RUN_ID


def test_delete_requires_admin_scope(monkeypatch):
    app = _client(monkeypatch, _principal({"assessments:read"}))
    with TestClient(app) as client:
        app.state.assessments = _Assessments()
        response = client.delete(f"/api/v1/assessments/{RUN_ID}")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_delete_conflict_has_stable_machine_readable_details(monkeypatch):
    app = _client(monkeypatch, _admin_principal())
    with TestClient(app) as client:
        app.state.assessments = _DeleteFailingAssessments()
        response = client.delete(f"/api/v1/assessments/{RUN_ID}")

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "delete_not_allowed",
        "message": "Assessment cannot be deleted in its current state",
        "request_id": response.headers["X-Request-ID"],
        "details": {
            "reason": "dependent_runs_exist",
            "status": "succeeded",
            "dependent_run_ids": [str(RETRY_ID)],
        },
    }


def test_events_support_json_and_terminal_sse_replay(monkeypatch):
    app = _client(monkeypatch)
    with TestClient(app) as client:
        app.state.assessments = _Assessments()
        page = client.get(f"/api/v1/assessments/{RUN_ID}/events?after=0")
        stream = client.get(
            f"/api/v1/assessments/{RUN_ID}/events",
            headers={"Accept": "text/event-stream", "Last-Event-ID": "0"},
        )

    assert page.json()["items"][0]["sequence"] == 1
    assert stream.status_code == 200
    assert "id: 1\ndata:" in stream.text
    assert "event: assessment.succeeded" not in stream.text
    assert '"sequence":1' in stream.text


def test_integrity_detail_summary_and_clean_reassessment_contract(monkeypatch):
    principal = Principal(
        "issuer",
        "admin",
        "user",
        frozenset({"assessments:read", "assessments:admin", "assessments:submit"}),
        roles=frozenset({"Admin"}),
    )
    app = _client(monkeypatch, principal)
    with TestClient(app) as client:
        app.state.integrity = _Integrity()
        detail = client.get(f"/api/v1/assessments/{RUN_ID}/integrity")
        summary = client.get("/api/v1/integrity/summary")
        clean = client.post(
            f"/api/v1/assessments/{RUN_ID}/clean-reassessment",
            json={},
        )

    assert detail.status_code == 200
    assert detail.json()["status"] == "at_risk"
    assert summary.json()["unassessed"] == 1
    assert clean.status_code == 202
    assert clean.headers["Location"].endswith(str(RETRY_ID))
    assert clean.json()["id"] == str(RETRY_ID)


def test_integrity_routes_translate_missing_and_conflict(monkeypatch):
    principal = Principal(
        "issuer",
        "admin",
        "user",
        frozenset({"assessments:read", "assessments:admin", "assessments:submit"}),
        roles=frozenset({"Admin"}),
    )
    app = _client(monkeypatch, principal)
    with TestClient(app) as client:
        app.state.integrity = _Integrity(IntegrityNotFound(RUN_ID))
        missing = client.get(f"/api/v1/assessments/{RUN_ID}/integrity")
        app.state.integrity = _Integrity(CleanReassessmentNotAllowed("source_run_is_safe"))
        conflict = client.post(
            f"/api/v1/assessments/{RUN_ID}/clean-reassessment",
            json={},
        )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "assessment_not_found"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "clean_reassessment_not_allowed"
