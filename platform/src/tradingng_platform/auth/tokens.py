from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select

from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.auth.principal import Principal
from tradingng_platform.models import ApiCredential, Role, User, UserRole


@dataclass(frozen=True)
class CreatedApiToken:
    credential: ApiCredential
    token: str


class CreateApiCredential(BaseModel):
    scopes: set[str] = Field(min_length=1)
    expires_at: datetime | None = None


class CreatedApiCredentialView(BaseModel):
    id: uuid.UUID
    token: str
    scopes: set[str]
    expires_at: datetime | None


class ApiCredentialView(BaseModel):
    id: uuid.UUID
    public_id: str
    scopes: set[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiTokenService:
    def __init__(self, sessions, pepper: str):
        if len(pepper) < 16:
            raise ValueError("API token pepper must contain at least 16 characters")
        self.sessions = sessions
        self.pepper = pepper.encode("utf-8")

    def _hash(self, token: str) -> str:
        return hashlib.sha256(self.pepper + token.encode("utf-8")).hexdigest()

    async def create(
        self,
        principal: Principal,
        scopes: set[str],
        expires_at: datetime | None = None,
        request_id: str | None = None,
    ) -> CreatedApiToken:
        principal.require("assessments:admin")
        if "Admin" not in principal.roles:
            raise PermissionError("Admin role is required to create API credentials")
        if not scopes or not scopes <= principal.scopes:
            raise PermissionError("API credential scopes exceed the issuing principal")
        now = datetime.now(timezone.utc)
        if expires_at is not None and expires_at <= now:
            raise ValueError("API credential expiry must be in the future")

        public_id = secrets.token_hex(8)
        raw_token = f"tng_{public_id}_{secrets.token_urlsafe(32)}"
        token_hash = self._hash(raw_token)
        async with self.sessions() as session, session.begin():
            user = await AssessmentRepository(session).upsert_user(principal)
            credential = ApiCredential(
                principal_id=user.id,
                public_id=public_id,
                token_hash=token_hash,
                scopes_json=sorted(scopes),
                expires_at=expires_at,
                last_used_at=None,
                revoked_at=None,
            )
            session.add(credential)
            await session.flush()
            if request_id is not None:
                await AssessmentRepository(session).append_audit(
                    principal,
                    "api_credential.create",
                    "api_credential",
                    str(credential.id),
                    request_id,
                    {"public_id": public_id, "scopes": sorted(scopes)},
                )
        return CreatedApiToken(credential=credential, token=raw_token)

    async def verify(self, token: str) -> Principal:
        if not token.startswith("tng_") or len(token) > 256:
            raise ValueError("invalid API credential")
        token_hash = self._hash(token)
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    select(ApiCredential, User)
                    .join(User, ApiCredential.principal_id == User.id)
                    .where(ApiCredential.token_hash == token_hash)
                    .with_for_update(of=ApiCredential)
                )
            ).one_or_none()
            if row is None:
                raise ValueError("invalid API credential")
            credential, user = row
            if not hmac.compare_digest(credential.token_hash, token_hash):
                raise ValueError("invalid API credential")
            now = datetime.now(timezone.utc)
            if credential.revoked_at is not None or (
                credential.expires_at is not None and credential.expires_at <= now
            ):
                raise ValueError("expired or revoked API credential")
            if user.status != "active":
                raise ValueError("API credential owner is inactive")
            roles = frozenset(
                await session.scalars(
                    select(Role.name)
                    .join(UserRole, UserRole.role_id == Role.id)
                    .where(UserRole.user_id == user.id)
                )
            )
            credential.last_used_at = now
            return Principal(
                issuer=user.issuer,
                subject=f"api:{credential.public_id}",
                actor_type="service",
                scopes=frozenset(credential.scopes_json),
                display_name=user.display_name,
                email=None,
                roles=roles,
            )

    async def list(self, principal: Principal) -> list[ApiCredentialView]:
        self._require_admin(principal)
        async with self.sessions() as session:
            user_id = await session.scalar(
                select(User.id).where(
                    User.issuer == principal.issuer,
                    User.subject == principal.subject,
                )
            )
            if user_id is None:
                return []
            credentials = list(
                await session.scalars(
                    select(ApiCredential)
                    .where(ApiCredential.principal_id == user_id)
                    .order_by(ApiCredential.created_at.desc(), ApiCredential.id.desc())
                )
            )
            return [self._view(credential) for credential in credentials]

    async def revoke(
        self,
        principal: Principal,
        credential_id: uuid.UUID,
        request_id: str,
    ) -> None:
        self._require_admin(principal)
        async with self.sessions() as session, session.begin():
            user_id = await session.scalar(
                select(User.id).where(
                    User.issuer == principal.issuer,
                    User.subject == principal.subject,
                )
            )
            credential = await session.scalar(
                select(ApiCredential)
                .where(
                    ApiCredential.id == credential_id,
                    ApiCredential.principal_id == user_id,
                )
                .with_for_update()
            )
            if credential is None:
                raise ValueError("API credential was not found")
            if credential.revoked_at is None:
                credential.revoked_at = datetime.now(timezone.utc)
                await AssessmentRepository(session).append_audit(
                    principal,
                    "api_credential.revoke",
                    "api_credential",
                    str(credential.id),
                    request_id,
                    {"public_id": credential.public_id},
                )

    @staticmethod
    def _require_admin(principal: Principal) -> None:
        principal.require("assessments:admin")
        if "Admin" not in principal.roles:
            raise PermissionError("Admin role is required for API credentials")

    @staticmethod
    def _view(credential: ApiCredential) -> ApiCredentialView:
        return ApiCredentialView(
            id=credential.id,
            public_id=credential.public_id,
            scopes=set(credential.scopes_json),
            expires_at=credential.expires_at,
            last_used_at=credential.last_used_at,
            revoked_at=credential.revoked_at,
            created_at=credential.created_at,
        )
