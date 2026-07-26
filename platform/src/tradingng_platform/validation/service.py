from __future__ import annotations

import uuid

from tradingng_platform.auth.principal import Principal
from tradingng_platform.validation.repository import ValidationRepository

_HORIZONS = frozenset({1, 5, 20})
SYSTEM_VALIDATION_PRINCIPAL = Principal(
    issuer="tradingng-system",
    subject="validation-scheduler",
    actor_type="service",
    scopes=frozenset({"validations:read", "validations:write"}),
    display_name="Validation Scheduler",
)


class ValidationService:
    def __init__(self, repository: ValidationRepository):
        self.repository = repository

    async def schedule(
        self,
        principal: Principal,
        run_id: uuid.UUID,
        horizons: list[int] | None = None,
        request_id: str | None = None,
    ):
        principal.require("validations:write")
        resolved = self._horizons(horizons)
        return await self.repository.schedule(
            run_id,
            resolved,
            principal,
            request_id or f"validation-{uuid.uuid4().hex}",
        )

    async def schedule_system(self, run_id: uuid.UUID, horizons: list[int] | None = None):
        return await self.schedule(SYSTEM_VALIDATION_PRINCIPAL, run_id, horizons)

    async def list_for_run(self, principal: Principal, run_id: uuid.UUID):
        principal.require("validations:read")
        return await self.repository.list_for_run(run_id)

    async def list(self, principal: Principal, status: str | None = None, limit: int = 100):
        principal.require("validations:read")
        if not 1 <= limit <= 200:
            raise ValueError("validation limit must be between 1 and 200")
        return await self.repository.list(status=status, limit=limit)

    @staticmethod
    def _horizons(horizons: list[int] | None) -> tuple[int, ...]:
        resolved = tuple(sorted(set(horizons or _HORIZONS)))
        if not resolved or any(item not in _HORIZONS for item in resolved):
            raise ValueError("horizons must contain only 1, 5, or 20")
        return resolved
