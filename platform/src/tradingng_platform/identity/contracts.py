from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


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
