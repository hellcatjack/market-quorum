import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradingng_platform.assessments.contracts import (
    AssessmentItem,
    MemorySourceView,
    RunDetailView,
    RunEventView,
    RunListFilters,
    RunMemoryView,
    RunPage,
    RunStepView,
    RunView,
    SubmitAssessments,
    decode_run_cursor,
    encode_run_cursor,
)
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.instruments.classification import InstrumentClassification
from tradingng_platform.models import (
    Artifact,
    AssessmentBatch,
    AssessmentRequest,
    AssessmentRun,
    AuditEvent,
    Comment,
    Decision,
    DecisionPriceBasis,
    EvidenceItem,
    Instrument,
    Review,
    Role,
    RunConfigSnapshot,
    RunEvent,
    RunIntegrityAssessment,
    RunStep,
    User,
    UserRole,
    Validation,
    WebhookDelivery,
    WorkerLease,
)
from tradingng_platform.persistence.locks import acquire_transaction_lock
from tradingng_platform.persistence.upsert import insert_ignore, session_dialect

_RECOGNIZED_ROLES = frozenset({"Admin", "Analyst", "Viewer"})


class InstrumentAssetTypeConflict(Exception):
    def __init__(self, ticker: str, existing: str, resolved: str):
        self.ticker = ticker
        self.existing = existing
        self.resolved = resolved
        super().__init__(
            f"stored asset type {existing} conflicts with resolved type {resolved} for {ticker}"
        )


@dataclass(frozen=True)
class RunContext:
    run: AssessmentRun
    request: AssessmentRequest
    instrument: Instrument
    batch: AssessmentBatch
    owner: User


class AssessmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_batch(
        self,
        submitted_by: uuid.UUID,
        idempotency_key: str,
    ) -> list[RunView] | None:
        batch_id = await self.session.scalar(
            select(AssessmentBatch.id).where(
                AssessmentBatch.submitted_by == submitted_by,
                AssessmentBatch.idempotency_key == idempotency_key,
            )
        )
        if batch_id is None:
            return None
        return await self.list_batch_runs(batch_id)

    async def get_batch_submission_hash(
        self,
        submitted_by: uuid.UUID,
        idempotency_key: str,
    ) -> str | None:
        defaults = await self.session.scalar(
            select(AssessmentBatch.defaults_json).where(
                AssessmentBatch.submitted_by == submitted_by,
                AssessmentBatch.idempotency_key == idempotency_key,
            )
        )
        return defaults.get("_submission_sha256") if defaults else None

    async def upsert_user(self, principal: Principal) -> User:
        await self.session.execute(
            insert_ignore(
                session_dialect(self.session),
                User,
                {
                    "issuer": principal.issuer,
                    "subject": principal.subject,
                    "display_name": principal.display_name or principal.subject,
                    "email": principal.email,
                    "status": "active",
                },
                [User.issuer, User.subject],
            )
        )
        user = await self.session.scalar(
            select(User)
            .where(
                User.issuer == principal.issuer,
                User.subject == principal.subject,
            )
            .with_for_update()
        )
        if user is None:
            raise RuntimeError("user upsert did not return a user")
        user.display_name = principal.display_name or principal.subject
        user.email = principal.email
        user.status = "active"
        await self._sync_roles(user.id, principal.roles)
        return user

    async def _sync_roles(self, user_id: uuid.UUID, principal_roles: frozenset[str]) -> None:
        for role_name in sorted(_RECOGNIZED_ROLES):
            await self.session.execute(
                insert_ignore(
                    session_dialect(self.session),
                    Role,
                    {"name": role_name},
                    [Role.name],
                )
            )

        roles = list(
            await self.session.scalars(select(Role).where(Role.name.in_(_RECOGNIZED_ROLES)))
        )
        role_ids = [role.id for role in roles]
        if role_ids:
            await self.session.execute(
                delete(UserRole).where(
                    UserRole.user_id == user_id,
                    UserRole.role_id.in_(role_ids),
                )
            )
        selected_names = principal_roles & _RECOGNIZED_ROLES
        self.session.add_all(
            UserRole(user_id=user_id, role_id=role.id)
            for role in roles
            if role.name in selected_names
        )

    async def get_or_create_instrument(
        self,
        ticker: str,
        asset_type: str,
        classification: InstrumentClassification,
    ) -> Instrument:
        await acquire_transaction_lock(self.session, f"ticker:{ticker}")
        instrument = await self.session.scalar(
            select(Instrument).where(Instrument.canonical_ticker == ticker).with_for_update()
        )
        if instrument is None:
            instrument = Instrument(
                canonical_ticker=ticker,
                asset_type=asset_type,
                exchange=classification.exchange,
                name=None,
                metadata_json={},
            )
            self.session.add(instrument)
            await self.session.flush()
        elif instrument.asset_type != asset_type:
            raise InstrumentAssetTypeConflict(
                ticker,
                instrument.asset_type,
                asset_type,
            )

        metadata = dict(instrument.metadata_json or {})
        metadata["asset_classification"] = {
            "asset_type": classification.asset_type.value,
            "exchange": classification.exchange,
            "name": classification.name,
            "quote_type": classification.quote_type,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "source": classification.source,
            "source_symbol": classification.source_symbol,
        }
        instrument.metadata_json = metadata
        if instrument.exchange is None:
            instrument.exchange = classification.exchange
        return instrument

    async def create_batch(
        self,
        user: User,
        command: SubmitAssessments,
    ) -> AssessmentBatch:
        batch = AssessmentBatch(
            submitted_by=user.id,
            idempotency_key=command.idempotency_key,
            defaults_json={
                "analysts": list(command.analysts),
                "depth": command.depth.value,
                "memory_mode": command.memory_mode.value,
                "language": command.language,
                "_submission_sha256": _submission_sha256(command),
            },
        )
        self.session.add(batch)
        await self.session.flush()
        return batch

    async def create_request_and_run(
        self,
        batch: AssessmentBatch,
        instrument: Instrument,
        item: AssessmentItem,
        request_config: dict,
    ) -> AssessmentRun:
        request = AssessmentRequest(
            batch_id=batch.id,
            instrument_id=instrument.id,
            analysis_date=item.analysis_date,
            requested_config_json=dict(request_config),
        )
        self.session.add(request)
        await self.session.flush()
        run = AssessmentRun(
            request_id=request.id,
            attempt=1,
            status=RunStatus.QUEUED.value,
            config_snapshot_id=None,
            retry_of_run_id=None,
            version=1,
        )
        self.session.add(run)
        await self.session.flush()
        await self.append_event(run.id, "assessment.queued", {})
        return run

    async def append_event(
        self,
        run_id: uuid.UUID,
        event_type: str,
        payload: dict,
    ) -> RunEvent:
        sequence = await self.session.scalar(
            select(func.coalesce(func.max(RunEvent.sequence), 0) + 1).where(
                RunEvent.run_id == run_id
            )
        )
        event = RunEvent(
            run_id=run_id,
            sequence=int(sequence or 1),
            event_type=event_type,
            payload_json=payload,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def append_audit(
        self,
        principal: Principal,
        action: str,
        object_type: str,
        object_id: str,
        request_id: str,
        metadata: dict,
    ) -> AuditEvent:
        event = AuditEvent(
            actor_type=principal.actor_type,
            actor_id=principal.subject,
            action=action,
            object_type=object_type,
            object_id=object_id,
            request_id=request_id,
            metadata_json=metadata,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_batch_runs(self, batch_id: uuid.UUID) -> list[RunView]:
        rows = (
            await self.session.execute(
                select(AssessmentRun, AssessmentRequest, Instrument)
                .join(
                    AssessmentRequest,
                    AssessmentRun.request_id == AssessmentRequest.id,
                )
                .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                .where(AssessmentRequest.batch_id == batch_id)
                .order_by(AssessmentRun.created_at, AssessmentRun.id)
            )
        ).all()
        return [self._run_view(run, request, instrument) for run, request, instrument in rows]

    async def get_run(self, run_id: uuid.UUID) -> RunView | None:
        row = (
            await self.session.execute(
                select(AssessmentRun, AssessmentRequest, Instrument)
                .join(
                    AssessmentRequest,
                    AssessmentRun.request_id == AssessmentRequest.id,
                )
                .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                .where(AssessmentRun.id == run_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return self._run_view(*row)

    async def find_dependent_run_ids(self, run_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
        return tuple(
            await self.session.scalars(
                select(AssessmentRun.id)
                .where(
                    or_(
                        AssessmentRun.retry_of_run_id == run_id,
                        AssessmentRun.clean_reassessment_of_run_id == run_id,
                    )
                )
                .order_by(AssessmentRun.created_at, AssessmentRun.id)
                .with_for_update()
            )
        )

    async def has_active_work(self, run_id: uuid.UUID) -> bool:
        lease_ids = tuple(
            await self.session.scalars(
                select(WorkerLease.id).where(WorkerLease.run_id == run_id).with_for_update()
            )
        )
        validation_statuses = tuple(
            await self.session.scalars(
                select(Validation.status).where(Validation.run_id == run_id).with_for_update()
            )
        )
        basis_statuses = tuple(
            await self.session.scalars(
                select(DecisionPriceBasis.status)
                .where(DecisionPriceBasis.run_id == run_id)
                .with_for_update()
            )
        )
        return bool(lease_ids or "running" in validation_statuses or "running" in basis_statuses)

    async def delete_assessment_graph(self, context: RunContext) -> dict[str, int]:
        run_id = context.run.id
        request_id = context.request.id
        batch_id = context.request.batch_id
        snapshot_id = context.run.config_snapshot_id
        event_ids = select(RunEvent.id).where(RunEvent.run_id == run_id)

        counts: dict[str, int] = {}

        async def remove(label: str, model, *criteria) -> None:
            result = await self.session.execute(delete(model).where(*criteria))
            counts[label] = max(int(result.rowcount or 0), 0)

        await remove("webhook_deliveries", WebhookDelivery, WebhookDelivery.event_id.in_(event_ids))
        await remove(
            "integrity_assessments",
            RunIntegrityAssessment,
            RunIntegrityAssessment.run_id == run_id,
        )
        await remove("validations", Validation, Validation.run_id == run_id)
        await remove(
            "decision_price_bases",
            DecisionPriceBasis,
            DecisionPriceBasis.run_id == run_id,
        )
        await remove("evidence_items", EvidenceItem, EvidenceItem.run_id == run_id)
        await remove("reviews", Review, Review.run_id == run_id)
        await remove("comments", Comment, Comment.run_id == run_id)
        await remove("decisions", Decision, Decision.run_id == run_id)
        await remove("worker_leases", WorkerLease, WorkerLease.run_id == run_id)
        await remove("run_steps", RunStep, RunStep.run_id == run_id)
        await remove("artifacts", Artifact, Artifact.run_id == run_id)
        await remove("events", RunEvent, RunEvent.run_id == run_id)
        await remove("runs", AssessmentRun, AssessmentRun.id == run_id)

        request_is_referenced = await self.session.scalar(
            select(AssessmentRun.id).where(AssessmentRun.request_id == request_id).limit(1)
        )
        if request_is_referenced is None:
            await remove("requests", AssessmentRequest, AssessmentRequest.id == request_id)
            batch_is_referenced = await self.session.scalar(
                select(AssessmentRequest.id).where(AssessmentRequest.batch_id == batch_id).limit(1)
            )
            if batch_is_referenced is None:
                await remove("batches", AssessmentBatch, AssessmentBatch.id == batch_id)

        if snapshot_id is not None:
            snapshot_is_referenced = await self.session.scalar(
                select(AssessmentRun.id)
                .where(AssessmentRun.config_snapshot_id == snapshot_id)
                .limit(1)
            )
            if snapshot_is_referenced is None:
                await remove(
                    "config_snapshots",
                    RunConfigSnapshot,
                    RunConfigSnapshot.id == snapshot_id,
                )
        return counts

    async def get_run_detail(self, run_id: uuid.UUID) -> RunDetailView | None:
        row = (
            await self.session.execute(
                select(AssessmentRun, AssessmentRequest, Instrument, RunConfigSnapshot)
                .join(
                    AssessmentRequest,
                    AssessmentRun.request_id == AssessmentRequest.id,
                )
                .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                .outerjoin(
                    RunConfigSnapshot,
                    AssessmentRun.config_snapshot_id == RunConfigSnapshot.id,
                )
                .where(AssessmentRun.id == run_id)
            )
        ).one_or_none()
        if row is None:
            return None
        run, request, instrument, snapshot = row
        base = self._run_view(run, request, instrument)
        content = dict(snapshot.content_json) if snapshot is not None else {}
        gateway = dict(content.get("gateway") or {})
        routes = dict(gateway.get("routes") or {})
        legacy_route = {
            "model": gateway.get("model"),
            "reasoning_effort": gateway.get("reasoning_effort"),
        }
        fast_route = dict(routes.get("fast") or legacy_route)
        slow_route = dict(routes.get("slow") or legacy_route)
        source = dict(content.get("source") or {})
        memory_content = dict(content.get("memory") or {})
        memory_sources = tuple(
            MemorySourceView.model_validate(
                {
                    key: entry[key]
                    for key in (
                        "source_run_id",
                        "validation_id",
                        "analysis_date",
                        "exit_session",
                        "horizon",
                        "rating",
                        "raw_return",
                        "alpha",
                        "direction_correct",
                        "price_target_hit",
                        "content_sha256",
                    )
                    if key in entry
                }
            )
            for entry in memory_content.get("entries", ())
        )
        return RunDetailView(
            **base.model_dump(),
            config_snapshot_sha256=snapshot.sha256 if snapshot is not None else None,
            gateway_snapshot_id=(snapshot.gateway_snapshot_id if snapshot is not None else None),
            gateway_model=gateway.get("model"),
            gateway_reasoning_effort=gateway.get("reasoning_effort"),
            gateway_fast_model=fast_route.get("model"),
            gateway_fast_reasoning_effort=fast_route.get("reasoning_effort"),
            gateway_slow_model=slow_route.get("model"),
            gateway_slow_reasoning_effort=slow_route.get("reasoning_effort"),
            model_routing_snapshot_id=gateway.get("routing_snapshot_id"),
            root_commit=source.get("root_commit"),
            tradingagents_commit=source.get("tradingagents_commit"),
            prompt_schema_version=content.get("prompt_schema_version"),
            request_config=dict(content.get("request") or {}),
            resolved_config=dict(content.get("resolved") or {}),
            data_vendors=dict(content.get("data_vendors") or {}),
            tool_vendors=dict(content.get("tool_vendors") or {}),
            memory=RunMemoryView(
                mode=memory_content.get("mode", "independent"),
                snapshot_sha256=memory_content.get("snapshot_sha256"),
                sources=memory_sources,
            ),
        )

    async def get_run_context(
        self,
        run_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> RunContext | None:
        statement = (
            select(AssessmentRun, AssessmentRequest, Instrument, AssessmentBatch, User)
            .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
            .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
            .join(AssessmentBatch, AssessmentRequest.batch_id == AssessmentBatch.id)
            .join(User, AssessmentBatch.submitted_by == User.id)
            .where(AssessmentRun.id == run_id)
        )
        if for_update:
            statement = statement.with_for_update(of=AssessmentRun)
        row = (await self.session.execute(statement)).one_or_none()
        return RunContext(*row) if row is not None else None

    async def list_runs(self, filters: RunListFilters) -> RunPage:
        statement = (
            select(AssessmentRun, AssessmentRequest, Instrument)
            .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
            .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
            .join(AssessmentBatch, AssessmentRequest.batch_id == AssessmentBatch.id)
        )
        if filters.ticker:
            statement = statement.where(Instrument.canonical_ticker == filters.ticker)
        if filters.status:
            statement = statement.where(
                AssessmentRun.status.in_(status.value for status in filters.status)
            )
        if filters.submitted_by:
            statement = statement.where(AssessmentBatch.submitted_by == filters.submitted_by)
        if filters.created_from:
            statement = statement.where(AssessmentRun.created_at >= filters.created_from)
        if filters.created_to:
            statement = statement.where(AssessmentRun.created_at <= filters.created_to)
        if filters.cursor:
            cursor_created_at, cursor_id = decode_run_cursor(filters.cursor)
            statement = statement.where(
                or_(
                    AssessmentRun.created_at < cursor_created_at,
                    and_(
                        AssessmentRun.created_at == cursor_created_at,
                        AssessmentRun.id < cursor_id,
                    ),
                )
            )
        rows = (
            await self.session.execute(
                statement.order_by(AssessmentRun.created_at.desc(), AssessmentRun.id.desc()).limit(
                    filters.limit + 1
                )
            )
        ).all()
        has_more = len(rows) > filters.limit
        visible = rows[: filters.limit]
        items = [self._run_view(*row) for row in visible]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_run_cursor(last.created_at, last.id)
        return RunPage(items=items, next_cursor=next_cursor)

    async def list_steps(self, run_id: uuid.UUID) -> list[RunStepView]:
        steps = list(
            await self.session.scalars(
                select(RunStep)
                .where(RunStep.run_id == run_id)
                .order_by(RunStep.attempt, RunStep.created_at, RunStep.id)
            )
        )
        return [
            RunStepView(
                name=step.name,
                status=step.status,
                attempt=step.attempt,
                started_at=step.started_at,
                finished_at=step.finished_at,
                error_code=step.error_code,
                summary=step.summary,
            )
            for step in steps
        ]

    async def list_events(
        self,
        run_id: uuid.UUID,
        *,
        after: int = 0,
        limit: int = 200,
    ) -> list[RunEventView]:
        events = list(
            await self.session.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.sequence > after)
                .order_by(RunEvent.sequence)
                .limit(limit)
            )
        )
        return [
            RunEventView(
                sequence=event.sequence,
                event_type=event.event_type,
                payload=dict(event.payload_json),
                created_at=event.created_at,
            )
            for event in events
        ]

    async def create_retry(self, context: RunContext) -> AssessmentRun:
        request = AssessmentRequest(
            batch_id=context.request.batch_id,
            instrument_id=context.request.instrument_id,
            analysis_date=context.request.analysis_date,
            requested_config_json=dict(context.request.requested_config_json),
        )
        self.session.add(request)
        await self.session.flush()
        run = AssessmentRun(
            request_id=request.id,
            attempt=context.run.attempt + 1,
            status=RunStatus.QUEUED.value,
            config_snapshot_id=None,
            retry_of_run_id=context.run.id,
            version=1,
        )
        self.session.add(run)
        await self.session.flush()
        await self.append_event(
            run.id,
            "assessment.queued",
            {"retry_of_run_id": str(context.run.id)},
        )
        return run

    async def find_clean_reassessment(
        self,
        source_run_id: uuid.UUID,
    ) -> RunView | None:
        row = (
            await self.session.execute(
                select(AssessmentRun, AssessmentRequest, Instrument)
                .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
                .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                .where(AssessmentRun.clean_reassessment_of_run_id == source_run_id)
                .order_by(AssessmentRun.created_at, AssessmentRun.id)
                .limit(1)
            )
        ).one_or_none()
        return self._run_view(*row) if row is not None else None

    async def create_clean_reassessment(
        self,
        context: RunContext,
        policy_version: str,
    ) -> RunView:
        defaults = dict(context.batch.defaults_json or {})
        defaults.pop("_submission_sha256", None)
        defaults.update(
            {
                "memory_mode": "independent",
                "clean_reassessment_of_run_id": str(context.run.id),
                "integrity_policy_version": policy_version,
            }
        )
        batch = AssessmentBatch(
            submitted_by=context.owner.id,
            idempotency_key=f"clean-{context.run.id}-{policy_version}",
            defaults_json=defaults,
        )
        self.session.add(batch)
        await self.session.flush()

        request_config = dict(context.request.requested_config_json or {})
        request_config["memory_mode"] = "independent"
        request = AssessmentRequest(
            batch_id=batch.id,
            instrument_id=context.request.instrument_id,
            analysis_date=context.request.analysis_date,
            requested_config_json=request_config,
        )
        self.session.add(request)
        await self.session.flush()
        run = AssessmentRun(
            request_id=request.id,
            attempt=1,
            status=RunStatus.QUEUED.value,
            config_snapshot_id=None,
            retry_of_run_id=None,
            clean_reassessment_of_run_id=context.run.id,
            version=1,
        )
        self.session.add(run)
        await self.session.flush()
        await self.append_event(
            run.id,
            "assessment.queued",
            {
                "clean_reassessment_of_run_id": str(context.run.id),
                "integrity_policy_version": policy_version,
            },
        )
        return self._run_view(run, request, context.instrument)

    async def comparison_metadata(
        self,
        run_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, dict]:
        rows = (
            await self.session.execute(
                select(
                    AssessmentRun.id,
                    AssessmentRun.status,
                    Decision.rating,
                    RunConfigSnapshot.sha256,
                )
                .outerjoin(Decision, Decision.run_id == AssessmentRun.id)
                .outerjoin(
                    RunConfigSnapshot,
                    RunConfigSnapshot.id == AssessmentRun.config_snapshot_id,
                )
                .where(AssessmentRun.id.in_(run_ids))
            )
        ).all()
        return {
            run_id: {
                "status": status,
                "rating": rating,
                "config_snapshot": config_sha,
            }
            for run_id, status, rating, config_sha in rows
        }

    @staticmethod
    def _run_view(
        run: AssessmentRun,
        request: AssessmentRequest,
        instrument: Instrument,
    ) -> RunView:
        return RunView(
            id=run.id,
            request_id=request.id,
            ticker=instrument.canonical_ticker,
            instrument_name=instrument.name,
            exchange=instrument.exchange,
            asset_type=instrument.asset_type,
            analysis_date=request.analysis_date,
            status=RunStatus(run.status),
            attempt=run.attempt,
            created_at=run.created_at,
        )


def _submission_sha256(command: SubmitAssessments) -> str:
    payload = command.model_dump(mode="json", exclude={"idempotency_key"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
