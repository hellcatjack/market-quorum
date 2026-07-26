import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.domain.runs import RunStatus


def test_run_view_includes_cached_instrument_identity():
    run = SimpleNamespace(
        id=uuid.uuid4(),
        status=RunStatus.SUCCEEDED.value,
        attempt=1,
        created_at=datetime(2026, 7, 25, 16, 0, tzinfo=timezone.utc),
    )
    request = SimpleNamespace(id=uuid.uuid4(), analysis_date=date(2026, 7, 25))
    instrument = SimpleNamespace(
        canonical_ticker="NVDA",
        asset_type="stock",
        name="英伟达",
        exchange="NASDAQ",
    )

    view = AssessmentRepository._run_view(run, request, instrument)

    assert view.instrument_name == "英伟达"
    assert view.exchange == "NASDAQ"
