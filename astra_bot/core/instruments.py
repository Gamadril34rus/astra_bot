"""
Юниверс торговых инструментов ASTRA BOT.

10 популярных ликвидных спот-пар к USDT на OKX. Все они проверены через
``/api/v5/public/instruments`` (state=live). Ликвидность важна: бот
торгует только там, где узкий спред, глубина стакана и нет проскальзываний
на крупных объёмах — это часть защиты от слива депозита.
"""

from __future__ import annotations

# Канонический формат "BASE/USDT" (внутренний) и "BASE-USDT" (для OKX).
TRADING_UNIVERSE: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "DOT/USDT",
    "TRX/USDT",
)

# «Мажоры» с максимальной ликвидностью — на них приходится основной объём.
MAJOR_SYMBOLS: frozenset[str] = frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"})

# Альткоины из топа — торгуются с пониженным размером позиции (волатильнее).
ALT_SYMBOLS: tuple[str, ...] = tuple(s for s in TRADING_UNIVERSE if s not in MAJOR_SYMBOLS)


def to_okx(symbol: str) -> str:
    """BTC/USDT -> BTC-USDT."""
    return symbol.replace("/", "-")


def is_alt(symbol: str) -> bool:
    return symbol not in MAJOR_SYMBOLS


def position_fraction_for(symbol: str, base_fraction: float) -> float:
    """Размер позиции под инструмент.

    На альтах (волатильнее и менее ликвидны) заходим меньшим номиналом,
    чтобы одна плохая сделка не пробивала дневной лимит потерь.
    """
    if is_alt(symbol):
        return base_fraction * 0.5
    return base_fraction
