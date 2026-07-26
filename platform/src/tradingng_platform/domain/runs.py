from enum import Enum


class RunStatus(str, Enum):
    QUEUED = "queued"
    ADMITTED = "admitted"
    STARTING = "starting"
    RUNNING_ANALYSTS = "running_analysts"
    RESEARCH_DEBATE = "research_debate"
    TRADER_PLAN = "trader_plan"
    RISK_DEBATE = "risk_debate"
    PORTFOLIO_DECISION = "portfolio_decision"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    NEEDS_ATTENTION = "needs_attention"


TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.NEEDS_ATTENTION,
}

ALLOWED_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.ADMITTED, RunStatus.CANCELLED},
    RunStatus.ADMITTED: {
        RunStatus.STARTING,
        RunStatus.CANCEL_REQUESTED,
        RunStatus.QUEUED,
    },
    RunStatus.STARTING: {
        RunStatus.RUNNING_ANALYSTS,
        RunStatus.FAILED,
        RunStatus.CANCEL_REQUESTED,
    },
    RunStatus.RUNNING_ANALYSTS: {
        RunStatus.RESEARCH_DEBATE,
        RunStatus.FAILED,
        RunStatus.CANCEL_REQUESTED,
    },
    RunStatus.RESEARCH_DEBATE: {
        RunStatus.TRADER_PLAN,
        RunStatus.FAILED,
        RunStatus.CANCEL_REQUESTED,
    },
    RunStatus.TRADER_PLAN: {
        RunStatus.RISK_DEBATE,
        RunStatus.FAILED,
        RunStatus.CANCEL_REQUESTED,
    },
    RunStatus.RISK_DEBATE: {
        RunStatus.PORTFOLIO_DECISION,
        RunStatus.FAILED,
        RunStatus.CANCEL_REQUESTED,
    },
    RunStatus.PORTFOLIO_DECISION: {
        RunStatus.FINALIZING,
        RunStatus.FAILED,
        RunStatus.CANCEL_REQUESTED,
    },
    RunStatus.FINALIZING: {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCEL_REQUESTED,
    },
    RunStatus.CANCEL_REQUESTED: {RunStatus.CANCELLING, RunStatus.CANCELLED},
    RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.NEEDS_ATTENTION},
}


def assert_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal run transition: {current.value} -> {target.value}")
