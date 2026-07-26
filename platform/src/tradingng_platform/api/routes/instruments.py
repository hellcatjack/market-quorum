from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from tradingng_platform.api.auth import require_scopes
from tradingng_platform.api.errors import ApiError
from tradingng_platform.auth.principal import Principal
from tradingng_platform.records.contracts import (
    InstrumentHistoryItem,
    InstrumentSummaryView,
)
from tradingng_platform.records.service import RecordNotFound

router = APIRouter(tags=["instruments"])


@router.get(
    "/instruments/{ticker}",
    response_model=InstrumentSummaryView,
    operation_id="get_instrument_summary",
)
async def instrument_summary(
    ticker: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> InstrumentSummaryView:
    try:
        return await request.app.state.records.instrument_summary(principal, ticker)
    except (RecordNotFound, ValueError):
        raise ApiError(404, "instrument_not_found", "Instrument was not found") from None


@router.get(
    "/instruments/{ticker}/history",
    response_model=list[InstrumentHistoryItem],
    operation_id="get_instrument_history",
)
async def instrument_history(
    ticker: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[InstrumentHistoryItem]:
    try:
        return await request.app.state.records.instrument_history(principal, ticker, limit)
    except (RecordNotFound, ValueError):
        raise ApiError(404, "instrument_not_found", "Instrument was not found") from None
