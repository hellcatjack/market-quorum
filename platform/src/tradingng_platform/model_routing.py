import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradingng_platform.auth.principal import Principal
from tradingng_platform.models import AuditEvent, ModelRoutingPolicyRecord, User
from tradingng_platform.persistence.upsert import insert_ignore, session_dialect

AVAILABLE_CODEX_MODELS = ("gpt-5.6-terra", "gpt-5.6-sol")
AVAILABLE_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")

CodexModel = Literal["gpt-5.6-terra", "gpt-5.6-sol"]
ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max", "ultra"]


class ModelRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: CodexModel
    reasoning_effort: ReasoningEffort


class ModelRoutingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fast: ModelRoute = ModelRoute(model="gpt-5.6-terra", reasoning_effort="high")
    slow: ModelRoute = ModelRoute(model="gpt-5.6-sol", reasoning_effort="high")

    @property
    def snapshot_id(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(canonical).hexdigest()


class ModelRoutingPolicyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self) -> ModelRoutingPolicy:
        default = ModelRoutingPolicy()
        await self.session.execute(
            insert_ignore(
                session_dialect(self.session),
                ModelRoutingPolicyRecord,
                {
                    "key": "default",
                    "content_json": default.model_dump(mode="json"),
                    "version": 1,
                    "updated_by": None,
                    "updated_at": datetime.now(timezone.utc),
                },
                [ModelRoutingPolicyRecord.key],
            )
        )
        record = await self.session.get(ModelRoutingPolicyRecord, "default")
        if record is None:
            raise RuntimeError("model routing policy seed is not visible")
        return ModelRoutingPolicy.model_validate(record.content_json)

    async def update(
        self,
        principal: Principal,
        policy: ModelRoutingPolicy,
        request_id: str,
    ) -> ModelRoutingPolicy:
        principal.require("assessments:admin")
        if "Admin" not in principal.roles:
            raise PermissionError("Admin role is required to update model routing policy")
        record = await self.session.scalar(
            select(ModelRoutingPolicyRecord)
            .where(ModelRoutingPolicyRecord.key == "default")
            .with_for_update()
        )
        if record is None:
            await self.get()
            record = await self.session.scalar(
                select(ModelRoutingPolicyRecord)
                .where(ModelRoutingPolicyRecord.key == "default")
                .with_for_update()
            )
        if record is None:
            raise RuntimeError("model routing policy is unavailable")

        user_id = await self.session.scalar(
            select(User.id).where(
                User.issuer == principal.issuer,
                User.subject == principal.subject,
            )
        )
        if user_id is None:
            raise RuntimeError("model routing policy actor is not synchronized")
        old_value = dict(record.content_json)
        new_value = policy.model_dump(mode="json")
        record.content_json = new_value
        record.version += 1
        record.updated_by = user_id
        record.updated_at = datetime.now(timezone.utc)
        self.session.add(
            AuditEvent(
                actor_type=principal.actor_type,
                actor_id=principal.subject,
                action="model_routing.policy.update",
                object_type="model_routing_policy",
                object_id="default",
                request_id=request_id,
                metadata_json={"old": old_value, "new": new_value},
            )
        )
        await self.session.flush()
        return policy
