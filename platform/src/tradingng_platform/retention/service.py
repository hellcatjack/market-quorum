from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.auth.principal import Principal
from tradingng_platform.models import Artifact, AuditEvent
from tradingng_platform.persistence.locks import acquire_transaction_lock
from tradingng_platform.retention.policy import is_due

_RETENTION_PRINCIPAL = Principal(
    "tradingng-system",
    "retention-worker",
    "service",
    frozenset(),
)


class RetentionService:
    def __init__(self, sessions, artifact_store: LocalArtifactStore):
        self.sessions = sessions
        self.artifact_store = artifact_store

    async def run(self, *, apply: bool = False, now: datetime | None = None) -> list[uuid.UUID]:
        observed_now = now or datetime.now(timezone.utc)
        async with self.sessions() as session, session.begin():
            if not await acquire_transaction_lock(session, "global:retention"):
                raise RuntimeError("retention coordination lock is unavailable")
            candidates = list(
                await session.scalars(
                    select(Artifact)
                    .where(Artifact.deleted_at.is_(None))
                    .order_by(Artifact.created_at, Artifact.id)
                    .with_for_update()
                )
            )
            due = [
                item
                for item in candidates
                if is_due(
                    item.retention_class,
                    item.created_at,
                    item.metadata_json,
                    observed_now,
                )
            ]
            if not apply:
                return [item.id for item in due]
            for item in due:
                path = self.artifact_store.resolve(item.storage_key)
                path.unlink(missing_ok=True)
                item.deleted_at = observed_now
                session.add(
                    AuditEvent(
                        actor_type=_RETENTION_PRINCIPAL.actor_type,
                        actor_id=_RETENTION_PRINCIPAL.subject,
                        action="artifact.retained_delete",
                        object_type="artifact",
                        object_id=str(item.id),
                        request_id=f"retention-{uuid.uuid4().hex}",
                        metadata_json={"retention_class": item.retention_class},
                    )
                )
            return [item.id for item in due]
