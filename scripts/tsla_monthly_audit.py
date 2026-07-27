from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

AUDIT_DATES = (
    date(2025, 7, 31),
    date(2025, 8, 29),
    date(2025, 9, 30),
    date(2025, 10, 31),
    date(2025, 11, 28),
    date(2025, 12, 31),
    date(2026, 1, 30),
    date(2026, 2, 27),
    date(2026, 3, 31),
    date(2026, 4, 30),
    date(2026, 5, 29),
    date(2026, 6, 25),
)

ALPHA_RESEARCH_CATEGORIES = frozenset(
    {
        "core_stock_apis",
        "technical_indicators",
        "fundamental_data",
        "news_data",
    }
)
_FORBIDDEN_STATE_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "client_secret",
        "api_key",
        "authorization",
        "bearer",
        "password",
    }
)


class AuditFailure(RuntimeError):
    """A monthly checkpoint violated an engineering quality gate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditFailure(message)


def _as_date(value: object, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise AuditFailure(f"{label} is not an ISO date") from error


def validate_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    run = dict(checkpoint.get("run") or {})
    _require(run.get("ticker") == "TSLA", "checkpoint is not a TSLA assessment")
    _require(run.get("status") == "succeeded", "assessment did not succeed")
    analysis_date = _as_date(run.get("analysis_date"), "analysis date")

    data_vendors = dict(run.get("data_vendors") or {})
    _require(
        all(
            data_vendors.get(category) == "alpha_vantage"
            for category in ALPHA_RESEARCH_CATEGORIES
        ),
        "research providers are not exclusive to Alpha Vantage",
    )

    memory = dict(run.get("memory") or {})
    _require(
        memory.get("mode") == "historical", "historical memory mode was not preserved"
    )
    sources = list(memory.get("sources") or [])
    _require(len(sources) <= 5, "historical memory must contain at most five sources")
    source_ids = [str(source.get("source_run_id") or "") for source in sources]
    _require(
        all(source_ids) and len(source_ids) == len(set(source_ids)),
        "historical memory sources must reference distinct runs",
    )
    for source in sources:
        source_date = _as_date(source.get("analysis_date"), "memory analysis date")
        exit_session = _as_date(source.get("exit_session"), "memory exit session")
        _require(
            source_date < analysis_date and exit_session < analysis_date,
            "historical memory contains look-ahead information",
        )

    steps = list(checkpoint.get("steps") or [])
    _require(len(steps) == 5, "assessment does not contain the five expected steps")
    _require(
        all(step.get("status") == "completed" for step in steps),
        "assessment steps are not all completed",
    )
    _require(
        all(step.get("started_at") and step.get("finished_at") for step in steps),
        "assessment step timestamp is incomplete",
    )

    decision = dict(checkpoint.get("decision") or {})
    required_decision_fields = (
        "rating",
        "executive_summary",
        "investment_thesis",
        "time_horizon",
    )
    _require(
        all(
            str(decision.get(field) or "").strip() for field in required_decision_fields
        ),
        "assessment decision is missing required content",
    )

    validations = list(checkpoint.get("validations") or [])
    _require(
        len(validations) == 3
        and {item.get("horizon") for item in validations} == {1, 5, 20},
        "assessment does not contain all validation horizons",
    )
    _require(
        all(item.get("status") == "completed" for item in validations),
        "assessment validations are not all completed",
    )
    _require(
        all(item.get("provider_id") == "alphavantage" for item in validations),
        "assessment validation did not use Alpha Vantage exclusively",
    )
    _require(
        all(
            item.get("calculation_version") == "validation.v2"
            and item.get("provider_adapter_version")
            and item.get("normalization_version")
            and item.get("entry_session")
            and item.get("exit_session")
            for item in validations
        ),
        "assessment validation metadata is incomplete",
    )

    artifacts = list(checkpoint.get("artifacts") or [])
    _require(artifacts, "assessment has no artifacts")
    _require(
        all(
            artifact.get("integrity_verified") is True
            and len(str(artifact.get("sha256") or "")) == 64
            for artifact in artifacts
        ),
        "assessment artifact integrity was not verified",
    )

    return {
        "run_id": str(run.get("id") or ""),
        "analysis_date": analysis_date.isoformat(),
        "rating": str(decision["rating"]),
        "price_target": decision.get("price_target"),
        "memory_source_count": len(sources),
        "memory_horizons": [int(source["horizon"]) for source in sources],
        "validation_horizons": sorted(int(item["horizon"]) for item in validations),
        "artifact_count": len(artifacts),
    }


def _contains_secret(value: object) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).strip().lower() in _FORBIDDEN_STATE_KEYS:
                return True
            if _contains_secret(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_secret(item) for item in value)
    return False


def load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "checkpoints": {}}
    if not isinstance(value, dict):
        raise AuditFailure("audit state is not a JSON object")
    if _contains_secret(value):
        raise AuditFailure("audit state contains secret material")
    return value


def save_state(path: Path, state: dict[str, Any]) -> None:
    if _contains_secret(state):
        raise AuditFailure("audit state contains secret material")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
