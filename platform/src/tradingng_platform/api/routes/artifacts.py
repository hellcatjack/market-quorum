import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from tradingng_platform.api.auth import require_scopes
from tradingng_platform.api.errors import ApiError
from tradingng_platform.auth.principal import Principal
from tradingng_platform.records.contracts import ArtifactView, DecisionView, EvidenceView
from tradingng_platform.records.service import ArtifactIntegrityError, RecordNotFound

router = APIRouter(tags=["records"])


def _record_error(error: Exception) -> None:
    if isinstance(error, RecordNotFound):
        raise ApiError(404, "record_not_found", "Assessment record was not found") from None
    if isinstance(error, ArtifactIntegrityError):
        raise ApiError(409, "artifact_integrity_error", "Artifact integrity check failed") from None
    raise error


@router.get(
    "/assessments/{run_id}/decision",
    response_model=DecisionView,
    operation_id="get_assessment_decision",
)
async def get_decision(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> DecisionView:
    try:
        return await request.app.state.records.decision(principal, run_id)
    except RecordNotFound as error:
        _record_error(error)


@router.get(
    "/assessments/{run_id}/evidence",
    response_model=list[EvidenceView],
    operation_id="list_assessment_evidence",
)
async def list_evidence(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> list[EvidenceView]:
    try:
        return await request.app.state.records.evidence(principal, run_id)
    except RecordNotFound as error:
        _record_error(error)


@router.get(
    "/assessments/{run_id}/artifacts",
    response_model=list[ArtifactView],
    operation_id="list_assessment_artifacts",
)
async def list_artifacts(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("artifacts:read"))],
) -> list[ArtifactView]:
    try:
        return await request.app.state.records.list_artifacts(principal, run_id)
    except RecordNotFound as error:
        _record_error(error)


@router.get("/artifacts/{artifact_id}", operation_id="download_artifact")
async def download_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("artifacts:read"))],
):
    try:
        opened = await request.app.state.records.open_artifact(principal, artifact_id)
    except (RecordNotFound, ArtifactIntegrityError) as error:
        _record_error(error)
    return FileResponse(
        opened.path,
        media_type=opened.media_type,
        filename=opened.filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "ETag": f'"sha256:{opened.sha256}"',
        },
    )
