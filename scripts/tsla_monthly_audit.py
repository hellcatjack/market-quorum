from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

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


class AuditApiClient:
    """Minimal bearer-authenticated client with one transparent token refresh."""

    def __init__(
        self,
        http: httpx.Client,
        *,
        token_url: str,
        api_url: str,
        client_id: str,
        client_secret: str,
        retry_sleep=time.sleep,
    ) -> None:
        self.http = http
        self.token_url = token_url
        self.api_url = api_url.rstrip("/")
        self.client_id = client_id
        self._client_secret = client_secret
        self._access_token: str | None = None
        self._retry_sleep = retry_sleep

    def __repr__(self) -> str:
        return (
            f"AuditApiClient(token_url={self.token_url!r}, api_url={self.api_url!r}, "
            f"client_id={self.client_id!r})"
        )

    def _authenticate(self) -> None:
        response = self.http.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self._client_secret,
            },
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise AuditFailure("identity provider did not return an access token")
        self._access_token = token

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        network_attempts = 3 if method == "GET" else 1
        for network_attempt in range(1, network_attempts + 1):
            try:
                return self._authenticated_request(method, path, payload=payload)
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                retriable_status = (
                    isinstance(error, httpx.HTTPStatusError)
                    and error.response.status_code >= 500
                )
                if method != "GET" or (
                    not isinstance(error, httpx.TransportError) and not retriable_status
                ):
                    raise
                if network_attempt == network_attempts:
                    raise
                self._retry_sleep(2 ** (network_attempt - 1))
        raise RuntimeError("unreachable network retry state")

    def _authenticated_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if self._access_token is None:
            self._authenticate()
        for authentication_attempt in range(2):
            response = self.http.request(
                method,
                f"{self.api_url}{path}",
                headers={"Authorization": f"Bearer {self._access_token}"},
                json=payload,
            )
            if response.status_code != 401 or authentication_attempt == 1:
                response.raise_for_status()
                return response
            self._access_token = None
            self._authenticate()
        raise RuntimeError("unreachable authentication retry state")

    def get_json(self, path: str) -> Any:
        return self._request("GET", path).json()

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload=payload).json()

    def get_bytes(self, path: str) -> bytes:
        return self._request("GET", path).content


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditFailure(message)


def _as_date(value: object, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise AuditFailure(f"{label} is not an ISO date") from error


def _parse_timestamp(value: object) -> datetime:
    timestamp = str(value)
    return datetime.fromisoformat(
        timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    )


def _assessment_elapsed_seconds(steps: list[dict[str, Any]]) -> float:
    try:
        started_values = [_parse_timestamp(step["started_at"]) for step in steps]
        finished_values = [_parse_timestamp(step["finished_at"]) for step in steps]
    except (KeyError, TypeError, ValueError) as error:
        raise AuditFailure("assessment step timestamps are incomplete") from error
    _require(
        started_values
        and finished_values
        and all(item.tzinfo is not None for item in started_values + finished_values),
        "assessment step timestamps must include a timezone",
    )
    elapsed_seconds = (max(finished_values) - min(started_values)).total_seconds()
    _require(elapsed_seconds >= 0, "assessment step timestamps are out of order")
    return round(elapsed_seconds, 3)


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
    )
    _require(
        all(
            str(decision.get(field) or "").strip() for field in required_decision_fields
        ),
        "assessment decision is missing required content",
    )
    _require(
        "time_horizon" in decision,
        "assessment decision is missing the time horizon field",
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
        "time_horizon": decision.get("time_horizon"),
        "time_horizon_status": (
            "set" if str(decision.get("time_horizon") or "").strip() else "not_set"
        ),
        "memory_source_count": len(sources),
        "memory_horizons": [int(source["horizon"]) for source in sources],
        "validation_horizons": sorted(int(item["horizon"]) for item in validations),
        "artifact_count": len(artifacts),
    }


def assessment_payload(analysis_date: date) -> dict[str, Any]:
    compact_date = analysis_date.strftime("%Y%m%d")
    return {
        "items": [{"ticker": "TSLA", "analysis_date": analysis_date.isoformat()}],
        "analysts": ["market", "social", "news", "fundamentals"],
        "depth": "deep",
        "memory_mode": "historical",
        "language": "Chinese",
        "idempotency_key": f"tsla-monthly-audit-{compact_date}-v1",
    }


def _persist_checkpoint_failure(
    state: dict[str, Any],
    state_path: Path,
    key: str,
    run_id: str,
    message: str,
) -> None:
    state["checkpoints"][key] = {
        "analysis_date": key,
        "run_id": run_id,
        "status": "failed",
        "error": message,
    }
    save_state(state_path, state)


def run_checkpoint(
    api: Any,
    analysis_date: date,
    state: dict[str, Any],
    state_path: Path,
    *,
    poll_seconds: float = 10,
    assessment_timeout_seconds: float = 90 * 60,
    validation_timeout_seconds: float = 20 * 60,
    sleep=time.sleep,
    clock=time.monotonic,
    force_verify: bool = False,
) -> dict[str, Any]:
    key = analysis_date.isoformat()
    checkpoints = state.setdefault("checkpoints", {})
    saved = dict(checkpoints.get(key) or {})
    if (
        not force_verify
        and saved.get("status") == "passed"
        and isinstance(saved.get("summary"), dict)
    ):
        return dict(saved["summary"])

    run_id = str(saved.get("run_id") or "")
    if force_verify and not run_id:
        raise AuditFailure(f"missing checkpoint for verify-only date {key}")
    if not run_id:
        submission = api.post_json(
            "/api/v1/assessments", assessment_payload(analysis_date)
        )
        items = submission.get("items") if isinstance(submission, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not items[0].get("id"):
            raise AuditFailure("assessment submission did not return exactly one run")
        run_id = str(items[0]["id"])
        checkpoints[key] = {
            "analysis_date": key,
            "run_id": run_id,
            "status": "submitted",
        }
        save_state(state_path, state)

    checkpoint_started = clock()
    assessment_deadline = checkpoint_started + assessment_timeout_seconds
    previous_status = None
    while True:
        run = api.get_json(f"/api/v1/assessments/{run_id}")
        status = str(run.get("status") or "")
        if status != previous_status:
            print(
                f"checkpoint={key} run_id={run_id} assessment_status={status}",
                flush=True,
            )
            previous_status = status
            checkpoints[key] = {
                "analysis_date": key,
                "run_id": run_id,
                "status": status,
            }
            save_state(state_path, state)
        if status == "succeeded":
            break
        if status in {"failed", "cancelled", "needs_attention"}:
            message = f"assessment failed with status {status}"
            _persist_checkpoint_failure(state, state_path, key, run_id, message)
            raise AuditFailure(message)
        if clock() >= assessment_deadline:
            message = "assessment timed out before reaching a terminal state"
            _persist_checkpoint_failure(state, state_path, key, run_id, message)
            raise AuditFailure(message)
        sleep(poll_seconds)

    validation_deadline = clock() + validation_timeout_seconds
    previous_validation_statuses = None
    while True:
        validations = api.get_json(f"/api/v1/assessments/{run_id}/validations")
        statuses = tuple(
            sorted(
                (int(item["horizon"]), str(item.get("status") or ""))
                for item in validations
            )
        )
        if statuses != previous_validation_statuses:
            rendered = ",".join(f"{horizon}d:{status}" for horizon, status in statuses)
            print(
                f"checkpoint={key} run_id={run_id} validation_statuses={rendered}",
                flush=True,
            )
            previous_validation_statuses = statuses
        terminal_failure = next(
            (
                item
                for item in validations
                if item.get("status") in {"failed", "unavailable"}
            ),
            None,
        )
        if terminal_failure is not None:
            message = (
                f"validation failed horizon={terminal_failure.get('horizon')} "
                f"status={terminal_failure.get('status')} "
                f"error_code={terminal_failure.get('error_code')}"
            )
            _persist_checkpoint_failure(state, state_path, key, run_id, message)
            raise AuditFailure(message)
        if len(validations) == 3 and all(
            item.get("status") == "completed" for item in validations
        ):
            break
        if clock() >= validation_deadline:
            message = "validation timed out before all horizons completed"
            _persist_checkpoint_failure(state, state_path, key, run_id, message)
            raise AuditFailure(message)
        sleep(poll_seconds)

    steps = api.get_json(f"/api/v1/assessments/{run_id}/steps")
    decision = api.get_json(f"/api/v1/assessments/{run_id}/decision")
    artifacts = api.get_json(f"/api/v1/assessments/{run_id}/artifacts")
    verified_artifacts = []
    for artifact in artifacts:
        content = api.get_bytes(f"/api/v1/artifacts/{artifact['id']}")
        observed_sha256 = hashlib.sha256(content).hexdigest()
        verified_artifacts.append(
            {
                **artifact,
                "integrity_verified": observed_sha256 == artifact.get("sha256"),
            }
        )
    checkpoint = {
        "run": run,
        "steps": steps,
        "decision": decision,
        "validations": validations,
        "artifacts": verified_artifacts,
    }
    summary = validate_checkpoint(checkpoint)
    summary.update(
        {
            "elapsed_seconds": _assessment_elapsed_seconds(steps),
            "time_horizon": decision.get("time_horizon"),
            "memory_sources": list((run.get("memory") or {}).get("sources") or []),
            "validations": validations,
            "artifact_hashes": [
                {"kind": item.get("kind"), "sha256": item.get("sha256")}
                for item in verified_artifacts
            ],
        }
    )
    checkpoints[key] = {
        "analysis_date": key,
        "run_id": run_id,
        "status": "passed",
        "summary": summary,
    }
    save_state(state_path, state)
    print(f"checkpoint={key} run_id={run_id} quality_status=passed", flush=True)
    return summary


def run_audit(
    api: Any,
    state_path: Path,
    *,
    dates: tuple[date, ...] = AUDIT_DATES,
    verify_only: bool = False,
    **checkpoint_options: Any,
) -> list[dict[str, Any]]:
    if tuple(sorted(dates)) != dates:
        raise AuditFailure("audit dates must be in chronological order")
    state = load_state(state_path)
    checkpoints = state.setdefault("checkpoints", {})
    if verify_only:
        for analysis_date in dates:
            saved = checkpoints.get(analysis_date.isoformat())
            if not isinstance(saved, dict) or not saved.get("run_id"):
                raise AuditFailure(
                    f"missing checkpoint for verify-only date {analysis_date.isoformat()}"
                )
    summaries = []
    for analysis_date in dates:
        summaries.append(
            run_checkpoint(
                api,
                analysis_date,
                state,
                state_path,
                force_verify=verify_only,
                **checkpoint_options,
            )
        )
    return summaries


def build_api_client(
    http: httpx.Client,
    env_file: Path,
    *,
    token_url: str,
    api_url: str,
) -> AuditApiClient:
    values = dotenv_values(env_file)
    client_secret = values.get("TRADINGNG_API_CLIENT_SECRET")
    if not isinstance(client_secret, str) or not client_secret:
        raise AuditFailure("TRADINGNG_API_CLIENT_SECRET is not configured")
    return AuditApiClient(
        http,
        token_url=token_url,
        api_url=api_url,
        client_id="tradingng-api",
        client_secret=client_secret,
    )


def validate_preflight(api: Any) -> dict[str, Any]:
    capacity = api.get_json("/api/v1/system/capacity")
    open_circuits = list(capacity.get("open_circuits") or [])
    _require(
        not open_circuits, f"preflight found open circuit breakers: {open_circuits}"
    )
    _require(
        capacity.get("admission_allowed") is True,
        "preflight admission is blocked: "
        + ",".join(str(item) for item in capacity.get("admission_reasons") or []),
    )
    _require(
        bool(capacity.get("gateway_model"))
        and bool(capacity.get("gateway_reasoning_effort")),
        "preflight Gateway configuration is incomplete",
    )
    return {
        "admission_allowed": True,
        "admitted_or_running": int(capacity.get("admitted_or_running") or 0),
        "queued": int(capacity.get("queued") or 0),
        "gateway_model": str(capacity["gateway_model"]),
        "gateway_reasoning_effort": str(capacity["gateway_reasoning_effort"]),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the resumable TSLA monthly assessment quality audit"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env.platform"))
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("var/tsla-monthly-audit"),
    )
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--assessment-timeout-seconds", type=float, default=90 * 60)
    parser.add_argument("--validation-timeout-seconds", type=float, default=20 * 60)
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
    _require(options.poll_seconds >= 0, "poll interval cannot be negative")
    _require(
        options.assessment_timeout_seconds > 0, "assessment timeout must be positive"
    )
    _require(
        options.validation_timeout_seconds > 0, "validation timeout must be positive"
    )
    state_path = options.state_dir / "state.json"
    with httpx.Client(timeout=httpx.Timeout(60, connect=10)) as http:
        api = build_api_client(
            http,
            options.env_file,
            token_url=options.token_url,
            api_url=options.api_url,
        )
        preflight = validate_preflight(api)
        print(
            "preflight=" + json.dumps(preflight, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        summaries = run_audit(
            api,
            state_path,
            verify_only=options.verify_only,
            poll_seconds=options.poll_seconds,
            assessment_timeout_seconds=options.assessment_timeout_seconds,
            validation_timeout_seconds=options.validation_timeout_seconds,
        )
    print(
        "audit_complete="
        + json.dumps(
            {
                "checkpoint_count": len(summaries),
                "analysis_dates": [item["analysis_date"] for item in summaries],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
