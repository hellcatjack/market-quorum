import re
from enum import Enum

_TICKER = re.compile(r"^[A-Z0-9^][A-Z0-9.^=_-]{0,31}$")


class AssetType(str, Enum):
    STOCK = "stock"
    FUND = "fund"
    CRYPTO = "crypto"


def canonicalize_ticker(raw: str) -> str:
    ticker = raw.strip().upper()
    if not _TICKER.fullmatch(ticker):
        raise ValueError(f"invalid ticker: {raw!r}")
    return ticker
