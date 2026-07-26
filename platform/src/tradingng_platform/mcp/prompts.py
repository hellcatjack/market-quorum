from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from tradingng_platform.mcp.context import current_principal

RunIds = Annotated[str, Field(min_length=3, max_length=400)]
Focus = Annotated[str, Field(min_length=1, max_length=200)]


def _parse_run_ids(raw_run_ids: str) -> list[str]:
    run_ids = [item.strip() for item in raw_run_ids.split(",") if item.strip()]
    if not 2 <= len(run_ids) <= 10 or len(set(run_ids)) != len(run_ids):
        raise ValueError("run_ids must contain 2 to 10 unique comma-separated values")
    return run_ids


def register_prompts(server: FastMCP) -> None:
    @server.prompt()
    def review_assessment(run_id: str, focus: Focus = "evidence quality") -> str:
        """Review one completed or in-progress assessment against stored evidence."""
        current_principal().require("assessments:read")
        return (
            "Review TradingNG assessment "
            + run_id
            + " with focus on "
            + focus
            + ". Read tradingng://assessments/"
            + run_id
            + "/summary and tradingng://assessments/"
            + run_id
            + "/evidence first. Separate observed facts, model inference, missing evidence "
            "and reviewer judgment. Do not invent evidence that is absent from these resources."
        )

    @server.prompt()
    def compare_instrument_runs(ticker: str, run_ids: RunIds) -> str:
        """Compare stored assessment conclusions for one instrument."""
        current_principal().require("assessments:read")
        ids = ", ".join(_parse_run_ids(run_ids))
        return (
            f"Compare TradingNG runs {ids} for {ticker}. Read "
            f"tradingng://instruments/{ticker}/history and each run summary. Identify "
            "config, model, reasoning-effort and data changes. Do not attribute a conclusion "
            "change to price movement unless the stored evidence supports it."
        )

    @server.prompt()
    def summarize_risk_changes(ticker: str, run_ids: RunIds) -> str:
        """Summarize how documented risk changed across stored runs."""
        current_principal().require("assessments:read")
        ids = ", ".join(_parse_run_ids(run_ids))
        return (
            f"Summarize risk changes across TradingNG runs {ids} for {ticker}. Read "
            f"tradingng://instruments/{ticker}/history, then the summary and risk report "
            "resources for each run. Distinguish changed evidence, changed assumptions, changed "
            "model output and unresolved risk."
        )

    @server.prompt()
    def validate_past_decision(run_id: str, focus: Focus = "decision quality") -> str:
        """Prepare an evidence-grounded retrospective validation of one past decision."""
        current_principal().require("assessments:read", "validations:read")
        return (
            f"Validate past TradingNG assessment {run_id} with focus on {focus}. Read "
            f"tradingng://assessments/{run_id}/summary and "
            f"tradingng://assessments/{run_id}/evidence. Compare only against observations "
            "available after its analysis date, state the evaluation horizon, and separate "
            "outcome quality from reasoning quality. This prompt must not modify the run."
        )
