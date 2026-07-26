from datetime import datetime, timedelta

_RETENTION_AGE = {
    "raw_180d": timedelta(days=180),
    "diagnostic_90d": timedelta(days=90),
}


def is_due(
    retention_class: str,
    created_at: datetime,
    metadata: dict,
    now: datetime,
) -> bool:
    if metadata.get("legal_hold") is True:
        return False
    age = _RETENTION_AGE.get(retention_class)
    return age is not None and created_at <= now - age
