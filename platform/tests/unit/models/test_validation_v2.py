import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

from tradingng_platform.models import DecisionPriceBasis, Validation
from tradingng_platform.validation.contracts import ValidationView


def test_validation_v2_columns_and_price_basis_table_are_available():
    validation_columns = set(Validation.__table__.columns.keys())

    assert {
        "calculation_version",
        "calendar_code",
        "entry_session",
        "exit_session",
        "matures_at",
        "claimed_at",
        "lease_expires_at",
        "worker_instance",
        "price_return",
        "total_return",
        "provider_id",
    } <= validation_columns
    assert "decision_price_bases" == DecisionPriceBasis.__tablename__
    assert DecisionPriceBasis.__table__.columns["run_id"].unique is True


def test_v1_view_exposes_legacy_returns_as_total_return_aliases():
    view = ValidationView.model_validate(
        SimpleNamespace(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            horizon=20,
            status="completed",
            scheduled_for=datetime(2026, 7, 25, tzinfo=timezone.utc),
            observed_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            raw_return="0.12",
            benchmark_return="0.10",
            alpha="0.02",
            max_adverse_excursion="-0.03",
            max_favorable_excursion="0.15",
            trigger_results_json={
                "entry_session": date(2026, 6, 1).isoformat(),
                "exit_session": date(2026, 6, 30).isoformat(),
            },
            data_artifact_id=uuid.uuid4(),
            error_code=None,
            calculation_version="validation.v1",
            calendar_code=None,
            entry_session=None,
            exit_session=None,
            matures_at=None,
            price_return=None,
            benchmark_price_return=None,
            price_alpha=None,
            total_return=None,
            benchmark_total_return=None,
            total_alpha=None,
            normalization_version=None,
            provider_adapter_version=None,
            provider_id=None,
        ),
        from_attributes=True,
    )

    assert view.calculation_version == "validation.v1"
    assert view.total_return == view.raw_return
    assert view.benchmark_total_return == view.benchmark_return
    assert view.total_alpha == view.alpha
