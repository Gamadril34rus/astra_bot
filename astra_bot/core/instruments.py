"""
Юниверс торговых инструментов ASTRA BOT.

Ликвидные спот-пары к USDT на OKX, проверенные через
/api/v5/public/instruments (state=live). Бот сам выбирает, где
выгодный сетап, не ограничиваясь монетами на счету (торговля
бумажная, расчёты в USDT).
"""

from __future__ import annotations

TRADING_UNIVERSE: tuple[str, ...] = (
    # мажоры
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    # крупные альты
    "ADA/USDT", "AVAX/USDT", "DOGE/USDT", "LINK/USDT", "DOT/USDT",
    "TRX/USDT", "LTC/USDT", "BCH/USDT", "ATOM/USDT", "NEAR/USDT",
    "APT/USDT", "ARB/USDT", "OP/USDT", "SUI/USDT", "INJ/USDT",
    "TIA/USDT", "FIL/USDT", "ICP/USDT", "HBAR/USDT", "AAVE/USDT",
    "UNI/USDT", "FET/USDT",
)

MAJOR_SYMBOLS: frozenset[str] = frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"})


def to_okx(symbol: str) -> str:
    return symbol.replace("/", "-")


def is_alt(symbol: str) -> bool:
    return symbol not in MAJOR_SYMBOLS


def position_fraction_for(symbol: str, base_fraction: float) -> float:
    """На альтах — половинный номинал (волатильнее)."""
    if is_alt(symbol):
        return base_fraction * 0.5
    return base_fraction
