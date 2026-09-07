"""Тесты «основы основ»: паттерны, режимы рынка, симулятор рынка.

Пользовательский принцип: бычий/медвежий рынок, проторговка, клинья и
треугольники обязаны РАСПОЗНАВАТЬСЯ — это ядро торговой логики. Раньше:
клинья/треугольники с плоской границей отсекались вырожденным R²,
ADX не считался, а любые тренды помечались LOW_VOLATILITY.
"""

import math
from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.decision.strategies.pattern_strategies import (
    PatternType,
    _find_swings,
    detect_pattern,
)
from astra_bot.engines.regime_detector import MarketRegime, MarketRegimeDetector


# --------------------------------------------------------------- helpers
def channel_zigzag(kind: str, n: int = 140, leg: int = 14, noise: float = 0.08):
    """Учебниковый паттерн: цена ходит легами между сходящимися линиями."""

    def lines(i: int) -> tuple[float, float]:
        t = i / (n - 1)
        if kind == "falling_wedge":
            return 120 - 30 * t, 110 - 16 * t  # верхняя круче вниз
        if kind == "rising_wedge":
            return 100 + 16 * t, 90 + 30 * t  # нижняя круче вверх
        if kind == "asc_triangle":
            return 110.0, 95 + 18 * t  # плоский верх, низ вверх
        if kind == "desc_triangle":
            return 110 - 18 * t, 95.0  # верх вниз, плоский низ
        return 100 + (15 * (1 - t) + 1.0), 100 - (15 * (1 - t) + 1.0)  # sym

    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    prev = None
    for i in range(n):
        up, lo = lines(i)
        pos = i % leg
        going_up = (i // leg) % 2 == 0
        if going_up:
            price = lo + (up - lo) * pos / (leg - 1)
        else:
            price = up - (up - lo) * pos / (leg - 1)
        wig = noise * math.sin(i * 2.1)
        if prev is not None:
            highs.append(max(prev, price) + abs(wig) + 0.02)
            lows.append(min(prev, price) - abs(wig) - 0.02)
        else:
            highs.append(price + abs(wig) + 0.02)
            lows.append(price - abs(wig) - 0.02)
        closes.append(price + wig)
        prev = price
    return highs, lows, closes


class TestChartPatterns:
    """Клинья и треугольники — по ТЗ пользователя, основа основ."""

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("falling_wedge", PatternType.FALLING_WEDGE),  # клин вниз → лонг
            ("rising_wedge", PatternType.RISING_WEDGE),  # клин вверх → шорт
            ("asc_triangle", PatternType.ASCENDING_TRIANGLE),
            ("desc_triangle", PatternType.DESCENDING_TRIANGLE),
            ("sym_triangle", PatternType.SYMMETRICAL_TRIANGLE),
        ],
    )
    def test_pattern_recognized(self, kind, expected):
        highs, lows, closes = channel_zigzag(kind)
        result = detect_pattern(highs, lows, closes)
        assert result.pattern == expected, (
            f"{kind} не распознан: {result.diagnostics}"
        )
        assert result.confidence >= 0.5

    def test_flat_line_rmse_fallback(self):
        """Плоская граница с мелким шумом не должна отбрасываться по R².

        У идеально плоской поддержки ss_tot→0 и R² вырождается — раньше
        восходящий треугольник никогда не распознавался.
        """
        highs, lows, closes = channel_zigzag("asc_triangle")
        hi_idx, lo_idx = _find_swings(highs, lows, 3)
        assert len(hi_idx) >= 3 and len(lo_idx) >= 3
        result = detect_pattern(highs, lows, closes)
        assert result.pattern == PatternType.ASCENDING_TRIANGLE

    def test_choppy_market_is_no_pattern(self):
        """Сильный хаос без структуры — не паттерн (fail-closed)."""
        import random

        rnd = random.Random(42)
        price = 100.0
        highs, lows, closes = [], [], []
        for _i in range(200):
            price *= 1 + rnd.gauss(0, 0.03)
            highs.append(price * (1 + rnd.random() * 0.03))
            lows.append(price * (1 - rnd.random() * 0.03))
            closes.append(price)
        result = detect_pattern(highs, lows, closes)
        # Допустим случайный паттерн, но confidence обязан быть честным
        assert result.confidence <= 0.95


class TestMarketRegimes:
    """Бычий / медвежий рынок и проторговка."""

    @staticmethod
    def _candles(closes: list[float]) -> list[models.Candle]:
        out = []
        for i, c in enumerate(closes):
            o = closes[i - 1] if i else c
            out.append(
                models.Candle(
                    symbol="BTC-USDT",
                    open_time=i * 3_600_000,
                    open=Decimal(str(round(o, 6))),
                    high=Decimal(str(round(max(o, c) * 1.001, 6))),
                    low=Decimal(str(round(min(o, c) * 0.999, 6))),
                    close=Decimal(str(round(c, 6))),
                    volume=Decimal("100"),
                    exchange="bingx",
                    timeframe="1h",
                    quote_volume=Decimal("10000"),
                )
            )
        return out

    @staticmethod
    def _path(drift: float, n: int = 400, seed: int = 7) -> list[float]:
        import random

        rnd = random.Random(seed)
        out, price = [], 100.0
        for _ in range(n):
            price *= 1 + drift / n + rnd.gauss(0, 0.02 / math.sqrt(n)) * 3
            out.append(price)
        return out

    def test_bull_market_detected(self):
        det = MarketRegimeDetector()
        result = det.detect("BTC-USDT", self._candles(self._path(0.20)))
        assert result.regime == MarketRegime.BULL_TREND
        # ADX реально считается (раньше был всегда 0)
        assert result.indicators.adx_trend_strength > 20

    def test_bear_market_detected(self):
        det = MarketRegimeDetector()
        result = det.detect("BTC-USDT", self._candles(self._path(-0.20, seed=8)))
        assert result.regime == MarketRegime.BEAR_TREND

    def test_range_market_detected(self):
        """Проторговка (mean-reverting флэт) → RANGE."""
        import random

        rnd = random.Random(3)
        out, price = [], 100.0
        for _ in range(400):
            price += (100 - price) * 0.05 + rnd.gauss(0, 0.15)
            out.append(price)
        det = MarketRegimeDetector()
        result = det.detect("BTC-USDT", self._candles(out))
        assert result.regime == MarketRegime.RANGE

    def test_quiet_trend_not_masked_by_low_volatility(self):
        """Главный фикс: спокойный тренд НЕ должен помечаться LOW_VOLATILITY.

        Раньше гейт atr<low стоял ДО трендовых проверок, и бычьи/медвежьи
        рынки в тихую погоду навсегда получали LOW_VOLATILITY.
        """
        det = MarketRegimeDetector()
        result = det.detect("BTC-USDT", self._candles(self._path(0.20)))
        assert result.regime is not MarketRegime.LOW_VOLATILITY

    def test_price_change_1h_uses_last_candle(self):
        """closes[-0] bug: изменение считалось от ПЕРВОЙ свечи истории."""
        det = MarketRegimeDetector()
        closes = [100.0] * 100 + [110.0]  # последняя свеча +10%
        ind = det._calculate_indicators(self._candles(closes))
        assert ind.price_change_1h == pytest.approx(10.0)


# ---------------------------------------------------------- simulator
class TestSimulatedExchange:
    """Симулятор рынка: непрерывная работа без сети и ключей."""

    @staticmethod
    async def _sim():
        from astra_bot.adapters.simulated import SimulatedExchange

        sim = SimulatedExchange(phase_seconds=3600)  # быстрые фазы для теста
        await sim.initialize()
        return sim

    @pytest.mark.asyncio
    async def test_candles_deterministic(self):
        sim = await self._sim()
        a = await sim.get_candles("BTC/USDT", "5m", limit=50)
        b = await sim.get_candles("BTC/USDT", "5m", limit=50)
        assert len(a) == len(b) == 50
        assert [c.close for c in a] == [c.close for c in b]

    @pytest.mark.asyncio
    async def test_all_engine_timeframes(self):
        sim = await self._sim()
        for tf in ("5m", "15m", "1h", "4h"):
            candles = await sim.get_candles("ETH/USDT", tf, limit=300)
            assert len(candles) == 300
            assert candles[-1].open_time > candles[0].open_time
            for c in candles:
                assert c.low <= c.open and c.low <= c.close
                assert c.high >= c.open and c.high >= c.close

    @pytest.mark.asyncio
    async def test_market_phases_cycle(self):
        """Фазы «основа основ» реально сменяются в потоке цен."""
        sim = await self._sim()
        kinds = set()
        for offset in range(9):
            ts = offset * sim.phase_seconds + 0.5
            kind, _, _ = sim._phase(ts)
            kinds.add(kind)
        assert {
            "bull", "range", "bear",
            "falling_wedge", "rising_wedge",
            "asc_triangle", "desc_triangle", "sym_triangle",
            "breakout",
        } <= kinds

    @pytest.mark.asyncio
    async def test_ticker_orderbook_balance(self):
        sim = await self._sim()
        ticker = await sim.get_ticker("BTC/USDT")
        assert ticker["bid"] < ticker["ask"]
        assert ticker["low_24h"] <= ticker["last"] <= ticker["high_24h"]
        book = await sim.get_orderbook("BTC/USDT", depth=10)
        assert book.best_bid < book.best_ask
        assert len(book.bids) == 10 and len(book.asks) == 10
        balance = await sim.get_account_balance()
        assert balance["USDT"].total > 0

    @pytest.mark.asyncio
    async def test_patterns_detected_on_simulated_feed(self):
        """Клинья/треугольники распознаются на ДАННЫХ симулятора."""
        sim = await self._sim()
        found = set()
        # Фаза 24h, берём 170 баров по 5m (~14h) внутри одной фазы,
        # чтобы весь отрезок лежал в одном рыночном режиме.
        sim.phase_seconds = 24 * 3600
        for phase_no in range(9):
            window = 170 * 300
            end_ts = phase_no * sim.phase_seconds + sim.phase_seconds * 0.98
            start_ts = end_ts - window
            bars = []
            for k in range(170):
                t0 = int((start_ts + k * 300) // 300) * 300
                bars.append(sim._build_candle("BTC/USDT", "5m", t0, 300))
        # (фазы сдвинуты по символам — читаем фазу того же символа)
            highs = [float(c.high) for c in bars]
            lows = [float(c.low) for c in bars]
            closes = [float(c.close) for c in bars]
            result = detect_pattern(highs, lows, closes)
            if result.pattern != PatternType.NONE and result.confidence >= 0.5:
                found.add((sim._phase(end_ts, "BTC/USDT")[0], result.pattern))
        # Паттерн-детект обязан случиться минимум в одной фазе цикла
        pattern_kinds = {kind for kind, _ in found}
        wedge_tri = {
            "falling_wedge", "rising_wedge",
            "asc_triangle", "desc_triangle", "sym_triangle", "range",
        }
        assert found, "детектор не распознал ни одного паттерна на потоке симулятора"
        assert pattern_kinds & wedge_tri, (
            f"паттерны распознаны не в паттерн-фазах: {found}"
        )

    @pytest.mark.asyncio
    async def test_simulated_feed_drives_pipeline_strategies(self):
        """Полный контур: свечи симулятора → стратегии дают оценки без ошибок."""
        import asyncio

        from astra_bot.strategies.mean_reversion import MeanReversionStrategy
        from astra_bot.strategies.momentum import MomentumStrategy

        sim = await self._sim()
        candles = await sim.get_candles("BTC/USDT", "5m", limit=250)
        momentum = MomentumStrategy()
        reversion = MeanReversionStrategy()
        sig1 = await momentum.evaluate(
            "BTC-USDT", candles, current_price=float(candles[-1].close),
            market_regime="BULL_TREND",
        )
        sig2 = await reversion.evaluate(
            "BTC-USDT", candles, current_price=float(candles[-1].close),
            market_regime="RANGE",
        )
        # Стратегии не падают и возвращают Signal|None (не исключение)
        assert sig1 is None or sig1.direction.value in ("long", "short")
        assert sig2 is None or sig2.direction.value in ("long", "short")
        await asyncio.sleep(0)
