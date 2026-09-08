"""Simulated market feed — детерминированный синтетический рынок.

Зачем: непрерывная работа и накопление опыта не должны требовать
сети/ключей. ASTRA_SIMULATE=1 подменяет биржевой клиент генератором,
который круглосуточно гонит рынок через фазы «основы основ»:

BULL → RANGE → BEAR → FALLING_WEDGE → RISING_WEDGE →
ASC_TRIANGLE → DESC_TRIANGLE → SYM_TRIANGLE → BREAKOUT → (цикл)

Цена — непрерывная функция времени (одинаковая для всех таймфреймов),
свечи детерминированы (одинаковы при рестарте), объёмные всплески — на
пробоях. Только paper-контур: движок и так fail-closed, но симулятор
никогда не отдаёт приватные эндпоинты с реальными деньгами.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from decimal import Decimal
from typing import Any

from ..core.models import AccountBalance, Candle, OrderBook, OrderBookEntry

# Длительность фаз в секундах реального времени. Цикл ~9 суток при
# дефолтах; для быстрых прогонов сокращается через ASTRA_SIM_PHASE_SEC.
_DEFAULT_PHASE_SEC = 26 * 3600

# (тип фазы, доля от длительности фазы не используется — все фазы равны)
_PHASE_CYCLE: tuple[str, ...] = (
    "bull",
    "range",
    "bear",
    "falling_wedge",
    "rising_wedge",
    "asc_triangle",
    "desc_triangle",
    "sym_triangle",
    "breakout",
)

_BASE_PRICES: dict[str, float] = {
    "BTC": 64000.0, "ETH": 3200.0, "SOL": 150.0, "BNB": 580.0,
    "XRP": 0.55, "ADA": 0.45, "AVAX": 35.0, "DOGE": 0.12,
    "LINK": 14.0, "DOT": 6.5, "TRX": 0.12, "LTC": 72.0,
}

_TF_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400,
}


def _stable_noise(*parts: Any) -> float:
    """Детерминированный шум [0, 1) из произвольных частей."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


class SimulatedExchange:
    """Duck-typed аналог биржевого REST-клиента (get_candles/get_ticker/...)."""

    exchange = "simulated"

    def __init__(self, phase_seconds: int | None = None, rate_limit_qps: float = 0):
        self.phase_seconds = phase_seconds or int(
            os.environ.get("ASTRA_SIM_PHASE_SEC", str(_DEFAULT_PHASE_SEC))
        )
        self.rate_limit_qps = rate_limit_qps

    # ------------------------------------------------------------ lifecycle
    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def test_connection(self) -> bool:
        return True

    # ------------------------------------------------------------- pricing
    def _base_price(self, symbol: str) -> float:
        base = symbol.split("/")[0].split("-")[0].upper()
        if base in _BASE_PRICES:
            return _BASE_PRICES[base]
        # 20..500 детерминированно из имени
        return 20.0 + _stable_noise("base", symbol) * 480.0

    def _phase(self, ts: float, symbol: str = "") -> tuple[str, float, int]:
        """(тип фазы, локальное время внутри фазы [0..1), номер фазы).

        Фазы сдвинуты по символам (стабильный хэш): разные монеты
        одновременно живут в разных режимах — как на реальном рынке.
        """
        offset = int(_stable_noise("phase", symbol) * len(_PHASE_CYCLE))
        idx = int(ts // self.phase_seconds) + offset
        kind = _PHASE_CYCLE[idx % len(_PHASE_CYCLE)]
        raw_u = ts - int(ts // self.phase_seconds) * self.phase_seconds
        u = raw_u / self.phase_seconds
        return kind, min(max(u, 0.0), 1.0), idx

    def _trend_amp(self) -> float:
        """Амплитуда трендовой фазы, масштабированная длиной фазы.

        18% размазаны на «эталонные» 26 часов; при коротких фазах
        (быстрые демо-прогоны) пропорционально меньше — иначе
        симулятор волатильнее любого реального рынка и паника-гейт
        честно блокирует торговлю.
        """
        return 0.18 * min(1.0, self.phase_seconds / (26 * 3600))

    def price_at(self, symbol: str, ts: float) -> float:
        """Непрерывная цена символа в момент ts (unix seconds)."""
        b = self._base_price(symbol)
        kind, u, idx = self._phase(ts, symbol)
        amp = self._trend_amp()
        if kind == "bull":
            return b * math.exp(amp * u)
        if kind == "bear":
            return b * math.exp(-amp * u)
        if kind == "range":
            return b * (1.0 + 0.025 * math.sin(2 * math.pi * 4 * u))
        if kind in ("falling_wedge", "rising_wedge"):
            # Канал с затухающей амплитудой: 6 касаний границ за фазу.
            if kind == "falling_wedge":
                mid = b * (1.0 - 0.10 * u)
            else:
                mid = b * (1.0 + 0.10 * u)
            amp = b * 0.05 * (1.0 - 0.85 * u) + b * 0.002
            return mid + amp * math.sin(2 * math.pi * 6 * u)
        if kind in ("asc_triangle", "desc_triangle", "sym_triangle"):
            if kind == "asc_triangle":
                upper = b * 1.03
                lower = b * (0.94 + 0.09 * u)
            elif kind == "desc_triangle":
                upper = b * (1.06 - 0.09 * u)
                lower = b * 0.97
            else:
                upper = b * (1.06 - 0.06 * u)
                lower = b * (0.94 + 0.06 * u)
            mid = (upper + lower) / 2
            half = (upper - lower) / 2
            # Ломаная синусоида гарантирует ретесты обеих границ.
            osc = math.sin(2 * math.pi * 5 * u)
            return mid + half * osc * (0.96 + 0.04 * _stable_noise(symbol, idx))
        if kind == "breakout":
            if u < 0.6:
                return b * (1.0 + 0.01 * math.sin(2 * math.pi * 8 * u))
            progress = (u - 0.6) / 0.4
            return b * (1.0 + 0.15 * progress * progress)
        return b

    # -------------------------------------------------------------- candles
    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        since: int | None = None,
        limit: int = 1000,
    ) -> list[Candle]:
        tf = _TF_SECONDS.get(timeframe)
        if tf is None:
            return []
        limit = max(1, min(int(limit), 1000))
        now = time.time()
        last_open = int(now // tf) * tf
        out: list[Candle] = []
        for k in range(limit):
            open_time = last_open - (limit - 1 - k) * tf
            if since and open_time < int(since) - tf:
                continue
            out.append(self._build_candle(symbol, timeframe, open_time, tf))
        return out

    def _build_candle(
        self, symbol: str, timeframe: str, open_time: int, tf: int
    ) -> Candle:
        samples = 8
        step = tf / samples
        prices = [
            self.price_at(symbol, open_time + i * step + 0.001)
            for i in range(samples)
        ]
        # Микрошум внутри бара (детерминированный): ±0.6% достаточно,
        # чтобы SL/TP отрабатывались в темпе демо-прогона.
        jittered = [
            p * (1.0 + (_stable_noise(symbol, open_time, i) - 0.5) * 0.012)
            for i, p in enumerate(prices)
        ]
        o = jittered[0]
        c = jittered[-1]
        hi = max(max(jittered), o, c)
        lo = min(min(jittered), o, c)

        # Объём: база + шум; всплески на пробойных барах фазы breakout.
        kind, u, _idx = self._phase(open_time + tf / 2)
        vol_base = max(1.0, 100000.0 / max(self._base_price(symbol), 1e-9))
        vol = vol_base * (1.5 + _stable_noise(symbol, open_time, "v"))
        # Спайки объёма: ~15% баров получают x2.2 (подтверждение объёмом
        # в стратегиях требует volume_ratio > 1.2-1.5).
        if _stable_noise(symbol, open_time, "vs") < 0.15:
            vol *= 2.2
        if kind == "breakout" and u >= 0.6:
            vol *= 3.0 + 2.0 * _stable_noise(symbol, open_time, "bv")
        # OHLC согласованно берётся из ОДНОГО (зашумлённого) ряда:
        # раньше close считался от чистой цены, а low/high от зашумлённой
        # — ломался инвариант low <= close <= high.
        move = abs(c - o) / max(abs(o), 1e-9)
        vol *= 1.0 + min(move * 40.0, 2.0)

        price_dec = Decimal(str(round(c, 8)))
        return Candle(
            exchange=self.exchange,
            symbol=symbol,
            timeframe=timeframe,
            open_time=open_time * 1000,
            open=Decimal(str(round(o, 8))),
            high=Decimal(str(round(hi, 8))),
            low=Decimal(str(round(lo, 8))),
            close=price_dec,
            volume=Decimal(str(round(vol, 4))),
            quote_volume=Decimal(str(round(vol * c, 2))),
            trades_count=int(50 + _stable_noise(symbol, open_time, "t") * 400),
        )

    async def get_candles_before(
        self,
        symbol: str,
        timeframe: str,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> list[Candle]:
        tf = _TF_SECONDS.get(timeframe)
        if tf is None:
            return []
        limit = max(1, min(int(limit), 1000))
        anchor = (end_time_ms or int(time.time() * 1000)) / 1000
        last_open = int(anchor // tf) * tf - tf  # строго ДО end_time
        out = [
            self._build_candle(symbol, timeframe, last_open - k * tf, tf)
            for k in range(limit - 1, -1, -1)
        ]
        return out

    # -------------------------------------------------------------- ticker
    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        now = time.time()
        last = self.price_at(symbol, now)
        samples = [self.price_at(symbol, now - h * 3600) for h in range(24)]
        spread = max(last * 0.0002, 1e-9)
        return {
            "symbol": symbol,
            "last": Decimal(str(round(last, 8))),
            "bid": Decimal(str(round(last - spread, 8))),
            "ask": Decimal(str(round(last + spread, 8))),
            "high_24h": Decimal(str(round(max(samples), 8))),
            "low_24h": Decimal(str(round(min(samples), 8))),
            "volume_24h": Decimal(str(round(1000.0 * (1 + _stable_noise(symbol, now // 3600)), 2))),
        }

    async def get_orderbook(
        self, symbol: str, depth: int = 20
    ) -> OrderBook:
        last = self.price_at(symbol, time.time())
        tick = max(last * 0.0001, 1e-9)
        bids: list[OrderBookEntry] = []
        asks: list[OrderBookEntry] = []
        level_depth = max(1, min(int(depth), 50))
        for i in range(1, level_depth + 1):
            q = Decimal(str(round(0.5 + _stable_noise(symbol, i, "q"), 4)))
            bids.append(
                OrderBookEntry(
                    price=Decimal(str(round(last - tick * i, 8))),
                    quantity=q,
                )
            )
            asks.append(
                OrderBookEntry(
                    price=Decimal(str(round(last + tick * i, 8))),
                    quantity=Decimal(str(round(0.5 + _stable_noise(symbol, i, "a"), 4))),
                )
            )
        return OrderBook(symbol=symbol, exchange=self.exchange, bids=bids, asks=asks)

    # ------------------------------------------------------------- account
    async def get_account_balance(self) -> dict[str, AccountBalance]:
        """Paper-баланс симулятора (не путать с реальным биржевым)."""
        return {
            "USDT": AccountBalance(
                account_id="simulated", exchange=self.exchange, asset="USDT",
                free=Decimal("10000"), locked=Decimal("0"),
                total=Decimal("10000"),
            )
        }

    async def get_funding_balance(self) -> dict[str, AccountBalance]:
        return {}

    async def get_instruments(self) -> list[dict[str, Any]]:
        return []

    # -------------------------------------------------------------- orders
    # Симулятор не исполняет ордера: сделки совершает PaperBroker.
    async def place_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("SimulatedExchange не исполняет ордера (paper-only)")
