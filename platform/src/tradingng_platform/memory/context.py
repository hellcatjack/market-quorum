import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tradingng_platform.assessments.contracts import MemoryMode

_ENTRY_SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_run_id: uuid.UUID
    validation_id: uuid.UUID
    ticker: str
    analysis_date: date
    exit_session: date
    horizon: Literal[1, 5, 20]
    rating: str
    executive_summary: str
    investment_thesis: str
    price_target: Decimal | None
    time_horizon: str | None
    raw_return: Decimal
    alpha: Decimal
    max_adverse_excursion: Decimal
    max_favorable_excursion: Decimal
    direction_correct: bool | None
    price_target_hit: bool | None


class MemoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_run_id: uuid.UUID
    validation_id: uuid.UUID
    ticker: str
    analysis_date: date
    exit_session: date
    horizon: Literal[1, 5, 20]
    rating: str
    raw_return: Decimal
    alpha: Decimal
    max_adverse_excursion: Decimal
    max_favorable_excursion: Decimal
    direction_correct: bool | None
    price_target_hit: bool | None
    decision: str
    reflection: str
    content_sha256: str


class MemorySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: MemoryMode
    entries: tuple[MemoryEntry, ...]
    snapshot_sha256: str


def _canonical_sha256(value: dict) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _decision(candidate: MemoryCandidate) -> str:
    sections = [
        f"Rating: {candidate.rating}",
        f"Executive Summary: {candidate.executive_summary}",
        f"Investment Thesis: {candidate.investment_thesis}",
    ]
    if candidate.price_target is not None:
        sections.append(f"Price Target: {candidate.price_target}")
    if candidate.time_horizon:
        sections.append(f"Time Horizon: {candidate.time_horizon}")
    return "\n".join(sections)


def _reflection(candidate: MemoryCandidate) -> str:
    direction = (
        "correct"
        if candidate.direction_correct is True
        else "incorrect"
        if candidate.direction_correct is False
        else "not classified"
    )
    target = (
        "hit"
        if candidate.price_target_hit is True
        else "not hit"
        if candidate.price_target_hit is False
        else "not set"
    )
    return (
        f"Validated after {candidate.horizon} sessions: direction {direction}; "
        f"raw return {candidate.raw_return:+.1%}; alpha {candidate.alpha:+.1%}; "
        f"maximum adverse excursion {candidate.max_adverse_excursion:+.1%}; "
        f"maximum favorable excursion {candidate.max_favorable_excursion:+.1%}; "
        f"price target {target}. Treat this as retrospective calibration, not as "
        "current market evidence. Historical ratings are calibration observations, "
        "not votes. Derive the current rating from current-date evidence first; use "
        "this record only to check rating semantics, sizing, and risk controls. "
        "Regime changes can invalidate even a previously correct direction."
    )


def _entry(candidate: MemoryCandidate) -> MemoryEntry:
    decision = _decision(candidate)
    reflection = _reflection(candidate)
    content = {
        **candidate.model_dump(mode="json"),
        "decision": decision,
        "reflection": reflection,
    }
    return MemoryEntry(
        source_run_id=candidate.source_run_id,
        validation_id=candidate.validation_id,
        ticker=candidate.ticker,
        analysis_date=candidate.analysis_date,
        exit_session=candidate.exit_session,
        horizon=candidate.horizon,
        rating=candidate.rating,
        raw_return=candidate.raw_return,
        alpha=candidate.alpha,
        max_adverse_excursion=candidate.max_adverse_excursion,
        max_favorable_excursion=candidate.max_favorable_excursion,
        direction_correct=candidate.direction_correct,
        price_target_hit=candidate.price_target_hit,
        decision=decision,
        reflection=reflection,
        content_sha256=_canonical_sha256(content),
    )


def build_memory_snapshot(
    mode: MemoryMode,
    ticker: str,
    analysis_date: date,
    candidates: Iterable[MemoryCandidate],
    *,
    limit: int = 5,
) -> MemorySnapshot:
    if limit < 1:
        raise ValueError("memory entry limit must be positive")

    selected: list[MemoryCandidate] = []
    if mode is MemoryMode.HISTORICAL:
        eligible = [
            candidate
            for candidate in candidates
            if candidate.ticker == ticker
            and candidate.analysis_date < analysis_date
            and candidate.exit_session < analysis_date
        ]
        eligible.sort(
            key=lambda candidate: (
                candidate.analysis_date,
                str(candidate.source_run_id),
                candidate.horizon,
            ),
            reverse=True,
        )
        seen_runs: set[uuid.UUID] = set()
        for candidate in eligible:
            if candidate.source_run_id in seen_runs:
                continue
            selected.append(candidate)
            seen_runs.add(candidate.source_run_id)
            if len(selected) == limit:
                break
        selected.sort(
            key=lambda candidate: (
                candidate.analysis_date,
                str(candidate.source_run_id),
            )
        )

    entries = tuple(_entry(candidate) for candidate in selected)
    payload = {
        "mode": mode.value,
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }
    return MemorySnapshot(
        mode=mode,
        entries=entries,
        snapshot_sha256=_canonical_sha256(payload),
    )


def empty_memory_snapshot() -> MemorySnapshot:
    return build_memory_snapshot(
        MemoryMode.INDEPENDENT,
        "",
        date.min,
        (),
    )


def render_tradingagents_memory(snapshot: MemorySnapshot) -> str:
    blocks = []
    for entry in snapshot.entries:
        tag = (
            f"[{entry.analysis_date.isoformat()} | {entry.ticker} | {entry.rating} | "
            f"{entry.raw_return:+.1%} | {entry.alpha:+.1%} | {entry.horizon}d]"
        )
        blocks.append(f"{tag}\n\nDECISION:\n{entry.decision}\n\nREFLECTION:\n{entry.reflection}")
    return _ENTRY_SEPARATOR.join(blocks) + (_ENTRY_SEPARATOR if blocks else "")
