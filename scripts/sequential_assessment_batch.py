from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from tsla_monthly_audit import build_api_client, save_state, validate_preflight

FAILURE_STATUSES = frozenset({"failed", "cancelled", "needs_attention"})
_PLAN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class SequentialBatchError(RuntimeError):
    """The sequential assessment plan cannot advance safely."""


def _timestamp(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None:
        raise SequentialBatchError("progress timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    result = tuple(str(symbol).strip().upper() for symbol in symbols)
    if not result or any(not symbol for symbol in result):
        raise SequentialBatchError("at least one non-empty symbol is required")
    if len(set(result)) != len(result):
        raise SequentialBatchError("symbols must be unique")
    return result


def _normalize_dates(analysis_dates: Iterable[date]) -> tuple[date, ...]:
    result = tuple(analysis_dates)
    if not result:
        raise SequentialBatchError("at least one analysis date is required")
    if result != tuple(sorted(set(result))):
        raise SequentialBatchError(
            "analysis dates must be unique and strictly ascending"
        )
    return result


def create_state(
    symbols: Iterable[str],
    analysis_dates: Iterable[date],
    *,
    plan_id: str,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    normalized_symbols = _normalize_symbols(symbols)
    normalized_dates = _normalize_dates(analysis_dates)
    if not _PLAN_ID_PATTERN.fullmatch(plan_id):
        raise SequentialBatchError("plan id must contain only safe filename characters")
    created_at = _timestamp(now)
    return {
        "version": 1,
        "plan_id": plan_id,
        "symbols": list(normalized_symbols),
        "analysis_dates": [item.isoformat() for item in normalized_dates],
        "created_at": created_at,
        "updated_at": created_at,
        "tracks": {
            symbol: {"status": "pending", "assessments": {}}
            for symbol in normalized_symbols
        },
    }


def validate_state(
    state: dict[str, Any],
    symbols: Iterable[str],
    analysis_dates: Iterable[date],
    *,
    plan_id: str,
) -> None:
    normalized_symbols = _normalize_symbols(symbols)
    normalized_dates = _normalize_dates(analysis_dates)
    date_keys = tuple(item.isoformat() for item in normalized_dates)
    if state.get("version") != 1:
        raise SequentialBatchError("unsupported state version")
    if state.get("plan_id") != plan_id:
        raise SequentialBatchError("state plan id does not match the requested plan")
    if tuple(state.get("symbols") or ()) != normalized_symbols:
        raise SequentialBatchError("state symbols do not match the requested plan")
    if tuple(state.get("analysis_dates") or ()) != date_keys:
        raise SequentialBatchError("state dates do not match the requested plan")
    tracks = state.get("tracks")
    if not isinstance(tracks, dict) or set(tracks) != set(normalized_symbols):
        raise SequentialBatchError("state tracks do not match the requested symbols")

    for symbol in normalized_symbols:
        assessments = tracks[symbol].get("assessments")
        if not isinstance(assessments, dict) or not set(assessments).issubset(
            date_keys
        ):
            raise SequentialBatchError(f"invalid assessment records for {symbol}")
        prior_succeeded = True
        for key in date_keys:
            record = assessments.get(key)
            if record is None:
                prior_succeeded = False
                continue
            if not prior_succeeded:
                raise SequentialBatchError(
                    f"out-of-order assessment record for {symbol} on {key}"
                )
            if record.get("analysis_date") != key or not record.get("run_id"):
                raise SequentialBatchError(
                    f"invalid assessment record for {symbol} on {key}"
                )
            prior_succeeded = record.get("status") == "succeeded"


def assessment_payload(
    ticker: str,
    analysis_date: date,
    *,
    plan_id: str,
) -> dict[str, Any]:
    idempotency_key = f"{plan_id}-{ticker.lower()}-{analysis_date:%Y%m%d}"
    return {
        "items": [{"ticker": ticker, "analysis_date": analysis_date.isoformat()}],
        "analysts": ["market", "social", "news", "fundamentals"],
        "depth": "deep",
        "memory_mode": "historical",
        "language": "Chinese",
        "idempotency_key": idempotency_key,
    }


def _assert_run_identity(run: dict[str, Any], ticker: str, analysis_date: str) -> None:
    if str(run.get("ticker") or "").upper() != ticker:
        raise SequentialBatchError(f"run ticker does not match {ticker}")
    if str(run.get("analysis_date") or "") != analysis_date:
        raise SequentialBatchError(f"run date does not match {ticker} {analysis_date}")
    if not run.get("id"):
        raise SequentialBatchError("assessment response has no run id")


def _save_progress(
    state: dict[str, Any],
    state_path: Path,
    now: Callable[[], datetime],
) -> None:
    state["updated_at"] = _timestamp(now)
    save_state(state_path, state)


def _summary(state: dict[str, Any]) -> dict[str, Any]:
    track_counts = Counter(track["status"] for track in state["tracks"].values())
    run_counts = Counter(
        record["status"]
        for track in state["tracks"].values()
        for record in track["assessments"].values()
    )
    return {
        "total": len(state["symbols"]) * len(state["analysis_dates"]),
        "submitted": sum(run_counts.values()),
        "succeeded": run_counts["succeeded"],
        "active": track_counts["active"],
        "pending": track_counts["pending"],
        "completed": track_counts["completed"],
        "blocked": track_counts["blocked"],
        "run_statuses": dict(sorted(run_counts.items())),
    }


def reconcile_once(
    api: Any,
    state: dict[str, Any],
    state_path: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    symbols = tuple(state.get("symbols") or ())
    analysis_dates = tuple(date.fromisoformat(item) for item in state["analysis_dates"])
    plan_id = str(state.get("plan_id") or "")
    validate_state(state, symbols, analysis_dates, plan_id=plan_id)

    for ticker in symbols:
        track = state["tracks"][ticker]
        if track["status"] in {"blocked", "completed"}:
            continue
        assessments = track["assessments"]
        current_date = next(
            (
                item
                for item in analysis_dates
                if assessments.get(item.isoformat(), {}).get("status") != "succeeded"
            ),
            None,
        )
        if current_date is None:
            track["status"] = "completed"
            _save_progress(state, state_path, now)
            continue

        key = current_date.isoformat()
        record = assessments.get(key)
        if record is None:
            response = api.post_json(
                "/api/v1/assessments",
                assessment_payload(ticker, current_date, plan_id=plan_id),
            )
            items = response.get("items") if isinstance(response, dict) else None
            if not isinstance(items, list) or len(items) != 1:
                raise SequentialBatchError("submission did not return exactly one run")
            run = dict(items[0])
            _assert_run_identity(run, ticker, key)
            record = {
                "analysis_date": key,
                "run_id": str(run["id"]),
                "status": str(run.get("status") or "queued"),
                "submitted_at": str(run.get("created_at") or _timestamp(now)),
                "finished_at": run.get("finished_at"),
            }
            assessments[key] = record
            track["status"] = "active"
            _save_progress(state, state_path, now)
            print(
                f"symbol={ticker} analysis_date={key} run_id={record['run_id']} "
                f"status={record['status']} action=submitted",
                flush=True,
            )
            continue

        run = dict(api.get_json(f"/api/v1/assessments/{record['run_id']}"))
        _assert_run_identity(run, ticker, key)
        status = str(run.get("status") or "")
        changed = status != record.get("status")
        record.update(
            {
                "status": status,
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "last_polled_at": _timestamp(now),
            }
        )
        if status in FAILURE_STATUSES:
            track["status"] = "blocked"
            track["error"] = f"{key} ended with {status}"
            changed = True
        elif status == "succeeded":
            is_last = current_date == analysis_dates[-1]
            track["status"] = "completed" if is_last else "pending"
            changed = True
        else:
            track["status"] = "active"
        if changed:
            _save_progress(state, state_path, now)
            print(
                f"symbol={ticker} analysis_date={key} run_id={record['run_id']} "
                f"status={status} action=observed",
                flush=True,
            )

    validate_state(state, symbols, analysis_dates, plan_id=plan_id)
    return _summary(state)


def run_plan(
    api: Any,
    state_path: Path,
    symbols: Iterable[str],
    analysis_dates: Iterable[date],
    *,
    plan_id: str,
    poll_seconds: float,
    timeout_seconds: float,
    heartbeat_seconds: float = 60,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    normalized_symbols = _normalize_symbols(symbols)
    normalized_dates = _normalize_dates(analysis_dates)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        validate_state(state, normalized_symbols, normalized_dates, plan_id=plan_id)
    else:
        state = create_state(
            normalized_symbols, normalized_dates, plan_id=plan_id, now=now
        )
        save_state(state_path, state)

    deadline = clock() + timeout_seconds
    next_heartbeat = clock()
    previous_summary = None
    while True:
        summary = reconcile_once(api, state, state_path, now=now)
        if summary != previous_summary or clock() >= next_heartbeat:
            print("progress=" + json.dumps(summary, sort_keys=True), flush=True)
            previous_summary = summary
            next_heartbeat = clock() + heartbeat_seconds
        if summary["completed"] + summary["blocked"] == len(normalized_symbols):
            if summary["blocked"]:
                blocked = [
                    symbol
                    for symbol, track in state["tracks"].items()
                    if track["status"] == "blocked"
                ]
                raise SequentialBatchError(
                    "sequential plan blocked for symbols: " + ",".join(blocked)
                )
            return summary
        if clock() >= deadline:
            raise SequentialBatchError("sequential plan timed out")
        sleep(poll_seconds)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run assessments in strict date order independently for each symbol"
    )
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--analysis-dates", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env.platform"))
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=18 * 60 * 60)
    parser.add_argument(
        "--token-url",
        default=(
            "http://127.0.0.1:18081/realms/tradingng/protocol/openid-connect/token"
        ),
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8010")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    if options.poll_seconds < 0 or options.timeout_seconds <= 0:
        raise SequentialBatchError("poll and timeout values are invalid")
    symbols = tuple(item.strip() for item in options.symbols.split(","))
    try:
        analysis_dates = tuple(
            date.fromisoformat(item.strip())
            for item in options.analysis_dates.split(",")
        )
    except ValueError as error:
        raise SequentialBatchError("analysis dates must use ISO format") from error
    with httpx.Client(timeout=httpx.Timeout(60, connect=10)) as http:
        api = build_api_client(
            http,
            options.env_file,
            token_url=options.token_url,
            api_url=options.api_url,
        )
        print(
            "preflight="
            + json.dumps(validate_preflight(api), ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        summary = run_plan(
            api,
            options.state_dir / "state.json",
            symbols,
            analysis_dates,
            plan_id=options.plan_id,
            poll_seconds=options.poll_seconds,
            timeout_seconds=options.timeout_seconds,
        )
    print(
        "sequential_batch_complete=" + json.dumps(summary, sort_keys=True), flush=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
