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
