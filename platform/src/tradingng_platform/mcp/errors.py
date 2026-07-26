from __future__ import annotations

import functools
import logging
import uuid
from collections.abc import Callable
from typing import ParamSpec, TypeVar

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from tradingng_platform.assessments.service import (
    AssessmentAccessDenied,
    AssessmentAnalystsIncompatible,
    AssessmentAssetTypeConflict,
    AssessmentIdempotencyConflict,
    AssessmentInstrumentIdentityConflict,
    AssessmentNotFound,
    AssessmentRetryNotAllowed,
)
from tradingng_platform.instruments.classification import (
    InstrumentClassificationNotFound,
    InstrumentClassificationUnavailable,
    InstrumentTypeUnsupported,
)
from tradingng_platform.records.service import RecordNotFound

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


def safe_tool(function: Callable[P, R]) -> Callable[P, R]:
    """Convert domain failures into stable, non-sensitive MCP tool errors."""

    @functools.wraps(function)
    async def guarded(*args: P.args, **kwargs: P.kwargs):
        try:
            return await function(*args, **kwargs)
        except ToolError:
            raise
        except Exception as error:
            raise ToolError(_safe_message(error)) from None

    return guarded


def safe_resource(function: Callable[P, R]) -> Callable[P, R]:
    """Apply the same safe error vocabulary to resource reads."""

    @functools.wraps(function)
    async def guarded(*args: P.args, **kwargs: P.kwargs):
        try:
            return await function(*args, **kwargs)
        except Exception as error:
            raise ValueError(_safe_message(error)) from None

    return guarded


def _safe_message(error: Exception) -> str:
    if isinstance(error, (ValidationError, ValueError, TypeError)):
        return "InvalidParams: one or more tool arguments are invalid"
    if isinstance(error, (AssessmentNotFound, RecordNotFound)):
        return "ResourceNotFound: the requested TradingNG record was not found"
    if isinstance(error, (PermissionError, AssessmentAccessDenied)):
        return "PermissionDenied: the authenticated principal lacks the required scope or access"
    if isinstance(
        error,
        (
            AssessmentIdempotencyConflict,
            AssessmentInstrumentIdentityConflict,
            AssessmentRetryNotAllowed,
        ),
    ):
        return "Conflict: the requested operation conflicts with existing assessment state"
    if isinstance(
        error,
        (
            AssessmentAnalystsIncompatible,
            AssessmentAssetTypeConflict,
            InstrumentClassificationNotFound,
            InstrumentTypeUnsupported,
        ),
    ):
        return "InvalidParams: the submitted instrument or analyst selection is not supported"
    if isinstance(error, InstrumentClassificationUnavailable):
        return "ServiceUnavailable: instrument classification is temporarily unavailable"
    if isinstance(error, (httpx.HTTPError, SQLAlchemyError, OSError, TimeoutError)):
        return "ServiceUnavailable: a required TradingNG dependency is unavailable"
    request_id = uuid.uuid4().hex
    logger.error(
        "Unexpected MCP tool failure request_id=%s type=%s",
        request_id,
        type(error).__name__,
    )
    return f"InternalError: request_id={request_id}"
