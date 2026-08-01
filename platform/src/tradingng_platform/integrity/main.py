from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone

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


def _stocklean_availability_resolver(
    settings: Settings,
    client: httpx.Client,
) -> AlphaEarningsAvailabilityResolver:
    token = settings.stocklean_internal_token.get_secret_value()
    if not token:
        raise RuntimeError("TRADINGNG_STOCKLEAN_INTERNAL_TOKEN is required")

    def load(ticker: str) -> str:
        response = client.get(
            f"{str(settings.stocklean_url).rstrip('/')}/api/internal/v1/alpha/documents/{ticker}",
            params={
                "functions": "EARNINGS",
                "as_of": datetime.now(timezone.utc).isoformat(),
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Caller-Service": "tradingng",
                "Accept": "application/json",
            },
            timeout=settings.stocklean_timeout_seconds,
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        if not items:
            return "{}"
        return json.dumps(items[0]["payload"])

    return AlphaEarningsAvailabilityResolver(load)


async def run(arguments) -> int:
    settings = Settings()
    database = Database(settings)
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=settings.stocklean_timeout_seconds,
        ) as client:
            sec = SecFilingClient(
                client=client,
                user_agent=settings.sec_user_agent,
                cache_dir=settings.sec_cache_dir,
                timeout_seconds=settings.sec_request_timeout_seconds,
            )
            alpha = _stocklean_availability_resolver(settings, client)
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
