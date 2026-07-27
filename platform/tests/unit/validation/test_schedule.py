import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tradingng_platform.auth.principal import Principal
from tradingng_platform.validation.contracts import ValidationView
from tradingng_platform.validation.service import ValidationService


class _Repository:
    def __init__(self):
        self.items = {}
        self.statuses = {}

    async def schedule(self, run_id, horizons, principal, request_id):
        del principal, request_id
        if self.statuses.get(run_id) != "succeeded":
            raise ValueError("only a successful run can be validated")
        for horizon in horizons:
            self.items.setdefault(
                (run_id, horizon),
                ValidationView(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{horizon}"),
                    run_id=run_id,
                    horizon=horizon,
                    status="scheduled",
                    scheduled_for=datetime(2026, 7, 25, tzinfo=timezone.utc),
                    observed_at=None,
                    raw_return=None,
                    benchmark_return=None,
                    alpha=None,
                    max_adverse_excursion=None,
                    max_favorable_excursion=None,
                ),
            )
        return [self.items[(run_id, horizon)] for horizon in sorted(horizons)]


@pytest.mark.asyncio
async def test_successful_run_schedules_three_unique_horizons():
    repository = _Repository()
    run_id = uuid.uuid4()
    repository.statuses[run_id] = "succeeded"
    service = ValidationService(repository)

    first = await service.schedule_system(run_id)
    second = await service.schedule_system(run_id)

    assert [item.horizon for item in first] == [1, 5, 20]
    assert [item.id for item in first] == [item.id for item in second]


@pytest.mark.asyncio
async def test_non_successful_run_cannot_be_validated():
    repository = _Repository()
    run_id = uuid.uuid4()
    repository.statuses[run_id] = "failed"

    with pytest.raises(ValueError, match="successful run"):
        await ValidationService(repository).schedule_system(run_id)


@pytest.mark.asyncio
async def test_user_scheduling_requires_write_scope_and_bounded_horizons():
    repository = _Repository()
    run_id = uuid.uuid4()
    repository.statuses[run_id] = "succeeded"
    service = ValidationService(repository)
    viewer = Principal("issuer", "viewer", "user", frozenset({"validations:read"}))

    with pytest.raises(PermissionError):
        await service.schedule(viewer, run_id)
    with pytest.raises(ValueError, match="horizons"):
        await service.schedule_system(run_id, [2])


def test_validation_view_exposes_typed_audit_metadata():
    artifact_id = uuid.uuid4()
    view = ValidationView.model_validate(
        SimpleNamespace(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            horizon=20,
            status="completed",
            scheduled_for=datetime(2026, 7, 25, tzinfo=timezone.utc),
            observed_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            raw_return="0.05",
            benchmark_return="0.02",
            alpha="0.03",
            max_adverse_excursion="-0.01",
            max_favorable_excursion="0.07",
            trigger_results_json={
                "rating": "Buy",
                "direction": "bullish",
                "direction_correct": True,
                "direction_basis": "instrument_total_return",
                "direction_rule_version": "rating-direction.v2",
                "price_target_hit": False,
                "entry_price": "100",
                "exit_price": "105",
                "entry_session": "2026-07-01",
                "exit_session": "2026-07-21",
            },
            data_artifact_id=artifact_id,
            error_code=None,
        ),
        from_attributes=True,
    )

    assert view.trigger_results.rating == "Buy"
    assert view.trigger_results.direction_correct is True
    assert view.trigger_results.direction_basis == "instrument_total_return"
    assert view.trigger_results.direction_rule_version == "rating-direction.v2"
    assert view.data_artifact_id == artifact_id
    assert view.error_code is None
    assert view.calculation_version == "validation.v1"
    payload = view.model_dump(mode="json")
    assert payload["trigger_results"]["entry_session"] == "2026-07-01"
    assert "trigger_results_json" not in payload
