from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


@dataclass(frozen=True)
class LocalIdentity:
    id: UUID
    issuer: str
    subject: str
    display_name: str
    email: str | None
    status: str
    role: Literal["Admin", "User"] | None
    synced_at: datetime


@dataclass(frozen=True)
class KeycloakUser:
    subject: str
    username: str
    display_name: str
    email: str | None
    enabled: bool
    role: Literal["Admin", "User"]


@dataclass(frozen=True)
class KeycloakSession:
    session_id: str
    started_at: datetime
    last_access_at: datetime


@dataclass(frozen=True)
class KeycloakPage:
    items: tuple[KeycloakUser, ...]
    total: int


@dataclass(frozen=True)
class IdentitySync:
    identity: LocalIdentity
    changed_fields: tuple[str, ...]
    old_role: str | None
    old_status: str | None


class CreateUserCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    role: Literal["Admin", "User"]

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return str(value).strip().lower()

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value):
        return str(value).strip()


class UpdateUserCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None
    role: Literal["Admin", "User"] | None = None
    enabled: bool | None = None

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value):
        return None if value is None else str(value).strip()

    @model_validator(mode="after")
    def require_change(self):
        if all(value is None for value in (self.display_name, self.email, self.role, self.enabled)):
            raise ValueError("at least one user field is required")
        return self


class UserView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    subject: str
    username: str
    display_name: str
    email: str | None
    role: Literal["Admin", "User"]
    enabled: bool
    synced_at: datetime


class SessionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    active_count: int
    last_access_at: datetime | None


class UserActionFlags(BaseModel):
    model_config = ConfigDict(frozen=True)

    edit_profile: bool
    change_role: bool
    change_enabled: bool
    reset_password: bool
    logout: bool


class UserDetailView(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: UserView
    sessions: SessionSummary
    allowed_actions: UserActionFlags
    action_reasons: dict[str, str]


class UserPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[UserView, ...]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class TemporaryCredential:
    user: UserView
    temporary_password: SecretStr


class TemporaryPasswordResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: UserView
    temporary_password: str
