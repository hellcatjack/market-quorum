import pytest

from tradingng_platform.domain.instruments import canonicalize_ticker


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" nvda ", "NVDA"), ("brk.b", "BRK.B"), ("btc-usd", "BTC-USD"), ("gc=f", "GC=F")],
)
def test_canonicalize_ticker(raw, expected):
    assert canonicalize_ticker(raw) == expected


def test_canonicalize_ticker_rejects_paths():
    with pytest.raises(ValueError, match="invalid ticker"):
        canonicalize_ticker("../../etc/passwd")
