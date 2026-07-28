from __future__ import annotations

import argparse
import asyncio
import json
import uuid

import httpx

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.integrity.audit import RetrospectiveAuditService
from tradingng_platform.integrity.financials import (
    AlphaEarningsAvailabilityResolver,
    CompositeAvailabilityResolver,
    SecFilingClient,
)
from tradingng_platform.vendors.alpha_vantage_client import SyncAlphaVantageBrokerClient


def _bounded_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 500:
        raise argparse.ArgumentTypeError("limit must be between 1 and 500")
    return parsed


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="tradingng-platform-integrity-audit")
    parser.add_argument("--limit", type=_bounded_limit, default=50)
    parser.add_argument("--run-id", type=uuid.UUID)
    return parser.parse_args(argv)


def _alpha_availability_resolver(
    settings: Settings,
    client: httpx.Client,
) -> AlphaEarningsAvailabilityResolver:
    broker = SyncAlphaVantageBrokerClient(
        str(settings.alpha_vantage_broker_url),
        consumer="research",
        timeout=settings.alpha_vantage_broker_request_timeout_seconds,
        client=client,
    )
    return AlphaEarningsAvailabilityResolver(
        lambda ticker: broker.query(
            "EARNINGS",
            {"symbol": ticker},
            run_id="integrity-audit",
        )
    )


async def run(arguments) -> int:
    settings = Settings()
    database = Database(settings)
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=settings.alpha_vantage_broker_request_timeout_seconds,
        ) as client:
            sec = SecFilingClient(
                client=client,
                user_agent=settings.sec_user_agent,
                cache_dir=settings.sec_cache_dir,
                timeout_seconds=settings.sec_request_timeout_seconds,
            )
            alpha = _alpha_availability_resolver(settings, client)
            service = RetrospectiveAuditService(
                database.sessions,
                LocalArtifactStore(settings.artifact_dir),
                CompositeAvailabilityResolver(sec, alpha),
            )
            rows = (
                [await service.audit_one(arguments.run_id)]
                if arguments.run_id is not None
                else await service.audit_pending(limit=arguments.limit)
            )
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        print(
            json.dumps(
                {
                    "audited": len(rows),
                    "counts": counts,
                    "run_ids": [str(row.run_id) for row in rows],
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        await database.close()


def main() -> None:
    raise SystemExit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()
