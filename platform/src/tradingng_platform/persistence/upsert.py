from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert


def insert_ignore(
    dialect: str,
    model,
    values: Mapping[str, Any],
    conflict_columns: Sequence,
):
    if dialect == "postgresql":
        return (
            postgresql_insert(model)
            .values(**values)
            .on_conflict_do_nothing(index_elements=list(conflict_columns))
        )
    if dialect == "mysql":
        statement = mysql_insert(model).values(**values)
        first = conflict_columns[0]
        return statement.on_duplicate_key_update(**{first.key: first})
    raise RuntimeError(f"unsupported database dialect: {dialect}")


def upsert(
    dialect: str,
    model,
    values: Mapping[str, Any],
    conflict_columns: Sequence,
    update_values: Mapping[str, Any],
):
    if dialect == "postgresql":
        return (
            postgresql_insert(model)
            .values(**values)
            .on_conflict_do_update(
                index_elements=list(conflict_columns),
                set_=dict(update_values),
            )
        )
    if dialect == "mysql":
        return mysql_insert(model).values(**values).on_duplicate_key_update(**dict(update_values))
    raise RuntimeError(f"unsupported database dialect: {dialect}")


def session_dialect(session) -> str:
    return session.get_bind().dialect.name
