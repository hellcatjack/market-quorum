#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from dotenv import dotenv_values
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.config import Settings
from tradingng_platform.models import Artifact, Base, CoordinationLock

_EXPECTED_REVISION = "20260726_0006"
_COORDINATION_SEEDS = {
    "global:admission",
    "global:archive",
    "global:retention",
}


class MigrationSafetyError(RuntimeError):
    """Raised before copying when source or target invariants are not met."""


@dataclass(frozen=True)
class TableDigest:
    table: str
    rows: int
    sha256: str


def _normalize_value(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        observed = (
            value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        )
        return observed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MigrationSafetyError(
                "non-finite floating point value cannot be migrated"
            )
        return {"__float__": format(value, ".12g")}
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _normalize_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return value


def canonical_row(row: Mapping[str, object]) -> bytes:
    normalized = _normalize_value(dict(sorted(row.items())))
    return json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()


def assert_empty_target(counts: Mapping[str, int]) -> None:
    populated = {
        table: count
        for table, count in counts.items()
        if count and not (table == "coordination_locks" and count <= 3)
    }
    if populated:
        names = ",".join(sorted(populated))
        raise MigrationSafetyError(f"target database is not empty: {names}")


def assert_seeded_coordination_locks(lock_keys: set[str]) -> None:
    if lock_keys != _COORDINATION_SEEDS:
        raise MigrationSafetyError("target coordination lock seeds are not exact")


async def digest_table(connection: AsyncConnection, table) -> TableDigest:
    statement = select(table)
    primary_key = tuple(table.primary_key.columns)
    if primary_key:
        statement = statement.order_by(*primary_key)
    rows = (await connection.execute(statement)).mappings()
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(canonical_row(row))
        digest.update(b"\n")
        count += 1
    return TableDigest(table.name, count, digest.hexdigest())


async def _table_counts(connection: AsyncConnection) -> dict[str, int]:
    return {
        table.name: int(
            await connection.scalar(select(func.count()).select_from(table)) or 0
        )
        for table in Base.metadata.sorted_tables
    }


async def _revision(connection: AsyncConnection) -> str | None:
    return await connection.scalar(text("SELECT version_num FROM alembic_version"))


async def _copy_tables(source: AsyncConnection, target: AsyncConnection) -> None:
    await target.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    try:
        await target.execute(delete(CoordinationLock))
        for table in Base.metadata.sorted_tables:
            rows = [
                dict(row) for row in (await source.execute(select(table))).mappings()
            ]
            if rows:
                await target.execute(insert(table), rows)
    finally:
        await target.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def foreign_key_orphan_statement(table, foreign_key):
    child = foreign_key.parent
    parent = foreign_key.column
    parent_alias = parent.table.alias(f"parent_{table.name}_{child.name}")
    parent_column = parent_alias.c[parent.name]
    return (
        select(func.count())
        .select_from(table.outerjoin(parent_alias, child == parent_column))
        .where(child.is_not(None), parent_column.is_(None))
    )


async def _foreign_key_failures(connection: AsyncConnection) -> list[str]:
    failures = []
    for table in Base.metadata.sorted_tables:
        for foreign_key in table.foreign_keys:
            child = foreign_key.parent
            parent = foreign_key.column
            orphan_count = await connection.scalar(
                foreign_key_orphan_statement(table, foreign_key)
            )
            if orphan_count:
                failures.append(
                    f"{table.name}.{child.name}->{parent.table.name}.{parent.name}"
                )
    return failures


def artifact_verification_statement():
    return (
        select(Artifact.id, Artifact.storage_key, Artifact.sha256)
        .where(Artifact.deleted_at.is_(None))
        .order_by(Artifact.id)
    )


async def _verify_artifacts(connection: AsyncConnection, artifact_root: Path) -> int:
    store = LocalArtifactStore(artifact_root)
    artifacts = (await connection.execute(artifact_verification_statement())).mappings()
    failures = []
    verified_count = 0
    for artifact in artifacts:
        verified_count += 1
        try:
            valid = store.verify(artifact["storage_key"], artifact["sha256"])
        except ValueError:
            valid = False
        if not valid:
            failures.append(str(artifact["id"]))
    if failures:
        raise MigrationSafetyError(
            f"artifact verification failed count={len(failures)}"
        )
    return verified_count


async def migrate(source_url: str, target_url: str, artifact_root: Path) -> dict:
    source_engine = create_async_engine(source_url, pool_pre_ping=True)
    target_engine = create_async_engine(target_url, pool_pre_ping=True)
    try:
        if source_engine.dialect.name != "postgresql":
            raise MigrationSafetyError("source database must be PostgreSQL")
        if target_engine.dialect.name != "mysql":
            raise MigrationSafetyError("target database must be MySQL")

        async with source_engine.begin() as source, target_engine.begin() as target:
            source_revision = await _revision(source)
            target_revision = await _revision(target)
            if (
                source_revision != _EXPECTED_REVISION
                or target_revision != _EXPECTED_REVISION
            ):
                raise MigrationSafetyError(
                    "source and target must both be at Alembic head"
                )
            assert_empty_target(await _table_counts(target))
            lock_keys = set(
                (await target.execute(select(CoordinationLock.lock_key))).scalars()
            )
            assert_seeded_coordination_locks(lock_keys)
            await _copy_tables(source, target)

        async with source_engine.connect() as source, target_engine.connect() as target:
            source_digests = {
                table.name: await digest_table(source, table)
                for table in Base.metadata.sorted_tables
            }
            target_digests = {
                table.name: await digest_table(target, table)
                for table in Base.metadata.sorted_tables
            }
            mismatches = [
                table
                for table in source_digests
                if source_digests[table] != target_digests[table]
            ]
            if mismatches:
                raise MigrationSafetyError(
                    f"table digest mismatch: {','.join(sorted(mismatches))}"
                )
            foreign_key_failures = await _foreign_key_failures(target)
            if foreign_key_failures:
                raise MigrationSafetyError(
                    f"foreign key verification failed: {','.join(foreign_key_failures)}"
                )
            verified_artifacts = await _verify_artifacts(target, artifact_root)

        return {
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_dialect": "postgresql",
            "target_dialect": "mysql",
            "revision": _EXPECTED_REVISION,
            "tables": {
                table: asdict(digest)
                for table, digest in sorted(source_digests.items())
            },
            "verified_artifacts": verified_artifacts,
        }
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


def _source_url(path: Path) -> str:
    values = dotenv_values(path)
    value = values.get("TRADINGNG_DATABASE_URL")
    if not value:
        raise MigrationSafetyError("source environment has no TRADINGNG_DATABASE_URL")
    return value


def _target_settings(path: Path) -> Settings:
    values = dotenv_values(path)
    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise MigrationSafetyError(
            f"target environment is missing: {','.join(missing)}"
        )
    return Settings(
        _env_file=None,
        DB_HOST=values["DB_HOST"],
        DB_NAME=values["DB_NAME"],
        DB_USER=values["DB_USER"],
        DB_PASSWORD=values["DB_PASSWORD"],
        DB_CHARSET=values.get("DB_CHARSET") or "utf8mb4",
        DB_COLLATE=values.get("DB_COLLATE") or "utf8mb4_unicode_ci",
    )


def _manifest_path(path: Path) -> Path:
    project_root = Path(__file__).resolve().parents[1]
    allowed_root = (project_root / "var" / "migrations").resolve()
    resolved = path.resolve()
    if resolved.parent != allowed_root and allowed_root not in resolved.parents:
        raise MigrationSafetyError("manifest must be beneath var/migrations")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate frozen TradingNG data to MySQL"
    )
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--target-env", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    arguments = parser.parse_args()

    source_url = _source_url(arguments.source_env)
    target_settings = _target_settings(arguments.target_env)
    manifest_path = _manifest_path(arguments.manifest_out)
    manifest = asyncio.run(
        migrate(
            source_url, target_settings.database_url, arguments.artifact_root.resolve()
        )
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"migrated_tables={len(manifest['tables'])} "
        f"verified_artifacts={manifest['verified_artifacts']}"
    )


if __name__ == "__main__":
    main()
