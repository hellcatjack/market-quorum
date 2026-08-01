from __future__ import annotations

from dataclasses import dataclass

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.service import AssessmentService
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.gateway.client import GatewayClient
from tradingng_platform.instruments.classification import StockLeanInstrumentClassifier
from tradingng_platform.integrity.service import IntegrityService
from tradingng_platform.records.service import RecordService
from tradingng_platform.scheduler.probes import SystemProbe
from tradingng_platform.system.service import SystemService
from tradingng_platform.validation.repository import ValidationRepository
from tradingng_platform.validation.service import ValidationService
from tradingng_platform.vendors.stocklean import StockLeanClient, UnavailableStockLeanClient


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
        stocklean_token = settings.stocklean_internal_token.get_secret_value()
        stocklean_client = (
            StockLeanClient(
                str(settings.stocklean_url),
                token=stocklean_token,
                timeout=settings.stocklean_timeout_seconds,
            )
            if stocklean_token
            else UnavailableStockLeanClient()
        )
        classifier = instrument_classifier or StockLeanInstrumentClassifier(stocklean_client)
        artifact_store = LocalArtifactStore(settings.artifact_dir)
        return cls(
            assessments=AssessmentService(
                database.sessions,
                classifier,
                artifact_store,
                settings.job_dir,
                stocklean_client=(None if instrument_classifier is not None else stocklean_client),
            ),
            records=RecordService(
                database.sessions,
                artifact_store,
                settings.job_dir,
            ),
            system=SystemService(
                database.sessions,
                GatewayClient(str(settings.gateway_url)),
                SystemProbe(settings.data_dir),
            ),
            validation=ValidationService(ValidationRepository(database.sessions)),
            integrity=IntegrityService(
                database.sessions,
                None if instrument_classifier is not None else stocklean_client,
            ),
        )
