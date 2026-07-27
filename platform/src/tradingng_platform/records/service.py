import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.contracts import decode_run_cursor, encode_run_cursor
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
    Validation,
)
from tradingng_platform.records.contracts import (
    ArtifactView,
    CommentView,
    DecisionView,
    EvidenceView,
    InstrumentHistoryItem,
    InstrumentIdentityView,
    InstrumentOverviewFilters,
    InstrumentOverviewItem,
    InstrumentOverviewPage,
    InstrumentRunCounts,
    InstrumentSummaryView,
    InstrumentValidationStats,
    InstrumentValidationView,
    OpenedArtifact,
    ReviewView,
)

_ACTIVE_STATUSES = frozenset(
    {
        "admitted",
        "starting",
        "running_analysts",
        "research_debate",
        "trader_plan",
        "risk_debate",
        "portfolio_decision",
        "finalizing",
        "cancel_requested",
        "cancelling",
    }
)
_ANOMALOUS_STATUSES = frozenset({"failed", "needs_attention"})


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


def _instrument_validation_view(validation: Validation) -> InstrumentValidationView:
    triggers = dict(validation.trigger_results_json or {})
    direction_correct = triggers.get("direction_correct")
    price_target_hit = triggers.get("price_target_hit")
    return InstrumentValidationView(
        id=validation.id,
        run_id=validation.run_id,
        horizon=validation.horizon,
        status=validation.status,
        scheduled_for=validation.scheduled_for,
        matures_at=validation.matures_at,
        exit_session=validation.exit_session,
        total_return=(
            validation.total_return
            if validation.total_return is not None
            else validation.raw_return
        ),
        total_alpha=(
            validation.total_alpha if validation.total_alpha is not None else validation.alpha
        ),
        direction_correct=(direction_correct if isinstance(direction_correct, bool) else None),
        price_target_hit=(price_target_hit if isinstance(price_target_hit, bool) else None),
        error_code=validation.error_code,
    )


def _decision_view(decision: Decision) -> DecisionView:
    return DecisionView(
        run_id=decision.run_id,
        rating=decision.rating,
        executive_summary=decision.executive_summary,
        investment_thesis=decision.investment_thesis,
        price_target=decision.price_target,
        time_horizon=decision.time_horizon,
        structured=dict(decision.structured_json or {}),
    )


def _run_counts(rows: list[tuple]) -> InstrumentRunCounts:
    final_by_request: dict[uuid.UUID, tuple] = {}
    for row in rows:
        request_id = row[1].id
        current = final_by_request.get(request_id)
        if current is None or (row[0].attempt, row[0].created_at, row[0].id) > (
            current[0].attempt,
            current[0].created_at,
            current[0].id,
        ):
            final_by_request[request_id] = row
    statuses = [row[0].status for row in final_by_request.values()]
    return InstrumentRunCounts(
        total=len(statuses),
        queued=statuses.count("queued"),
        active=sum(status in _ACTIVE_STATUSES for status in statuses),
        succeeded=statuses.count("succeeded"),
        anomalous=sum(status in _ANOMALOUS_STATUSES for status in statuses),
    )


def _build_overview_items(
    rows: list[tuple],
    validations_by_run: dict[uuid.UUID, list[InstrumentValidationView]],
) -> list[InstrumentOverviewItem]:
    grouped: dict[uuid.UUID, list[tuple]] = defaultdict(list)
    for row in rows:
        grouped[row[2].id].append(row)

    items: list[InstrumentOverviewItem] = []
    for instrument_rows in grouped.values():
        instrument_rows.sort(
            key=lambda row: (row[0].created_at, row[0].id),
            reverse=True,
        )
        latest_run, latest_request, instrument, _, _ = instrument_rows[0]
        successful_rows = [
            row for row in instrument_rows if row[0].status == "succeeded" and row[3] is not None
        ]
        latest_successful = successful_rows[0] if successful_rows else None
        latest_successful_validations = (
            validations_by_run.get(latest_successful[0].id, [])
            if latest_successful is not None
            else []
        )
        all_validations = [
            item for row in instrument_rows for item in validations_by_run.get(row[0].id, [])
        ]
        items.append(
            InstrumentOverviewItem(
                instrument=InstrumentIdentityView(
                    id=instrument.id,
                    ticker=instrument.canonical_ticker,
                    name=instrument.name,
                    exchange=instrument.exchange,
                    asset_type=instrument.asset_type,
                ),
                latest_run=AssessmentRepository._run_view(
                    latest_run,
                    latest_request,
                    instrument,
                ),
                latest_successful_run=(
                    AssessmentRepository._run_view(
                        latest_successful[0],
                        latest_successful[1],
                        instrument,
                    )
                    if latest_successful is not None
                    else None
                ),
                latest_decision=(
                    _decision_view(latest_successful[3]) if latest_successful is not None else None
                ),
                previous_rating=(
                    successful_rows[1][3].rating if len(successful_rows) > 1 else None
                ),
                preferred_validation=_preferred_validation(latest_successful_validations),
                validation_stats=_validation_stats(all_validations),
                run_counts=_run_counts(instrument_rows),
            )
        )
    items.sort(key=lambda item: (item.latest_run.created_at, item.latest_run.id), reverse=True)
    return items


def _validation_outcome(validation: InstrumentValidationView | None) -> str | None:
    if validation is None:
        return None
    prefix = f"{validation.horizon}D"
    if validation.status == "completed":
        performance = (
            f"{validation.total_return * 100:.2f}%"
            if validation.total_return is not None
            else "收益待补"
        )
        direction = {
            True: "方向正确",
            False: "方向错误",
            None: "方向未判定",
        }[validation.direction_correct]
        return f"{prefix} · {performance} · {direction}"
    if validation.status in {"failed", "unavailable"}:
        return f"{prefix} · 验证异常"
    return f"{prefix} · 待验证"


def _gateway_route_value(
    snapshot: RunConfigSnapshot | None,
    route: str,
    field: str,
) -> str | None:
    if snapshot is None:
        return None
    gateway = snapshot.content_json.get("gateway") or {}
    routes = gateway.get("routes") or {}
    value = (routes.get(route) or {}).get(field)
    return value if isinstance(value, str) else None


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
            latest_run, _ = rows[0]
            latest_rating = next(
                (
                    rating
                    for run, rating in rows
                    if run.status == "succeeded" and rating is not None
                ),
                None,
            )
            return InstrumentSummaryView(
                ticker=ticker,
                asset_types=asset_types,
                assessment_count=len(rows),
                latest_run_id=latest_run.id,
                latest_rating=latest_rating,
                latest_created_at=latest_run.created_at,
            )

    async def instrument_overviews(
        self,
        principal: Principal,
        filters: InstrumentOverviewFilters,
    ) -> InstrumentOverviewPage:
        principal.require("assessments:read")
        validations_visible = "validations:read" in principal.scopes
        async with self.sessions() as session:
            statement = (
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
                .order_by(AssessmentRun.created_at.desc(), AssessmentRun.id.desc())
            )
            if filters.asset_type is not None:
                statement = statement.where(Instrument.asset_type == filters.asset_type.value)
            rows = list((await session.execute(statement)).all())

            validations_by_run: dict[uuid.UUID, list[InstrumentValidationView]] = defaultdict(list)
            if validations_visible and rows:
                run_ids = [row[0].id for row in rows]
                validations = list(
                    await session.scalars(
                        select(Validation)
                        .where(Validation.run_id.in_(run_ids))
                        .order_by(Validation.run_id, Validation.horizon)
                    )
                )
                for validation in validations:
                    validations_by_run[validation.run_id].append(
                        _instrument_validation_view(validation)
                    )

            items = _build_overview_items(rows, validations_by_run)
            query = filters.query.strip().casefold() if filters.query else None
            if query:
                items = [
                    item
                    for item in items
                    if query in item.instrument.ticker.casefold()
                    or query in (item.instrument.name or "").casefold()
                ]
            if filters.statuses:
                statuses = {status.value for status in filters.statuses}
                items = [item for item in items if item.latest_run.status.value in statuses]
            if filters.anomalous_only:
                items = [item for item in items if item.run_counts.anomalous > 0]
            if filters.created_from is not None:
                items = [
                    item for item in items if item.latest_run.created_at >= filters.created_from
                ]
            if filters.created_to is not None:
                items = [item for item in items if item.latest_run.created_at <= filters.created_to]

            instrument_count = len(items)
            totals = InstrumentRunCounts(
                total=sum(item.run_counts.total for item in items),
                queued=sum(item.run_counts.queued for item in items),
                active=sum(item.run_counts.active for item in items),
                succeeded=sum(item.run_counts.succeeded for item in items),
                anomalous=sum(item.run_counts.anomalous for item in items),
            )
            if filters.cursor:
                cursor_created_at, cursor_id = decode_run_cursor(filters.cursor)
                items = [
                    item
                    for item in items
                    if (item.latest_run.created_at, item.latest_run.id)
                    < (cursor_created_at, cursor_id)
                ]
            has_next = len(items) > filters.limit
            page_items = items[: filters.limit]
            next_cursor = None
            if has_next and page_items:
                last = page_items[-1].latest_run
                next_cursor = encode_run_cursor(last.created_at, last.id)
            return InstrumentOverviewPage(
                items=page_items,
                next_cursor=next_cursor,
                instrument_count=instrument_count,
                run_counts=totals,
                validations_visible=validations_visible,
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
            validations_by_run: dict[uuid.UUID, list[InstrumentValidationView]] = defaultdict(list)
            if "validations:read" in principal.scopes:
                validations = list(
                    await session.scalars(
                        select(Validation)
                        .where(Validation.run_id.in_([row[0].id for row in rows]))
                        .order_by(Validation.run_id, Validation.horizon)
                    )
                )
                for validation in validations:
                    validations_by_run[validation.run_id].append(
                        _instrument_validation_view(validation)
                    )
            attempts_by_request: dict[uuid.UUID, int] = defaultdict(int)
            for run, request, *_ in rows:
                attempts_by_request[request.id] = max(
                    attempts_by_request[request.id],
                    run.attempt,
                )
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
                    gateway_fast_model=_gateway_route_value(snapshot, "fast", "model"),
                    gateway_fast_reasoning_effort=_gateway_route_value(
                        snapshot,
                        "fast",
                        "reasoning_effort",
                    ),
                    gateway_slow_model=_gateway_route_value(snapshot, "slow", "model"),
                    gateway_slow_reasoning_effort=_gateway_route_value(
                        snapshot,
                        "slow",
                        "reasoning_effort",
                    ),
                    config_snapshot_sha256=snapshot.sha256 if snapshot is not None else None,
                    validation_outcome=_validation_outcome(
                        _preferred_validation(validations_by_run.get(run.id, []))
                    ),
                    validations=validations_by_run.get(run.id, []),
                    memory_mode=(
                        (snapshot.content_json.get("memory") or {}).get("mode", "independent")
                        if snapshot is not None
                        else request.requested_config_json.get("memory_mode", "independent")
                    ),
                    memory_source_count=(
                        len((snapshot.content_json.get("memory") or {}).get("entries", ()))
                        if snapshot is not None
                        else 0
                    ),
                    is_latest_attempt=run.attempt == attempts_by_request[request.id],
                    request_attempt_count=attempts_by_request[request.id],
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
