from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from tradingng_platform.api.auth import require_admin_scope
from tradingng_platform.api.errors import request_id_for
from tradingng_platform.auth.principal import Principal
from tradingng_platform.identity.contracts import (
    CreateUserCommand,
    TemporaryPasswordResponse,
    UpdateUserCommand,
    UserDetailView,
    UserPage,
)

router = APIRouter(prefix="/admin/users", tags=["user administration"])


@router.get("", response_model=UserPage, operation_id="list_admin_users")
async def list_users(
    request: Request,
    principal: Annotated[Principal, Depends(require_admin_scope())],
    search: str | None = Query(default=None, max_length=255),
    role: Literal["Admin", "User"] | None = None,
    status_filter: Literal["active", "disabled"] | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> UserPage:
    return await request.app.state.identity_admin.list_users(
        principal,
        search=search,
        role=role,
        status=status_filter,
        page=page,
        page_size=page_size,
    )


@router.get("/{user_id}", response_model=UserDetailView, operation_id="get_admin_user")
async def get_user(
    user_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_admin_scope())],
) -> UserDetailView:
    return await request.app.state.identity_admin.get_user(principal, user_id)


@router.post(
    "",
    response_model=TemporaryPasswordResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_admin_user",
)
async def create_user(
    command: CreateUserCommand,
    request: Request,
    principal: Annotated[Principal, Depends(require_admin_scope())],
) -> TemporaryPasswordResponse:
    created = await request.app.state.identity_admin.create_user(
        principal,
        command,
        request_id_for(request),
    )
    return TemporaryPasswordResponse(
        user=created.user,
        temporary_password=created.temporary_password.get_secret_value(),
    )


@router.patch(
    "/{user_id}",
    response_model=UserDetailView,
    operation_id="update_admin_user",
)
async def update_user(
    user_id: UUID,
    command: UpdateUserCommand,
    request: Request,
    principal: Annotated[Principal, Depends(require_admin_scope())],
) -> UserDetailView:
    return await request.app.state.identity_admin.update_user(
        principal,
        user_id,
        command,
        request_id_for(request),
    )


@router.post(
    "/{user_id}/reset-password",
    response_model=TemporaryPasswordResponse,
    operation_id="reset_admin_user_password",
)
async def reset_password(
    user_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_admin_scope())],
) -> TemporaryPasswordResponse:
    created = await request.app.state.identity_admin.reset_password(
        principal,
        user_id,
        request_id_for(request),
    )
    return TemporaryPasswordResponse(
        user=created.user,
        temporary_password=created.temporary_password.get_secret_value(),
    )


@router.post(
    "/{user_id}/logout",
    response_model=UserDetailView,
    operation_id="logout_admin_user",
)
async def logout_user(
    user_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_admin_scope())],
) -> UserDetailView:
    return await request.app.state.identity_admin.logout_user(
        principal,
        user_id,
        request_id_for(request),
    )
