"""
ASTRA BOT - Enhanced Market Regime Engine

Расширенный классификатор режима рынка (ТЗ Пункты 6, 30)

Минимальные режимы:
- TREND_UP
- TREND_DOWN
- RANGE
- HIGH_VOLATILITY
- LOW_VOLATILITY
- BREAKOUT
- BREAKDOWN
- REVERSAL
- PANIC
- EUPHORIA
- ILLIQUID
- EVENT_DRIVEN

Режим определяется по:
- volatility
- trend strength
- ATR
- ADX
- volume
- correlation
- funding
- open interest
- market breadth
- order book
- momentum
- realized volatility
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np

from ...core import models, utils

logger = logging.getLogger(__name__)


class MarketRegime(str, Enum):
    """Режимы рынка"""
    # Основные режимы
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"
    REVERSAL = "REVERSAL"
    
    # Экстремальные режимы
    PANIC = "PANIC"
    EUPHORIA = "EUPHORIA"
    ILLIQUID = "ILLIQUID"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    
    # Неопределённый
    UNKNOWN = "UNKNOWN"
    
    @property
    def is_trend(self) -> bool:
        """Является ли режимом тренда"""
        return self in [MarketRegime.TREND_UP, MarketRegime.TREND_DOWN]
    
    @property
    def is_range(self) -> bool:
        """Является ли режимом боковика"""
        return self == MarketRegime.RANGE
    
    @property
    def is_volatile(self) -> bool:
        """Является ли режимом высокой волатильности"""
        return self in [MarketRegime.HIGH_VOLATILITY, MarketRegime.PANIC]
    
    @property
    def is_extreme(self) -> bool:
        """Является ли экстремальным режимом"""
        return self in [MarketRegime.PANIC, MarketRegime.EUPHORIA, MarketRegime.ILLIQUID]


@dataclass
class TrendIndicators:
    """Индикаторы тренда"""
    # EMA
    ema_fast: float | None = None
    ema_mid: float | None = None
    ema_slow: float | None = None
    
    # EMA trend
    ema_trend_up: bool = False
    ema_trend_down: bool = False
    ema_trend_strength: float = 0.0
    
    # ADX
    adx: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    adx_trend_strength: float = 0.0
    
    # MACD
    macd_line: float | None = None
    signal_line: float | None = None
    histogram: float | None = None
    macd_trend: str = "neutral"  # up/down/neutral
    
    # Momentum
    momentum: float | None = None
    momentum_direction: str = "neutral"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "ema_fast": self.ema_fast,
            "ema_mid": self.ema_mid,
            "ema_slow": self.ema_slow,
            "ema_trend_up": self.ema_trend_up,
            "ema_trend_down": self.ema_trend_down,
            "ema_trend_strength": self.ema_trend_strength,
            "adx": self.adx,
            "plus_di": self.plus_di,
            "minus_di": self.minus_di,
            "adx_trend_strength": self.adx_trend_strength,
            "macd_line": self.macd_line,
            "signal_line": self.signal_line,
            "histogram": self.histogram,
            "macd_trend": self.macd_trend,
            "momentum": self.momentum,
            "momentum_direction": self.momentum_direction,
        }


@dataclass
class VolatilityIndicators:
    """Индикаторы волатильности"""
    atr: float | None = None
    atr_percent: float = 0.0
    atr_trend: str = "neutral"  # increasing/decreasing/stable
    
    # Bollinger Bands
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    bb_width: float = 0.0
    bb_position: float = 0.0  # -1 до 1
    
    # Standard Deviation
    std_20: float | None = None
    std_50: float | None = None
    std_100: float | None = None
    
    # Realized Volatility
    realized_volatility: float = 0.0
    volatility_percentile: float = 0.0  # Перцентиль волатильности
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "atr": self.atr,
            "atr_percent": self.atr_percent,
            "atr_trend": self.atr_trend,
            "bb_upper": self.bb_upper,
            "bb_middle": self.bb_middle,
            "bb_lower": self.bb_lower,
            "bb_width": self.bb_width,
            "bb_position": self.bb_position,
            "std_20": self.std_20,
            "std_50": self.std_50,
            "std_100": self.std_100,
            "realized_volatility": self.realized_volatility,
            "volatility_percentile": self.volatility_percentile,
        }


@dataclass
class VolumeIndicators:
    """Индикаторы объема"""
    volume: float = 0.0
    volume_sma_20: float = 0.0
    volume_sma_50: float = 0.0
    volume_ratio: float = 1.0
    volume_zscore: float = 0.0
    
    # On-Balance Volume
    obv: float | None = None
    obv_trend: str = "neutral"
    
    # Volume Profile
    volume_at_bid: float = 0.0
    volume_at_ask: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "volume": self.volume,
            "volume_sma_20": self.volume_sma_20,
            "volume_sma_50": self.volume_sma_50,
            "volume_ratio": self.volume_ratio,
            "volume_zscore": self.volume_zscore,
            "obv": self.obv,
            "obv_trend": self.obv_trend,
            "volume_at_bid": self.volume_at_bid,
            "volume_at_ask": self.volume_at_ask,
        }


@dataclass
class MarketBreadthIndicators:
    """Индикаторы ширины рынка"""
    advancing_issues: int = 0
    declining_issues: int = 0
    unchanged_issues: int = 0
    
    advance_decline_ratio: float = 1.0
    advance_decline_line: float = 0.0
    
    new_highs: int = 0
    new_lows: int = 0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "advancing_issues": self.advancing_issues,
            "declining_issues": self.declining_issues,
            "unchanged_issues": self.unchanged_issues,
            "advance_decline_ratio": self.advance_decline_ratio,
            "advance_decline_line": self.advance_decline_line,
            "new_highs": self.new_highs,
            "new_lows": self.new_lows,
        }


@dataclass
class OrderBookIndicators:
    """Индикаторы стакана"""
    spread: float = 0.0
    spread_pct: float = 0.0
    spread_trend: str = "neutral"
    
    order_book_imbalance: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    total_depth: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "spread_trend": self.spread_trend,
            "order_book_imbalance": self.order_book_imbalance,
            "bid_depth": self.bid_depth,
            "ask_depth": self.ask_depth,
            "total_depth": self.total_depth,
        }


@dataclass
class FundingIndicators:
    """Индикаторы фандинга (для фьючерсов)"""
    funding_rate: float | None = None
    funding_rate_8h: float | None = None
    next_funding_time: datetime | None = None
    funding_trend: str = "neutral"
    
    # Open Interest
    open_interest: float | None = None
    oi_change: float = 0.0
    oi_trend: str = "neutral"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "funding_rate": self.funding_rate,
            "funding_rate_8h": self.funding_rate_8h,
            "next_funding_time": self.next_funding_time.isoformat() if self.next_funding_time else None,
            "funding_trend": self.funding_trend,
            "open_interest": self.open_interest,
            "oi_change": self.oi_change,
            "oi_trend": self.oi_trend,
        }


@dataclass
class RegimeAnalysis:
    """Полный анализ режима"""
    symbol: str
    timestamp: datetime
    regime: MarketRegime
    confidence: float  # 0-1
    
    # Индикаторы
    trend: TrendIndicators
    volatility: VolatilityIndicators
    volume: VolumeIndicators
    breadth: MarketBreadthIndicators
    order_book: OrderBookIndicators
    funding: FundingIndicators
    
    # Дополнительная информация
    regime_history: list[MarketRegime] = field(default_factory=list)
    regime_stability: float = 0.0  # Стабильность текущего режима
    transition_probability: float = 0.0  # Вероятность смены режима
    
    # Статистика по режиму
    regime_statistics: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "regime": self.regime.value,
            "confidence": self.confidence,
            "trend": self.trend.to_dict(),
            "volatility": self.volatility.to_dict(),
            "volume": self.volume.to_dict(),
            "breadth": self.breadth.to_dict(),
            "order_book": self.order_book.to_dict(),
            "funding": self.funding.to_dict(),
            "regime_stability": self.regime_stability,
            "transition_probability": self.transition_probability,
            "regime_statistics": self.regime_statistics,
        }


class MarketRegimeEngine:
    """
    Расширенный движок классификации режима рынка.
    
    Определяет текущий режим на основе множества индикаторов
    и предоставляет детальный анализ.
    """
    
    def __init__(self):
        # Пороги для классификации
        self.thresholds = {
            # Trend
            "ema_trend_up": 0.01,  # EMA20 > EMA50 > EMA200
            "ema_trend_down": -0.01,  # EMA20 < EMA50 < EMA200
            "adx_strong_trend": 25.0,
            "adx_very_strong_trend": 40.0,
            
            # Volatility
            "high_volatility_atr_pct": 4.0,  # ATR/price > 4%
            "low_volatility_atr_pct": 1.0,  # ATR/price < 1%
            "bb_width_high": 0.1,  # Широкие BB
            "bb_width_low": 0.02,  # Узкие BB
            
            # Volume
            "high_volume_ratio": 2.0,
            "low_volume_ratio": 0.5,
            
            # Spread
            "high_spread_pct": 0.1,  # Spread > 0.1%
            "illiquid_spread_pct": 0.5,  # Spread > 0.5%
            
            # Funding
            "extreme_funding_rate": 0.05,  # |funding| > 0.05%
            
            # Regime stability
            "min_stability": 0.7,
        }
        
        # История режимов
        self._regime_history: dict[str, list[tuple[datetime, MarketRegime]]] = {}
        
        # Статистика по режимам
        self._regime_statistics: dict[str, dict[str, Any]] = {}
    
    def calculate_trend_indicators(
        self,
        candles: list[models.Candle],
    ) -> TrendIndicators:
        """
        Рассчитать индикаторы тренда.
        
        Args:
            candles: Список свечей
        
        Returns:
            Индикаторы тренда
        """
        if not candles:
            return TrendIndicators()
        
        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        
        indicators = TrendIndicators()
        
        # EMA
        if len(closes) >= 20:
            indicators.ema_fast = float(utils.simple_moving_average(closes[-20:], 20))
        if len(closes) >= 50:
            indicators.ema_mid = float(utils.simple_moving_average(closes[-50:], 50))
        if len(closes) >= 200:
            indicators.ema_slow = float(utils.simple_moving_average(closes[-200:], 200))
        
        # EMA Trend
        if indicators.ema_fast and indicators.ema_mid:
            if indicators.ema_fast > indicators.ema_mid:
                indicators.ema_trend_up = True
                indicators.ema_trend_strength = min(1.0, (indicators.ema_fast - indicators.ema_mid) / indicators.ema_mid)
            else:
                indicators.ema_trend_down = True
                indicators.ema_trend_strength = min(1.0, (indicators.ema_mid - indicators.ema_fast) / indicators.ema_mid)
        
        # ADX
        if len(highs) >= 15 and len(lows) >= 15 and len(closes) >= 15:
            indicators.adx = float(utils.calculate_adx(highs, lows, closes, period=14))
            plus_di, minus_di = utils.calculate_plus_minus_di(highs, lows, closes, period=14)
            indicators.plus_di = plus_di
            indicators.minus_di = minus_di
            
            if indicators.adx:
                indicators.adx_trend_strength = min(1.0, indicators.adx / 100)
        
        # Momentum
        if len(closes) >= 10:
            momentum = (closes[-1] - closes[-10]) / closes[-10] * 100
            indicators.momentum = momentum
            if momentum > 0:
                indicators.momentum_direction = "up"
            elif momentum < 0:
                indicators.momentum_direction = "down"
        
        return indicators
    
    def calculate_volatility_indicators(
        self,
        candles: list[models.Candle],
    ) -> VolatilityIndicators:
        """
        Рассчитать индикаторы волатильности.
        
        Args:
            candles: Список свечей
        
        Returns:
            Индикаторы волатильности
        """
        if not candles:
            return VolatilityIndicators()
        
        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        
        indicators = VolatilityIndicators()
        
        # ATR
        if len(highs) >= 15 and len(lows) >= 15 and len(closes) >= 15:
            indicators.atr = float(utils.calculate_atr(highs, lows, closes, period=14))
            if closes[-1] > 0:
                indicators.atr_percent = (indicators.atr / closes[-1]) * 100
        
        # Bollinger Bands
        if len(closes) >= 20:
            bb = utils.calculate_bollinger_bands(closes, period=20, std_dev=2)
            if bb:
                indicators.bb_upper = bb.get("upper")
                indicators.bb_middle = bb.get("middle")
                indicators.bb_lower = bb.get("lower")
                indicators.bb_width = bb.get("bandwidth", 0)
                if indicators.bb_upper and indicators.bb_lower:
                    current_price = closes[-1]
                    indicators.bb_position = ((current_price - indicators.bb_lower) / 
                                           (indicators.bb_upper - indicators.bb_lower) * 2 - 1)
        
        # Standard Deviation
        if len(closes) >= 20:
            indicators.std_20 = float(np.std(closes[-20:]))
        if len(closes) >= 50:
            indicators.std_50 = float(np.std(closes[-50:]))
        if len(closes) >= 100:
            indicators.std_100 = float(np.std(closes[-100:]))
        
        # Volatility percentile
        if indicators.atr and indicators.atr > 0:
            # Сравнить с исторической волатильностью
            if len(closes) >= 100:
                historical_atr = float(utils.calculate_atr(highs[-100:], lows[-100:], closes[-100:], period=14))
                if historical_atr > 0:
                    indicators.volatility_percentile = min(100.0, (indicators.atr / historical_atr) * 100)
        
        return indicators
    
    def calculate_volume_indicators(
        self,
        candles: list[models.Candle],
    ) -> VolumeIndicators:
        """
        Рассчитать индикаторы объема.
        
        Args:
            candles: Список свечей
        
        Returns:
            Индикаторы объема
        """
        if not candles:
            return VolumeIndicators()
        
        volumes = [float(c.volume) for c in candles if hasattr(c, 'volume') and c.volume is not None]
        closes = [float(c.close) for c in candles]
        
        indicators = VolumeIndicators()
        
        if volumes:
            indicators.volume = volumes[-1]
            
            if len(volumes) >= 20:
                indicators.volume_sma_20 = float(utils.simple_moving_average(volumes[-20:], 20))
                if indicators.volume_sma_20 > 0:
                    indicators.volume_ratio = volumes[-1] / indicators.volume_sma_20
                    avg_volume = indicators.volume_sma_20
                    std_volume = float(np.std(volumes[-20:])) if len(volumes) >= 20 else 0
                    if std_volume > 0:
                        indicators.volume_zscore = (volumes[-1] - avg_volume) / std_volume
            
            if len(volumes) >= 50:
                indicators.volume_sma_50 = float(utils.simple_moving_average(volumes[-50:], 50))
        
        # OBV
        if len(volumes) >= 2 and len(closes) >= 2:
            obv = 0
            for i in range(1, len(closes)):
                if closes[i] > closes[i-1]:
                    obv += volumes[i]
                elif closes[i] < closes[i-1]:
                    obv -= volumes[i]
            indicators.obv = obv
            if len(closes) >= 20:
                # Trend OBV
                obv_values = []
                current_obv = 0
                for i in range(1, len(closes)):
                    if closes[i] > closes[i-1]:
                        current_obv += volumes[i]
                    elif closes[i] < closes[i-1]:
                        current_obv -= volumes[i]
                    obv_values.append(current_obv)
                if len(obv_values) >= 2:
                    if obv_values[-1] > obv_values[0]:
                        indicators.obv_trend = "up"
                    elif obv_values[-1] < obv_values[0]:
                        indicators.obv_trend = "down"
        
        return indicators
    
    def calculate_order_book_indicators(
        self,
        orderbook: models.OrderBook | None = None,
        spread: float | None = None,
        spread_pct: float | None = None,
    ) -> OrderBookIndicators:
        """
        Рассчитать индикаторы стакана.
        
        Args:
            orderbook: Объект стакана
            spread: Spread (если известен)
            spread_pct: Spread в % (если известен)
        
        Returns:
            Индикаторы стакана
        """
        indicators = OrderBookIndicators()
        
        if orderbook:
            if orderbook.best_bid and orderbook.best_ask:
                indicators.best_bid = float(orderbook.best_bid)
                indicators.best_ask = float(orderbook.best_ask)
                indicators.spread = float(orderbook.best_ask - orderbook.best_bid)
            
            if orderbook.bids:
                indicators.bid_depth = sum(float(b.quantity) for b in orderbook.bids)
            if orderbook.asks:
                indicators.ask_depth = sum(float(a.quantity) for a in orderbook.asks)
            
            indicators.total_depth = indicators.bid_depth + indicators.ask_depth
            
            if indicators.total_depth > 0:
                indicators.order_book_imbalance = (indicators.bid_depth - indicators.ask_depth) / indicators.total_depth
        
        if spread is not None:
            indicators.spread = spread
        if spread_pct is not None:
            indicators.spread_pct = spread_pct
        
        return indicators
    
    def calculate_funding_indicators(
        self,
        funding_rate: float | None = None,
        funding_rate_8h: float | None = None,
        next_funding_time: datetime | None = None,
        open_interest: float | None = None,
        oi_change: float = 0.0,
    ) -> FundingIndicators:
        """
        Рассчитать индикаторы фандинга.
        
        Args:
            funding_rate: Текущая ставка фандинга
            funding_rate_8h: Ставка фандинга за 8 часов
            next_funding_time: Время следующего фандинга
            open_interest: Открытый интерес
            oi_change: Изменение открытого интереса
        
        Returns:
            Индикаторы фандинга
        """
        indicators = FundingIndicators(
            funding_rate=funding_rate,
            funding_rate_8h=funding_rate_8h,
            next_funding_time=next_funding_time,
            open_interest=open_interest,
            oi_change=oi_change,
        )
        
        if funding_rate is not None:
            if funding_rate > 0:
                indicators.funding_trend = "positive"
            elif funding_rate < 0:
                indicators.funding_trend = "negative"
        
        if open_interest is not None and oi_change != 0:
            if oi_change > 0:
                indicators.oi_trend = "increasing"
            else:
                indicators.oi_trend = "decreasing"
        
        return indicators
    
    def classify_regime(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook: models.OrderBook | None = None,
        funding_rate: float | None = None,
        open_interest: float | None = None,
        cross_market_data: dict[str, Any] | None = None,
    ) -> RegimeAnalysis:
        """
        Классифицировать режим рынка.
        
        Args:
            symbol: Символ
            candles: Список свечей
            orderbook: Объект стакана
            funding_rate: Ставка фандинга
            open_interest: Открытый интерес
            cross_market_data: Данные по другим рынкам
        
        Returns:
            Полный анализ режима
        """
        timestamp = datetime.now(timezone.utc)
        
        # Рассчитать индикаторы
        trend = self.calculate_trend_indicators(candles)
        volatility = self.calculate_volatility_indicators(candles)
        volume = self.calculate_volume_indicators(candles)
        order_book = self.calculate_order_book_indicators(orderbook)
        funding = self.calculate_funding_indicators(funding_rate, open_interest=open_interest)
        
        # Определить режим
        regime, confidence = self._determine_regime(
            symbol, trend, volatility, volume, order_book, funding
        )
        
        # Обновить историю режимов
        if symbol not in self._regime_history:
            self._regime_history[symbol] = []
        self._regime_history[symbol].append((timestamp, regime))
        
        # Ограничить историю
        if len(self._regime_history[symbol]) > 1000:
            self._regime_history[symbol] = self._regime_history[symbol][-1000:]
        
        # Рассчитать стабильность режима
        regime_stability = self._calculate_regime_stability(symbol, regime)
        
        # Рассчитать вероятность перехода
        transition_probability = self._calculate_transition_probability(symbol, regime)
        
        # Получить статистику по режиму
        regime_statistics = self._get_regime_statistics(symbol, regime)
        
        analysis = RegimeAnalysis(
            symbol=symbol,
            timestamp=timestamp,
            regime=regime,
            confidence=confidence,
            trend=trend,
            volatility=volatility,
            volume=volume,
            breadth=MarketBreadthIndicators(),  # Пока не реализовано
            order_book=order_book,
            funding=funding,
            regime_history=[r.value for _, r in self._regime_history.get(symbol, [])],
            regime_stability=regime_stability,
            transition_probability=transition_probability,
            regime_statistics=regime_statistics,
        )
        
        return analysis
    
    def _determine_regime(
        self,
        symbol: str,
        trend: TrendIndicators,
        volatility: VolatilityIndicators,
        volume: VolumeIndicators,
        order_book: OrderBookIndicators,
        funding: FundingIndicators,
    ) -> tuple[MarketRegime, float]:
        """
        Определить режим на основе индикаторов.
        
        Args:
            symbol: Символ
            trend: Индикаторы тренда
            volatility: Индикаторы волатильности
            volume: Индикаторы объема
            order_book: Индикаторы стакана
            funding: Индикаторы фандинга
        
        Returns:
            Режим и уверенность
        """
        confidence = 0.5
        
        # 1. Проверка паники (самый высокий приоритет)
        if self._is_panic(trend, volatility, volume, order_book):
            return MarketRegime.PANIC, 0.9
        
        # 2. Проверка эйфории
        if self._is_euphoria(trend, volatility, volume, funding):
            return MarketRegime.EUPHORIA, 0.85
        
        # 3. Проверка илликвидности
        if self._is_illiquid(order_book):
            return MarketRegime.ILLIQUID, 0.8
        
        # 4. Проверка высокой волатильности
        if self._is_high_volatility(volatility):
            return MarketRegime.HIGH_VOLATILITY, 0.8
        
        # 5. Проверка низкой волатильности
        if self._is_low_volatility(volatility):
            return MarketRegime.LOW_VOLATILITY, 0.75
        
        # 6. Проверка breakout
        if self._is_breakout(trend, volatility, volume):
            return MarketRegime.BREAKOUT, 0.75
        
        # 7. Проверка breakdown
        if self._is_breakdown(trend, volatility, volume):
            return MarketRegime.BREAKDOWN, 0.75
        
        # 8. Проверка тренда вверх
        if self._is_trend_up(trend, volatility):
            return MarketRegime.TREND_UP, 0.7
        
        # 9. Проверка тренда вниз
        if self._is_trend_down(trend, volatility):
            return MarketRegime.TREND_DOWN, 0.7
        
        # 10. Проверка боковика
        if self._is_range(trend, volatility):
            return MarketRegime.RANGE, 0.65
        
        # 11. Проверка разворота
        if self._is_reversal(trend, volatility):
            return MarketRegime.REVERSAL, 0.6
        
        # 12. По умолчанию - неизвестный режим
        return MarketRegime.UNKNOWN, 0.3
    
    def _is_panic(
        self,
        trend: TrendIndicators,
        volatility: VolatilityIndicators,
        volume: VolumeIndicators,
        order_book: OrderBookIndicators,
    ) -> bool:
        """Проверить панику"""
        # Резкое падение цены
        if trend.momentum and trend.momentum < -5:  # Падение более 5%
            # Высокий объём
            if volume.volume_ratio and volume.volume_ratio > 3:
                # Расширение spread
                if order_book.spread_pct and order_book.spread_pct > self.thresholds["high_spread_pct"]:
                    return True
        
        # Экстремальный spread
        if order_book.spread_pct and order_book.spread_pct > self.thresholds["illiquid_spread_pct"]:
            return True
        
        return False
    
    def _is_euphoria(
        self,
        trend: TrendIndicators,
        volatility: VolatilityIndicators,
        volume: VolumeIndicators,
        funding: FundingIndicators,
    ) -> bool:
        """Проверить эйфорию"""
        # Резкий рост цены
        if trend.momentum and trend.momentum > 5:  # Рост более 5%
            # Высокий объём
            if volume.volume_ratio and volume.volume_ratio > 3:
                # Экстремальный положительный фандинг
                if funding.funding_rate and funding.funding_rate > self.thresholds["extreme_funding_rate"]:
                    return True
        
        return False
    
    def _is_illiquid(self, order_book: OrderBookIndicators) -> bool:
        """Проверить илликвидность"""
        if order_book.spread_pct and order_book.spread_pct > self.thresholds["illiquid_spread_pct"]:
            return True
        if order_book.total_depth and order_book.total_depth < 1000:  # Малая глубина
            return True
        return False
    
    def _is_high_volatility(self, volatility: VolatilityIndicators) -> bool:
        """Проверить высокую волатильность"""
        if volatility.atr_percent and volatility.atr_percent > self.thresholds["high_volatility_atr_pct"]:
            return True
        if volatility.bb_width and volatility.bb_width > self.thresholds["bb_width_high"]:
            return True
        if volatility.volatility_percentile and volatility.volatility_percentile > 80:
            return True
        return False
    
    def _is_low_volatility(self, volatility: VolatilityIndicators) -> bool:
        """Проверить низкую волатильность"""
        if volatility.atr_percent and volatility.atr_percent < self.thresholds["low_volatility_atr_pct"]:
            return True
        if volatility.bb_width and volatility.bb_width < self.thresholds["bb_width_low"]:
            return True
        if volatility.volatility_percentile and volatility.volatility_percentile < 20:
            return True
        return False
    
    def _is_breakout(self, trend: TrendIndicators, volatility: VolatilityIndicators, volume: VolumeIndicators) -> bool:
        """Проверить breakout"""
        # Цена выше верхней BB
        if volatility.bb_position and volatility.bb_position > 0.9:
            # Высокий объём
            if volume.volume_ratio and volume.volume_ratio > self.thresholds["high_volume_ratio"]:
                # Сильный тренд
                if trend.adx and trend.adx > self.thresholds["adx_strong_trend"]:
                    return True
        return False
    
    def _is_breakdown(self, trend: TrendIndicators, volatility: VolatilityIndicators, volume: VolumeIndicators) -> bool:
        """Проверить breakdown"""
        # Цена ниже нижней BB
        if volatility.bb_position and volatility.bb_position < -0.9:
            # Высокий объём
            if volume.volume_ratio and volume.volume_ratio > self.thresholds["high_volume_ratio"]:
                # Сильный тренд
                if trend.adx and trend.adx > self.thresholds["adx_strong_trend"]:
                    return True
        return False
    
    def _is_trend_up(self, trend: TrendIndicators, volatility: VolatilityIndicators) -> bool:
        """Проверить тренд вверх"""
        if trend.ema_trend_up and trend.adx and trend.adx > self.thresholds["adx_strong_trend"]:
            return True
        if trend.plus_di and trend.minus_di:
            if trend.plus_di > trend.minus_di and trend.adx and trend.adx > self.thresholds["adx_strong_trend"]:
                return True
        return False
    
    def _is_trend_down(self, trend: TrendIndicators, volatility: VolatilityIndicators) -> bool:
        """Проверить тренд вниз"""
        if trend.ema_trend_down and trend.adx and trend.adx > self.thresholds["adx_strong_trend"]:
            return True
        if trend.plus_di and trend.minus_di:
            if trend.minus_di > trend.plus_di and trend.adx and trend.adx > self.thresholds["adx_strong_trend"]:
                return True
        return False
    
    def _is_range(self, trend: TrendIndicators, volatility: VolatilityIndicators) -> bool:
        """Проверить боковик"""
        # Слабый тренд
        if not trend.ema_trend_up and not trend.ema_trend_down:
            # Низкая волатильность
            if volatility.atr_percent and volatility.atr_percent < self.thresholds["low_volatility_atr_pct"]:
                # Узкие BB
                if volatility.bb_width and volatility.bb_width < self.thresholds["bb_width_low"]:
                    return True
        return False
    
    def _is_reversal(self, trend: TrendIndicators, volatility: VolatilityIndicators) -> bool:
        """Проверить разворот"""
        # Изменение направления тренда
        if trend.momentum_direction == "up" and trend.momentum and trend.momentum < 0:
            return True
        if trend.momentum_direction == "down" and trend.momentum and trend.momentum > 0:
            return True
        return False
    
    def _calculate_regime_stability(self, symbol: str, current_regime: MarketRegime) -> float:
        """
        Рассчитать стабильность текущего режима.
        
        Args:
            symbol: Символ
            current_regime: Текущий режим
        
        Returns:
            Стабильность (0-1)
        """
        if symbol not in self._regime_history or len(self._regime_history[symbol]) < 2:
            return 0.5
        
        history = self._regime_history[symbol][-20:]  # Последние 20 записей
        same_regime_count = sum(1 for _, r in history if r == current_regime)
        
        return same_regime_count / len(history)
    
    def _calculate_transition_probability(self, symbol: str, current_regime: MarketRegime) -> float:
        """
        Рассчитать вероятность смены режима.
        
        Args:
            symbol: Символ
            current_regime: Текущий режим
        
        Returns:
            Вероятность смены (0-1)
        """
        stability = self._calculate_regime_stability(symbol, current_regime)
        return 1.0 - stability
    
    def _get_regime_statistics(self, symbol: str, regime: MarketRegime) -> dict[str, Any]:
        """
        Получить статистику по режиму.
        
        Args:
            symbol: Символ
            regime: Режим
        
        Returns:
            Статистика по режиму
        """
        key = f"{symbol}_{regime.value}"
        if key not in self._regime_statistics:
            self._regime_statistics[key] = {
                "count": 0,
                "duration": [],
                "returns": [],
                "volatility": [],
            }
        
        return self._regime_statistics[key]


# Глобальный экземпляр
_market_regime_engine: MarketRegimeEngine | None = None


def get_market_regime_engine() -> MarketRegimeEngine:
    """Получить глобальный Market Regime Engine"""
    global _market_regime_engine
    if _market_regime_engine is None:
        _market_regime_engine = MarketRegimeEngine()
    return _market_regime_engine


def reset_market_regime_engine():
    """Сбросить Market Regime Engine (для тестов)"""
    global _market_regime_engine
    _market_regime_engine = MarketRegimeEngine()
