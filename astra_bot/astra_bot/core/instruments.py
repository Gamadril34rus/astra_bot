"""Юниверс торговых инструментов ASTRA BOT."""

from __future__ import annotations

# Широкий набор известных ликвидных криптоактивов.
# Перед торговлей worker фильтрует его по фактическим SPOT-инструментам
# BingX (свечи/стакан тянутся оттуда же).
TRADING_UNIVERSE: tuple[str, ...] = (
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "DOGE/USDT", "LINK/USDT", "DOT/USDT",
    "TRX/USDT", "LTC/USDT", "BCH/USDT", "ATOM/USDT", "NEAR/USDT",
    "APT/USDT", "ARB/USDT", "OP/USDT", "SUI/USDT", "INJ/USDT",
    "TIA/USDT", "FIL/USDT", "ICP/USDT", "HBAR/USDT", "AAVE/USDT",
    "UNI/USDT", "FET/USDT", "TON/USDT", "XLM/USDT", "SHIB/USDT",
    "PEPE/USDT", "ETC/USDT", "CRO/USDT", "MKR/USDT", "XMR/USDT",
)

MAJOR_SYMBOLS: frozenset[str] = frozenset({
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
})


def to_bingx(symbol: str) -> str:
    """Формат символа BingX spot: ``BTC/USDT`` → ``BTC-USDT``."""
    return symbol.replace("/", "-")


# Deprecated alias: формат символов OKX spot совпадает с BingX (дефис).
# Оставлен на время переходного периода, новые вызовы используют to_bingx.
to_okx = to_bingx


def is_alt(symbol: str) -> bool:
    return symbol not in MAJOR_SYMBOLS


def position_fraction_for(symbol: str, base_fraction: float) -> float:
    """На менее крупных активах уменьшаем номинал позиции."""
    return base_fraction if not is_alt(symbol) else base_fraction * 0.7
