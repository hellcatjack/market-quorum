import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.instruments import canonicalize_ticker
from tradingng_platform.models import (
    Artifact,
    AssessmentRequest,
    AssessmentRun,
    Comment,
    Decision,
    EvidenceItem,
    Instrument,
    Review,
    RunConfigSnapshot,
    User,
)
from tradingng_platform.records.contracts import (
    ArtifactView,
    CommentView,
    DecisionView,
    EvidenceView,
    InstrumentHistoryItem,
    InstrumentSummaryView,
    InstrumentValidationStats,
    InstrumentValidationView,
    OpenedArtifact,
    ReviewView,
)


def _preferred_validation(
    validations: list[InstrumentValidationView],
) -> InstrumentValidationView | None:
    completed = {item.horizon: item for item in validations if item.status == "completed"}
    for horizon in (20, 5, 1):
        if horizon in completed:
            return completed[horizon]
    return max(validations, key=lambda item: item.horizon, default=None)


def _validation_stats(
    validations: list[InstrumentValidationView],
) -> list[InstrumentValidationStats]:
    completed_by_horizon: dict[int, list[InstrumentValidationView]] = defaultdict(list)
    for item in validations:
        if item.status == "completed":
            completed_by_horizon[item.horizon].append(item)

    result = []
    for horizon in (1, 5, 20):
        completed = completed_by_horizon[horizon]
        observed = [item for item in completed if item.direction_correct is not None]
        correct = sum(item.direction_correct is True for item in observed)
        result.append(
            InstrumentValidationStats(
                horizon=horizon,
                completed=len(completed),
                direction_observed=len(observed),
                direction_correct=correct,
                accuracy=(Decimal(correct) / Decimal(len(observed)) if observed else None),
            )
        )
    return result


class RecordNotFound(Exception):
    pass


class ArtifactIntegrityError(Exception):
    pass


class RecordService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        artifact_store: LocalArtifactStore,
    ):
        self.sessions = sessions
        self.artifact_store = artifact_store

    async def decision(self, principal: Principal, run_id: uuid.UUID) -> DecisionView:
        principal.require("assessments:read")
        async with self.sessions() as session:
            decision = await session.scalar(select(Decision).where(Decision.run_id == run_id))
            if decision is None:
                await self._ensure_run(session, run_id)
                raise RecordNotFound("assessment decision is not available")
            return DecisionView(
                run_id=run_id,
                rating=decision.rating,
                executive_summary=decision.executive_summary,
                investment_thesis=decision.investment_thesis,
                price_target=decision.price_target,
                time_horizon=decision.time_horizon,
                structured=dict(decision.structured_json),
            )

    async def evidence(self, principal: Principal, run_id: uuid.UUID) -> list[EvidenceView]:
        principal.require("assessments:read")
        async with self.sessions() as session:
            await self._ensure_run(session, run_id)
            evidence = list(
                await session.scalars(
                    select(EvidenceItem)
                    .where(EvidenceItem.run_id == run_id)
                    .order_by(EvidenceItem.collected_at, EvidenceItem.id)
                )
            )
            return [
                EvidenceView(
                    id=item.id,
                    source=item.source,
                    tool_name=item.tool_name,
                    arguments=dict(item.arguments_json),
                    collected_at=item.collected_at,
                    effective_at=item.effective_at,
                    freshness=item.freshness,
                    content_hash=item.content_hash,
                )
                for item in evidence
            ]

    async def list_artifacts(
        self,
        principal: Principal,
        run_id: uuid.UUID,
    ) -> list[ArtifactView]:
        principal.require("artifacts:read")
        async with self.sessions() as session:
            await self._ensure_run(session, run_id)
            artifacts = list(
                await session.scalars(
                    select(Artifact)
                    .where(Artifact.run_id == run_id)
                    .order_by(Artifact.created_at, Artifact.id)
                )
            )
            return [self._artifact_view(artifact) for artifact in artifacts]

    async def open_artifact(
        self,
        principal: Principal,
        artifact_id: uuid.UUID,
    ) -> OpenedArtifact:
        principal.require("artifacts:read")
        async with self.sessions() as session:
            artifact = await session.get(Artifact, artifact_id)
            if artifact is None:
                raise RecordNotFound("artifact was not found")
            try:
                path = self.artifact_store.resolve(artifact.storage_key)
            except ValueError as error:
                raise ArtifactIntegrityError("artifact storage key is invalid") from error
            if not self.artifact_store.verify(artifact.storage_key, artifact.sha256):
                raise ArtifactIntegrityError("artifact content hash does not match")
            suffix = _media_suffix(artifact.media_type)
            return OpenedArtifact(
                id=artifact.id,
                path=path,
                media_type=artifact.media_type,
                filename=f"{artifact.kind}{suffix}",
                sha256=artifact.sha256,
            )

    async def read_report(
        self,
        principal: Principal,
        artifact_id: uuid.UUID,
    ) -> str:
        opened = await self.open_artifact(principal, artifact_id)
        if opened.media_type not in {"text/markdown", "text/plain"}:
            raise ValueError("artifact is not a text report")
        return opened.path.read_text(encoding="utf-8")

    async def add_review(
        self,
        principal: Principal,
        run_id: uuid.UUID,
        verdict: str,
        comment: str,
        request_id: str,
    ) -> ReviewView:
        principal.require("assessments:review")
        async with self.sessions() as session, session.begin():
            await self._ensure_run(session, run_id)
            repository = AssessmentRepository(session)
            user = await repository.upsert_user(principal)
            review = Review(
                run_id=run_id,
                reviewer_id=user.id,
                verdict=verdict,
                comment=comment,
            )
            session.add(review)
            await session.flush()
            await repository.append_audit(
                principal,
                "assessment.review",
                "assessment_run",
                str(run_id),
                request_id,
                {"review_id": str(review.id), "verdict": verdict},
            )
            return self._review_view(review, user)

    async def list_reviews(
        self,
        principal: Principal,
        run_id: uuid.UUID,
    ) -> list[ReviewView]:
        principal.require("assessments:read")
        async with self.sessions() as session:
            await self._ensure_run(session, run_id)
            rows = (
                await session.execute(
                    select(Review, User)
                    .join(User, Review.reviewer_id == User.id)
                    .where(Review.run_id == run_id)
                    .order_by(Review.created_at, Review.id)
                )
            ).all()
            return [self._review_view(review, user) for review, user in rows]

    async def add_comment(
        self,
        principal: Principal,
        run_id: uuid.UUID,
        body: str,
        request_id: str,
    ) -> CommentView:
        principal.require("assessments:read")
        async with self.sessions() as session, session.begin():
            await self._ensure_run(session, run_id)
            repository = AssessmentRepository(session)
            user = await repository.upsert_user(principal)
            comment = Comment(run_id=run_id, author_id=user.id, body=body)
            session.add(comment)
            await session.flush()
            await repository.append_audit(
                principal,
                "assessment.comment",
                "assessment_run",
                str(run_id),
                request_id,
                {"comment_id": str(comment.id)},
            )
            return self._comment_view(comment, user)

    async def list_comments(
        self,
        principal: Principal,
        run_id: uuid.UUID,
    ) -> list[CommentView]:
        principal.require("assessments:read")
        async with self.sessions() as session:
            await self._ensure_run(session, run_id)
            rows = (
                await session.execute(
                    select(Comment, User)
                    .join(User, Comment.author_id == User.id)
                    .where(Comment.run_id == run_id)
                    .order_by(Comment.created_at, Comment.id)
                )
            ).all()
            return [self._comment_view(comment, user) for comment, user in rows]

    async def instrument_summary(
        self,
        principal: Principal,
        ticker: str,
    ) -> InstrumentSummaryView:
        principal.require("assessments:read")
        ticker = canonicalize_ticker(ticker)
        async with self.sessions() as session:
            asset_types = list(
                await session.scalars(
                    select(Instrument.asset_type)
                    .where(Instrument.canonical_ticker == ticker)
                    .distinct()
                    .order_by(Instrument.asset_type)
                )
            )
            if not asset_types:
                raise RecordNotFound("instrument was not found")
            statement = (
                select(AssessmentRun, Decision.rating)
                .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
                .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                .outerjoin(Decision, Decision.run_id == AssessmentRun.id)
                .where(Instrument.canonical_ticker == ticker)
                .order_by(AssessmentRun.created_at.desc(), AssessmentRun.id.desc())
            )
            rows = (await session.execute(statement)).all()
            latest_run, latest_rating = rows[0]
            return InstrumentSummaryView(
                ticker=ticker,
                asset_types=asset_types,
                assessment_count=len(rows),
                latest_run_id=latest_run.id,
                latest_rating=latest_rating,
                latest_created_at=latest_run.created_at,
            )

    async def instrument_history(
        self,
        principal: Principal,
        ticker: str,
        limit: int = 50,
    ) -> list[InstrumentHistoryItem]:
        principal.require("assessments:read")
        ticker = canonicalize_ticker(ticker)
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        AssessmentRun,
                        AssessmentRequest,
                        Instrument,
                        Decision,
                        RunConfigSnapshot,
                    )
                    .join(AssessmentRequest, AssessmentRun.request_id == AssessmentRequest.id)
                    .join(Instrument, AssessmentRequest.instrument_id == Instrument.id)
                    .outerjoin(Decision, Decision.run_id == AssessmentRun.id)
                    .outerjoin(
                        RunConfigSnapshot,
                        RunConfigSnapshot.id == AssessmentRun.config_snapshot_id,
                    )
                    .where(Instrument.canonical_ticker == ticker)
                    .order_by(AssessmentRun.created_at.desc(), AssessmentRun.id.desc())
                    .limit(limit)
                )
            ).all()
            if not rows:
                raise RecordNotFound("instrument was not found")
            return [
                InstrumentHistoryItem(
                    run=AssessmentRepository._run_view(run, request, instrument),
                    rating=decision.rating if decision is not None else None,
                    executive_summary=(
                        decision.executive_summary if decision is not None else None
                    ),
                    price_target=decision.price_target if decision is not None else None,
                    gateway_model=(snapshot.content_json.get("gateway") or {}).get("model")
                    if snapshot is not None
                    else None,
                    gateway_reasoning_effort=(
                        (snapshot.content_json.get("gateway") or {}).get("reasoning_effort")
                        if snapshot is not None
                        else None
                    ),
                    config_snapshot_sha256=snapshot.sha256 if snapshot is not None else None,
                    validation_outcome=None,
                )
                for run, request, instrument, decision, snapshot in rows
            ]

    @staticmethod
    async def _ensure_run(session: AsyncSession, run_id: uuid.UUID) -> None:
        exists = await session.scalar(
            select(func.count()).select_from(AssessmentRun).where(AssessmentRun.id == run_id)
        )
        if not exists:
            raise RecordNotFound("assessment was not found")

    @staticmethod
    def _artifact_view(artifact: Artifact) -> ArtifactView:
        return ArtifactView(
            id=artifact.id,
            run_id=artifact.run_id,
            kind=artifact.kind,
            media_type=artifact.media_type,
            size=artifact.size,
            sha256=artifact.sha256,
            created_at=artifact.created_at,
        )

    @staticmethod
    def _review_view(review: Review, user: User) -> ReviewView:
        return ReviewView(
            id=review.id,
            run_id=review.run_id,
            reviewer=user.display_name or user.subject,
            verdict=review.verdict,
            comment=review.comment,
            created_at=review.created_at,
        )

    @staticmethod
    def _comment_view(comment: Comment, user: User) -> CommentView:
        return CommentView(
            id=comment.id,
            run_id=comment.run_id,
            author=user.display_name or user.subject,
            body=comment.body,
            created_at=comment.created_at,
        )


def _media_suffix(media_type: str) -> str:
    return {
        "application/json": ".json",
        "application/x-ndjson": ".jsonl",
        "text/markdown": ".md",
        "text/plain": ".txt",
    }.get(media_type, ".bin")
