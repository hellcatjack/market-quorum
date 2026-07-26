from sqlalchemy import select

from tradingng_platform.models.coordination import CoordinationLock
from tradingng_platform.persistence.upsert import insert_ignore, session_dialect


def coordination_lock_statement(lock_key: str, *, wait: bool):
    return (
        select(CoordinationLock.lock_key)
        .where(CoordinationLock.lock_key == lock_key)
        .with_for_update(skip_locked=not wait)
    )


async def ensure_coordination_lock(session, lock_key: str) -> None:
    await session.execute(
        insert_ignore(
            session_dialect(session),
            CoordinationLock,
            {"lock_key": lock_key},
            [CoordinationLock.lock_key],
        )
    )


async def acquire_transaction_lock(session, lock_key: str, *, wait: bool = True) -> bool:
    if wait:
        exists = await session.scalar(
            select(CoordinationLock.lock_key).where(CoordinationLock.lock_key == lock_key)
        )
        if exists is None:
            await ensure_coordination_lock(session, lock_key)
        locked = await session.scalar(coordination_lock_statement(lock_key, wait=True))
        return locked is not None

    locked = await session.scalar(coordination_lock_statement(lock_key, wait=False))
    if locked is not None:
        return True
    exists = await session.scalar(
        select(CoordinationLock.lock_key).where(CoordinationLock.lock_key == lock_key)
    )
    if exists is not None:
        return False
    await ensure_coordination_lock(session, lock_key)
    locked = await session.scalar(coordination_lock_statement(lock_key, wait=False))
    return locked is not None
