import uuid
from datetime import datetime

from pydantic import BaseModel, Field, SecretStr, field_validator

SUPPORTED_EVENT_TYPES = frozenset(
    {
        "assessment.admitted",
        "assessment.started",
        "assessment.succeeded",
        "assessment.failed",
        "assessment.cancelled",
    }
)


class CreateWebhook(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2048)
    event_types: set[str] = Field(min_length=1, max_length=len(SUPPORTED_EVENT_TYPES))
    secret: SecretStr = Field(min_length=16, max_length=1024)

    @field_validator("event_types")
    @classmethod
    def supported_events_only(cls, value: set[str]) -> set[str]:
        unsupported = value - SUPPORTED_EVENT_TYPES
        if unsupported:
            raise ValueError(f"unsupported webhook event type: {sorted(unsupported)[0]}")
        return value


class WebhookView(BaseModel):
    id: uuid.UUID
    endpoint: str
    event_types: set[str]
    status: str
    created_at: datetime


class WebhookDeliveryView(BaseModel):
    id: uuid.UUID
    webhook_id: uuid.UUID
    event_id: uuid.UUID
    attempt: int
    status: str
    response_code: int | None
    next_attempt_at: datetime | None
    created_at: datetime
