import hashlib
from copy import deepcopy
from datetime import date
from pathlib import Path

import httpx
import pytest
from tsla_monthly_audit import (
    AUDIT_DATES,
    AuditApiClient,
    AuditFailure,
    assessment_payload,
    build_api_client,
    load_state,
    parse_args,
    run_audit,
    run_checkpoint,
    save_state,
    validate_checkpoint,
    validate_preflight,
)


@pytest.fixture
def valid_checkpoint():
    return {
        "run": {
            "id": "00000000-0000-0000-0000-000000000012",
            "ticker": "TSLA",
            "analysis_date": "2025-08-29",
            "status": "succeeded",
            "data_vendors": {
                "core_stock_apis": "alpha_vantage",
                "technical_indicators": "alpha_vantage",
                "fundamental_data": "alpha_vantage",
                "news_data": "alpha_vantage",
            },
            "memory": {
                "mode": "historical",
                "sources": [
                    {
                        "source_run_id": "00000000-0000-0000-0000-000000000011",
                        "validation_id": "00000000-0000-0000-0000-000000000021",
                        "analysis_date": "2025-07-31",
                        "exit_session": "2025-08-08",
                        "horizon": 5,
                    }
                ],
            },
        },
        "steps": [
            {
                "name": name,
                "status": "completed",
                "started_at": f"2026-07-27T00:0{index}:00Z",
                "finished_at": f"2026-07-27T00:0{index}:30Z",
            }
            for index, name in enumerate(
                (
                    "analyst_research",
                    "research_debate",
                    "trader_plan",
                    "risk_debate",
                    "portfolio_decision",
                )
            )
        ],
        "decision": {
            "rating": "Hold",
            "executive_summary": "Point-in-time summary",
            "investment_thesis": "Point-in-time thesis",
            "price_target": None,
            "time_horizon": "6-12 months",
        },
        "validations": [
            {
                "horizon": horizon,
                "status": "completed",
                "calculation_version": "validation.v2",
                "provider_id": "alphavantage",
                "provider_adapter_version": "alphavantage.v1",
                "normalization_version": "prices.v1",
                "entry_session": "2025-08-29",
                "exit_session": exit_session,
                "trigger_results": {
                    "direction_basis": "instrument_total_return",
                    "direction_rule_version": "rating-direction.v2",
                },
            }
            for horizon, exit_session in (
                (1, "2025-09-02"),
                (5, "2025-09-08"),
                (20, "2025-09-29"),
            )
        ],
        "artifacts": [
            {
                "id": "00000000-0000-0000-0000-000000000031",
                "kind": "final_report",
                "sha256": "a" * 64,
                "integrity_verified": True,
            }
        ],
    }


def test_schedule_has_one_session_per_month_and_a_mature_final_cutoff():
    assert (
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
    ) == AUDIT_DATES
    assert len({(item.year, item.month) for item in AUDIT_DATES}) == 12


def test_checkpoint_accepts_complete_alpha_only_point_in_time_record(valid_checkpoint):
    summary = validate_checkpoint(valid_checkpoint)

    assert summary["analysis_date"] == "2025-08-29"
    assert summary["memory_source_count"] == 1
    assert summary["validation_horizons"] == [1, 5, 20]
    assert summary["artifact_count"] == 1


def test_checkpoint_rejects_nonexclusive_research_vendor(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["run"]["data_vendors"]["news_data"] = "alpha_vantage,yfinance"

    with pytest.raises(AuditFailure, match="exclusive"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_lookahead_memory(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["run"]["memory"]["sources"][0]["exit_session"] = "2025-08-29"

    with pytest.raises(AuditFailure, match="look-ahead"):
        validate_checkpoint(invalid)


@pytest.mark.parametrize("status", ["running", "failed", "cancelled"])
def test_checkpoint_rejects_noncompleted_step(valid_checkpoint, status):
    invalid = deepcopy(valid_checkpoint)
    invalid["steps"][2]["status"] = status

    with pytest.raises(AuditFailure, match="steps"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_missing_step_timestamp(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["steps"][0]["finished_at"] = None

    with pytest.raises(AuditFailure, match="timestamp"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_incomplete_or_nonalpha_validation(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["validations"][2]["provider_id"] = "yfinance"

    with pytest.raises(AuditFailure, match="Alpha Vantage"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_legacy_direction_semantics(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["validations"][0]["trigger_results"].pop("direction_rule_version")

    with pytest.raises(AuditFailure, match="direction semantics"):
        validate_checkpoint(invalid)


def test_checkpoint_preserves_explicitly_unset_time_horizon(valid_checkpoint):
    checkpoint = deepcopy(valid_checkpoint)
    checkpoint["decision"]["time_horizon"] = None

    summary = validate_checkpoint(checkpoint)

    assert summary["time_horizon"] is None
    assert summary["time_horizon_status"] == "not_set"


def test_checkpoint_rejects_absent_time_horizon_field(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["decision"].pop("time_horizon")

    with pytest.raises(AuditFailure, match="time horizon"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_unverified_artifact(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["artifacts"][0]["integrity_verified"] = False

    with pytest.raises(AuditFailure, match="artifact"):
        validate_checkpoint(invalid)


def test_checkpoint_rejects_more_than_five_or_duplicate_memory_runs(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    source = invalid["run"]["memory"]["sources"][0]
    invalid["run"]["memory"]["sources"] = [deepcopy(source) for _ in range(2)]

    with pytest.raises(AuditFailure, match="distinct"):
        validate_checkpoint(invalid)

    invalid["run"]["memory"]["sources"] = [
        {
            **deepcopy(source),
            "source_run_id": f"00000000-0000-0000-0000-{index:012d}",
            "validation_id": f"10000000-0000-0000-0000-{index:012d}",
        }
        for index in range(6)
    ]
    with pytest.raises(AuditFailure, match="at most five"):
        validate_checkpoint(invalid)


def test_state_round_trip_is_atomic_and_rejects_secrets(tmp_path):
    path = tmp_path / "state.json"
    state = {"version": 1, "checkpoints": {"2025-07-31": {"status": "submitted"}}}

    save_state(path, state)

    assert load_state(path) == state
    assert not list(tmp_path.glob(".*.tmp"))
    with pytest.raises(AuditFailure, match="secret"):
        save_state(path, {"access_token": "must-not-persist"})


def test_api_client_refreshes_expired_token_once_without_leaking_secret():
    token_calls = 0
    api_authorizations = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path.endswith("/token"):
            token_calls += 1
            return httpx.Response(200, json={"access_token": f"token-{token_calls}"})
        api_authorizations.append(request.headers.get("Authorization"))
        if api_authorizations[-1] == "Bearer token-1":
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json={"status": "ok"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        api = AuditApiClient(
            http,
            token_url="https://identity.test/token",
            api_url="https://api.test",
            client_id="audit-client",
            client_secret="must-not-leak",
        )

        assert api.get_json("/api/v1/system/status") == {"status": "ok"}
        assert "must-not-leak" not in repr(api)

    assert token_calls == 2
    assert api_authorizations == ["Bearer token-1", "Bearer token-2"]


def test_api_client_retries_idempotent_get_after_transport_failure():
    api_calls = 0
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "token"})
        api_calls += 1
        if api_calls == 1:
            raise httpx.ConnectError("temporary disconnect", request=request)
        return httpx.Response(200, json={"status": "ok"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        api = AuditApiClient(
            http,
            token_url="https://identity.test/token",
            api_url="https://api.test",
            client_id="audit-client",
            client_secret="secret",
            retry_sleep=sleeps.append,
        )

        assert api.get_json("/api/v1/system/status") == {"status": "ok"}

    assert api_calls == 2
    assert sleeps == [1]


def test_api_client_does_not_blindly_retry_post_after_transport_failure():
    api_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "token"})
        api_calls += 1
        raise httpx.ConnectError("unknown submission outcome", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        api = AuditApiClient(
            http,
            token_url="https://identity.test/token",
            api_url="https://api.test",
            client_id="audit-client",
            client_secret="secret",
            retry_sleep=lambda seconds: None,
        )

        with pytest.raises(httpx.ConnectError):
            api.post_json("/api/v1/assessments", {"items": []})

    assert api_calls == 1


def test_assessment_payload_uses_deep_historical_chinese_defaults():
    payload = assessment_payload(date(2025, 7, 31))

    assert payload == {
        "items": [{"ticker": "TSLA", "analysis_date": "2025-07-31"}],
        "analysts": ["market", "social", "news", "fundamentals"],
        "depth": "deep",
        "memory_mode": "historical",
        "language": "Chinese",
        "idempotency_key": "tsla-monthly-audit-20250731-v1",
    }


class FakeAuditApi:
    def __init__(self, checkpoint):
        self.checkpoint = deepcopy(checkpoint)
        self.posts = []
        self.gets = []
        self.artifact_body = b"verified artifact\n"
        self.checkpoint["artifacts"][0]["sha256"] = hashlib.sha256(self.artifact_body).hexdigest()

    def post_json(self, path, payload):
        self.posts.append((path, payload))
        return {"items": [self.checkpoint["run"]]}

    def get_json(self, path):
        self.gets.append(path)
        run_id = self.checkpoint["run"]["id"]
        mapping = {
            f"/api/v1/assessments/{run_id}": self.checkpoint["run"],
            f"/api/v1/assessments/{run_id}/steps": self.checkpoint["steps"],
            f"/api/v1/assessments/{run_id}/decision": self.checkpoint["decision"],
            f"/api/v1/assessments/{run_id}/artifacts": [
                {key: value for key, value in artifact.items() if key != "integrity_verified"}
                for artifact in self.checkpoint["artifacts"]
            ],
            f"/api/v1/assessments/{run_id}/validations": self.checkpoint["validations"],
        }
        return deepcopy(mapping[path])

    def get_bytes(self, path):
        self.gets.append(path)
        return self.artifact_body


def test_run_checkpoint_submits_polls_verifies_and_persists(tmp_path, valid_checkpoint):
    state_path = tmp_path / "state.json"
    state = load_state(state_path)
    api = FakeAuditApi(valid_checkpoint)

    summary = run_checkpoint(
        api,
        date(2025, 8, 29),
        state,
        state_path,
        poll_seconds=0,
        sleep=lambda seconds: None,
    )

    assert len(api.posts) == 1
    assert api.posts[0] == (
        "/api/v1/assessments",
        assessment_payload(date(2025, 8, 29)),
    )
    assert summary["analysis_date"] == "2025-08-29"
    stored = load_state(state_path)
    assert stored["checkpoints"]["2025-08-29"]["status"] == "passed"
    assert "must-not-leak" not in state_path.read_text(encoding="utf-8")
    assert f"/api/v1/artifacts/{valid_checkpoint['artifacts'][0]['id']}" in api.gets


def test_run_checkpoint_resumes_submitted_run_without_duplicate_post(tmp_path, valid_checkpoint):
    run_id = valid_checkpoint["run"]["id"]
    state_path = tmp_path / "state.json"
    state = {
        "version": 1,
        "checkpoints": {
            "2025-08-29": {
                "analysis_date": "2025-08-29",
                "run_id": run_id,
                "status": "submitted",
            }
        },
    }
    save_state(state_path, state)
    api = FakeAuditApi(valid_checkpoint)

    summary = run_checkpoint(
        api,
        date(2025, 8, 29),
        state,
        state_path,
        poll_seconds=0,
        sleep=lambda seconds: None,
        clock=lambda: 5000.082,
    )

    assert api.posts == []
    assert summary["elapsed_seconds"] == 270.0


def test_run_checkpoint_force_verify_refetches_passed_run(tmp_path, valid_checkpoint):
    run_id = valid_checkpoint["run"]["id"]
    state_path = tmp_path / "state.json"
    state = {
        "version": 1,
        "checkpoints": {
            "2025-08-29": {
                "analysis_date": "2025-08-29",
                "run_id": run_id,
                "status": "passed",
                "summary": {"analysis_date": "stale"},
            }
        },
    }
    api = FakeAuditApi(valid_checkpoint)

    summary = run_checkpoint(
        api,
        date(2025, 8, 29),
        state,
        state_path,
        poll_seconds=0,
        sleep=lambda seconds: None,
        force_verify=True,
    )

    assert summary["analysis_date"] == "2025-08-29"
    assert f"/api/v1/assessments/{run_id}" in api.gets
    assert api.posts == []


@pytest.mark.parametrize(
    ("section", "status", "message"),
    [
        ("run", "failed", "assessment failed"),
        ("validations", "failed", "validation failed"),
    ],
)
def test_run_checkpoint_stops_on_terminal_failure(
    tmp_path, valid_checkpoint, section, status, message
):
    invalid = deepcopy(valid_checkpoint)
    if section == "run":
        invalid["run"]["status"] = status
    else:
        invalid["validations"][0]["status"] = status
    api = FakeAuditApi(invalid)

    with pytest.raises(AuditFailure, match=message):
        run_checkpoint(
            api,
            date(2025, 8, 29),
            load_state(tmp_path / "state.json"),
            tmp_path / "state.json",
            poll_seconds=0,
            sleep=lambda seconds: None,
        )


def test_run_audit_processes_dates_in_chronological_order(monkeypatch, tmp_path):
    observed = []

    def fake_checkpoint(api, analysis_date, state, state_path, **options):
        observed.append((analysis_date, options["force_verify"]))
        summary = {"analysis_date": analysis_date.isoformat()}
        state["checkpoints"][analysis_date.isoformat()] = {
            "run_id": analysis_date.strftime("run-%Y%m%d"),
            "status": "passed",
            "summary": summary,
        }
        save_state(state_path, state)
        return summary

    monkeypatch.setattr("tsla_monthly_audit.run_checkpoint", fake_checkpoint)
    dates = (date(2025, 7, 31), date(2025, 8, 29))

    summaries = run_audit(object(), tmp_path / "state.json", dates=dates)

    assert observed == [(dates[0], False), (dates[1], False)]
    assert [item["analysis_date"] for item in summaries] == [
        "2025-07-31",
        "2025-08-29",
    ]


def test_verify_only_requires_every_checkpoint_before_network_calls(tmp_path):
    with pytest.raises(AuditFailure, match="missing checkpoint"):
        run_audit(
            object(),
            tmp_path / "state.json",
            dates=(date(2025, 7, 31),),
            verify_only=True,
        )


def test_build_api_client_reads_secret_without_exposing_it(tmp_path):
    env_file = tmp_path / ".env.platform"
    env_file.write_text(
        "TRADINGNG_API_CLIENT_SECRET=must-not-leak\n",
        encoding="utf-8",
    )
    with httpx.Client(transport=httpx.MockTransport(lambda request: None)) as http:
        api = build_api_client(
            http,
            env_file,
            token_url="https://identity.test/token",
            api_url="https://api.test",
        )

    assert isinstance(api, AuditApiClient)
    assert "must-not-leak" not in repr(api)


def test_preflight_rejects_open_circuit_or_denied_admission():
    class CapacityApi:
        def __init__(self, payload):
            self.payload = payload

        def get_json(self, path):
            assert path == "/api/v1/system/capacity"
            return self.payload

    healthy = {
        "admission_allowed": True,
        "admission_reasons": [],
        "open_circuits": [],
        "gateway_model": "gpt-5.6-sol",
        "gateway_reasoning_effort": "xhigh",
        "admitted_or_running": 0,
        "queued": 0,
    }

    assert validate_preflight(CapacityApi(healthy))["gateway_model"] == "gpt-5.6-sol"
    with pytest.raises(AuditFailure, match="circuit"):
        validate_preflight(CapacityApi({**healthy, "open_circuits": ["alpha"]}))
    with pytest.raises(AuditFailure, match="admission"):
        validate_preflight(
            CapacityApi({**healthy, "admission_allowed": False, "admission_reasons": ["memory"]})
        )


def test_parse_args_supports_resumable_and_verify_only_paths(tmp_path):
    options = parse_args(
        [
            "--env-file",
            str(tmp_path / ".env.platform"),
            "--state-dir",
            str(tmp_path / "audit"),
            "--verify-only",
        ]
    )

    assert options.env_file == tmp_path / ".env.platform"
    assert options.state_dir == tmp_path / "audit"
    assert options.verify_only is True


def test_script_entrypoint_runs_after_every_function_definition():
    script = Path(__file__).resolve().parents[3] / "scripts" / "tsla_monthly_audit.py"
    source = script.read_text(encoding="utf-8")

    assert source.rfind('if __name__ == "__main__":') > source.rfind("def save_state(")
