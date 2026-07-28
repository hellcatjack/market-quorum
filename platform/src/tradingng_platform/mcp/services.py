from __future__ import annotations

from dataclasses import dataclass

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.service import AssessmentService
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.gateway.client import GatewayClient
from tradingng_platform.instruments.classification import YahooInstrumentClassifier
from tradingng_platform.integrity.service import IntegrityService
from tradingng_platform.records.service import RecordService
from tradingng_platform.scheduler.probes import SystemProbe
from tradingng_platform.system.service import SystemService
from tradingng_platform.validation.repository import ValidationRepository
from tradingng_platform.validation.service import ValidationService
from tradingng_platform.vendors.alpha_vantage_client import AsyncAlphaVantageBrokerClient


@dataclass(frozen=True)
class McpServices:
    assessments: AssessmentService
    records: RecordService
    system: SystemService
    validation: ValidationService | None = None
    integrity: IntegrityService | None = None

    @classmethod
    def from_database(
        cls,
        database: Database,
        settings: Settings,
        instrument_classifier=None,
    ) -> McpServices:
        classifier = instrument_classifier or YahooInstrumentClassifier()
        return cls(
            assessments=AssessmentService(database.sessions, classifier),
            records=RecordService(
                database.sessions,
                LocalArtifactStore(settings.artifact_dir),
                settings.job_dir,
            ),
            system=SystemService(
                database.sessions,
                GatewayClient(str(settings.gateway_url)),
                SystemProbe(settings.data_dir),
                alpha_broker_client=AsyncAlphaVantageBrokerClient(
                    str(settings.alpha_vantage_broker_url),
                    consumer="system",
                    timeout=5,
                ),
                alpha_broker_queue_limit=settings.alpha_vantage_broker_admission_queue_limit,
            ),
            validation=ValidationService(ValidationRepository(database.sessions)),
            integrity=IntegrityService(database.sessions),
        )
