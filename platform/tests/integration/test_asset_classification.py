from datetime import date

import pytest
from sqlalchemy import select

from tradingng_platform.assessments.contracts import AssessmentItem, SubmitAssessments
from tradingng_platform.assessments.service import (
    AssessmentInstrumentIdentityConflict,
    AssessmentService,
)
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.instruments import AssetType
from tradingng_platform.models import AssessmentRequest, Instrument


def _principal() -> Principal:
    return Principal(
        "issuer",
        "classification-owner",
        "user",
        frozenset({"assessments:submit", "assessments:read"}),
        roles=frozenset({"Analyst"}),
    )


async def test_submission_persists_classification_and_per_asset_analysts(
    session_factory,
    instrument_classifier,
):
    service = AssessmentService(session_factory, instrument_classifier)

    runs = await service.submit(
        _principal(),
        SubmitAssessments(
            items=[
                AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 25)),
                AssessmentItem(ticker="GLD", analysis_date=date(2026, 7, 25)),
                AssessmentItem(ticker="BTC-USD", analysis_date=date(2026, 7, 25)),
            ],
            idempotency_key="classification-20260725",
        ),
        "request-classification",
    )

    async with session_factory() as session:
        instruments = {
            instrument.canonical_ticker: instrument
            for instrument in await session.scalars(select(Instrument))
        }
        request_configs = {
            request.instrument_id: request.requested_config_json
            for request in await session.scalars(select(AssessmentRequest))
        }

    assert [run.asset_type for run in runs] == ["stock", "fund", "crypto"]
    assert request_configs[instruments["NVDA"].id]["analysts"] == [
        "market",
        "social",
        "news",
        "fundamentals",
    ]
    assert request_configs[instruments["GLD"].id]["analysts"] == [
        "market",
        "social",
        "news",
    ]
    assert request_configs[instruments["BTC-USD"].id]["analysts"] == [
        "market",
        "social",
        "news",
    ]
    assert instruments["GLD"].name is None
    assert instruments["GLD"].exchange == "TEST"
    classification_metadata = instruments["GLD"].metadata_json["asset_classification"]
    assert classification_metadata.pop("resolved_at")
    assert classification_metadata == {
        "asset_type": "fund",
        "exchange": "TEST",
        "name": "GLD test instrument",
        "quote_type": "ETF",
        "source": "test",
        "source_symbol": "GLD",
    }


async def test_existing_ticker_cannot_be_reclassified_as_another_asset_type(
    session_factory,
    instrument_classifier,
):
    service = AssessmentService(session_factory, instrument_classifier)
    principal = _principal()
    await service.submit(
        principal,
        SubmitAssessments(
            items=[AssessmentItem(ticker="GLD", analysis_date=date(2026, 7, 25))],
            idempotency_key="classification-first-20260725",
        ),
        "request-first",
    )

    instrument_classifier.classify_many = _stock_gld
    with pytest.raises(AssessmentInstrumentIdentityConflict) as captured:
        await service.submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="GLD", analysis_date=date(2026, 7, 26))],
                idempotency_key="classification-second-20260725",
            ),
            "request-second",
        )
    assert captured.value.existing == "fund"
    assert captured.value.resolved is AssetType.STOCK


async def test_invalid_legacy_asset_type_is_reported_as_identity_conflict(
    session_factory,
    instrument_classifier,
):
    async with session_factory() as session, session.begin():
        session.add(
            Instrument(
                canonical_ticker="ODD",
                asset_type="legacy-etf",
                metadata_json={},
            )
        )

    service = AssessmentService(session_factory, instrument_classifier)
    with pytest.raises(AssessmentInstrumentIdentityConflict) as captured:
        await service.submit(
            _principal(),
            SubmitAssessments(
                items=[AssessmentItem(ticker="ODD", analysis_date=date(2026, 7, 25))],
                idempotency_key="classification-legacy-20260725",
            ),
            "request-legacy",
        )

    assert captured.value.existing == "legacy-etf"
    assert captured.value.resolved is AssetType.STOCK


async def _stock_gld(tickers):
    from tradingng_platform.instruments.classification import InstrumentClassification

    return {
        ticker: InstrumentClassification(
            ticker=ticker,
            asset_type=AssetType.STOCK,
            quote_type="EQUITY",
            source="test",
            source_symbol=ticker,
        )
        for ticker in tickers
    }
