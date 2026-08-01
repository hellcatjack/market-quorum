import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from tradingng_platform.assessments import service as service_module
from tradingng_platform.assessments.contracts import AssessmentItem, SubmitAssessments
from tradingng_platform.assessments.service import (
    AssessmentAccessDenied,
    AssessmentAnalystsIncompatible,
    AssessmentAssetTypeConflict,
    AssessmentDeleteNotAllowed,
    AssessmentService,
)
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.instruments import AssetType
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.instruments.classification import InstrumentClassification


def test_submission_memory_mode_defaults_to_independent_and_accepts_historical():
    base = {
        "items": [{"ticker": "NVDA", "analysis_date": "2026-07-25"}],
        "idempotency_key": "memory-mode-20260725",
    }

    independent = SubmitAssessments.model_validate(base)
    historical = SubmitAssessments.model_validate({**base, "memory_mode": "historical"})

    assert independent.memory_mode.value == "independent"
    assert historical.memory_mode.value == "historical"


class _AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakeSession(_AsyncContext):
    def begin(self):
        return _AsyncContext()

    def begin_nested(self):
        return _AsyncContext()

    async def flush(self):
        return None


class _FakeRepository:
    def __init__(self):
        self.user = SimpleNamespace(id=uuid.UUID(int=1))
        self.batches = {}
        self.runs = {}
        self.created_batch_count = 0
        self.audit_actions = []
        self.request_configs = []

    async def upsert_user(self, principal):
        return self.user

    async def find_batch(self, submitted_by, idempotency_key):
        batch = self.batches.get((submitted_by, idempotency_key))
        return None if batch is None else list(self.runs[batch.id])

    async def get_batch_submission_hash(self, submitted_by, idempotency_key):
        return None

    async def create_batch(self, user, command):
        batch = SimpleNamespace(id=uuid.uuid4())
        self.batches[(user.id, command.idempotency_key)] = batch
        self.runs[batch.id] = []
        self.created_batch_count += 1
        return batch

    async def get_or_create_instrument(self, ticker, asset_type, classification):
        assert classification.ticker == ticker
        return SimpleNamespace(
            id=uuid.uuid4(),
            canonical_ticker=ticker,
            asset_type=asset_type,
        )

    async def create_request_and_run(
        self,
        batch,
        instrument,
        item,
        request_config,
        *,
        initial_status=RunStatus.QUEUED,
        data_requirement=None,
    ):
        self.request_configs.append(request_config)
        run = SimpleNamespace(id=uuid.uuid4())
        self.runs[batch.id].append(
            service_module.RunView(
                id=run.id,
                request_id=uuid.uuid4(),
                ticker=instrument.canonical_ticker,
                asset_type=instrument.asset_type,
                analysis_date=item.analysis_date,
                status=initial_status,
                attempt=1,
                created_at=datetime.now(timezone.utc),
            )
        )
        return run

    async def append_audit(
        self,
        principal,
        action,
        object_type,
        object_id,
        request_id,
        metadata,
    ):
        self.audit_actions.append(action)

    async def list_batch_runs(self, batch_id):
        return list(self.runs[batch_id])


def _classification(ticker: str, asset_type: AssetType) -> InstrumentClassification:
    quote_types = {
        AssetType.STOCK: "EQUITY",
        AssetType.FUND: "ETF",
        AssetType.CRYPTO: "CRYPTOCURRENCY",
    }
    return InstrumentClassification(
        ticker=ticker,
        asset_type=asset_type,
        quote_type=quote_types[asset_type],
        source="test",
        source_symbol=ticker,
    )


class _Classifier:
    def __init__(self, values):
        self.values = values
        self.calls = []

    async def classify_many(self, tickers):
        self.calls.append(tickers)
        return {ticker: self.values[ticker] for ticker in tickers}


def _principal():
    return Principal(
        "issuer",
        "alice",
        "user",
        frozenset({"assessments:submit"}),
        roles=frozenset({"Analyst"}),
    )


def _admin():
    return Principal(
        "issuer",
        "admin",
        "user",
        frozenset({"assessments:admin"}),
        roles=frozenset({"Admin"}),
    )


class _DeleteRepository:
    def __init__(
        self,
        status=RunStatus.SUCCEEDED,
        *,
        dependent_run_ids=(),
        active_work=False,
    ):
        self.context = SimpleNamespace(
            run=SimpleNamespace(
                id=uuid.UUID(int=101),
                status=status.value,
                config_snapshot_id=uuid.UUID(int=105),
            ),
            request=SimpleNamespace(
                id=uuid.UUID(int=102),
                batch_id=uuid.UUID(int=103),
                analysis_date=date(2026, 7, 25),
            ),
            instrument=SimpleNamespace(canonical_ticker="NVDA"),
            batch=SimpleNamespace(id=uuid.UUID(int=103)),
            owner=SimpleNamespace(issuer="issuer", subject="owner"),
        )
        self.dependent_run_ids = tuple(dependent_run_ids)
        self.active_work = active_work
        self.deleted = False
        self.audit = None

    async def get_run_context(self, run_id, for_update=False):
        assert for_update is True
        return self.context

    async def find_dependent_run_ids(self, run_id):
        return self.dependent_run_ids

    async def has_active_work(self, run_id):
        return self.active_work

    async def delete_assessment_graph(self, context):
        self.deleted = True
        return {"artifacts": 2, "events": 3}

    async def append_audit(
        self,
        principal,
        action,
        object_type,
        object_id,
        request_id,
        metadata,
    ):
        self.audit = {
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "request_id": request_id,
            "metadata": metadata,
        }


async def test_submit_is_idempotent_and_audited(monkeypatch):
    repository = _FakeRepository()
    monkeypatch.setattr(service_module, "AssessmentRepository", lambda session: repository)
    classifier = _Classifier(
        {
            "NVDA": _classification("NVDA", AssetType.STOCK),
            "TSLA": _classification("TSLA", AssetType.STOCK),
        }
    )
    service = AssessmentService(lambda: _FakeSession(), classifier)
    command = SubmitAssessments(
        items=[
            AssessmentItem(ticker=" nvda ", analysis_date=date(2026, 7, 25)),
            AssessmentItem(ticker="tsla", analysis_date=date(2026, 7, 25)),
        ],
        idempotency_key="portfolio-20260725",
    )

    result1 = await service.submit(_principal(), command, request_id="req-1")
    result2 = await service.submit(_principal(), command, request_id="req-2")

    assert [run.id for run in result1] == [run.id for run in result2]
    assert [run.ticker for run in result1] == ["NVDA", "TSLA"]
    assert repository.created_batch_count == 1
    assert repository.audit_actions == ["assessment.submit"]


async def test_submit_resolves_each_asset_and_derives_compatible_analysts(monkeypatch):
    repository = _FakeRepository()
    monkeypatch.setattr(service_module, "AssessmentRepository", lambda session: repository)
    classifier = _Classifier(
        {
            "NVDA": _classification("NVDA", AssetType.STOCK),
            "GLD": _classification("GLD", AssetType.FUND),
            "BTC-USD": _classification("BTC-USD", AssetType.CRYPTO),
        }
    )
    service = AssessmentService(lambda: _FakeSession(), classifier)
    command = SubmitAssessments(
        items=[
            AssessmentItem(ticker=ticker, analysis_date=date(2026, 7, 25))
            for ticker in ("NVDA", "GLD", "BTC-USD")
        ],
        analysts=("market", "fundamentals"),
        idempotency_key="mixed-assets-20260725",
    )

    runs = await service.submit(_principal(), command, request_id="req-mixed")

    assert [run.asset_type for run in runs] == ["stock", "fund", "crypto"]
    assert repository.request_configs == [
        {"analysts": ["market", "fundamentals"]},
        {"analysts": ["market"]},
        {"analysts": ["market"]},
    ]
    assert classifier.calls == [("NVDA", "GLD", "BTC-USD")]


async def test_submit_accepts_matching_legacy_asset_type_assertion(monkeypatch):
    repository = _FakeRepository()
    monkeypatch.setattr(service_module, "AssessmentRepository", lambda session: repository)
    classifier = _Classifier({"GLD": _classification("GLD", AssetType.FUND)})
    service = AssessmentService(lambda: _FakeSession(), classifier)

    runs = await service.submit(
        _principal(),
        SubmitAssessments(
            items=[
                AssessmentItem(
                    ticker="GLD",
                    asset_type=AssetType.FUND,
                    analysis_date=date(2026, 7, 25),
                )
            ],
            idempotency_key="matching-type-20260725",
        ),
        request_id="req-match",
    )

    assert runs[0].asset_type == "fund"


async def test_submit_rejects_conflicting_asset_type_before_creating_batch(monkeypatch):
    repository = _FakeRepository()
    monkeypatch.setattr(service_module, "AssessmentRepository", lambda session: repository)
    classifier = _Classifier({"GLD": _classification("GLD", AssetType.FUND)})
    service = AssessmentService(lambda: _FakeSession(), classifier)
    command = SubmitAssessments(
        items=[
            AssessmentItem(
                ticker="GLD",
                asset_type=AssetType.STOCK,
                analysis_date=date(2026, 7, 25),
            )
        ],
        idempotency_key="conflicting-type-20260725",
    )

    with pytest.raises(AssessmentAssetTypeConflict) as captured:
        await service.submit(_principal(), command, request_id="req-conflict")

    assert captured.value.ticker == "GLD"
    assert captured.value.requested is AssetType.STOCK
    assert captured.value.resolved is AssetType.FUND
    assert repository.created_batch_count == 0


async def test_stocklean_waiting_submission_creates_data_gate_without_yahoo(monkeypatch):
    repository = _FakeRepository()
    captured = {}

    async def create_request_and_run(
        batch,
        instrument,
        item,
        request_config,
        *,
        initial_status=RunStatus.QUEUED,
        data_requirement=None,
    ):
        captured["status"] = initial_status
        captured["requirement"] = data_requirement
        return await _FakeRepository.create_request_and_run(
            repository,
            batch,
            instrument,
            item,
            request_config,
            initial_status=initial_status,
            data_requirement=data_requirement,
        )

    repository.create_request_and_run = create_request_and_run
    monkeypatch.setattr(service_module, "AssessmentRepository", lambda session: repository)

    class StockLean:
        calls = 0

        async def resolve_candidates(self, *, subject_ref, items):
            from tradingng_platform.vendors.stocklean import (
                StockLeanResearchCandidateResponse,
            )

            self.calls += 1
            return StockLeanResearchCandidateResponse.model_validate(
                {
                    "contract_version": "stocklean.research-intake.v1",
                    "items": [
                        {
                            "external_request_key": items[0]["external_request_key"],
                            "candidate_request_id": 42,
                            "candidate_id": 7,
                            "symbol": "XYZ",
                            "scope": "research",
                            "identity": {
                                "asset_type": "stock",
                                "exchange": "NASDAQ",
                                "name": "Example",
                                "vendor_symbol": "XYZ",
                            },
                            "readiness": "waiting",
                            "required_products": ["market", "fundamental"],
                            "job": {
                                "batch_id": 5,
                                "stage": "queued",
                                "completed_items": 0,
                                "total_items": 2,
                            },
                        }
                    ],
                }
            )

    stocklean = StockLean()
    service = AssessmentService(lambda: _FakeSession(), classifier=None, stocklean_client=stocklean)
    principal = Principal(
        "issuer",
        "alice",
        "user",
        frozenset({"assessments:submit", "research_symbols:enroll"}),
    )
    command = SubmitAssessments(
        items=[AssessmentItem(ticker="XYZ", analysis_date=date(2026, 7, 31))],
        analysts=("market", "fundamentals"),
        idempotency_key="stocklean-waiting-xyz",
    )

    first = await service.submit(principal, command, request_id="request-1")
    second = await service.submit(principal, command, request_id="request-2")

    assert first[0].status is RunStatus.WAITING_FOR_DATA
    assert second[0].id == first[0].id
    assert stocklean.calls == 1
    assert captured["status"] is RunStatus.WAITING_FOR_DATA
    assert captured["requirement"]["provider_request_id"] == "42"


async def test_retry_without_pinned_manifest_reenters_stocklean_data_gate(monkeypatch):
    source_id = uuid.uuid4()
    retry_id = uuid.uuid4()
    principal = Principal(
        "issuer",
        "alice",
        "user",
        frozenset({"assessments:submit"}),
        roles=frozenset({"Analyst"}),
    )
    source_context = SimpleNamespace(
        run=SimpleNamespace(id=source_id, status="failed", attempt=1),
        request=SimpleNamespace(
            analysis_date=date(2026, 7, 31),
            requested_config_json={"analysts": ["market"]},
        ),
        instrument=SimpleNamespace(canonical_ticker="XYZ", asset_type="stock"),
        batch=SimpleNamespace(
            defaults_json={
                "analysts": ["market"],
                "depth": "deep",
                "memory_mode": "independent",
                "language": "Chinese",
            }
        ),
        owner=SimpleNamespace(issuer="issuer", subject="alice"),
    )
    captured = {}

    class Repository:
        async def get_run_context(self, run_id, for_update=False):
            if run_id == source_id:
                return source_context
            return SimpleNamespace(
                run=SimpleNamespace(
                    id=retry_id,
                    status="waiting_for_data",
                    attempt=2,
                    created_at=datetime.now(timezone.utc),
                ),
                request=source_context.request,
                instrument=source_context.instrument,
            )

        async def create_retry(self, context, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id=retry_id, attempt=2)

        async def append_audit(self, *args, **kwargs):
            return None

        def _run_view(self, run, request, instrument):
            return service_module.RunView(
                id=run.id,
                request_id=uuid.uuid4(),
                ticker=instrument.canonical_ticker,
                asset_type=instrument.asset_type,
                analysis_date=request.analysis_date,
                status=RunStatus(run.status),
                attempt=run.attempt,
                created_at=run.created_at,
            )

    monkeypatch.setattr(service_module, "AssessmentRepository", lambda session: Repository())

    class StockLean:
        async def resolve_candidates(self, *, subject_ref, items):
            from tradingng_platform.vendors.stocklean import StockLeanResearchCandidateResponse

            return StockLeanResearchCandidateResponse.model_validate(
                {
                    "contract_version": "stocklean.research-intake.v1",
                    "items": [
                        {
                            "external_request_key": items[0]["external_request_key"],
                            "candidate_request_id": 84,
                            "candidate_id": 9,
                            "symbol": "XYZ",
                            "scope": "production",
                            "identity": {
                                "asset_type": "stock",
                                "exchange": "NASDAQ",
                                "name": "Example",
                                "vendor_symbol": "XYZ",
                            },
                            "readiness": "waiting",
                            "required_products": ["market", "validation", "integrity"],
                            "job": {
                                "batch_id": 6,
                                "stage": "queued",
                                "completed_items": 0,
                                "total_items": 3,
                            },
                        }
                    ],
                }
            )

    service = AssessmentService(
        lambda: _FakeSession(),
        classifier=None,
        stocklean_client=StockLean(),
    )

    retried = await service.retry(principal, source_id, "retry-request")

    assert retried.status is RunStatus.WAITING_FOR_DATA
    assert captured["initial_status"] is RunStatus.WAITING_FOR_DATA
    assert captured["data_requirement"]["provider_request_id"] == "84"
    assert "data_manifest" not in captured["request_config"]


async def test_submit_rejects_asset_with_no_compatible_analysts(monkeypatch):
    repository = _FakeRepository()
    monkeypatch.setattr(service_module, "AssessmentRepository", lambda session: repository)
    classifier = _Classifier({"BTC-USD": _classification("BTC-USD", AssetType.CRYPTO)})
    service = AssessmentService(lambda: _FakeSession(), classifier)
    command = SubmitAssessments(
        items=[AssessmentItem(ticker="BTC-USD", analysis_date=date(2026, 7, 25))],
        analysts=("fundamentals",),
        idempotency_key="incompatible-analysts-20260725",  # gitleaks:allow
    )

    with pytest.raises(AssessmentAnalystsIncompatible) as captured:
        await service.submit(_principal(), command, request_id="req-analysts")

    assert captured.value.ticker == "BTC-USD"
    assert captured.value.asset_type is AssetType.CRYPTO
    assert repository.created_batch_count == 0


async def test_delete_requires_admin_role_even_with_admin_scope(monkeypatch):
    repository = _DeleteRepository()
    monkeypatch.setattr(service_module, "AssessmentRepository", lambda session: repository)
    service = AssessmentService(lambda: _FakeSession(), _Classifier({}))
    analyst = Principal(
        "issuer",
        "alice",
        "user",
        frozenset({"assessments:admin"}),
        roles=frozenset({"Analyst"}),
    )

    with pytest.raises(AssessmentAccessDenied):
        await service.delete(analyst, uuid.UUID(int=101), "request-delete")

    assert repository.deleted is False


@pytest.mark.parametrize(
    ("repository", "reason"),
    [
        (_DeleteRepository(RunStatus.RUNNING_ANALYSTS), "run_not_terminal"),
        (_DeleteRepository(active_work=True), "active_work"),
        (
            _DeleteRepository(dependent_run_ids=(uuid.UUID(int=201),)),
            "dependent_runs_exist",
        ),
    ],
)
async def test_delete_rejects_unsafe_state(monkeypatch, repository, reason):
    monkeypatch.setattr(service_module, "AssessmentRepository", lambda session: repository)
    service = AssessmentService(lambda: _FakeSession(), _Classifier({}))

    with pytest.raises(AssessmentDeleteNotAllowed) as captured:
        await service.delete(_admin(), uuid.UUID(int=101), "request-delete")

    assert captured.value.reason == reason
    assert repository.deleted is False


async def test_delete_terminal_assessment_records_durable_audit(monkeypatch):
    repository = _DeleteRepository()
    monkeypatch.setattr(service_module, "AssessmentRepository", lambda session: repository)
    service = AssessmentService(lambda: _FakeSession(), _Classifier({}))

    deleted = await service.delete(_admin(), uuid.UUID(int=101), "request-delete")

    assert deleted.run_id == uuid.UUID(int=101)
    assert deleted.ticker == "NVDA"
    assert deleted.analysis_date == date(2026, 7, 25)
    assert deleted.status is RunStatus.SUCCEEDED
    assert repository.deleted is True
    assert repository.audit == {
        "action": "assessment.delete",
        "object_type": "assessment_run",
        "object_id": str(uuid.UUID(int=101)),
        "request_id": "request-delete",
        "metadata": {
            "ticker": "NVDA",
            "analysis_date": "2026-07-25",
            "status": "succeeded",
            "request_id": str(uuid.UUID(int=102)),
            "batch_id": str(uuid.UUID(int=103)),
            "deleted": {"artifacts": 2, "events": 3},
        },
    }
