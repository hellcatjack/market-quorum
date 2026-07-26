import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from tradingng_platform.assessments import service as service_module
from tradingng_platform.assessments.contracts import AssessmentItem, SubmitAssessments
from tradingng_platform.assessments.service import (
    AssessmentAnalystsIncompatible,
    AssessmentAssetTypeConflict,
    AssessmentService,
)
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.instruments import AssetType
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.instruments.classification import InstrumentClassification


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

    async def create_request_and_run(self, batch, instrument, item, request_config):
        self.request_configs.append(request_config)
        run = SimpleNamespace(id=uuid.uuid4())
        self.runs[batch.id].append(
            service_module.RunView(
                id=run.id,
                request_id=uuid.uuid4(),
                ticker=instrument.canonical_ticker,
                asset_type=instrument.asset_type,
                analysis_date=item.analysis_date,
                status=RunStatus.QUEUED,
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
