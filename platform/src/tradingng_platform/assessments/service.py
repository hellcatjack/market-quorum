from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.contracts import (
    AssessmentItem,
    ComparisonView,
    RunDetailView,
    RunEventView,
    RunListFilters,
    RunPage,
    RunStepView,
    RunView,
    SubmitAssessments,
)
from tradingng_platform.assessments.files import delete_run_directory
from tradingng_platform.assessments.repository import (
    AssessmentRepository,
    InstrumentAssetTypeConflict,
    RunContext,
    _submission_sha256,
)
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.instruments import AssetType
from tradingng_platform.domain.runs import TERMINAL_STATUSES, RunStatus, assert_transition
from tradingng_platform.instruments.classification import InstrumentClassification

_BATCH_IDEMPOTENCY_CONSTRAINT = "uq_assessment_batches_submitted_by"
_RETRYABLE_STATUSES = {
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.NEEDS_ATTENTION,
}

logger = logging.getLogger(__name__)


class AssessmentNotFound(Exception):
    def __init__(self, run_id: uuid.UUID):
        self.run_id = run_id
        super().__init__(f"assessment not found: {run_id}")


class AssessmentAccessDenied(Exception):
    pass


class AssessmentRetryNotAllowed(Exception):
    def __init__(self, status: RunStatus):
        self.status = status
        super().__init__(f"assessment in {status.value} cannot be retried")


class AssessmentDeleteNotAllowed(Exception):
    def __init__(
        self,
        reason: str,
        *,
        status: RunStatus | None = None,
        dependent_run_ids: tuple[uuid.UUID, ...] = (),
    ):
        self.reason = reason
        self.status = status
        self.dependent_run_ids = dependent_run_ids
        super().__init__(f"assessment deletion is not allowed: {reason}")


class AssessmentIdempotencyConflict(Exception):
    pass


class AssessmentAssetTypeConflict(Exception):
    def __init__(self, ticker: str, requested: AssetType, resolved: AssetType):
        self.ticker = ticker
        self.requested = requested
        self.resolved = resolved
        super().__init__(
            f"requested asset type {requested.value} conflicts with "
            f"resolved type {resolved.value} for {ticker}"
        )


class AssessmentInstrumentIdentityConflict(Exception):
    def __init__(self, ticker: str, existing: str, resolved: AssetType):
        self.ticker = ticker
        self.existing = existing
        self.resolved = resolved
        super().__init__(
            f"stored asset type {existing} conflicts with "
            f"resolved type {resolved.value} for {ticker}"
        )


class AssessmentAnalystsIncompatible(Exception):
    def __init__(self, ticker: str, asset_type: AssetType):
        self.ticker = ticker
        self.asset_type = asset_type
        super().__init__(f"no compatible analysts remain for {ticker} ({asset_type.value})")


@dataclass(frozen=True)
class ResolvedAssessmentItem:
    item: AssessmentItem
    classification: InstrumentClassification
    analysts: tuple[str, ...]


@dataclass(frozen=True)
class DeletedAssessment:
    run_id: uuid.UUID
    ticker: str
    analysis_date: date
    status: RunStatus


def _compatible_analysts(
    analysts: tuple[str, ...],
    asset_type: AssetType,
) -> tuple[str, ...]:
    if asset_type in {AssetType.FUND, AssetType.CRYPTO}:
        return tuple(analyst for analyst in analysts if analyst != "fundamentals")
    return analysts


class AssessmentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        classifier,
        artifact_store: LocalArtifactStore | None = None,
        job_dir: Path | None = None,
    ):
        self.sessions = sessions
        self.classifier = classifier
        self.artifact_store = artifact_store
        self.job_dir = job_dir

    async def submit(
        self,
        principal: Principal,
        command: SubmitAssessments,
        request_id: str,
    ) -> list[RunView]:
        principal.require("assessments:submit")
        resolved_items = await self._resolve_items(command)
        async with self.sessions() as session, session.begin():
            repository = AssessmentRepository(session)
            user = await repository.upsert_user(principal)
            found = await repository.find_batch(user.id, command.idempotency_key)
            if found is not None:
                await self._assert_idempotent_payload(repository, user.id, command)
                return found

            try:
                async with session.begin_nested():
                    batch = await repository.create_batch(user, command)
                    await session.flush()
            except IntegrityError as error:
                if not _is_batch_idempotency_conflict(error):
                    raise
                found = await repository.find_batch(user.id, command.idempotency_key)
                if found is None:
                    raise RuntimeError("idempotent batch winner is not visible") from error
                await self._assert_idempotent_payload(repository, user.id, command)
                return found

            for resolved_item in resolved_items:
                item = resolved_item.item
                classification = resolved_item.classification
                try:
                    instrument = await repository.get_or_create_instrument(
                        item.ticker,
                        classification.asset_type.value,
                        classification,
                    )
                except InstrumentAssetTypeConflict as error:
                    raise AssessmentInstrumentIdentityConflict(
                        error.ticker,
                        error.existing,
                        AssetType(error.resolved),
                    ) from error
                await repository.create_request_and_run(
                    batch,
                    instrument,
                    item,
                    {"analysts": list(resolved_item.analysts)},
                )
            await repository.append_audit(
                principal,
                "assessment.submit",
                "assessment_batch",
                str(batch.id),
                request_id,
                {"count": len(command.items)},
            )
            return await repository.list_batch_runs(batch.id)

    async def _resolve_items(self, command: SubmitAssessments) -> list[ResolvedAssessmentItem]:
        tickers = tuple(dict.fromkeys(item.ticker for item in command.items))
        classifications = await self.classifier.classify_many(tickers)
        resolved_items = []
        for item in command.items:
            classification = classifications[item.ticker]
            if item.asset_type is not None and item.asset_type != classification.asset_type:
                raise AssessmentAssetTypeConflict(
                    item.ticker,
                    item.asset_type,
                    classification.asset_type,
                )
            analysts = _compatible_analysts(command.analysts, classification.asset_type)
            if not analysts:
                raise AssessmentAnalystsIncompatible(
                    item.ticker,
                    classification.asset_type,
                )
            resolved_items.append(
                ResolvedAssessmentItem(
                    item=item,
                    classification=classification,
                    analysts=analysts,
                )
            )
        return resolved_items

    @staticmethod
    async def _assert_idempotent_payload(
        repository: AssessmentRepository,
        user_id: uuid.UUID,
        command: SubmitAssessments,
    ) -> None:
        stored_hash = await repository.get_batch_submission_hash(
            user_id,
            command.idempotency_key,
        )
        if stored_hash is not None and stored_hash != _submission_sha256(command):
            raise AssessmentIdempotencyConflict(
                "idempotency key was already used for another assessment payload"
            )

    async def get(self, principal: Principal, run_id: uuid.UUID) -> RunDetailView | None:
        principal.require("assessments:read")
        async with self.sessions() as session:
            return await AssessmentRepository(session).get_run_detail(run_id)

    async def list(self, principal: Principal, filters: RunListFilters) -> RunPage:
        principal.require("assessments:read")
        async with self.sessions() as session:
            return await AssessmentRepository(session).list_runs(filters)

    async def steps(self, principal: Principal, run_id: uuid.UUID) -> list[RunStepView]:
        principal.require("assessments:read")
        async with self.sessions() as session:
            repository = AssessmentRepository(session)
            if await repository.get_run(run_id) is None:
                raise AssessmentNotFound(run_id)
            return await repository.list_steps(run_id)

    async def events(
        self,
        principal: Principal,
        run_id: uuid.UUID,
        after: int = 0,
        limit: int = 200,
    ) -> list[RunEventView]:
        principal.require("assessments:read")
        async with self.sessions() as session:
            repository = AssessmentRepository(session)
            if await repository.get_run(run_id) is None:
                raise AssessmentNotFound(run_id)
            return await repository.list_events(run_id, after=after, limit=limit)

    async def cancel(
        self,
        principal: Principal,
        run_id: uuid.UUID,
        request_id: str,
    ) -> RunView:
        principal.require("assessments:cancel")
        async with self.sessions() as session, session.begin():
            repository = AssessmentRepository(session)
            context = await repository.get_run_context(run_id, for_update=True)
            if context is None:
                raise AssessmentNotFound(run_id)
            self._require_owner_or_admin(principal, context)
            current = RunStatus(context.run.status)
            if current == RunStatus.QUEUED:
                assert_transition(current, RunStatus.CANCELLED)
                context.run.status = RunStatus.CANCELLED.value
                context.run.finished_at = datetime.now(timezone.utc)
                context.run.version += 1
                await repository.append_event(run_id, "assessment.cancelled", {})
            elif current not in TERMINAL_STATUSES and current not in {
                RunStatus.CANCEL_REQUESTED,
                RunStatus.CANCELLING,
            }:
                assert_transition(current, RunStatus.CANCEL_REQUESTED)
                context.run.status = RunStatus.CANCEL_REQUESTED.value
                context.run.version += 1
                await repository.append_event(run_id, "assessment.cancel_requested", {})
            await repository.append_audit(
                principal,
                "assessment.cancel",
                "assessment_run",
                str(run_id),
                request_id,
                {"status": context.run.status},
            )
            return repository._run_view(
                context.run,
                context.request,
                context.instrument,
            )

    async def retry(
        self,
        principal: Principal,
        run_id: uuid.UUID,
        request_id: str,
    ) -> RunView:
        principal.require("assessments:submit")
        async with self.sessions() as session, session.begin():
            repository = AssessmentRepository(session)
            context = await repository.get_run_context(run_id, for_update=True)
            if context is None:
                raise AssessmentNotFound(run_id)
            self._require_owner_or_admin(principal, context)
            status = RunStatus(context.run.status)
            if status not in _RETRYABLE_STATUSES:
                raise AssessmentRetryNotAllowed(status)
            retry = await repository.create_retry(context)
            await repository.append_audit(
                principal,
                "assessment.retry",
                "assessment_run",
                str(retry.id),
                request_id,
                {"retry_of_run_id": str(run_id), "attempt": retry.attempt},
            )
            retry_context = await repository.get_run_context(retry.id)
            if retry_context is None:
                raise RuntimeError("new retry is not visible")
            return repository._run_view(
                retry_context.run,
                retry_context.request,
                retry_context.instrument,
            )

    async def delete(
        self,
        principal: Principal,
        run_id: uuid.UUID,
        request_id: str,
    ) -> DeletedAssessment:
        principal.require("assessments:admin")
        if "Admin" not in principal.roles:
            raise AssessmentAccessDenied("assessment deletion requires the Admin role")

        async with self.sessions() as session, session.begin():
            repository = AssessmentRepository(session)
            context = await repository.get_run_context(run_id, for_update=True)
            if context is None:
                raise AssessmentNotFound(run_id)

            run_status = RunStatus(context.run.status)
            if run_status not in TERMINAL_STATUSES:
                raise AssessmentDeleteNotAllowed(
                    "run_not_terminal",
                    status=run_status,
                )
            if await repository.has_active_work(run_id):
                raise AssessmentDeleteNotAllowed(
                    "active_work",
                    status=run_status,
                )
            dependent_run_ids = tuple(await repository.find_dependent_run_ids(run_id))
            if dependent_run_ids:
                raise AssessmentDeleteNotAllowed(
                    "dependent_runs_exist",
                    status=run_status,
                    dependent_run_ids=dependent_run_ids,
                )

            deleted = DeletedAssessment(
                run_id=run_id,
                ticker=context.instrument.canonical_ticker,
                analysis_date=context.request.analysis_date,
                status=run_status,
            )
            request_row_id = context.request.id
            batch_id = context.request.batch_id
            deletion_counts = await repository.delete_assessment_graph(context)
            await repository.append_audit(
                principal,
                "assessment.delete",
                "assessment_run",
                str(run_id),
                request_id,
                {
                    "ticker": deleted.ticker,
                    "analysis_date": deleted.analysis_date.isoformat(),
                    "status": deleted.status.value,
                    "request_id": str(request_row_id),
                    "batch_id": str(batch_id),
                    "deleted": deletion_counts,
                },
            )

        self._cleanup_run_files(run_id)
        return deleted

    def _cleanup_run_files(self, run_id: uuid.UUID) -> None:
        if self.artifact_store is not None:
            try:
                self.artifact_store.delete_run(run_id)
            except (OSError, ValueError):
                logger.warning(
                    "assessment_artifact_cleanup_failed run_id=%s",
                    run_id,
                    exc_info=True,
                )
        if self.job_dir is not None:
            try:
                delete_run_directory(self.job_dir, run_id)
            except (OSError, ValueError):
                logger.warning(
                    "assessment_job_cleanup_failed run_id=%s",
                    run_id,
                    exc_info=True,
                )

    async def compare(
        self,
        principal: Principal,
        run_ids: list[uuid.UUID],
    ) -> ComparisonView:
        principal.require("assessments:read")
        async with self.sessions() as session:
            repository = AssessmentRepository(session)
            runs = []
            for run_id in run_ids:
                run = await repository.get_run(run_id)
                if run is None:
                    raise AssessmentNotFound(run_id)
                runs.append(run)
            metadata = await repository.comparison_metadata(run_ids)
            ratings = {run_id: metadata[run_id]["rating"] for run_id in run_ids}
            changed_sections = {}
            for section in ("status", "rating", "config_snapshot"):
                values = {metadata[run_id][section] for run_id in run_ids}
                if len(values) > 1:
                    changed_sections[section] = list(run_ids)
            return ComparisonView(
                runs=runs,
                ratings=ratings,
                changed_sections=changed_sections,
            )

    @staticmethod
    def _require_owner_or_admin(principal: Principal, context: RunContext) -> None:
        if "Admin" in principal.roles and "assessments:admin" in principal.scopes:
            return
        if context.owner.issuer != principal.issuer or context.owner.subject != principal.subject:
            raise AssessmentAccessDenied("assessment belongs to another principal")


def _is_batch_idempotency_conflict(error: IntegrityError) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == _BATCH_IDEMPOTENCY_CONSTRAINT
