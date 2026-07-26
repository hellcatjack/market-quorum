import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from tradingng_platform.api.auth import require_scopes
from tradingng_platform.api.errors import ApiError, request_id_for
from tradingng_platform.auth.principal import Principal
from tradingng_platform.records.contracts import (
    CommentView,
    CreateComment,
    CreateReview,
    ReviewView,
)
from tradingng_platform.records.service import RecordNotFound

router = APIRouter(tags=["collaboration"])


def _not_found() -> None:
    raise ApiError(404, "assessment_not_found", "Assessment was not found")


@router.post(
    "/assessments/{run_id}/reviews",
    response_model=ReviewView,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_assessment_review",
)
async def create_review(
    run_id: uuid.UUID,
    command: CreateReview,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:review"))],
) -> ReviewView:
    try:
        return await request.app.state.records.add_review(
            principal,
            run_id,
            command.verdict,
            command.comment,
            request_id_for(request),
        )
    except RecordNotFound:
        _not_found()


@router.get(
    "/assessments/{run_id}/reviews",
    response_model=list[ReviewView],
    operation_id="list_assessment_reviews",
)
async def list_reviews(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> list[ReviewView]:
    try:
        return await request.app.state.records.list_reviews(principal, run_id)
    except RecordNotFound:
        _not_found()


@router.post(
    "/assessments/{run_id}/comments",
    response_model=CommentView,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_assessment_comment",
)
async def create_comment(
    run_id: uuid.UUID,
    command: CreateComment,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> CommentView:
    try:
        return await request.app.state.records.add_comment(
            principal,
            run_id,
            command.body,
            request_id_for(request),
        )
    except RecordNotFound:
        _not_found()


@router.get(
    "/assessments/{run_id}/comments",
    response_model=list[CommentView],
    operation_id="list_assessment_comments",
)
async def list_comments(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> list[CommentView]:
    try:
        return await request.app.state.records.list_comments(principal, run_id)
    except RecordNotFound:
        _not_found()
