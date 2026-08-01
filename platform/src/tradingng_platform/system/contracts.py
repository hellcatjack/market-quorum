from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from tradingng_platform.model_routing import (
    ModelRoute,
    ModelRoutingPolicy,
)
from tradingng_platform.scheduler.policy import (
    ABSOLUTE_MAX_RUNNING_TOTAL,
    AdmissionPolicy,
)


class CapacityView(BaseModel):
    admitted_or_running: int
    max_running_total: int
    hard_max_running_total: int
    queued: int
    oldest_queued_seconds: int | None
    gateway_active_completions: int
    gateway_model: str
    gateway_reasoning_effort: str
    model_routing: ModelRoutingPolicy
    open_circuits: list[str]
    admission_allowed: bool
    admission_reasons: list[str]
    waiting_for_data: int = 0
    oldest_waiting_seconds: int | None = None


class SchedulerPolicyCommand(BaseModel):
    max_running_total: int = Field(ge=1, le=ABSOLUTE_MAX_RUNNING_TOTAL)
    hard_max_running_total: int = Field(
        default=ABSOLUTE_MAX_RUNNING_TOTAL,
        ge=1,
        le=ABSOLUTE_MAX_RUNNING_TOTAL,
    )
    gateway_active_limit: int = Field(ge=1)
    cpu_limit_percent: float = Field(gt=0, le=100)
    minimum_memory_gib: float = Field(ge=0)
    minimum_disk_gib: float = Field(ge=0)
    minimum_disk_percent: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_policy(self):
        self.to_policy()
        return self

    def to_policy(self) -> AdmissionPolicy:
        return AdmissionPolicy(
            max_running_total=self.max_running_total,
            hard_max_running_total=self.hard_max_running_total,
            gateway_active_limit=self.gateway_active_limit,
            cpu_limit_percent=self.cpu_limit_percent,
            minimum_memory_gib=self.minimum_memory_gib,
            minimum_disk_gib=self.minimum_disk_gib,
            minimum_disk_percent=self.minimum_disk_percent,
        )


class SchedulerPolicyView(SchedulerPolicyCommand):
    version: int
    updated_at: datetime


class ModelRoutingPolicyCommand(BaseModel):
    fast: ModelRoute
    slow: ModelRoute

    def to_policy(self) -> ModelRoutingPolicy:
        return ModelRoutingPolicy(fast=self.fast, slow=self.slow)


class ModelRoutingPolicyView(ModelRoutingPolicyCommand):
    available_models: list[str]
    available_reasoning_efforts: list[str]
    routing_snapshot_id: str
    version: int
    updated_at: datetime
