"""
ASTRA BOT — Portfolio Exposure Engine

Движок расчёта экспозиции портфеля (Master Specification v2, Section 24)

Рассчитывает:
- BTC beta
- market beta
- sector exposure
- correlation exposure
- factor exposure
- gross exposure
- net exposure

Risk Engine должен видеть не только отдельную позицию, но и весь портфель.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Позиция в портфеле"""
    symbol: str
    side: str  # long/short
    quantity: float
    entry_price: float
    current_price: float
    
    @property
    def notional(self) -> float:
        """Номинал позиции"""
        return self.quantity * self.current_price
    
    @property
    def pnl(self) -> float:
        """PnL позиции"""
        if self.side == "long":
            return (self.current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.current_price) * self.quantity
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "notional": self.notional,
            "pnl": self.pnl,
        }


@dataclass
class BetaExposure:
    """Экспозиция бета"""
    symbol: str
    btc_beta: float  # Бета к BTC
    market_beta: float  # Бета к рынку
    sector_beta: dict[str, float] = field(default_factory=dict)  # Бета к секторам
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "btc_beta": self.btc_beta,
            "market_beta": self.market_beta,
            "sector_beta": self.sector_beta,
        }


@dataclass
class PortfolioExposure:
    """Экспозиция портфеля"""
    # Агрегированная экспозиция
    gross_exposure: float  # Сумма абсолютных номиналов
    net_exposure: float  # Сумма номиналов с учётом стороны
    
    # Экспозиция по символам
    symbol_exposure: dict[str, float] = field(default_factory=dict)
    
    # Экспозиция по секторам
    sector_exposure: dict[str, float] = field(default_factory=dict)
    
    # Бета экспозиция
    btc_beta: float = 0.0
    market_beta: float = 0.0
    
    # Корреляционная экспозиция
    correlation_exposure: dict[str, float] = field(default_factory=dict)
    
    # Факторная экспозиция
    factor_exposure: dict[str, float] = field(default_factory=dict)
    
    # Временная метка
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "symbol_exposure": self.symbol_exposure,
            "sector_exposure": self.sector_exposure,
            "btc_beta": self.btc_beta,
            "market_beta": self.market_beta,
            "correlation_exposure": self.correlation_exposure,
            "factor_exposure": self.factor_exposure,
            "timestamp": self.timestamp.isoformat(),
        }


class PortfolioExposureEngine:
    """
    Движок расчёта экспозиции портфеля.
    
    Рассчитывает различные виды экспозиции для управления рисками.
    """
    
    def __init__(self):
        # Дефолтные бета значения
        self.default_betas = {
            "BTC": {"btc_beta": 1.0, "market_beta": 1.0, "sector": "crypto"},
            "ETH": {"btc_beta": 1.4, "market_beta": 1.2, "sector": "crypto"},
            "SOL": {"btc_beta": 1.8, "market_beta": 1.5, "sector": "crypto"},
            "XRP": {"btc_beta": 1.3, "market_beta": 1.1, "sector": "crypto"},
            "DOGE": {"btc_beta": 1.8, "market_beta": 1.6, "sector": "crypto"},
            "TON": {"btc_beta": 1.5, "market_beta": 1.3, "sector": "crypto"},
        }
        
        # Корреляции между символами
        self.correlations = {
            ("BTC", "ETH"): 0.8,
            ("BTC", "SOL"): 0.7,
            ("BTC", "XRP"): 0.75,
            ("BTC", "DOGE"): 0.6,
            ("BTC", "TON"): 0.65,
            ("ETH", "SOL"): 0.7,
            ("ETH", "XRP"): 0.75,
            ("ETH", "DOGE"): 0.6,
            ("ETH", "TON"): 0.65,
        }
        
        # Факторы
        self.factors = {
            "BTC": {"trend": 0.5, "momentum": 0.3, "volatility": 0.2},
            "ETH": {"trend": 0.4, "momentum": 0.4, "volatility": 0.2},
            "SOL": {"trend": 0.3, "momentum": 0.5, "volatility": 0.2},
        }
    
    def calculate_position_exposure(self, position: Position) -> float:
        """
        Рассчитать экспозицию позиции.
        
        Args:
            position: Позиция
        
        Returns:
            Экспозиция позиции
        """
        return abs(position.notional)
    
    def calculate_portfolio_exposure(
        self,
        positions: list[Position]
    ) -> PortfolioExposure:
        """
        Рассчитать экспозицию портфеля.
        
        Args:
            positions: Список позиций
        
        Returns:
            PortfolioExposure
        """
        gross_exposure = 0.0
        net_exposure = 0.0
        symbol_exposure = {}
        sector_exposure = {}
        
        # Рассчитать экспозицию по символам
        for position in positions:
            position_exposure = self.calculate_position_exposure(position)
            gross_exposure += position_exposure
            
            if position.side == "long":
                net_exposure += position.notional
            else:
                net_exposure -= position.notional
            
            # Экспозиция по символам
            if position.symbol not in symbol_exposure:
                symbol_exposure[position.symbol] = 0.0
            symbol_exposure[position.symbol] += position.notional
            
            # Экспозиция по секторам
            beta_info = self.default_betas.get(position.symbol, {})
            sector = beta_info.get("sector", "unknown")
            if sector not in sector_exposure:
                sector_exposure[sector] = 0.0
            sector_exposure[sector] += position.notional
        
        # Рассчитать бета экспозицию
        btc_beta = self.calculate_btc_beta(positions)
        market_beta = self.calculate_market_beta(positions)
        
        # Рассчитать корреляционную экспозицию
        correlation_exposure = self.calculate_correlation_exposure(positions)
        
        # Рассчитать факторную экспозицию
        factor_exposure = self.calculate_factor_exposure(positions)
        
        return PortfolioExposure(
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            symbol_exposure=symbol_exposure,
            sector_exposure=sector_exposure,
            btc_beta=btc_beta,
            market_beta=market_beta,
            correlation_exposure=correlation_exposure,
            factor_exposure=factor_exposure
        )
    
    def calculate_btc_beta(self, positions: list[Position]) -> float:
        """
        Рассчитать бета портфеля к BTC.
        
        Args:
            positions: Список позиций
        
        Returns:
            Бета портфеля к BTC
        """
        if not positions:
            return 0.0
        
        total_beta = 0.0
        total_notional = 0.0
        
        for position in positions:
            beta_info = self.default_betas.get(position.symbol, {})
            btc_beta = beta_info.get("btc_beta", 1.0)
            
            # Учесть направление
            if position.side == "short":
                btc_beta = -btc_beta
            
            total_beta += position.notional * btc_beta
            total_notional += abs(position.notional)
        
        if total_notional > 0:
            return total_beta / total_notional
        
        return 0.0
    
    def calculate_market_beta(self, positions: list[Position]) -> float:
        """
        Рассчитать бета портфеля к рынку.
        
        Args:
            positions: Список позиций
        
        Returns:
            Бета портфеля к рынку
        """
        if not positions:
            return 0.0
        
        total_beta = 0.0
        total_notional = 0.0
        
        for position in positions:
            beta_info = self.default_betas.get(position.symbol, {})
            market_beta = beta_info.get("market_beta", 1.0)
            
            # Учесть направление
            if position.side == "short":
                market_beta = -market_beta
            
            total_beta += position.notional * market_beta
            total_notional += abs(position.notional)
        
        if total_notional > 0:
            return total_beta / total_notional
        
        return 0.0
    
    def calculate_correlation_exposure(
        self,
        positions: list[Position]
    ) -> dict[str, float]:
        """
        Рассчитать корреляционную экспозицию.
        
        Args:
            positions: Список позиций
        
        Returns:
            Корреляционная экспозиция
        """
        correlation_exposure = {}
        
        # Для каждого символа
        symbols = set(p.symbol for p in positions)
        
        for symbol in symbols:
            # Найти позиции по этому символу
            symbol_positions = [p for p in positions if p.symbol == symbol]
            symbol_notional = sum(p.notional for p in symbol_positions)
            
            # Найти корреляции с другими символами
            for other_symbol in symbols:
                if symbol == other_symbol:
                    continue
                
                # Получить корреляцию
                corr = self.correlations.get((symbol, other_symbol), 0.0)
                corr = self.correlations.get((other_symbol, symbol), corr)
                
                if corr > 0:
                    # Добавить корреляционную экспозицию
                    key = f"{symbol}:{other_symbol}"
                    if key not in correlation_exposure:
                        correlation_exposure[key] = 0.0
                    correlation_exposure[key] += symbol_notional * corr
        
        return correlation_exposure
    
    def calculate_factor_exposure(
        self,
        positions: list[Position]
    ) -> dict[str, float]:
        """
        Рассчитать факторную экспозицию.
        
        Args:
            positions: Список позиций
        
        Returns:
            Факторная экспозиция
        """
        factor_exposure = {}
        
        for position in positions:
            factors = self.factors.get(position.symbol, {})
            
            for factor, weight in factors.items():
                if factor not in factor_exposure:
                    factor_exposure[factor] = 0.0
                
                # Учесть направление
                if position.side == "short":
                    weight = -weight
                
                factor_exposure[factor] += position.notional * weight
        
        return factor_exposure
    
    def check_exposure_limits(
        self,
        portfolio_exposure: PortfolioExposure,
        limits: dict[str, float]
    ) -> dict[str, bool]:
        """
        Проверить превышение лимитов экспозиции.
        
        Args:
            portfolio_exposure: Экспозиция портфеля
            limits: Лимиты экспозиции
        
        Returns:
            Словарь с флагами превышения
        """
        violations = {}
        
        # Проверить gross exposure
        if "gross_exposure" in limits:
            violations["gross_exposure"] = portfolio_exposure.gross_exposure > limits["gross_exposure"]
        
        # Проверить net exposure
        if "net_exposure" in limits:
            violations["net_exposure"] = abs(portfolio_exposure.net_exposure) > limits["net_exposure"]
        
        # Проверить btc beta
        if "btc_beta" in limits:
            violations["btc_beta"] = abs(portfolio_exposure.btc_beta) > limits["btc_beta"]
        
        # Проверить market beta
        if "market_beta" in limits:
            violations["market_beta"] = abs(portfolio_exposure.market_beta) > limits["market_beta"]
        
        # Проверить экспозицию по символам
        for symbol, exposure in portfolio_exposure.symbol_exposure.items():
            limit_key = f"symbol_{symbol}"
            if limit_key in limits:
                violations[limit_key] = abs(exposure) > limits[limit_key]
        
        # Проверить экспозицию по секторам
        for sector, exposure in portfolio_exposure.sector_exposure.items():
            limit_key = f"sector_{sector}"
            if limit_key in limits:
                violations[limit_key] = abs(exposure) > limits[limit_key]
        
        return violations
    
    def get_exposure_breakdown(
        self,
        portfolio_exposure: PortfolioExposure
    ) -> dict[str, Any]:
        """
        Получить детализацию экспозиции.
        
        Args:
            portfolio_exposure: Экспозиция портфеля
        
        Returns:
            Детализация экспозиции
        """
        breakdown = {
            "gross_exposure": portfolio_exposure.gross_exposure,
            "net_exposure": portfolio_exposure.net_exposure,
            "btc_beta": portfolio_exposure.btc_beta,
            "market_beta": portfolio_exposure.market_beta,
            "symbol_exposure": portfolio_exposure.symbol_exposure,
            "sector_exposure": portfolio_exposure.sector_exposure,
            "correlation_exposure": portfolio_exposure.correlation_exposure,
            "factor_exposure": portfolio_exposure.factor_exposure,
        }
        
        # Рассчитать проценты
        if portfolio_exposure.gross_exposure > 0:
            breakdown["symbol_exposure_pct"] = {
                k: (v / portfolio_exposure.gross_exposure) * 100
                for k, v in portfolio_exposure.symbol_exposure.items()
            }
            
            breakdown["sector_exposure_pct"] = {
                k: (v / portfolio_exposure.gross_exposure) * 100
                for k, v in portfolio_exposure.sector_exposure.items()
            }
        
        return breakdown


# Глобальный экземпляр Portfolio Exposure Engine
_portfolio_exposure_engine: PortfolioExposureEngine | None = None


def get_portfolio_exposure_engine() -> PortfolioExposureEngine:
    """Получить глобальный Portfolio Exposure Engine"""
    global _portfolio_exposure_engine
    if _portfolio_exposure_engine is None:
        _portfolio_exposure_engine = PortfolioExposureEngine()
    return _portfolio_exposure_engine


def reset_portfolio_exposure_engine():
    """Сбросить Portfolio Exposure Engine (для тестов)"""
    global _portfolio_exposure_engine
    _portfolio_exposure_engine = PortfolioExposureEngine()
