import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from tradingng_platform.runner.contracts import RunnerEvent

STAGE_KEYS = (
    ("market_report", "running_analysts"),
    ("sentiment_report", "running_analysts"),
    ("news_report", "running_analysts"),
    ("fundamentals_report", "running_analysts"),
    ("investment_debate_state", "research_debate"),
    ("trader_investment_plan", "trader_plan"),
    ("risk_debate_state", "risk_debate"),
    ("final_trade_decision", "portfolio_decision"),
)
_STAGE_RANK = {status: rank for rank, status in enumerate(dict(STAGE_KEYS).values())}


def _has_progress(value) -> bool:
    if isinstance(value, dict):
        return any(_has_progress(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_progress(item) for item in value)
    return value not in (None, "", False, 0)


@dataclass(frozen=True)
class StageUpdate:
    status: str
    progress_key: str
    content_hash: str
    transitioned: bool


class StageTracker:
    def __init__(self):
        self._rank = -1
        self._fingerprints: dict[str, str] = {}

    def consume(self, chunk: dict) -> StageUpdate | None:
        candidates = []
        for order, (key, status) in enumerate(STAGE_KEYS):
            if key not in chunk or not _has_progress(chunk[key]):
                continue
            encoded = json.dumps(
                chunk[key],
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode()
            fingerprint = hashlib.sha256(encoded).hexdigest()
            rank = _STAGE_RANK[status]
            if rank >= self._rank and self._fingerprints.get(key) != fingerprint:
                candidates.append((rank, order, key, status, fingerprint))
        if not candidates:
            return None
        rank, _, key, status, fingerprint = max(candidates)
        transitioned = rank > self._rank
        self._rank = max(self._rank, rank)
        self._fingerprints[key] = fingerprint
        return StageUpdate(status, key, fingerprint, transitioned)


class EventEmitter:
    def __init__(self, sink):
        self.sink = sink
        self.sequence = 0

    def emit(self, event_type: str, name: str, payload: dict) -> RunnerEvent:
        self.sequence += 1
        event = RunnerEvent(
            sequence=self.sequence,
            type=event_type,
            name=name,
            payload=payload,
            emitted_at=datetime.now(timezone.utc),
        )
        self.sink(event)
        return event
