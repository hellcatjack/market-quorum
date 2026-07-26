import pytest

from tradingng_platform.domain.runs import RunStatus, assert_transition


def test_state_machine_accepts_normal_and_cancel_paths():
    assert_transition(RunStatus.QUEUED, RunStatus.ADMITTED)
    assert_transition(RunStatus.RUNNING_ANALYSTS, RunStatus.CANCEL_REQUESTED)
    assert_transition(RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLING)
    assert_transition(RunStatus.CANCELLING, RunStatus.CANCELLED)


def test_state_machine_rejects_terminal_rewrite():
    with pytest.raises(ValueError, match="illegal run transition"):
        assert_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING_ANALYSTS)
