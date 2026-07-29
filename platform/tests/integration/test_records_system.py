from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.contracts import AssessmentItem, SubmitAssessments
from tradingng_platform.assessments.service import AssessmentService
from tradingng_platform.auth.principal import Principal
from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.integrity.contracts import IntegrityStatus
from tradingng_platform.integrity.policy import PointInTimeRecorder
from tradingng_platform.integrity.repository import IntegrityRepository
from tradingng_platform.models import (
    Artifact,
    AssessmentRequest,
    AssessmentRun,
    AuditEvent,
    Decision,
    EvidenceItem,
    Instrument,
    RunConfigSnapshot,
    Validation,
    Worker,
)
from tradingng_platform.records.contracts import InstrumentOverviewFilters
from tradingng_platform.records.service import ArtifactIntegrityError, RecordService
from tradingng_platform.scheduler.policy import SystemSnapshot
from tradingng_platform.system.contracts import ModelRoutingPolicyCommand, SchedulerPolicyCommand
from tradingng_platform.system.service import SystemService
from tradingng_platform.vendors.alpha_vantage_client import AlphaBrokerStatus


class _Gateway:
    async def status(self):
        return GatewaySnapshot(
            status="ok",
            active_completions=0,
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            snapshot_id="a" * 64,
            latency_ms=1,
        )


class _Probe:
    def sample(self):
        return SystemSnapshot(20, 32, 100, 50, False)


class _AlphaBroker:
    async def status(self):
        return AlphaBrokerStatus(
            status="cooldown",
            configured_requests_per_minute=75,
            effective_requests_per_minute=60,
            max_in_flight=3,
            in_flight=2,
            queued=5,
            oldest_queued_seconds=12.5,
            blocked_until="2026-07-28T12:00:00+00:00",
            requests=100,
            upstream_requests=80,
            cache_hits=15,
            coalesced_requests=5,
            rate_limits=2,
            transient_errors=1,
        )


def _admin():
    return Principal(
        "issuer",
        "records-admin",
        "user",
        frozenset(
            {
                "assessments:submit",
                "assessments:read",
                "assessments:review",
                "artifacts:read",
                "system:read",
                "assessments:admin",
                "validations:read",
            }
        ),
        display_name="Records Admin",
        roles=frozenset({"Admin"}),
    )


async def test_records_are_hash_verified_and_collaboration_is_audited(
    session_factory,
    instrument_classifier,
    tmp_path,
):
    principal = _admin()
    run = (
        await AssessmentService(session_factory, instrument_classifier).submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 25))],
                idempotency_key="records-20260725",
            ),
            "request-submit",
        )
    )[0]
    store = LocalArtifactStore(tmp_path / "artifacts")
    source = tmp_path / "report.md"
    source.write_text("verified report", encoding="utf-8")
    stored = store.put(run.id, "report_1_complete", "text/markdown", source)
    async with session_factory() as session, session.begin():
        snapshot = RunConfigSnapshot(
            content_json={
                "gateway": {
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "xhigh",
                    "routes": {
                        "fast": {
                            "model": "gpt-5.6-terra",
                            "reasoning_effort": "high",
                        },
                        "slow": {
                            "model": "gpt-5.6-sol",
                            "reasoning_effort": "xhigh",
                        },
                    },
                },
            },
            sha256="c" * 64,
            gateway_snapshot_id="snapshot-records",
        )
        session.add(snapshot)
        await session.flush()
        persisted_run = await session.get(AssessmentRun, run.id)
        persisted_run.config_snapshot_id = snapshot.id
        instrument = await session.scalar(
            select(Instrument).where(Instrument.canonical_ticker == "NVDA")
        )
        instrument.name = "NVIDIA CORP"
        instrument.exchange = "NASDAQ"
        artifact = Artifact(
            run_id=run.id,
            kind=stored.kind,
            media_type=stored.media_type,
            size=stored.size,
            sha256=stored.sha256,
            storage_key=stored.storage_key,
            redacted=True,
        )
        session.add(artifact)
        session.add(
            Decision(
                run_id=run.id,
                rating="Hold",
                executive_summary="Wait",
                investment_thesis="Balanced",
                price_target=Decimal("100"),
                time_horizon="5 days",
                structured_json={},
            )
        )
        session.add(
            EvidenceItem(
                run_id=run.id,
                source="yfinance",
                tool_name="get_stock_data",
                arguments_json={"ticker": "NVDA"},
                collected_at=datetime.now(timezone.utc),
                effective_at=None,
                freshness="fresh",
                artifact_id=artifact.id,
                content_hash="b" * 64,
            )
        )
        await session.flush()
        artifact_id = artifact.id

    service = RecordService(session_factory, store)
    assert (await service.decision(principal, run.id)).rating == "Hold"
    assert (await service.evidence(principal, run.id))[0].source == "yfinance"
    public_artifact = (await service.list_artifacts(principal, run.id))[0]
    assert not hasattr(public_artifact, "storage_key")
    opened = await service.open_artifact(principal, artifact_id)
    assert opened.path.read_text(encoding="utf-8") == "verified report"
    review = await service.add_review(principal, run.id, "approved", "Reviewed", "request-review")
    comment = await service.add_comment(principal, run.id, "Watch valuation", "request-comment")
    assert review.reviewer == "Records Admin"
    assert comment.author == "Records Admin"
    assert len(await service.list_reviews(principal, run.id)) == 1
    assert len(await service.list_comments(principal, run.id)) == 1
    summary = await service.instrument_summary(principal, "nvda")
    assert summary.assessment_count == 1
    assert summary.name == "NVIDIA CORP"
    assert summary.exchange == "NASDAQ"
    history = await service.instrument_history(principal, "NVDA")
    assert len(history) == 1
    assert history[0].executive_summary == "Wait"
    assert history[0].price_target == Decimal("100")
    assert history[0].gateway_model == "gpt-5.6-sol"
    assert history[0].gateway_reasoning_effort == "xhigh"
    assert history[0].gateway_fast_model == "gpt-5.6-terra"
    assert history[0].gateway_fast_reasoning_effort == "high"
    assert history[0].gateway_slow_model == "gpt-5.6-sol"
    assert history[0].gateway_slow_reasoning_effort == "xhigh"
    assert history[0].config_snapshot_sha256 == "c" * 64
    assert history[0].validation_outcome is None

    opened.path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError):
        await service.open_artifact(principal, artifact_id)


async def test_system_capacity_and_policy_update_are_bounded_and_audited(
    session_factory,
    instrument_classifier,
):
    principal = _admin()
    await AssessmentService(session_factory, instrument_classifier).submit(
        principal,
        SubmitAssessments(
            items=[AssessmentItem(ticker="TSLA", analysis_date=date(2026, 7, 25))],
            idempotency_key="system-20260725",
        ),
        "request-submit",
    )
    service = SystemService(session_factory, _Gateway(), _Probe())

    capacity = await service.capacity(principal)
    assert capacity.queued == 1
    assert capacity.max_running_total == 2
    assert capacity.model_routing.fast.model == "gpt-5.6-terra"
    assert capacity.model_routing.fast.reasoning_effort == "high"
    assert capacity.model_routing.slow.model == "gpt-5.6-sol"
    assert capacity.model_routing.slow.reasoning_effort == "high"
    updated = await service.update_scheduler_policy(
        principal,
        SchedulerPolicyCommand(
            max_running_total=32,
            hard_max_running_total=32,
            gateway_active_limit=32,
            cpu_limit_percent=85,
            minimum_memory_gib=8,
            minimum_disk_gib=10,
            minimum_disk_percent=10,
        ),
        "request-policy",
    )
    assert updated.max_running_total == 32
    assert updated.hard_max_running_total == 32
    assert updated.version == 2
    async with session_factory() as session:
        actions = tuple(await session.scalars(select(AuditEvent.action)))
    assert "scheduler.policy.update" in actions


async def test_model_routing_defaults_are_persistent_and_updates_are_audited(
    session_factory,
):
    principal = _admin()
    service = SystemService(session_factory, _Gateway(), _Probe())

    current = await service.get_model_routing(principal)

    assert current.fast.model == "gpt-5.6-terra"
    assert current.fast.reasoning_effort == "high"
    assert current.slow.model == "gpt-5.6-sol"
    assert current.slow.reasoning_effort == "high"
    assert current.available_models == ["gpt-5.6-terra", "gpt-5.6-sol"]
    assert "high" in current.available_reasoning_efforts
    assert current.version == 1

    updated = await service.update_model_routing(
        principal,
        ModelRoutingPolicyCommand(
            fast={"model": "gpt-5.6-sol", "reasoning_effort": "medium"},
            slow={"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
        ),
        "request-model-routing",
    )

    assert updated.fast.reasoning_effort == "medium"
    assert updated.slow.reasoning_effort == "xhigh"
    assert updated.version == 2
    assert (await service.get_model_routing(principal)).version == 2
    async with session_factory() as session:
        actions = tuple(await session.scalars(select(AuditEvent.action)))
    assert "model_routing.policy.update" in actions


async def test_system_status_only_lists_workers_with_recent_heartbeats(session_factory):
    now = datetime.now(timezone.utc)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                Worker(
                    instance_name="host:1",
                    status="idle",
                    capabilities_json={"runner": "tradingagents"},
                    pid=1001,
                    heartbeat_at=now,
                ),
                Worker(
                    instance_name="host:old-pid",
                    status="idle",
                    capabilities_json={"runner": "tradingagents"},
                    pid=999,
                    heartbeat_at=now - timedelta(seconds=31),
                ),
            ]
        )

    status = await SystemService(session_factory, _Gateway(), _Probe()).status(_admin())

    assert [worker["instance_name"] for worker in status["workers"]] == ["host:1"]


async def test_system_status_counts_official_name_resolution_health(session_factory):
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                Instrument(
                    canonical_ticker="PG",
                    asset_type="stock",
                    name="PROCTER & GAMBLE Co",
                    metadata_json={
                        "name_resolution": {
                            "status": "resolved",
                            "provider": "sec_edgar",
                        }
                    },
                ),
                Instrument(
                    canonical_ticker="PENDING",
                    asset_type="stock",
                    metadata_json={},
                ),
                Instrument(
                    canonical_ticker="MISSING",
                    asset_type="stock",
                    metadata_json={
                        "name_resolution": {
                            "status": "unresolved",
                            "provider": "sec_edgar",
                            "reason": "ticker_not_listed",
                        }
                    },
                ),
                Instrument(
                    canonical_ticker="CONFLICT",
                    asset_type="stock",
                    metadata_json={
                        "name_resolution": {
                            "status": "unresolved",
                            "provider": "sec_edgar",
                            "reason": "exchange_mismatch",
                        }
                    },
                ),
            ]
        )

    status = await SystemService(session_factory, _Gateway(), _Probe()).status(_admin())

    assert status["instrument_names"] == {
        "total": 4,
        "official": 1,
        "pending": 1,
        "unresolved": 1,
        "conflicts": 1,
    }


async def test_system_status_exposes_safe_alpha_global_quota_snapshot(session_factory):
    status = await SystemService(
        session_factory,
        _Gateway(),
        _Probe(),
        alpha_broker_client=_AlphaBroker(),
    ).status(_admin())

    assert status["alpha_vantage"] == {
        "status": "cooldown",
        "configured_requests_per_minute": 75,
        "effective_requests_per_minute": 60.0,
        "max_in_flight": 3,
        "in_flight": 2,
        "queued": 5,
        "oldest_queued_seconds": 12.5,
        "blocked_until": "2026-07-28T12:00:00+00:00",
        "requests": 100,
        "upstream_requests": 80,
        "cache_hits": 15,
        "coalesced_requests": 5,
        "rate_limits": 2,
        "transient_errors": 1,
    }
    assert "key" not in str(status["alpha_vantage"]).lower()


async def test_instrument_overview_preserves_decision_and_binds_validations(
    session_factory,
    instrument_classifier,
    tmp_path,
):
    principal = _admin()
    assessments = AssessmentService(session_factory, instrument_classifier)
    nvda_success = (
        await assessments.submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 6, 1))],
                idempotency_key="overview-nvda-success",
            ),
            "request-overview-success",
        )
    )[0]
    nvda_failed = (
        await assessments.submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="NVDA", analysis_date=date(2026, 7, 20))],
                idempotency_key="overview-nvda-failed",
            ),
            "request-overview-failed",
        )
    )[0]
    tsla_success = (
        await assessments.submit(
            principal,
            SubmitAssessments(
                items=[AssessmentItem(ticker="TSLA", analysis_date=date(2026, 7, 18))],
                idempotency_key="overview-tsla-success",
            ),
            "request-overview-tsla",
        )
    )[0]

    base_time = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    async with session_factory() as session, session.begin():
        stored_nvda_success = await session.get(AssessmentRun, nvda_success.id)
        stored_nvda_failed = await session.get(AssessmentRun, nvda_failed.id)
        stored_tsla_success = await session.get(AssessmentRun, tsla_success.id)
        stored_nvda_success.status = "succeeded"
        stored_nvda_success.created_at = base_time - timedelta(days=3)
        stored_nvda_failed.status = "failed"
        stored_nvda_failed.created_at = base_time - timedelta(hours=1)
        stored_tsla_success.status = "succeeded"
        stored_tsla_success.created_at = base_time - timedelta(hours=2)

        retry = AssessmentRun(
            request_id=stored_nvda_failed.request_id,
            attempt=2,
            status="failed",
            retry_of_run_id=stored_nvda_failed.id,
            created_at=base_time,
        )
        session.add(retry)
        session.add_all(
            [
                Decision(
                    run_id=nvda_success.id,
                    rating="Underweight",
                    executive_summary="Valuation risk remains elevated.",
                    investment_thesis="Expect relative underperformance.",
                    price_target=Decimal("110"),
                    time_horizon="20 trading days",
                    structured_json={},
                ),
                Decision(
                    run_id=tsla_success.id,
                    rating="Hold",
                    executive_summary="Balanced setup.",
                    investment_thesis="Wait for confirmation.",
                    price_target=None,
                    time_horizon="20 trading days",
                    structured_json={},
                ),
            ]
        )
        nvda_request = await session.get(AssessmentRequest, stored_nvda_success.request_id)
        nvda_instrument = await session.get(Instrument, nvda_request.instrument_id)
        nvda_instrument.name = "英伟达"
        for horizon, total_return, total_alpha, correct, status in (
            (1, "-0.02", "-0.01", True, "completed"),
            (5, "-0.08", "-0.04", True, "completed"),
            (20, "-0.2065", "-0.1459", True, "completed"),
            (5, None, None, True, "failed"),
        ):
            run_id = nvda_success.id if status == "completed" else tsla_success.id
            session.add(
                Validation(
                    run_id=run_id,
                    horizon=horizon,
                    status=status,
                    scheduled_for=base_time - timedelta(days=1),
                    observed_at=base_time if status == "completed" else None,
                    raw_return=Decimal(total_return) if total_return else None,
                    benchmark_return=None,
                    alpha=Decimal(total_alpha) if total_alpha else None,
                    max_adverse_excursion=None,
                    max_favorable_excursion=None,
                    trigger_results_json={
                        "direction_correct": correct,
                        "price_target_hit": False,
                    },
                    error_code="provider_error" if status == "failed" else None,
                    calculation_version="validation.v2",
                    exit_session=date(2026, 7, 1) if status == "completed" else None,
                    matures_at=base_time - timedelta(days=1),
                    total_return=Decimal(total_return) if total_return else None,
                    total_alpha=Decimal(total_alpha) if total_alpha else None,
                )
            )
        recorder = PointInTimeRecorder(
            date(2026, 6, 1),
            now=datetime(2026, 7, 26, tzinfo=timezone.utc),
        )
        recorder.record(
            "evidence",
            IntegrityStatus.SAFE,
            "sealed_evidence_verified",
        )
        await IntegrityRepository(session).persist_document(
            nvda_success.id,
            recorder.finalize(),
            artifact_id=None,
            audit_mode="retrospective",
        )

    service = RecordService(session_factory, LocalArtifactStore(tmp_path / "artifacts"))
    first_page = await service.instrument_overviews(
        principal,
        InstrumentOverviewFilters(limit=1),
    )
    assert first_page.instrument_count == 2
    assert first_page.items[0].instrument.ticker == "NVDA"
    assert first_page.items[0].instrument.name == "英伟达"
    assert first_page.items[0].latest_run.status == "failed"
    assert first_page.items[0].latest_successful_run.status == "succeeded"
    assert first_page.items[0].latest_decision.rating == "Underweight"
    assert first_page.items[0].preferred_validation.horizon == 20
    twenty_day = next(item for item in first_page.items[0].validation_stats if item.horizon == 20)
    assert twenty_day.completed == 1
    assert twenty_day.accuracy == Decimal("1")
    assert twenty_day.excluded_at_risk == 0
    assert twenty_day.excluded_unknown == 0
    assert first_page.items[0].run_counts.anomalous == 1
    assert first_page.items[0].run_counts.total == 2
    assert first_page.next_cursor is not None

    second_page = await service.instrument_overviews(
        principal,
        InstrumentOverviewFilters(limit=1, cursor=first_page.next_cursor),
    )
    assert [item.instrument.ticker for item in second_page.items] == ["TSLA"]

    failed_page = await service.instrument_overviews(
        principal,
        InstrumentOverviewFilters(statuses=("failed",)),
    )
    assert [item.instrument.ticker for item in failed_page.items] == ["NVDA"]
    assert failed_page.items[0].latest_decision.rating == "Underweight"

    named_page = await service.instrument_overviews(
        principal,
        InstrumentOverviewFilters(query="英伟"),
    )
    assert [item.instrument.ticker for item in named_page.items] == ["NVDA"]

    history = await service.instrument_history(principal, "NVDA")
    assert history[0].request_attempt_count == 2
    assert history[0].is_latest_attempt is True
    successful = next(item for item in history if item.run.id == nvda_success.id)
    assert {validation.horizon for validation in successful.validations} == {1, 5, 20}
    assert successful.validation_outcome == "20D · -20.65% · 方向正确"

    limited_principal = Principal(
        principal.issuer,
        principal.subject,
        principal.actor_type,
        frozenset({"assessments:read"}),
    )
    limited_page = await service.instrument_overviews(
        limited_principal,
        InstrumentOverviewFilters(query="NVDA"),
    )
    assert limited_page.validations_visible is False
    assert limited_page.items[0].preferred_validation is None
