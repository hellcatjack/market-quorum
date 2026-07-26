from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.models import Artifact


async def verify(artifact_root: Path, database_url: str) -> tuple[int, list[str]]:
    store = LocalArtifactStore(artifact_root)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    failures = []
    checked = 0
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            artifacts = list(
                await session.scalars(
                    select(Artifact)
                    .where(Artifact.deleted_at.is_(None))
                    .order_by(Artifact.id)
                )
            )
        for artifact in artifacts:
            checked += 1
            try:
                valid = store.verify(artifact.storage_key, artifact.sha256)
            except ValueError:
                valid = False
            if not valid:
                failures.append(str(artifact.id))
    finally:
        await engine.dispose()
    return checked, failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify immutable TradingNG artifact hashes"
    )
    parser.add_argument("--artifact-root", type=Path, required=True)
    database = parser.add_mutually_exclusive_group(required=True)
    database.add_argument("--database-url")
    database.add_argument("--database-url-env")
    arguments = parser.parse_args()
    database_url = arguments.database_url
    if arguments.database_url_env:
        database_url = os.getenv(arguments.database_url_env)
        if not database_url:
            parser.error("database URL environment variable is empty")
    checked, failures = asyncio.run(verify(arguments.artifact_root, database_url))
    if failures:
        raise SystemExit(f"artifact verification failed count={len(failures)}")
    print(f"verified_artifacts={checked}")


if __name__ == "__main__":
    main()
