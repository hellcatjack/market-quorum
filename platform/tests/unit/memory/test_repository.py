import importlib
import importlib.util
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from tradingng_platform.assessments.contracts import MemoryMode


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def execute(self, statement):
        del statement
        self.calls += 1
        return _Result(self.rows)


def _row(*, run: int, horizon: int, exit_session: str):
    return (
        SimpleNamespace(
            id=uuid.UUID(int=run),
            status="succeeded",
            created_at=datetime(2026, 7, run, tzinfo=timezone.utc),
        ),
        SimpleNamespace(analysis_date=date(2026, 7, run)),
        SimpleNamespace(canonical_ticker="NVDA"),
        SimpleNamespace(
            rating="Buy",
            executive_summary=f"Summary {run}",
            investment_thesis=f"Thesis {run}",
            price_target=Decimal("200"),
            time_horizon="6 months",
        ),
        SimpleNamespace(
            id=uuid.UUID(int=run * 100 + horizon),
            horizon=horizon,
            status="completed",
            observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            raw_return=Decimal("0.05"),
            alpha=Decimal("0.02"),
            max_adverse_excursion=Decimal("-0.03"),
            max_favorable_excursion=Decimal("0.07"),
            trigger_results_json={
                "exit_session": exit_session,
                "direction_correct": True,
                "price_target_hit": False,
            },
        ),
    )


async def test_repository_builds_snapshot_from_completed_validations_only():
    spec = importlib.util.find_spec("tradingng_platform.memory.repository")
    assert spec is not None, "historical memory repository module must exist"
    module = importlib.import_module("tradingng_platform.memory.repository")
    session = _Session(
        [
            _row(run=1, horizon=5, exit_session="2026-07-06"),
            _row(run=2, horizon=1, exit_session="2026-07-03"),
            _row(run=3, horizon=20, exit_session="2026-07-25"),
        ]
    )

    snapshot = await module.HistoricalMemoryRepository(session).build(
        "NVDA",
        date(2026, 7, 25),
        MemoryMode.HISTORICAL,
    )

    assert session.calls == 1
    assert [entry.source_run_id for entry in snapshot.entries] == [
        uuid.UUID(int=1),
        uuid.UUID(int=2),
    ]


async def test_repository_does_not_query_for_independent_mode():
    spec = importlib.util.find_spec("tradingng_platform.memory.repository")
    assert spec is not None, "historical memory repository module must exist"
    module = importlib.import_module("tradingng_platform.memory.repository")
    session = _Session([_row(run=1, horizon=5, exit_session="2026-07-06")])

    snapshot = await module.HistoricalMemoryRepository(session).build(
        "NVDA",
        date(2026, 7, 25),
        MemoryMode.INDEPENDENT,
    )

    assert session.calls == 0
    assert snapshot.entries == ()
