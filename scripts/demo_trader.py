#!/usr/bin/env python3
"""Непрерывный OKX Demo Trader ASTRA BOT."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np

from astra_bot.adapters.okx import OKXClient
from astra_bot.core import models
from astra_bot.core.instruments import TRADING_UNIVERSE
from astra_bot.core.logger import setup_logging
from astra_bot.core.utils import calculate_atr, calculate_rsi
from astra_bot.ml.feature_pipeline import FeaturePipeline
from astra_bot.ml.historical_training import fetch_historical_candles
from astra_bot.ml.model_trainer import MLModel
from astra_bot.ml.news_features import NewsFeatureService
from astra_bot.ml.weekly_learner import train_weekly

LOGGER = logging.getLogger("demo_trader")
STATE_PATH = Path("models/demo_state.json")
LESSONS_PATH = Path("models/lessons.jsonl")
MODEL_PATH = Path("models/current.pkl")

CAPITAL_FRACTION = 0.50
RISK_PER_TRADE = 0.004
MAX_POSITIONS = 8
MAX_POSITION_FRACTION = 0.10
ENTRY_SCORE = 0.68
MIN_ML_PROB = 0.60
STOP_ATR_MULTIPLIER = 1.8
TAKE_PROFIT_R = 2.2
MAX_HOLDING_HOURS = 48
SCAN_INTERVAL_SECONDS = 60
RETRAIN_EVERY_LESSONS = 200


@dataclass
class ManagedPosition:
    symbol: str
    quantity: float
    entry_price: float
    entry_time: str
    stop_price: float
    take_price: float
    score: float
    ml_probability: float
    news_sentiment: float
    features: dict[str, float]

    @property
    def entry_dt(self) -> datetime:
        return datetime.fromisoformat(self.entry_time)


class StateStore:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
        self.data = self._load()

    def _empty(self) -> dict:
        now = datetime.now(tz=UTC).isoformat()
        return {
            "day": now[:10], "daily_trades": 0, "daily_wins": 0,
            "daily_losses": 0, "daily_pnl_usdt": 0.0,
            "total_trades": 0, "total_wins": 0, "total_losses": 0,
            "total_pnl_usdt": 0.0, "positions": {}, "lessons": 0,
            "last_tick": now, "total_equity_usdt": 0.0,
            "allocated_equity_usdt": 0.0, "reserved_equity_usdt": 0.0,
        }

    def _load(self) -> dict:
        if not self.path.exists():
            return self._empty()
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return self._empty()

    def reset_day_if_needed(self) -> None:
        day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        if self.data.get("day") != day:
            self.data.update({
                "day": day, "daily_trades": 0, "daily_wins": 0,
                "daily_losses": 0, "daily_pnl_usdt": 0.0,
            })

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


class DemoTrader:
    def __init__(self) -> None:
        self.client = OKXClient({
            "api_key": os.getenv("OKX_API_KEY", ""),
            "api_secret": os.getenv("OKX_API_SECRET", ""),
            "passphrase": os.getenv("OKX_API_PASSPHRASE", "") or os.getenv("OKX_PASSPHRASE", ""),
            "sandbox": True,
            "enabled": True,
            "rate_limit_qps": 8,
        })
        self.pipeline = FeaturePipeline()
        self.news = NewsFeatureService()
        self.state = StateStore()
        self.model: MLModel | None = None
        self.valid_symbols: list[str] = list(TRADING_UNIVERSE)

    async def initialize(self) -> None:
        if os.getenv("OKX_DEMO", "1").lower() in {"0", "false", "no"}:
            raise RuntimeError("Demo trader refused to start: OKX_DEMO must be enabled")
        if not os.getenv("OKX_API_KEY") or not os.getenv("OKX_API_SECRET"):
            raise RuntimeError("OKX demo API secrets are required")
        await self.client.initialize()
        await self._filter_live_symbols()
        self._load_model()
        self.state.reset_day_if_needed()
        self.state.save()

    async def _filter_live_symbols(self) -> None:
        live: set[str] = set()
        try:
            for inst in await self.client.get_instruments():
                if getattr(inst, "trading_status", "") in {"live", "trading"}:
                    live.add(inst.symbol)
        except Exception as exc:
            LOGGER.warning("Instrument refresh failed: %s", exc)
        self.valid_symbols = [s for s in TRADING_UNIVERSE if s.replace("/", "-") in live] or list(TRADING_UNIVERSE)
        LOGGER.info("Trading universe: %d/%d symbols", len(self.valid_symbols), len(TRADING_UNIVERSE))

    def _load_model(self) -> None:
        if not MODEL_PATH.exists():
            raise RuntimeError("models/current.pkl отсутствует: сначала scripts/pretrain_5y.py")
        self.model = MLModel.load(str(MODEL_PATH))
        if not self.model.is_fitted:
            raise RuntimeError("models/current.pkl не обучена")
        LOGGER.info("Model=%s features=%d", self.model.version, len(self.model.feature_names))

    async def total_equity_usdt(self) -> Decimal:
        balances = await self.client.get_account_balance()
        total = Decimal("0")
        for asset, balance in balances.items():
            amount = balance.total
            if amount <= 0:
                continue
            if asset.upper() == "USDT":
                total += amount
                continue
            symbol = f"{asset.upper()}/USDT"
            if symbol not in self.valid_symbols:
                continue
            ticker = await self.client.get_ticker(symbol.replace("/", "-"))
            price = ticker.get("last", Decimal("0")) if ticker else Decimal("0")
            if price > 0:
                total += amount * price
        return total

    def _position_objects(self) -> dict[str, ManagedPosition]:
        out: dict[str, ManagedPosition] = {}
        for symbol, row in self.state.data.get("positions", {}).items():
            try:
                out[symbol] = ManagedPosition(**row)
            except TypeError:
                continue
        return out

    def _score(self, features: dict[str, float], news_features: dict[str, float]) -> tuple[float, float]:
        if self.model is None:
            return 0.5, 0.5
        names = self.model.feature_names
        vector = np.asarray([[float(features.get(name, 0.0)) for name in names]], dtype=float)
        try:
            ml_probability = float(self.model.predict_probability(vector))
        except Exception as exc:
            LOGGER.warning("Model inference failed: %s", exc)
            ml_probability = 0.5

        tech = 0.5
        tech += float(np.clip(features.get("return_1h", 0.0) * 3.0 + features.get("return_4h", 0.0) * 1.5, -0.2, 0.2))
        tech += 0.08 if features.get("trend_direction", 0.0) > 0 else -0.08 if features.get("trend_direction", 0.0) < 0 else 0.0
        rsi = features.get("rsi", 50.0)
        tech += 0.05 if 45 <= rsi <= 68 else -0.05 if rsi > 78 else 0.0
        tech += float(np.clip((features.get("volume_ratio", 1.0) - 1.0) * 0.04, -0.06, 0.06))
        tech = float(np.clip(tech, 0.0, 1.0))

        news_score = 0.5 + 0.5 * float(np.clip(news_features.get("news_sentiment", 0.0), -1.0, 1.0))
        news_weight = 0.10 if news_features.get("news_confidence", 0.0) > 0.15 else 0.03
        score = 0.65 * ml_probability + (0.35 - news_weight) * tech + news_weight * news_score
        return float(np.clip(score, 0.0, 1.0)), ml_probability

    async def _features_for(self, symbol: str) -> tuple[dict[str, float], dict[str, float], float]:
        candles = await fetch_historical_candles(
            client=self.client,
            symbol=symbol.replace("/", "-"),
            timeframe="1h", lookback_days=12,
            sleep_between_requests=0.0,
        )
        if len(candles) < 200:
            return {}, {}, 0.0
        for candle in candles:
            candle.symbol = symbol

        fv = self.pipeline.generate_features(
            symbol=symbol, candles=candles,
            current_time=datetime.now(tz=UTC),
        )
        if not fv.is_valid:
            return {}, {}, 0.0

        closes = [float(c.close) for c in candles]
        volumes = [float(c.volume) for c in candles]
        highs = [float(c.high) for c in candles]
        lows = [float(c.low) for c in candles]
        last = closes[-1]
        sma20 = sum(closes[-20:]) / 20
        atr = calculate_atr(highs[-20:], lows[-20:], closes[-20:], period=14) or 0.0
        rsi = calculate_rsi(closes, period=14) or 50.0
        avg_vol = sum(volumes[-20:]) / 20
        features = dict(fv.features)
        features.update({
            "return_1h": closes[-1] / closes[-2] - 1 if closes[-2] else 0.0,
            "return_4h": closes[-1] / closes[-5] - 1 if closes[-5] else 0.0,
            "return_24h": closes[-1] / closes[-25] - 1 if closes[-25] else 0.0,
            "sma20_gap": last / sma20 - 1 if sma20 else 0.0,
            "atr_pct": atr / last * 100 if last else 0.0,
            "rsi": rsi,
            "volume_ratio": volumes[-1] / avg_vol if avg_vol else 1.0,
            "confidence": 0.5,
        })
        news = await self.news.current(symbol)
        return features, news.to_features(), last

    async def _open_position(self, symbol: str, features: dict[str, float], news_features: dict[str, float], score: float, ml_probability: float, price: float, allocated: Decimal) -> None:
        if len(self._position_objects()) >= MAX_POSITIONS or score < ENTRY_SCORE or ml_probability < MIN_ML_PROB:
            return

        atr = float(features.get("atr", price * 0.01))
        stop_distance = max(atr * STOP_ATR_MULTIPLIER, price * 0.006)
        risk_usdt = float(allocated) * RISK_PER_TRADE
        risk_notional = risk_usdt / max(stop_distance / price, 0.003)
        max_notional = float(allocated) * MAX_POSITION_FRACTION
        current_open = sum(p.entry_price * p.quantity for p in self._position_objects().values())
        free_budget = max(0.0, float(allocated) - current_open)
        notional = min(risk_notional, max_notional, free_budget)
        if notional <= 10:
            return

        quantity = Decimal(str(notional / price))
        result = await self.client.place_order(
            symbol=symbol.replace("/", "-"), side="buy", order_type="market", quantity=quantity,
        )
        if result.status not in {"new", "live", "filled"}:
            return
        await asyncio.sleep(1)
        order = await self.client.get_order(symbol.replace("/", "-"), result.id)
        fill_price = float(order.filled_price or price) if order else price
        qty = float(order.filled_quantity or quantity) if order else float(quantity)
        stop = max(fill_price - stop_distance, fill_price * 0.001)
        take = fill_price + (fill_price - stop) * TAKE_PROFIT_R
        position = ManagedPosition(
            symbol=symbol, quantity=qty, entry_price=fill_price,
            entry_time=datetime.now(tz=UTC).isoformat(), stop_price=stop,
            take_price=take, score=score, ml_probability=ml_probability,
            news_sentiment=float(news_features.get("news_sentiment", 0.0)),
            features={**features, **news_features},
        )
        self.state.data.setdefault("positions", {})[symbol] = asdict(position)
        self.state.save()
        LOGGER.info("OPEN %s qty=%.8f entry=%.6f score=%.3f ml=%.3f", symbol, qty, fill_price, score, ml_probability)

    async def _close_position(self, position: ManagedPosition, price: float, reason: str) -> None:
        result = await self.client.place_order(
            symbol=position.symbol.replace("/", "-"), side="sell", order_type="market",
            quantity=Decimal(str(position.quantity)),
        )
        if result.status not in {"new", "live", "filled"}:
            return
        await asyncio.sleep(1)
        order = await self.client.get_order(position.symbol.replace("/", "-"), result.id)
        exit_price = float(order.filled_price or price) if order else price
        gross = (exit_price - position.entry_price) * position.quantity
        fees = (exit_price + position.entry_price) * position.quantity * 0.001
        pnl = gross - fees
        outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"

        lesson = {
            "trade_id": f"demo-{int(time.time()*1000)}", "symbol": position.symbol,
            "direction": "long", "entry_time": int(position.entry_dt.timestamp() * 1000),
            "exit_time": int(datetime.now(tz=UTC).timestamp() * 1000),
            "entry_price": position.entry_price, "exit_price": exit_price,
            "qty": position.quantity, "pnl": pnl,
            "pnl_pct": pnl / (position.entry_price * position.quantity) * 100 if position.entry_price else 0.0,
            "outcome": outcome, "strategy": "ML+TECH+NEWS_DEMO",
            "confidence": position.score, "features": position.features,
            "market_regime": "LIVE_DEMO", "news_impulse": abs(position.news_sentiment) > 0.5,
            "influencing_factor": reason,
            "counterfactual": "KEEP" if outcome == "win" else "REDUCE_OR_SKIP",
            "takeaway": f"{position.symbol}: {reason}; pnl={pnl:.4f} USDT",
            "recommendation": "HOLD_WINNER" if outcome == "win" else "REVIEW_LOSS",
        }
        LESSONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LESSONS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(lesson, ensure_ascii=False) + "\n")

        self.state.data["daily_trades"] += 1
        self.state.data["total_trades"] += 1
        self.state.data["daily_pnl_usdt"] += pnl
        self.state.data["total_pnl_usdt"] += pnl
        if outcome == "win":
            self.state.data["daily_wins"] += 1
            self.state.data["total_wins"] += 1
        elif outcome == "loss":
            self.state.data["daily_losses"] += 1
            self.state.data["total_losses"] += 1
        self.state.data["lessons"] = int(self.state.data.get("lessons", 0)) + 1
        self.state.data.setdefault("positions", {}).pop(position.symbol, None)
        self.state.save()

        if self.state.data["lessons"] % RETRAIN_EVERY_LESSONS == 0:
            result = train_weekly(
                lessons_path=LESSONS_PATH,
                model_path=MODEL_PATH,
                min_samples=RETRAIN_EVERY_LESSONS,
            )
            if result.trained:
                self.model = MLModel.load(str(MODEL_PATH))
                LOGGER.info("RETRAIN %s", result.message)

    async def manage_positions(self) -> None:
        for position in list(self._position_objects().values()):
            ticker = await self.client.get_ticker(position.symbol.replace("/", "-"))
            price = float(ticker.get("last", 0)) if ticker else 0.0
            if price <= 0:
                continue
            age_h = (datetime.now(tz=UTC) - position.entry_dt).total_seconds() / 3600
            if price <= position.stop_price:
                await self._close_position(position, price, "STOP_LOSS")
            elif price >= position.take_price:
                await self._close_position(position, price, "TAKE_PROFIT")
            elif age_h >= MAX_HOLDING_HOURS:
                await self._close_position(position, price, "TIME_EXIT")

    async def scan_and_trade(self) -> None:
        self.state.reset_day_if_needed()
        await self.manage_positions()
        equity = await self.total_equity_usdt()
        allocated = equity * Decimal(str(CAPITAL_FRACTION))
        self.state.data.update({
            "total_equity_usdt": float(equity),
            "allocated_equity_usdt": float(allocated),
            "reserved_equity_usdt": float(equity - allocated),
            "last_tick": datetime.now(tz=UTC).isoformat(),
        })

        candidates = []
        for symbol in self.valid_symbols:
            try:
                features, news_features, price = await self._features_for(symbol)
                if not features:
                    continue
                score, ml_prob = self._score(features, news_features)
                if score >= ENTRY_SCORE:
                    candidates.append((score, ml_prob, symbol, features, news_features, price))
            except Exception as exc:
                LOGGER.warning("scan %s failed: %s", symbol, exc)

        candidates.sort(reverse=True, key=lambda x: x[0])
        for score, ml_prob, symbol, features, news_features, price in candidates:
            if symbol in self._position_objects():
                continue
            await self._open_position(symbol, features, news_features, score, ml_prob, price, allocated)
            if len(self._position_objects()) >= MAX_POSITIONS:
                break
        self.state.save()

    async def run(self) -> None:
        await self.initialize()
        while True:
            started = time.monotonic()
            try:
                await self.scan_and_trade()
            except Exception:
                LOGGER.exception("demo loop failed")
            await asyncio.sleep(max(1.0, SCAN_INTERVAL_SECONDS - (time.monotonic() - started)))


async def amain() -> None:
    setup_logging(level=os.getenv("LOG_LEVEL", "INFO"), log_dir=os.getenv("LOG_DIR", "logs"))
    trader = DemoTrader()
    try:
        await trader.run()
    finally:
        await trader.client.close()


if __name__ == "__main__":
    asyncio.run(amain())
