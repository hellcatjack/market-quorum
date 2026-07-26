from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

_US_EXCHANGES = frozenset(
    {
        "ASE",
        "AMEX",
        "ARCA",
        "BATS",
        "NCM",
        "NGM",
        "NMS",
        "NASDAQ",
        "NYQ",
        "NYSE",
        "PCX",
    }
)
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ValidationSchedule:
    calendar_code: str
    entry_session: date
    exit_session: date
    matures_at: datetime


class MarketCalendarResolver:
    def schedule(
        self,
        asset_type: str,
        exchange: str | None,
        analysis_date: date,
        horizon: int,
    ) -> ValidationSchedule:
        if horizon < 0:
            raise ValueError("horizon cannot be negative")
        normalized_asset = asset_type.strip().lower()
        if normalized_asset == "crypto":
            exit_session = analysis_date + timedelta(days=horizon)
            return ValidationSchedule(
                calendar_code="24/7",
                entry_session=analysis_date,
                exit_session=exit_session,
                matures_at=datetime.combine(
                    exit_session + timedelta(days=1),
                    time(hour=0, minute=15),
                    timezone.utc,
                ),
            )
        if (exchange or "").strip().upper() in _US_EXCHANGES:
            return self._us_schedule(normalized_asset, analysis_date, horizon)
        return self._weekday_fallback(normalized_asset, analysis_date, horizon)

    @staticmethod
    def _us_schedule(asset_type: str, analysis_date: date, horizon: int) -> ValidationSchedule:
        calendar = xcals.get_calendar("XNYS")
        entry = calendar.date_to_session(analysis_date, direction="next")
        exit_session = calendar.session_offset(entry, horizon)
        provider_buffer = timedelta(hours=6 if asset_type == "fund" else 2)
        matures_at = calendar.session_close(exit_session).to_pydatetime() + provider_buffer
        return ValidationSchedule(
            calendar_code="XNYS",
            entry_session=entry.date(),
            exit_session=exit_session.date(),
            matures_at=matures_at.astimezone(timezone.utc),
        )

    @staticmethod
    def _weekday_fallback(
        asset_type: str,
        analysis_date: date,
        horizon: int,
    ) -> ValidationSchedule:
        entry = analysis_date
        while entry.weekday() >= 5:
            entry += timedelta(days=1)
        exit_session = entry
        remaining = horizon
        while remaining:
            exit_session += timedelta(days=1)
            if exit_session.weekday() < 5:
                remaining -= 1
        provider_buffer = timedelta(hours=6 if asset_type == "fund" else 2)
        local_close = datetime.combine(exit_session, time(hour=16), _NEW_YORK)
        return ValidationSchedule(
            calendar_code="WEEKDAY_FALLBACK",
            entry_session=entry,
            exit_session=exit_session,
            matures_at=(local_close + provider_buffer).astimezone(timezone.utc),
        )
