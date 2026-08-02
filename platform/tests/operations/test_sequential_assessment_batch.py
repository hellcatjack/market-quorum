import json
from datetime import date, datetime, timezone

import pytest
from sequential_assessment_batch import (
    SequentialBatchError,
    create_state,
    reconcile_once,
    run_plan,
    validate_state,
)

NOW = datetime(2026, 8, 2, 5, 30, tzinfo=timezone.utc)
DATES = (date(2026, 2, 27), date(2026, 3, 31))


class FakeApi:
    def __init__(self):
        self.posts = []
        self.runs = {}

    def post_json(self, path, payload):
        self.posts.append((path, payload))
        if path.endswith("/retry"):
            raise AssertionError("unexpected retry")
        item = payload["items"][0]
        run_id = f"run-{item['ticker'].lower()}-{item['analysis_date']}"
        run = {
            "id": run_id,
            "ticker": item["ticker"],
            "analysis_date": item["analysis_date"],
            "status": "queued",
            "created_at": NOW.isoformat(),
            "finished_at": None,
        }
        self.runs[run_id] = run
        return {"items": [dict(run)], "next_cursor": None}

    def get_json(self, path):
        run_id = path.rsplit("/", 1)[-1]
        return dict(self.runs[run_id])


def test_same_symbol_submits_next_date_only_after_prior_success(tmp_path):
    api = FakeApi()
    state = create_state(("AMD",), DATES, plan_id="monthly-v2", now=lambda: NOW)
    state_path = tmp_path / "state.json"

    reconcile_once(api, state, state_path, now=lambda: NOW)
    reconcile_once(api, state, state_path, now=lambda: NOW)

    assert [call[1]["items"][0]["analysis_date"] for call in api.posts] == ["2026-02-27"]

    first_run = api.runs["run-amd-2026-02-27"]
    first_run["status"] = "succeeded"
    first_run["finished_at"] = "2026-08-02T05:40:00+00:00"
    reconcile_once(api, state, state_path, now=lambda: NOW)
    reconcile_once(api, state, state_path, now=lambda: NOW)

    assert [call[1]["items"][0]["analysis_date"] for call in api.posts] == [
        "2026-02-27",
        "2026-03-31",
    ]
    assert api.posts[1][1]["memory_mode"] == "historical"


def test_different_symbols_each_submit_their_earliest_date(tmp_path):
    api = FakeApi()
    state = create_state(("AMD", "HD"), DATES, plan_id="monthly-v2", now=lambda: NOW)

    summary = reconcile_once(api, state, tmp_path / "state.json", now=lambda: NOW)

    assert {
        (payload["items"][0]["ticker"], payload["items"][0]["analysis_date"])
        for _, payload in api.posts
    } == {
        ("AMD", "2026-02-27"),
        ("HD", "2026-02-27"),
    }
    assert summary["active"] == 2
    assert summary["completed"] == 0


def test_terminal_failure_blocks_symbol_without_submitting_later_date(tmp_path):
    api = FakeApi()
    state = create_state(("AMD",), DATES, plan_id="monthly-v2", now=lambda: NOW)
    state_path = tmp_path / "state.json"
    reconcile_once(api, state, state_path, now=lambda: NOW)
    api.runs["run-amd-2026-02-27"]["status"] = "failed"

    summary = reconcile_once(api, state, state_path, now=lambda: NOW)
    reconcile_once(api, state, state_path, now=lambda: NOW)

    assert summary["blocked"] == 1
    assert len(api.posts) == 1
    assert state["tracks"]["AMD"]["status"] == "blocked"


def test_validate_state_rejects_a_later_run_before_prior_success():
    state = create_state(("AMD",), DATES, plan_id="monthly-v2", now=lambda: NOW)
    state["tracks"]["AMD"]["assessments"] = {
        "2026-03-31": {
            "analysis_date": "2026-03-31",
            "run_id": "out-of-order",
            "status": "queued",
        }
    }

    with pytest.raises(SequentialBatchError, match="out-of-order"):
        validate_state(state, ("AMD",), DATES, plan_id="monthly-v2")


def test_reconcile_resumes_saved_current_run_without_duplicate_submission(tmp_path):
    api = FakeApi()
    state_path = tmp_path / "state.json"
    state = create_state(("AMD",), DATES, plan_id="monthly-v2", now=lambda: NOW)
    reconcile_once(api, state, state_path, now=lambda: NOW)

    restored = json.loads(state_path.read_text(encoding="utf-8"))
    reconcile_once(api, restored, state_path, now=lambda: NOW)

    assert len(api.posts) == 1
    assert restored["tracks"]["AMD"]["assessments"]["2026-02-27"]["run_id"] == (
        "run-amd-2026-02-27"
    )


def test_run_plan_completes_all_dates_in_order(tmp_path):
    class AutoSuccessApi(FakeApi):
        def get_json(self, path):
            run = super().get_json(path)
            run["status"] = "succeeded"
            run["finished_at"] = "2026-08-02T05:40:00+00:00"
            self.runs[run["id"]] = run
            return run

    api = AutoSuccessApi()

    summary = run_plan(
        api,
        tmp_path / "state.json",
        ("AMD",),
        DATES,
        plan_id="monthly-v2",
        poll_seconds=0,
        timeout_seconds=60,
        sleep=lambda _: None,
        clock=lambda: 0,
        now=lambda: NOW,
    )

    assert summary["completed"] == 1
    assert summary["succeeded"] == 2
    assert [payload["items"][0]["analysis_date"] for _, payload in api.posts] == [
        "2026-02-27",
        "2026-03-31",
    ]
