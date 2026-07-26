from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.models import AssessmentRequest, AssessmentRun, Validation
from tradingng_platform.persistence.upsert import insert_ignore, session_dialect
from tradingng_platform.validation.contracts import ValidationView


class ValidationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def schedule(
        self,
        run_id: uuid.UUID,
        horizons: tuple[int, ...],
        principal: Principal,
        request_id: str,
    ) -> list[ValidationView]:
        async with self.sessions() as session, session.begin():
            rows = await schedule_validations(
                session,
                run_id,
                horizons,
                principal,
                request_id,
            )
            return [self._view(item) for item in rows]

    async def list_for_run(self, run_id: uuid.UUID) -> list[ValidationView]:
        async with self.sessions() as session:
            rows = list(
                await session.scalars(
                    select(Validation)
                    .where(Validation.run_id == run_id)
                    .order_by(Validation.horizon)
                )
            )
            return [self._view(item) for item in rows]

    async def list(self, status: str | None = None, limit: int = 100) -> list[ValidationView]:
        async with self.sessions() as session:
            statement = select(Validation).order_by(
                Validation.scheduled_for.desc(), Validation.id.desc()
            )
            if status is not None:
                statement = statement.where(Validation.status == status)
            rows = list(await session.scalars(statement.limit(limit)))
            return [self._view(item) for item in rows]

    @staticmethod
    def _view(item: Validation) -> ValidationView:
        return ValidationView.model_validate(item, from_attributes=True)


async def schedule_validations(
    session: AsyncSession,
    run_id: uuid.UUID,
    horizons: tuple[int, ...],
    principal: Principal,
    request_id: str,
) -> list[Validation]:
    row = (
        await session.execute(
            select(AssessmentRun, AssessmentRequest)
            .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
            .where(AssessmentRun.id == run_id)
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise ValueError("assessment run was not found")
    run, request = row
    if run.status != RunStatus.SUCCEEDED.value:
        raise ValueError("only a successful run can be validated")

    for horizon in horizons:
        scheduled_for = datetime.combine(
            request.analysis_date + timedelta(days=horizon),
            time.min,
            timezone.utc,
        )
        await session.execute(
            insert_ignore(
                session_dialect(session),
                Validation,
                {
                    "run_id": run_id,
                    "horizon": horizon,
                    "status": "scheduled",
                    "scheduled_for": scheduled_for,
                    "trigger_results_json": {},
                    "attempts": 0,
                },
                [Validation.run_id, Validation.horizon],
            )
        )
    await AssessmentRepository(session).append_audit(
        principal,
        "validation.schedule",
        "assessment_run",
        str(run_id),
        request_id,
        {"horizons": list(horizons)},
    )
    return list(
        await session.scalars(
            select(Validation)
            .where(
                Validation.run_id == run_id,
                Validation.horizon.in_(horizons),
            )
            .order_by(Validation.horizon)
        )
    )
