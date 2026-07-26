import argparse
import asyncio

from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.retention.service import RetentionService


async def _run(apply: bool) -> None:
    settings = Settings()
    database = Database(settings)
    try:
        due = await RetentionService(
            database.sessions,
            LocalArtifactStore(settings.artifact_dir),
        ).run(apply=apply)
        print(f"{'deleted' if apply else 'due'}={len(due)}")
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply TradingNG artifact retention")
    parser.add_argument(
        "--apply", action="store_true", help="Delete due files and tombstone rows"
    )
    arguments = parser.parse_args()
    asyncio.run(_run(arguments.apply))


if __name__ == "__main__":
    main()
