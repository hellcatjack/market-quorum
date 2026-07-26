#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

import asyncmy
from sqlalchemy.engine import URL, make_url
from tradingng_platform.config import Settings

_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_TEST_DATABASE_NAME = re.compile(r"^tradingng_test_[A-Za-z0-9]{6,64}$")


def validate_database_name(name: str) -> str:
    if not _DATABASE_NAME.fullmatch(name):
        raise ValueError("database name is not a safe identifier")
    return name


def validate_test_database_name(name: str) -> str:
    if not _TEST_DATABASE_NAME.fullmatch(name):
        raise ValueError("test database name must use a bounded tradingng_test_ suffix")
    return name


def _settings(env_file: Path) -> Settings:
    return Settings(_env_file=env_file)


def _mysql_url(settings: Settings, database: str | None = None) -> URL:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "mysql":
        raise ValueError("configured database is not MySQL")
    return url.set(database=database)


async def _server_connection(settings: Settings):
    url = _mysql_url(settings, database=None)
    return await asyncmy.connect(
        host=str(url.host),
        port=int(url.port or 3306),
        user=str(url.username),
        password=str(url.password),
        charset=str(url.query.get("charset", "utf8mb4")),
        autocommit=True,
    )


async def _database_exists(connection, name: str) -> bool:
    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s",
            (name,),
        )
        row = await cursor.fetchone()
    return bool(row and row[0])


async def _tables(connection, name: str) -> tuple[str, ...]:
    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s ORDER BY table_name",
            (name,),
        )
        rows = await cursor.fetchall()
    return tuple(str(row[0]) for row in rows)


async def create_database(settings: Settings, name: str, *, test_only: bool) -> None:
    validate_test_database_name(name) if test_only else validate_database_name(name)
    connection = await _server_connection(settings)
    try:
        exists = await _database_exists(connection, name)
        if test_only and exists:
            raise RuntimeError("test database already exists")
        if exists:
            tables = await _tables(connection, name)
            if tables:
                raise RuntimeError("target database is not empty")
            return
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"CREATE DATABASE `{name}` CHARACTER SET {settings.db_charset} "
                f"COLLATE {settings.db_collate}"
            )
    finally:
        connection.close()


async def drop_test_database(settings: Settings, name: str, confirmation: str) -> None:
    validate_test_database_name(name)
    if confirmation != name:
        raise ValueError("test database drop confirmation does not match")
    connection = await _server_connection(settings)
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        connection.close()


def database_url(settings: Settings, name: str) -> str:
    validate_test_database_name(name)
    return _mysql_url(settings, database=name).render_as_string(hide_password=False)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage guarded TradingNG MySQL databases"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("create-production")
    create_test = subparsers.add_parser("create-test")
    create_test.add_argument("--name", required=True)
    drop_test = subparsers.add_parser("drop-test")
    drop_test.add_argument("--name", required=True)
    drop_test.add_argument("--confirm-drop", required=True)
    url = subparsers.add_parser("url")
    url.add_argument("--name", required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    settings = _settings(arguments.env_file)
    configured_name = validate_database_name(str(_mysql_url(settings).database))
    if arguments.command == "create-production":
        asyncio.run(create_database(settings, configured_name, test_only=False))
    elif arguments.command == "create-test":
        asyncio.run(create_database(settings, arguments.name, test_only=True))
    elif arguments.command == "drop-test":
        asyncio.run(
            drop_test_database(settings, arguments.name, arguments.confirm_drop)
        )
    elif arguments.command == "url":
        print(database_url(settings, arguments.name))


if __name__ == "__main__":
    main()
