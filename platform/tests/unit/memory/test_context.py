import importlib
import importlib.util
import uuid
from datetime import date
from decimal import Decimal

from tradingng_platform.assessments.contracts import MemoryMode


def _memory_module():
    try:
        spec = importlib.util.find_spec("tradingng_platform.memory.context")
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, "historical memory context module must exist"
    return importlib.import_module("tradingng_platform.memory.context")


def _candidate(module, *, run: int, horizon: int, exit_session: date, ticker: str = "NVDA"):
    return module.MemoryCandidate(
        source_run_id=uuid.UUID(int=run),
        validation_id=uuid.UUID(int=run * 100 + horizon),
        ticker=ticker,
        analysis_date=date(2026, 7, run),
        exit_session=exit_session,
        horizon=horizon,
        rating="Buy",
        executive_summary=f"Summary {run}",
        investment_thesis=f"Thesis {run}",
        price_target=Decimal("200"),
        time_horizon="6 months",
        raw_return=Decimal("0.0500000000"),
        alpha=Decimal("0.0200000000"),
        max_adverse_excursion=Decimal("-0.0300000000"),
        max_favorable_excursion=Decimal("0.0700000000"),
        direction_correct=True,
        price_target_hit=False,
    )


def test_historical_snapshot_is_deterministic_bounded_and_lookahead_safe():
    module = _memory_module()
    candidates = [
        _candidate(module, run=1, horizon=1, exit_session=date(2026, 7, 2)),
        _candidate(module, run=1, horizon=5, exit_session=date(2026, 7, 6)),
        _candidate(module, run=2, horizon=1, exit_session=date(2026, 7, 3)),
        _candidate(module, run=3, horizon=20, exit_session=date(2026, 7, 25)),
        _candidate(
            module,
            run=4,
            horizon=5,
            exit_session=date(2026, 7, 9),
            ticker="AAPL",
        ),
    ]

    first = module.build_memory_snapshot(
        MemoryMode.HISTORICAL,
        "NVDA",
        date(2026, 7, 25),
        candidates,
        limit=5,
    )
    second = module.build_memory_snapshot(
        MemoryMode.HISTORICAL,
        "NVDA",
        date(2026, 7, 25),
        reversed(candidates),
        limit=5,
    )

    assert first == second
    assert first.mode is MemoryMode.HISTORICAL
    assert [(item.analysis_date, item.horizon) for item in first.entries] == [
        (date(2026, 7, 1), 5),
        (date(2026, 7, 2), 1),
    ]
    assert len(first.snapshot_sha256) == 64
    assert first.entries[0].direction_correct is True
    assert first.entries[0].content_sha256


def test_independent_snapshot_stays_empty_and_historical_snapshot_renders_for_tradingagents():
    module = _memory_module()
    candidate = _candidate(
        module,
        run=1,
        horizon=5,
        exit_session=date(2026, 7, 6),
    )

    independent = module.build_memory_snapshot(
        MemoryMode.INDEPENDENT,
        "NVDA",
        date(2026, 7, 25),
        [candidate],
    )
    historical = module.build_memory_snapshot(
        MemoryMode.HISTORICAL,
        "NVDA",
        date(2026, 7, 25),
        [candidate],
    )
    rendered = module.render_tradingagents_memory(historical)

    assert independent.entries == ()
    assert independent.mode is MemoryMode.INDEPENDENT
    assert "[2026-07-01 | NVDA | Buy | +5.0% | +2.0% | 5d]" in rendered
    assert "DECISION:\nRating: Buy" in rendered
    assert "REFLECTION:\nValidated after 5 sessions" in rendered
    assert "<!-- ENTRY_END -->" in rendered


def test_rendered_snapshot_is_consumed_by_the_pinned_tradingagents_memory_log(tmp_path):
    from tradingagents.agents.utils.memory import TradingMemoryLog

    module = _memory_module()
    snapshot = module.build_memory_snapshot(
        MemoryMode.HISTORICAL,
        "NVDA",
        date(2026, 7, 25),
        [
            _candidate(
                module,
                run=1,
                horizon=5,
                exit_session=date(2026, 7, 6),
            )
        ],
    )
    memory_path = tmp_path / "trading_memory.md"
    memory_path.write_text(
        module.render_tradingagents_memory(snapshot),
        encoding="utf-8",
    )

    memory_log = TradingMemoryLog({"memory_log_path": str(memory_path)})
    entries = memory_log.load_entries()
    past_context = memory_log.get_past_context("NVDA")

    assert len(entries) == 1
    assert entries[0]["pending"] is False
    assert entries[0]["ticker"] == "NVDA"
    assert "Past analyses of NVDA" in past_context
    assert "Earlier" not in past_context
    assert "Summary 1" in past_context
    assert "Validated after 5 sessions" in past_context
