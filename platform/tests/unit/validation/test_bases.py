from datetime import date, datetime, timezone
from decimal import Decimal

from tradingng_platform.validation.bases import prepare_target_basis
from tradingng_platform.validation.normalizer import normalize_prices
from tradingng_platform.validation.price_contracts import OhlcBasis, ProviderPriceSeries


def test_target_basis_uses_last_session_on_or_before_analysis_date():
    sessions = [date(2026, 7, 23), date(2026, 7, 24), date(2026, 7, 27)]
    values = [Decimal("98"), Decimal("100"), Decimal("102")]
    prices = normalize_prices(
        ProviderPriceSeries(
            ticker="TEST",
            provider_symbol="TEST",
            provider_id="fixture",
            provider_adapter_version="fixture.v1",
            request_fingerprint="d" * 64,
            ohlc_basis=OhlcBasis.SPLIT_NORMALIZED,
            capabilities=frozenset(),
            sessions=sessions,
            open=values,
            high=values,
            low=values,
            close=values,
            adjusted_close=None,
            cash_distributions=[Decimal("0")] * 3,
            split_coefficient=[Decimal("1")] * 3,
            collected_at=datetime.now(timezone.utc),
        )
    )

    basis = prepare_target_basis(Decimal("120"), date(2026, 7, 25), prices)

    assert basis.reference_session == date(2026, 7, 24)
    assert basis.reference_close == Decimal("100")
    assert basis.target_multiple == Decimal("1.2")
