#!/usr/bin/env python3
"""Continuous OKX Demo trader using the same market-understanding engine as pretrain."""

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
from astra_bot.core.instruments import TRADING_UNIVERSE
from astra_bot.core.logger import setup_logging
from astra_bot.ml.market_memory import MarketMemory
from astra_bot.ml.market_understanding import compute_market_features
from astra_bot.ml.model_trainer import MLModel
from astra_bot.ml.news_features import NewsFeatureService
from astra_bot.ml.weekly_learner import train_weekly

LOG = logging.getLogger("demo_trader_pro")
STATE = Path("models/demo_state.json")
LESSONS = Path("models/lessons.jsonl")
MODEL = Path("models/current.pkl")
MEMORY = Path("models/market_memory.json")

CAPITAL_FRACTION = Decimal("0.50")
RISK_PER_TRADE = Decimal("0.004")
MAX_POSITION_FRACTION = Decimal("0.10")
MAX_POSITIONS = 8
MIN_MODEL_PROB = 0.60
MIN_SCORE = 0.67
STOP_ATR = 1.8
TAKE_R = 2.2
MAX_HOURS = 48
SCAN_SECONDS = 60
RETRAIN_LESSONS = 200


@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    entry_time: str
    stop: float
    take: float
    score: float
    model_probability: float
    features: dict[str, float]


class DemoTraderPro:
    def __init__(self) -> None:
        self.client = OKXClient({
            "api_key": os.getenv("OKX_API_KEY", ""),
            "api_secret": os.getenv("OKX_API_SECRET", ""),
            "passphrase": os.getenv("OKX_API_PASSPHRASE", ""),
            "sandbox": True,
            "enabled": True,
            "rate_limit_qps": 8,
        })
        self.news = NewsFeatureService(Path("models/news_cache.json"))
        self.memory = MarketMemory(MEMORY)
        self.model: MLModel | None = None
        self.positions: dict[str, Position] = {}
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if not STATE.exists():
            return {
                "day": datetime.now(tz=UTC).strftime("%Y-%m-%d"),
                "daily_trades": 0, "daily_wins": 0, "daily_losses": 0,
                "daily_pnl": 0.0, "total_trades": 0, "total_wins": 0,
                "total_losses": 0, "total_pnl": 0.0, "lessons": 0,
                "positions": {}, "last_tick": None,
            }
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return self._load_empty()

    def _load_empty(self) -> dict:
        return {"day": datetime.now(tz=UTC).strftime("%Y-%m-%d"), "daily_trades": 0,
                "daily_wins": 0, "daily_losses": 0, "daily_pnl": 0.0,
                "total_trades": 0, "total_wins": 0, "total_losses": 0,
                "total_pnl": 0.0, "lessons": 0, "positions": {}, "last_tick": None}

    def save_state(self) -> None:
        self.state["positions"] = {k: asdict(v) for k, v in self.positions.items()}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE)

    async def initialize(self) -> None:
        if os.getenv("OKX_DEMO", "1").lower() in {"0", "false", "no"}:
            raise RuntimeError("OKX_DEMO must remain enabled")
        if not os.getenv("OKX_API_KEY") or not os.getenv("OKX_API_SECRET"):
            raise RuntimeError("OKX demo credentials are missing")
        await self.client.initialize()
        for symbol, raw in self.state.get("positions", {}).items():
            try:
                self.positions[symbol] = Position(**raw)
            except Exception:
                continue
        if not MODEL.exists():
            raise RuntimeError("models/current.pkl is missing; run the five-year pretrain first")
        self.model = MLModel.load(str(MODEL))
        if not self.model.is_fitted:
            raise RuntimeError("models/current.pkl is not fitted")
        LOG.info("model=%s features=%d", self.model.version, len(self.model.feature_names))

    async def equity(self) -> Decimal:
        balances = await self.client.get_account_balance()
        total = Decimal("0")
        for asset, balance in balances.items():
            amount = balance.total
            if amount <= 0:
                continue
            if asset == "USDT":
                total += amount
            else:
                symbol = f"{asset}/USDT"
                if symbol in TRADING_UNIVERSE:
                    ticker = await self.client.get_ticker(symbol.replace("/", "-"))
                    price = ticker.get("last", Decimal("0")) if ticker else Decimal("0")
                    total += amount * price
        return total

    async def features(self, symbol: str) -> tuple[dict[str, float], float]:
        candles = await self.client.get_candles(symbol.replace("/", "-"), "1h", limit=250)
        if len(candles) < 200:
            return {}, 0.0
        technical = compute_market_features(candles, timeframe="1h")
        news = (await self.news.current(symbol)).to_features()
        merged = {**technical, **news}
        merged.update(self.memory.features_for(merged))
        # A live order-book snapshot is attached only to strong candidates.
        try:
            book = await self.client.get_orderbook(symbol.replace("/", "-"), depth=20)
            if book.bids and book.asks:
                bid = float(book.bids[0].price)
                ask = float(book.asks[0].price)
                bid_qty = sum(float(x.quantity) for x in book.bids[:10])
                ask_qty = sum(float(x.quantity) for x in book.asks[:10])
                merged["spread_pct"] = ((ask - bid) / max((ask + bid) / 2, 1e-9)) * 100
                merged["order_book_imbalance"] = (bid_qty - ask_qty) / max(bid_qty + ask_qty, 1e-9)
        except Exception:
            pass
        return merged, float(candles[-1].close)

    def model_probability(self, features: dict[str, float]) -> float:
        if self.model is None:
            return 0.5
        names = self.model.feature_names
        vector = np.asarray([[float(features.get(n, 0.0)) for n in names]], dtype=float)
        try:
            return float(self.model.predict_probability(vector))
        except Exception as exc:
            LOG.warning("model inference failed: %s", exc)
            return 0.5

    def score(self, features: dict[str, float]) -> tuple[float, float]:
        probability = self.model_probability(features)
        trend = float(features.get("trend_direction", 0.0))
        structure = float(features.get("structure_bias", 0.0))
        breakout = float(features.get("breakout_up", 0.0)) - float(features.get("breakout_down", 0.0))
        candle = sum(float(features.get(k, 0.0)) for k in ("bullish_engulfing", "candle_hammer", "morning_star"))
        candle -= sum(float(features.get(k, 0.0)) for k in ("bearish_engulfing", "candle_shooting_star", "evening_star"))
        news = float(features.get("news_sentiment", 0.0))
        news_conf = float(features.get("news_confidence", 0.0))
        memory_wr = float(features.get("memory_pattern_win_rate", 0.5))

        chart = 0.5 + 0.08 * trend + 0.08 * structure + 0.08 * breakout + 0.05 * np.clip(candle, -1.0, 1.0)
        chart += 0.08 * np.clip(news, -1.0, 1.0) * min(1.0, news_conf)
        chart += 0.13 * (memory_wr - 0.5)
        chart = float(np.clip(chart, 0.0, 1.0))
        total = float(np.clip(0.62 * probability + 0.38 * chart, 0.0, 1.0))
        return total, probability

    async def open_position(self, symbol: str, features: dict[str, float], price: float, score: float, probability: float, allocated: Decimal) -> None:
        if symbol in self.positions or len(self.positions) >= MAX_POSITIONS:
            return
        if score < MIN_SCORE or probability < MIN_MODEL_PROB:
            return

        atr = float(features.get("atr_pct", 1.0)) / 100.0 * price
        stop_distance = max(atr * STOP_ATR, price * 0.006)
        risk_notional = float(allocated * RISK_PER_TRADE) / max(stop_distance / price, 0.003)
        max_notional = float(allocated * MAX_POSITION_FRACTION)
        used = sum(p.entry_price * p.quantity for p in self.positions.values())
        notional = min(risk_notional, max_notional, max(0.0, float(allocated) - used))
        if notional <= 10:
            return

        quantity = Decimal(str(notional / price))
        result = await self.client.place_order(symbol=symbol.replace("/", "-"), side="buy", order_type="market", quantity=quantity)
        if result.status not in {"new", "live", "filled"}:
            return
        await asyncio.sleep(1)
        order = await self.client.get_order(symbol.replace("/", "-"), result.id)
        fill = float(order.filled_price or price) if order else price
        qty = float(order.filled_quantity or quantity) if order else float(quantity)
        stop = max(fill - stop_distance, fill * 0.001)
        take = fill + (fill - stop) * TAKE_R
        self.positions[symbol] = Position(symbol, qty, fill, datetime.now(tz=UTC).isoformat(), stop, take, score, probability, {**features})
        self.save_state()
        LOG.info("OPEN %s price=%.8f score=%.3f p=%.3f", symbol, fill, score, probability)

    async def close_position(self, pos: Position, price: float, reason: str) -> None:
        result = await self.client.place_order(symbol=pos.symbol.replace("/", "-"), side="sell", order_type="market", quantity=Decimal(str(pos.quantity)))
        if result.status not in {"new", "live", "filled"}:
            return
        await asyncio.sleep(1)
        order = await self.client.get_order(pos.symbol.replace("/", "-"), result.id)
        exit_price = float(order.filled_price or price) if order else price
        pnl = (exit_price - pos.entry_price) * pos.quantity - (exit_price + pos.entry_price) * pos.quantity * 0.001
        outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
        lesson = {
            "trade_id": f"demo-{int(time.time()*1000)}",
            "symbol": pos.symbol, "direction": "long",
            "entry_time": int(datetime.fromisoformat(pos.entry_time).timestamp() * 1000),
            "exit_time": int(datetime.now(tz=UTC).timestamp() * 1000),
            "entry_price": pos.entry_price, "exit_price": exit_price,
            "qty": pos.quantity, "pnl": pnl,
            "pnl_pct": pnl / max(pos.entry_price * pos.quantity, 1e-9) * 100,
            "outcome": outcome, "strategy": "MARKET_AWARE_ML_DEMO",
            "confidence": pos.score, "features": pos.features,
            "market_regime": "LIVE_DEMO", "news_impulse": abs(pos.features.get("news_sentiment", 0.0)) > 0.5,
            "influencing_factor": reason,
            "counterfactual": "KEEP" if outcome == "win" else "REVIEW_PATTERN",
            "takeaway": f"{pos.symbol}: {reason}; pnl={pnl:.4f} USDT",
            "recommendation": "HOLD_WINNER" if outcome == "win" else "REVIEW_LOSS",
        }
        LESSONS.parent.mkdir(parents=True, exist_ok=True)
        with LESSONS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(lesson, ensure_ascii=False) + "\n")
        self.memory.observe(lesson)
        self.memory.save()

        self.state["daily_trades"] += 1
        self.state["total_trades"] += 1
        self.state["daily_pnl"] += pnl
        self.state["total_pnl"] += pnl
        self.state["lessons"] += 1
        if outcome == "win":
            self.state["daily_wins"] += 1
            self.state["total_wins"] += 1
        elif outcome == "loss":
            self.state["daily_losses"] += 1
            self.state["total_losses"] += 1
        self.positions.pop(pos.symbol, None)
        self.save_state()

        if self.state["lessons"] % RETRAIN_LESSONS == 0:
            result = train_weekly(lessons_path=LESSONS, model_path=MODEL, min_samples=RETRAIN_LESSONS)
            if result.trained:
                self.model = MLModel.load(str(MODEL))
                LOG.info("RETRAIN %s", result.message)

    async def manage_positions(self) -> None:
        for pos in list(self.positions.values()):
            ticker = await self.client.get_ticker(pos.symbol.replace("/", "-"))
            price = float(ticker.get("last", 0)) if ticker else 0.0
            if price <= 0:
                continue
            age = (datetime.now(tz=UTC) - datetime.fromisoformat(pos.entry_time)).total_seconds() / 3600
            if price <= pos.stop:
                await self.close_position(pos, price, "STOP_LOSS")
            elif price >= pos.take:
                await self.close_position(pos, price, "TAKE_PROFIT")
            elif age >= MAX_HOURS:
                await self.close_position(pos, price, "TIME_EXIT")

    async def tick(self) -> None:
        day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        if self.state.get("day") != day:
            self.state["day"] = day
            self.state["daily_trades"] = self.state["daily_wins"] = self.state["daily_losses"] = 0
            self.state["daily_pnl"] = 0.0
        await self.manage_positions()
        equity = await self.equity()
        allocated = equity * CAPITAL_FRACTION

        candidates = []
        for symbol in TRADING_UNIVERSE:
            try:
                feats, price = await self.features(symbol)
                if not feats or price <= 0:
                    continue
                score, prob = self.score(feats)
                if score >= MIN_SCORE and prob >= MIN_MODEL_PROB:
                    candidates.append((score, prob, symbol, feats, price))
            except Exception as exc:
                LOG.warning("scan %s: %s", symbol, exc)
        candidates.sort(reverse=True, key=lambda x: x[0])
        for score, prob, symbol, feats, price in candidates:
            await self.open_position(symbol, feats, price, score, prob, allocated)
            if len(self.positions) >= MAX_POSITIONS:
                break
        self.save_state()

    async def run(self) -> None:
        await self.initialize()
        while True:
            started = time.monotonic()
            try:
                await self.tick()
            except Exception:
                LOG.exception("demo tick failed")
            await asyncio.sleep(max(1.0, SCAN_SECONDS - (time.monotonic() - started)))


async def main() -> None:
    setup_logging(level=os.getenv("LOG_LEVEL", "INFO"), log_dir=os.getenv("LOG_DIR", "logs"))
    trader = DemoTraderPro()
    try:
        await trader.run()
    finally:
        await trader.client.close()


if __name__ == "__main__":
    asyncio.run(main())
