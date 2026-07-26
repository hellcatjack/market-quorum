from datetime import date, datetime, timezone

from tradingng_platform.validation.calendars import MarketCalendarResolver


def test_us_weekend_schedule_uses_exact_trading_sessions():
    resolver = MarketCalendarResolver()

    schedule = resolver.schedule("stock", "NMS", date(2026, 7, 25), 20)

    assert schedule.calendar_code == "XNYS"
    assert schedule.entry_session == date(2026, 7, 27)
    assert schedule.exit_session == date(2026, 8, 24)
    assert schedule.matures_at == datetime(2026, 8, 24, 22, tzinfo=timezone.utc)


def test_us_early_close_uses_calendar_close_before_provider_buffer():
    resolver = MarketCalendarResolver()

    schedule = resolver.schedule("fund", "PCX", date(2026, 11, 27), 0)

    assert schedule.entry_session == date(2026, 11, 27)
    assert schedule.exit_session == date(2026, 11, 27)
    assert schedule.matures_at == datetime(2026, 11, 28, 0, tzinfo=timezone.utc)


def test_crypto_uses_utc_daily_sessions():
    resolver = MarketCalendarResolver()

    schedule = resolver.schedule("crypto", None, date(2026, 7, 25), 1)

    assert schedule.calendar_code == "24/7"
    assert schedule.entry_session == date(2026, 7, 25)
    assert schedule.exit_session == date(2026, 7, 26)
    assert schedule.matures_at == datetime(2026, 7, 27, 0, 15, tzinfo=timezone.utc)


def test_unknown_stock_exchange_uses_explicit_weekday_fallback():
    resolver = MarketCalendarResolver()

    schedule = resolver.schedule("stock", "UNKNOWN", date(2026, 7, 25), 1)

    assert schedule.calendar_code == "WEEKDAY_FALLBACK"
    assert schedule.entry_session == date(2026, 7, 27)
    assert schedule.exit_session == date(2026, 7, 28)
    assert schedule.matures_at == datetime(2026, 7, 28, 22, tzinfo=timezone.utc)
