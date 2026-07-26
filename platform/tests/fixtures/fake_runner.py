#!/usr/bin/env python3
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

STAGES = (
    "running_analysts",
    "research_debate",
    "trader_plan",
    "risk_debate",
    "portfolio_decision",
)


def emit(sequence, event_type, name, payload):
    print(
        json.dumps(
            {
                "sequence": sequence,
                "type": event_type,
                "name": name,
                "payload": payload,
                "emitted_at": datetime.now(timezone.utc).isoformat(),
            },
            separators=(",", ":"),
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    arguments = parser.parse_args()
    config = json.loads(arguments.config.read_text(encoding="utf-8"))
    work_dir = Path(config["work_dir"])
    delay = int(os.getenv("FAKE_RUNNER_DELAY_MS", "250")) / 1000 / len(STAGES)
    started = time.monotonic()

    for path in (work_dir / "reports", work_dir / "working"):
        path.mkdir(parents=True, exist_ok=True)
    decision = {
        "rating": "Hold",
        "executive_summary": "Deterministic fake decision.",
        "investment_thesis": "Used only for scheduler integration tests.",
        "price_target": "100.00",
        "time_horizon": "5 days",
        "structured_json": {"fixture": True},
    }
    (work_dir / "decision.json").write_text(json.dumps(decision), encoding="utf-8")
    (work_dir / "final_state.json").write_text(
        json.dumps({"ticker": config["ticker"], "decision": decision}),
        encoding="utf-8",
    )
    (work_dir / "reports" / "complete_report.md").write_text(
        f"# {config['ticker']}\n\nFake report.",
        encoding="utf-8",
    )
    evidence = {
        "tool_name": "get_stock_data",
        "source": "fixture",
        "arguments": {"ticker": config["ticker"]},
        "output": {"close": 100},
        "output_sha256": "a" * 64,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "retention_class": "raw_180d",
    }
    (work_dir / "working" / "evidence.jsonl").write_text(
        json.dumps(evidence) + "\n",
        encoding="utf-8",
    )
    (work_dir / "working" / "llm_interactions.jsonl").write_text(
        json.dumps({"response": "fixture", "retention_class": "raw_180d"}) + "\n",
        encoding="utf-8",
    )

    for sequence, stage in enumerate(STAGES, start=1):
        time.sleep(delay)
        emit(sequence, "stage", stage, {"status": stage, "fixture": True})
    emit(
        len(STAGES) + 1,
        "result",
        "assessment.completed",
        {"started_monotonic": started, "ended_monotonic": time.monotonic()},
    )
    raise SystemExit(int(os.getenv("FAKE_RUNNER_EXIT_CODE", "0")))


if __name__ == "__main__":
    main()
