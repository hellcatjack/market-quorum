from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingng_platform.validation.normalizer import normalize_prices
from tradingng_platform.validation.price_contracts import OhlcBasis, ProviderPriceSeries


def _series(
    *,
    basis: OhlcBasis,
    closes: list[str],
    splits: list[str] | None = None,
    distributions: list[str] | None = None,
    adjusted: list[str] | None = None,
) -> ProviderPriceSeries:
    values = [Decimal(value) for value in closes]
    sessions = [date(2026, 1, 5) + timedelta(days=index) for index in range(len(values))]
    return ProviderPriceSeries(
        ticker="TEST",
        provider_symbol="TEST",
        provider_id="fixture",
        provider_adapter_version="fixture.v1",
        request_fingerprint="a" * 64,
        ohlc_basis=basis,
        capabilities=frozenset({"splits", "cash_dividends"}),
        currency="USD",
        timezone="America/New_York",
        sessions=sessions,
        open=values,
        high=values,
        low=values,
        close=values,
        adjusted_close=([Decimal(value) for value in adjusted] if adjusted is not None else None),
        cash_distributions=[Decimal(value) for value in (distributions or ["0"] * len(values))],
        split_coefficient=[Decimal(value) for value in (splits or ["1"] * len(values))],
        collected_at=datetime(2026, 1, 20, tzinfo=timezone.utc),
    )


def test_as_traded_and_split_normalized_sources_produce_the_same_price_path():
    alpha = _series(
        basis=OhlcBasis.AS_TRADED,
        closes=["100", "50", "55"],
        splits=["1", "2", "1"],
    )
    yahoo = _series(
        basis=OhlcBasis.SPLIT_NORMALIZED,
        closes=["50", "50", "55"],
        splits=["1", "2", "1"],
    )

    alpha_normalized = normalize_prices(alpha)
    yahoo_normalized = normalize_prices(yahoo)

    assert (
        alpha_normalized.close
        == yahoo_normalized.close
        == [
            Decimal("50"),
            Decimal("50"),
            Decimal("55"),
        ]
    )
    assert alpha_normalized.price_index == yahoo_normalized.price_index
    assert alpha_normalized.price_index[-1] == Decimal("110.0")


def test_cash_distribution_changes_total_return_but_not_price_return():
    normalized = normalize_prices(
        _series(
            basis=OhlcBasis.SPLIT_NORMALIZED,
            closes=["100", "99"],
            distributions=["0", "1"],
            adjusted=["100", "100"],
        )
    )

    assert normalized.price_index == [Decimal("100"), Decimal("99.00")]
    assert normalized.total_return_index == [Decimal("100"), Decimal("100.0")]
    assert normalized.data_quality_status == "matched"


def test_provider_adjusted_close_difference_is_classified():
    minor = normalize_prices(
        _series(
            basis=OhlcBasis.SPLIT_NORMALIZED,
            closes=["100", "101"],
            adjusted=["100", "101.05"],
        )
    )
    material = normalize_prices(
        _series(
            basis=OhlcBasis.SPLIT_NORMALIZED,
            closes=["100", "101"],
            adjusted=["100", "103"],
        )
    )

    assert minor.data_quality_status == "minor_difference"
    assert material.data_quality_status == "material_difference"


def test_unknown_ohlc_basis_fails_closed():
    series = _series(basis=OhlcBasis.UNKNOWN, closes=["100", "101"])

    with pytest.raises(ValueError, match="OHLC basis"):
        normalize_prices(series)


def test_provider_contract_rejects_misaligned_actions():
    with pytest.raises(ValueError, match="equal length"):
        ProviderPriceSeries(
            ticker="TEST",
            provider_symbol="TEST",
            provider_id="fixture",
            provider_adapter_version="fixture.v1",
            request_fingerprint="b" * 64,
            ohlc_basis=OhlcBasis.AS_TRADED,
            capabilities=frozenset(),
            sessions=[date(2026, 1, 5)],
            open=[Decimal("1")],
            high=[Decimal("1")],
            low=[Decimal("1")],
            close=[Decimal("1")],
            adjusted_close=None,
            cash_distributions=[],
            split_coefficient=[Decimal("1")],
            collected_at=datetime.now(timezone.utc),
        )
