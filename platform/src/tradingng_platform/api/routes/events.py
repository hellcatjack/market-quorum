import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from tradingng_platform.api.auth import require_scopes
from tradingng_platform.api.errors import ApiError
from tradingng_platform.assessments.contracts import RunEventPage, RunEventView
from tradingng_platform.assessments.service import AssessmentNotFound
from tradingng_platform.auth.principal import Principal

router = APIRouter(tags=["assessment-events"])
_TERMINAL_EVENTS = frozenset(
    {
        "assessment.succeeded",
        "assessment.failed",
        "assessment.cancelled",
        "assessment.needs_attention",
    }
)


def encode_sse(event: RunEventView) -> bytes:
    payload = event.model_dump_json()
    # Use the default EventSource ``message`` event so clients can consume
    # dynamic runner event types from the JSON payload without pre-registering
    # every possible tool/stage name.
    return (f"id: {event.sequence}\ndata: {payload}\n\n").encode()


def _last_sequence(after: int, last_event_id: str | None) -> int:
    if last_event_id is None or not last_event_id.strip():
        return after
    try:
        parsed = int(last_event_id)
    except ValueError:
        raise ApiError(422, "invalid_last_event_id", "Last-Event-ID must be an integer") from None
    if parsed < 0:
        raise ApiError(422, "invalid_last_event_id", "Last-Event-ID must not be negative")
    return max(after, parsed)


@router.get(
    "/assessments/{run_id}/events",
    operation_id="list_assessment_events",
)
async def assessment_events(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    accept: Annotated[str | None, Header()] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
):
    start = _last_sequence(after, last_event_id)
    try:
        initial_events = await request.app.state.assessments.events(
            principal,
            run_id,
            after=start,
            limit=limit,
        )
    except AssessmentNotFound:
        raise ApiError(404, "assessment_not_found", "Assessment was not found") from None
    if accept is None or "text/event-stream" not in accept.lower():
        return RunEventPage(
            items=initial_events,
            next_after=(initial_events[-1].sequence if len(initial_events) == limit else None),
        )

    async def stream() -> AsyncIterator[bytes]:
        sequence = start
        idle_seconds = 0
        events = initial_events
        while not await request.is_disconnected():
            if events:
                idle_seconds = 0
                for event in events:
                    sequence = event.sequence
                    yield encode_sse(event)
                    if event.event_type in _TERMINAL_EVENTS:
                        return
                events = await request.app.state.assessments.events(
                    principal,
                    run_id,
                    after=sequence,
                    limit=limit,
                )
                continue
            await asyncio.sleep(1)
            idle_seconds += 1
            if idle_seconds >= 15:
                yield b": keepalive\n\n"
                idle_seconds = 0
            events = await request.app.state.assessments.events(
                principal,
                run_id,
                after=sequence,
                limit=limit,
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
