"""
ASTRA BOT — Trading engine.

Связывает DecisionPipeline, OKX market data и PaperBroker:

1. Тянет свечи 4h/1h/15m/5m и стакан по инструментам.
2. На каждом 5m-баре вызывает ``pipeline.decide``.
3. При сигнале LONG/SHORT открывает бумажную позицию.
4. По каждому новому бару обновляет стопы/тейки PaperBroker.
5. Пишет метрики и логи.

Это изолированный движок для непрерывной paper-торговли.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..adapters.okx import OKXClient
from ..core import models
from ..core.market_safety import MarketSafety
from ..core import trading_schedule
from ..ml.live_lessons import append_lessons
from .broker import PaperBroker
from .context import MarketContext
from .pipeline import DecisionPipeline

logger = logging.getLogger(__name__)


@dataclass
class TradingEngineConfig:
    symbols: tuple[str, ...] = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    # Основной таймфрейм — 15m: больше сигналов для быстрого обучения.
    timeframes: tuple[str, ...] = ("15m", "1h", "4h")
    # Сколько баров тянуть для каждого таймфрейма.
    bars_per_tf: dict[str, int] = field(
        default_factory=lambda: {"15m": 300, "1h": 300, "4h": 200}
    )
    # 0.3% капитала на сделку — мелкие частые ставки, серия стопов не
    # обнуляет счёт.
    risk_per_trade_pct: Decimal = Decimal("0.003")
    poll_interval_seconds: int = 60 * 5
    state_path: str = "models/paper_positions.json"
    trades_path: str = "models/paper_trades.jsonl"


class TradingEngine:
    def __init__(
        self,
        okx: OKXClient,
        pipeline: DecisionPipeline | None = None,
        config: TradingEngineConfig | None = None,
        broker: PaperBroker | None = None,
        notifier: Any | None = None,
    ):
        # Колбэк для уведомлений в Telegram: notifier(text, severity).
        self._notifier = notifier
        self.okx = okx
        self.config = config or TradingEngineConfig()
        if pipeline is None:
            from ..strategies import (
                MeanReversionStrategy,
                MomentumStrategy,
                PullbackStrategy,
                ScalpStrategy,
            )
            from .config import DecisionConfig
            # Пороги согласованы со стратегиями. Scalp даёт много мелких
            # сделок на 15m для быстрого обучения; Pullback — крупнее на 1h.
            cfg = DecisionConfig()
            cfg.min_rr = 0.7
            cfg.min_ml_probability = 0.0
            cfg.min_expected_edge_pct = 0.0
            cfg.max_spread_pct = 0.30
            cfg.slippage_buffer_pct = 0.02
            cfg.min_book_depth = 1_000.0
            pipeline = DecisionPipeline(
                cfg,
                strategies=[
                    ScalpStrategy(),
                    PullbackStrategy(),
                    MomentumStrategy(),
                    MeanReversionStrategy(),
                ],
            )
        self.pipeline = pipeline
        self.broker = broker or PaperBroker(
            state_path=__import__("pathlib").Path(self.config.state_path),
            trades_path=__import__("pathlib").Path(self.config.trades_path),
        )
        # Единая проверка «можно ли входить прямо сейчас»: расписание/бюджет
        # часов, новости, волатильность, спред, дисбаланс стакана.
        self.safety = MarketSafety()
        self._last_bar_ts: dict[str, int] = {}
        self._running = False
        self._capital_synced = False
        self._minute_bucket: int | None = None

    async def sync_capital(self) -> Decimal:
        """Подтянуть стартовый капитал брокера под реальный баланс демо OKX.

        Тянем USDT-баланс один раз (при первом шаге); дальше paper-брокер
        считает realized PnL сам. Это даёт сделкам масштаб «сколько есть»,
        а не зашитые 2000.
        """
        if self._capital_synced:
            return self.broker.initial_capital
        try:
            bals = await self.okx.get_account_balance()
            usdt = bals.get("USDT")
            if usdt and float(usdt.total) > 0:
                cap = Decimal(str(usdt.total))
                # Если уже есть сохранённые позиции — НЕ сбрасываем их и
                # realized_pnl, продолжаем с текущего капитала брокера.
                if self.broker.positions:
                    logger.info(
                        "Капитал демо OKX=%s USDT, но есть открытые позиции — "
                        "продолжаю с broker capital=%s", cap, self.broker.initial_capital,
                    )
                else:
                    logger.info("Синхронизация капитала с демо OKX: %s USDT", cap)
                    from .broker import PaperBroker
                    self.broker = PaperBroker(
                        state_path=__import__("pathlib").Path(self.config.state_path),
                        trades_path=__import__("pathlib").Path(self.config.trades_path),
                        initial_capital=cap,
                    )
                self._capital_synced = True
                return cap
        except Exception as exc:  # noqa: BLE001
            logger.debug("Не смог синхронизировать капитал: %s", exc)
        self._capital_synced = True
        return self.broker.initial_capital

    # ----------------------------------------------------------- market data
    async def fetch_context(self, symbol: str) -> MarketContext:
        candles: dict[str, list[models.Candle]] = {}
        for tf in self.config.timeframes:
            candles[tf] = await self.okx.get_candles(
                symbol,
                timeframe=tf,
                limit=self.config.bars_per_tf.get(tf, 300),
            )
        primary = candles.get("5m") or candles.get("1h") or []
        if not primary:
            raise RuntimeError(f"Нет данных по {symbol}")
        price = Decimal(str(primary[-1].close))
        try:
            orderbook = await self.okx.get_orderbook(symbol, depth=20)
        except Exception as exc:
            logger.warning("Стакан %s недоступен: %s", symbol, exc)
            orderbook = None
        return MarketContext(
            symbol=symbol,
            current_price=price,
            candles=candles,
            orderbook=orderbook,
        )

    # ----------------------------------------------------------- sizing
    def _position_size(
        self,
        equity: Decimal,
        entry: Decimal,
        stop: Decimal,
    ) -> Decimal:
        risk_amount = equity * self.config.risk_per_trade_pct
        stop_distance = abs(entry - stop)
        if stop_distance <= 0:
            return Decimal("0")
        qty = risk_amount / stop_distance
        # Не допускаем размер больше 30% капитала (notional).
        max_notional = equity * Decimal("0.30")
        if qty * entry > max_notional:
            qty = max_notional / entry
        return qty.quantize(Decimal("0.000001"))

    # ----------------------------------------------------------- main loop
    async def process_symbol(self, symbol: str) -> list[Any]:
        ctx = await self.fetch_context(symbol)
        primary = ctx.candles.get("5m") or ctx.candles.get("1h") or []
        if not primary:
            return []

        # Обновляем уже открытые позиции по последнему бару.
        last_bar = primary[-1]
        closed = self.broker.on_bar(last_bar)

        # Закрытые на этом баре сделки → реальные уроки + уведомления.
        if closed:
            self._record_closed(closed)

        # Одна позиция на символ.
        if any(p.symbol == symbol for p in self.broker.positions):
            return closed

        # Рыночная «безопасность»: расписание/бюджет часов, новости,
        # волатильность, спред, дисбаланс стакана. Если не прошли —
        # пропускаем вход (это главный щит от слива депозита).
        try:
            ticker = await self.okx.get_ticker(symbol)
        except Exception:
            ticker = {}
        ob = ctx.orderbook
        book_dict = None
        if ob is not None and getattr(ob, "bids", None) and getattr(ob, "asks", None):
            bids_depth = sum(float(b.price) * float(b.quantity) for b in ob.bids[:10])
            asks_depth = sum(float(a.price) * float(a.quantity) for a in ob.asks[:10])
            book_dict = {
                "best_bid": float(ob.bids[0].price),
                "best_ask": float(ob.asks[0].price),
                "bids_depth": bids_depth,
                "asks_depth": asks_depth,
            }
        ticker_map = None
        if ticker:
            # get_ticker возвращает high_24h/low_24h без open24h; для
            # проверки резкого движения считаем open из last и диапазона.
            last_f = float(ticker.get("last") or 0)
            hi = float(ticker.get("high_24h") or 0)
            lo = float(ticker.get("low_24h") or 0)
            # Грубая оценка open24h как середины диапазона (нам важен лишь
            # факт резкого движения > 8%, точность до долей процента не нужна).
            open24 = (hi + lo) / 2 if hi and lo else 0.0
            ticker_map = {"last": last_f, "open24h": open24}
        verdict = self.safety.check(
            symbol,
            ticker=ticker_map,
            orderbook=book_dict,
            candles=list(primary),
        )
        if not verdict.allowed:
            logger.info("%s: вход запрещён: %s", symbol, "; ".join(verdict.reasons))
            return closed

        decision = self.pipeline.decide(ctx)
        if decision.action == "NO_TRADE" or decision.candidate is None:
            logger.debug("NO_TRADE %s: %s", symbol, decision.reasons)
            return closed

        cand = decision.candidate
        size = self._position_size(
            self.broker.equity,
            cand.entry_price,
            cand.stop_loss,
        )
        if size <= 0:
            logger.info("%s: size=0, пропускаю", symbol)
            return closed

        pos = self.broker.open_position(
            symbol=symbol,
            direction="long" if cand.direction == "long" else "short",
            entry_price=cand.entry_price,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            quantity=size,
            strategy=cand.strategy,
            notes={
                "score": cand.total_score,
                "ml_probability": cand.ml_probability,
                "edge_pct": cand.expected_edge_pct,
                "rr": cand.risk_reward,
            },
        )
        # Уведомления по каждой сделке отключены: шлём только утренний
        # отчёт и отвечаем на команды из меню.
        logger.info("OPEN %s %s entry=%s", pos.direction, pos.symbol, pos.entry_price)
        return closed

    def _record_closed(self, closed: list) -> None:
        """Сохранить закрытые сделки как уроки и уведомить о результате."""
        try:
            trades = []
            for t in closed:
                d = {
                    "id": getattr(t, "id", ""),
                    "symbol": getattr(t, "symbol", ""),
                    "direction": getattr(t, "direction", ""),
                    "entry_price": getattr(t, "entry_price", 0.0),
                    "exit_price": getattr(t, "exit_price", 0.0),
                    "quantity": getattr(t, "quantity", 0.0),
                    "pnl": getattr(t, "pnl", 0.0),
                    "pnl_pct": getattr(t, "pnl_pct", 0.0),
                    "exit_reason": getattr(t, "exit_reason", ""),
                    "strategy": getattr(t, "strategy", ""),
                    "opened_at": getattr(t, "opened_at", 0),
                    "closed_at": getattr(t, "closed_at", 0),
                }
                trades.append(d)
            if trades:
                append_lessons(trades)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не смог записать уроки/уведомления: %s", exc)

    def _notify(self, text: str, severity: str = "info") -> None:
        if self._notifier is None:
            return
        try:
            res = self._notifier(text, severity)
            if asyncio.iscoroutine(res):
                # Отправка идёт fire-and-forget, чтобы не блокировать цикл.
                asyncio.ensure_future(res)
        except Exception as exc:  # noqa: BLE001
            logger.debug("notifier error: %s", exc)

    async def step(self) -> None:
        # Раз при первом шаге подтягиваем реальный капитал демо OKX.
        if not self._capital_synced:
            await self.sync_capital()

        # Учёт минуты бюджета торговых часов (раз в минуту цикл может
        # вызываться чаще, но тарифицируем только целые минуты).
        from datetime import datetime, timezone as _tz
        bucket = int(datetime.now(_tz.utc).timestamp() // 60)
        if self._minute_bucket != bucket:
            self._minute_bucket = bucket
            trading_schedule.tick()

        for symbol in self.config.symbols:
            try:
                await self.process_symbol(symbol)
            except Exception as exc:
                logger.exception("Ошибка обработки %s: %s", symbol, exc)

    async def run_forever(self) -> None:
        self._running = True
        status = trading_schedule.get_status()
        logger.info(
            "Trading engine запущен: %s, интервал %ss; бюджет %.0f ч/мес, "
            "активные часы %s МСК, сейчас торговля %s",
            self.config.symbols,
            self.config.poll_interval_seconds,
            status["budget_hours"],
            status["active_hours_msk"],
            "разрешена" if status["can_trade_now"] else "на паузе",
        )
        while self._running:
            await self.step()
            await asyncio.sleep(self.config.poll_interval_seconds)

    def stop(self) -> None:
        self._running = False
