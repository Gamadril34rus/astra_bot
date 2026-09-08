"""
ASTRA BOT — Paper execution engine (бессрочные фьючерсы, USDT-M).

Принимает финальное решение от ``DecisionPipeline`` и исполняет его
на виртуальном счёте. Эмулирует именно перпетуал-фьючерсы:

* комиссии — биржевые (BingX USDT-M: тейкер 0.05%, мейкер 0.02%);
* фандинг — выплаты между лонгами и шортами по ставке за 8-часовой
  интервал (положительная ставка: лонг платит, шорт получает);
* ликвидации — по mark price, изолированная маржа;
* несколько частичных тейков (TP1 50%, TP2 30%, TP3 20%);
* ATR/структура-based стоп-лосс;
* трейлинг-стоп после первого тейка;
* сохранение открытых позиций в ``models/paper_positions.json``;
* фиксацию сделок в ``models/paper_trades.jsonl``.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..engines.cost_model import (
    BINGX_PERPS_TAKER_FEE,
    CostModel,
    bingx_perps_cost_model,
    cost_model_from_flat,
)

logger = logging.getLogger(__name__)

# Длительность бара в минутах для начисления фандинга (детерминизм
# реплея: wall-clock не используется, плата одинакова при реплее сессии).
_TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720, "1d": 1440,
}


@dataclass
class PaperPosition:
    id: str
    symbol: str
    direction: str  # long/short
    entry_price: Decimal
    quantity: Decimal
    stop_loss: Decimal
    take_profits: list[Decimal]
    tp_filled: list[bool] = field(default_factory=list)
    tp_fractions: list[float] = field(default_factory=lambda: [0.5, 0.3, 0.2])
    # Сохраняем исходный объём, чтобы частичные тейки считались
    # от первоначального размера, а не от остатка.
    initial_quantity: Decimal = Decimal("0")
    trailing_activated: bool = False
    trailing_distance: Decimal | None = None
    highest_price: Decimal | None = None
    lowest_price: Decimal | None = None
    strategy: str = ""
    opened_at: int = field(
        default_factory=lambda: int(
            datetime.now(tz=UTC).timestamp() * 1000
        )
    )
    notes: dict[str, Any] = field(default_factory=dict)
    # Эффективная цена входа с учётом slippage (None для позиций,
    # открытых до введения издержек — тогда считается entry_price).
    fill_price: Decimal | None = None
    # Тейкер-комиссия на единицу объёма, начисленная при входе.
    entry_fee_per_unit: Decimal = Decimal("0")
    # Первоначальное расстояние входа-стоп (R-единица). Не меняется при
    # трейлинге — R-метрики сделок всегда считаются от исходного риска.
    risk_distance: Decimal = Decimal("0")
    # Контекст входа для статистики по режимам (meta-strategy, TZ §3.1).
    regime: str = ""
    timeframe: str = ""
    # Regime 2.0 (МТЗ §10): композитный ключ осей (T../V../L..); пусто —
    # позиция открыта до A2 или оси не вычислились (legacy-режим работает).
    regime_axes: str = ""
    # Сколько баров прожито позицией (для TIME_STOP и ATR_STOP, TZ §16).
    bars_held: int = 0
    # Плечо и задействованная изолированная маржа перпетуал-позиции
    # (margin_used = notional / leverage; плечо 1 = позиция без плеча,
    # но всё равно фьючерсная: фандинг и комиссии перпов применяются).
    leverage: Decimal = Decimal("1")
    margin_used: Decimal = Decimal("0")


@dataclass
class ClosedTrade:
    id: str
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    strategy: str
    opened_at: int
    closed_at: int
    # Комиссии + slippage-издержки, учтённые в pnl (нетто-результат).
    fees: float = 0.0
    # Выплаченный фандинг перпов (знак: + заплатили, − получили).
    # Учтён в pnl отдельно от fees, чтобы было видно структуру издержек.
    funding: float = 0.0
    # R-метрики (R = первоначальный риск входа-стоп, net-значения).
    r_multiple: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    # Контекст входа (из позиции).
    regime: str = ""
    timeframe: str = ""
    regime_axes: str = ""


class PaperBroker:
    """Виртуальный брокер с частичными тейками и трейлинг-стопом."""

    def __init__(
        self,
        state_path: Path = Path("models/paper_positions.json"),
        trades_path: Path = Path("models/paper_trades.jsonl"),
        initial_capital: Decimal = Decimal("2000"),
        fee_pct: Decimal | None = None,
        slippage_pct: Decimal | None = None,
        cost_model: CostModel | None = None,
        max_leverage: Decimal = Decimal("2"),
        funding_rate: Decimal = Decimal("0.0001"),
        funding_interval_hours: Decimal = Decimal("8"),
        maintenance_margin_pct: Decimal = Decimal("0.005"),
    ):
        self.state_path = state_path
        self.trades_path = trades_path
        self.initial_capital = initial_capital
        self.positions: list[PaperPosition] = []
        self.realized_pnl: Decimal = Decimal("0")
        # Плечо: максимум. Фандинг перпов (ставка за интервал, по
        # умолчанию 0.01% за 8 часов — типичное значение, когда живая
        # ставка с биржи недоступна) ОБЯЗАТЕЛЬНО включается в PnL при
        # закрытии — «забыть» его нельзя.
        self.max_leverage = max(Decimal("1"), max_leverage)
        self.funding_rate = funding_rate
        self.funding_interval_hours = max(Decimal("1") / Decimal("60"), funding_interval_hours)
        self.maintenance_margin_pct = max(Decimal("0"), maintenance_margin_pct)
        # Живые значения с биржи (обновляет движок каждый тик):
        # mark price для ликвидаций, ставка фандинга для начислений.
        self._mark_prices: dict[str, Decimal] = {}
        self._funding_rates: dict[str, Decimal] = {}

        # Единая модель издержек (TZ P0-1).
        # Если передан cost_model — используем его напрямую.
        # Если переданы fee_pct/slippage_pct — создаём CostModel из них.
        # Если ничего не передано — пресет перпов BingX USDT-M
        # (тейкер 0.05% / мейкер 0.02%) и slippage 0.1%.
        if cost_model is not None:
            self.cost_model = cost_model
        elif fee_pct is None and slippage_pct is None:
            self.cost_model = bingx_perps_cost_model()
            self.fee_pct = BINGX_PERPS_TAKER_FEE
            self.slippage_pct = Decimal("0.001")
        else:
            fp = fee_pct if fee_pct is not None else BINGX_PERPS_TAKER_FEE
            sp = slippage_pct if slippage_pct is not None else Decimal("0.001")
            if fp == 0 and sp == 0:
                # Legacy test mode: нулевые издержки для проверки механики.
                # CostModel не допускает taker_fee_rate=0, поэтому храним
                # плоские значения и используем упрощённую логику.
                self.cost_model = None
                self.fee_pct = Decimal("0")
                self.slippage_pct = Decimal("0")
            else:
                try:
                    self.cost_model = cost_model_from_flat(fp, sp)
                    self.fee_pct = fp
                    self.slippage_pct = sp
                except ValueError:
                    # Fallback if fee_pct=0 but slippage>0 or vice versa.
                    self.cost_model = None
                    self.fee_pct = fp
                    self.slippage_pct = sp

        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
            self.positions = [PaperPosition(**p) for p in data.get("positions", [])]
            # Decimal поля после json — приводим.
            for pos in self.positions:
                pos.entry_price = Decimal(str(pos.entry_price))
                pos.quantity = Decimal(str(pos.quantity))
                pos.stop_loss = Decimal(str(pos.stop_loss))
                pos.take_profits = [Decimal(str(x)) for x in pos.take_profits]
                if pos.highest_price is not None:
                    pos.highest_price = Decimal(str(pos.highest_price))
                if pos.lowest_price is not None:
                    pos.lowest_price = Decimal(str(pos.lowest_price))
                if pos.initial_quantity is None or pos.initial_quantity == 0:
                    pos.initial_quantity = pos.quantity
                else:
                    pos.initial_quantity = Decimal(str(pos.initial_quantity))
                if pos.fill_price is not None:
                    pos.fill_price = Decimal(str(pos.fill_price))
                else:
                    # Позиция открыта до введения модели издержек.
                    pos.fill_price = pos.entry_price
                if pos.entry_fee_per_unit:
                    pos.entry_fee_per_unit = Decimal(str(pos.entry_fee_per_unit))
                # Плечо: поля могли отсутствовать в старых файлах.
                pos.leverage = Decimal(str(getattr(pos, "leverage", 1) or 1))
                pos.margin_used = Decimal(str(getattr(pos, "margin_used", 0) or 0))
                # FIX: migrate risk_distance=0 (old files) -> 1% of entry
                try:
                    rd = Decimal(str(getattr(pos, 'risk_distance', 0) or 0))
                    if rd <= 0:
                        rd = abs(pos.entry_price - pos.stop_loss)
                        if rd <= 0:
                            rd = abs(pos.entry_price) * Decimal("0.01")
                            if rd <= 0:
                                rd = Decimal("0.001")
                        pos.risk_distance = rd
                    else:
                        pos.risk_distance = rd
                except Exception:
                    pos.risk_distance = abs(pos.entry_price - pos.stop_loss) or Decimal("0.01")
                # FIX: regime fallback
                if not getattr(pos, 'regime', None):
                    pos.regime = "UNKNOWN"
            self.realized_pnl = Decimal(str(data.get("realized_pnl", 0)))
            # Восстанавливаем стартовый капитал из состояния, если он там есть.
            if data.get("initial_capital"):
                self.initial_capital = Decimal(str(data["initial_capital"]))
        except Exception as exc:
            logger.warning("Не загрузил состояние paper-брокера: %s", exc)

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "positions": [
                {
                    **asdict(p),
                    "entry_price": str(p.entry_price),
                    "quantity": str(p.quantity),
                    "stop_loss": str(p.stop_loss),
                    "take_profits": [str(x) for x in p.take_profits],
                    "initial_quantity": str(p.initial_quantity),
                    "highest_price": str(p.highest_price) if p.highest_price else None,
                    "lowest_price": str(p.lowest_price) if p.lowest_price else None,
                    "fill_price": str(p.fill_price) if p.fill_price is not None else None,
                    "entry_fee_per_unit": str(p.entry_fee_per_unit),
                    "risk_distance": str(p.risk_distance),
                    "leverage": str(p.leverage),
                    "margin_used": str(p.margin_used),
                    "regime": p.regime,
                    "timeframe": p.timeframe,
                }
                for p in self.positions
            ],
            "realized_pnl": str(self.realized_pnl),
            "initial_capital": str(self.initial_capital),
        }
        self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def _log_trade(self, trade: ClosedTrade) -> None:
        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.trades_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(trade), ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ public API
    @property
    def equity(self) -> Decimal:
        return self.initial_capital + self.realized_pnl

    def open_position(
        self,
        *,
        symbol: str,
        direction: str,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        quantity: Decimal,
        strategy: str = "",
        notes: dict | None = None,
        no_take_profit: bool = False,
        regime: str = "",
        timeframe: str = "",
        regime_axes: str = "",
        leverage: int | Decimal = 1,
    ) -> PaperPosition:
        lev = max(Decimal("1"), min(Decimal(str(leverage)), self.max_leverage))
        if lev > Decimal("1"):
            self._check_margin(entry_price, quantity, lev)
            self._check_liquidation(entry_price, stop_loss, direction, lev)
        # Разбиваем тейк на 3 уровня: 1R, 1.8R, 2.5R.
        # Флип-стратегии (ts_momentum) живут до смены режима — тейки им
        # не нужны, иначе частичные выходы искажают проверенное правило.
        if no_take_profit:
            tps: list[Decimal] = []
        else:
            risk = abs(entry_price - stop_loss)
            if direction == "long":
                tps = [
                    entry_price + risk * Decimal("1.0"),
                    entry_price + risk * Decimal("1.8"),
                    entry_price + risk * Decimal("2.5"),
                ]
            else:
                tps = [
                    entry_price - risk * Decimal("1.0"),
                    entry_price - risk * Decimal("1.8"),
                    entry_price - risk * Decimal("2.5"),
                ]
        qty = quantity
        # Эффективный вход: slippage против нас + тейкер-комиссия.
        if self.cost_model is not None:
            fill = self.cost_model.effective_entry_price(entry_price, direction)
            fee_per_unit = fill * self.cost_model.taker_fee_rate
        else:
            if direction == "long":
                fill = entry_price * (Decimal("1") + self.slippage_pct)
            else:
                fill = entry_price * (Decimal("1") - self.slippage_pct)
            fee_per_unit = fill * self.fee_pct
        # FIX: защита от risk_distance=0 (тогда R-метрики=0 и strategy_stats не растёт)
        _risk_dist = abs(entry_price - stop_loss)
        if _risk_dist <= 0:
            # fallback 1% от цены как минимальный риск
            _risk_dist = abs(entry_price) * __import__('decimal').Decimal("0.01")
            if _risk_dist <= 0:
                _risk_dist = __import__('decimal').Decimal("0.001")
        pos = PaperPosition(
            id=str(uuid.uuid4()),
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profits=tps,
            leverage=lev,
            margin_used=(entry_price * quantity / lev),
            strategy=strategy,
            notes=notes or {},
            fill_price=fill,
            entry_fee_per_unit=fee_per_unit,
            risk_distance=_risk_dist,
            regime=regime or "UNKNOWN",
            timeframe=timeframe,
            regime_axes=regime_axes,
        )
        pos.tp_filled = [False] * len(tps)
        pos.initial_quantity = qty
        self.positions.append(pos)
        logger.info(
            "OPEN %s %s qty=%s entry=%s stop=%s tp1=%s lev=%s margin=%s",
            direction, symbol, quantity, entry_price, stop_loss,
            tps[0] if tps else "-", pos.leverage, pos.margin_used,
        )
        self.save()
        return pos

    def on_bar(self, bar) -> list[ClosedTrade]:
        """Обработать новый бар (совместимость): экстремумы + выходы."""
        self.update_extremes(bar)
        return self.check_exits(bar)

    def update_extremes(self, bar) -> None:
        """Обновить экстремумы/счётчик баров (вынесено из on_bar, чтобы
        Exit Controller мог скорректировать стопы ДО проверки их срабатывания
        на том же баре — TZ §16)."""
        high = Decimal(str(bar.high))
        low = Decimal(str(bar.low))
        for pos in self.positions:
            if pos.symbol != getattr(bar, "symbol", pos.symbol):
                continue
            pos.highest_price = high if pos.highest_price is None else max(pos.highest_price, high)
            pos.lowest_price = low if pos.lowest_price is None else min(pos.lowest_price, low)
            pos.bars_held += 1

    def check_exits(self, bar) -> list[ClosedTrade]:
        """Стоп-лосс и частичные тейки по обновлённым экстремумам."""
        closed: list[ClosedTrade] = []
        high = Decimal(str(bar.high))
        low = Decimal(str(bar.low))
        for pos in list(self.positions):
            if pos.symbol != getattr(bar, "symbol", pos.symbol):
                continue
            # Стоп-лосс.
            stop_hit = (
                (pos.direction == "long" and low <= pos.stop_loss)
                or (pos.direction == "short" and high >= pos.stop_loss)
            )
            if stop_hit:
                closed.append(self._close(pos, pos.stop_loss, "stop_loss"))
                continue

            # Частичные тейки.
            for i, tp in enumerate(pos.take_profits):
                if pos.tp_filled[i]:
                    continue
                hit = (
                    (pos.direction == "long" and high >= tp)
                    or (pos.direction == "short" and low <= tp)
                )
                if not hit:
                    continue
                pos.tp_filled[i] = True
                frac = pos.tp_fractions[i]
                part_qty = pos.initial_quantity * Decimal(str(frac))
                pnl, fees, funding = self._pnl_with_fees(pos, part_qty, tp)
                r_mult, mfe_r, mae_r = self._r_metrics(pos, part_qty, pnl)
                self.realized_pnl += pnl
                trade = ClosedTrade(
                    id=pos.id,
                    symbol=pos.symbol,
                    direction=pos.direction,
                    entry_price=float(pos.entry_price),
                    exit_price=float(tp),
                    quantity=float(part_qty),
                    pnl=float(pnl),
                    pnl_pct=float(pnl / (pos.entry_price * part_qty) * 100) if part_qty else 0,
                    exit_reason=f"tp{i+1}",
                    strategy=pos.strategy,
                    opened_at=pos.opened_at,
                    closed_at=int(datetime.now(tz=UTC).timestamp() * 1000),
                    fees=float(fees),
                    funding=float(funding),
                    r_multiple=r_mult,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                    regime=pos.regime,
                    timeframe=pos.timeframe,
                    regime_axes=pos.regime_axes,
                )
                self._log_trade(trade)
                closed.append(trade)
                # Уменьшаем оставшийся объём.
                pos.quantity -= part_qty
                # После первого тейка включаем трейлинг и двигаем стоп в
                # НЕТТО-безубыток (вход + комиссии + плата за плечо):
                # сырой вход с комиссиями — это маленький гарантированный минус.
                if i == 0:
                    pos.trailing_activated = True
                    be = self.net_breakeven_price(pos)
                    if pos.direction == "long":
                        pos.stop_loss = max(pos.stop_loss, be)
                    else:
                        pos.stop_loss = min(pos.stop_loss, be)
                # После второго — тянем стоп вслед за ценой.
                if i == 1 and pos.direction == "long" and pos.highest_price:
                    pos.stop_loss = max(
                        pos.stop_loss, pos.highest_price - abs(pos.entry_price - pos.take_profits[0])
                    )
                if i == 1 and pos.direction == "short" and pos.lowest_price:
                    pos.stop_loss = min(
                        pos.stop_loss, pos.lowest_price + abs(pos.entry_price - pos.take_profits[0])
                    )

            # Если позиция полностью закрыта тейками.
            if pos.quantity <= 0:
                self.positions.remove(pos)
        self.save()
        return closed

    def close_position(self, pos_id: str, price: Decimal, reason: str) -> ClosedTrade | None:
        """Закрыть одну позицию (для Exit Controller, TZ §16)."""
        for pos in list(self.positions):
            if pos.id == pos_id:
                trade = self._close(pos, price, reason)
                self.save()
                return trade
        return None

    def close_positions(
        self, symbol: str, price: Decimal, reason: str
    ) -> list[ClosedTrade]:
        """Закрыть ВСЕ открытые позиции по символу (для флипов/выхода)."""
        closed: list[ClosedTrade] = []
        for pos in list(self.positions):
            if pos.symbol == symbol:
                closed.append(self._close(pos, price, reason))
        if closed:
            self.save()
        return closed

    def _close(self, pos: PaperPosition, price: Decimal, reason: str) -> ClosedTrade:
        qty = pos.quantity
        pnl, fees, funding = self._pnl_with_fees(pos, qty, price)
        r_mult, mfe_r, mae_r = self._r_metrics(pos, qty, pnl)
        self.realized_pnl += pnl
        self.positions.remove(pos)
        trade = ClosedTrade(
            id=pos.id,
            symbol=pos.symbol,
            direction=pos.direction,
            entry_price=float(pos.entry_price),
            exit_price=float(price),
            quantity=float(qty),
            pnl=float(pnl),
            pnl_pct=float(pnl / (pos.entry_price * qty) * 100) if qty else 0,
            exit_reason=reason,
            strategy=pos.strategy,
            opened_at=pos.opened_at,
            closed_at=int(datetime.now(tz=UTC).timestamp() * 1000),
            fees=float(fees),
            funding=float(funding),
            r_multiple=r_mult,
            mfe_r=mfe_r,
            mae_r=mae_r,
            regime=pos.regime,
            timeframe=pos.timeframe,
            regime_axes=pos.regime_axes,
        )
        self._log_trade(trade)
        return trade

    def _r_metrics(
        self, pos: PaperPosition, qty: Decimal, pnl: Decimal
    ) -> tuple[float, float, float]:
        """(r_multiple, mfe_r, mae_r) закрытой части в R-единицах (net)."""
        if not pos.risk_distance or qty <= 0:
            return 0.0, 0.0, 0.0
        risk_d = pos.risk_distance
        r = float(pnl / (risk_d * qty))
        if pos.direction == "long":
            mfe = (
                (pos.highest_price - pos.entry_price) / risk_d
                if pos.highest_price is not None
                else Decimal("0")
            )
            mae = (
                (pos.entry_price - pos.lowest_price) / risk_d
                if pos.lowest_price is not None
                else Decimal("0")
            )
        else:
            mfe = (
                (pos.entry_price - pos.lowest_price) / risk_d
                if pos.lowest_price is not None
                else Decimal("0")
            )
            mae = (
                (pos.highest_price - pos.entry_price) / risk_d
                if pos.highest_price is not None
                else Decimal("0")
            )
        return r, float(mfe), float(mae)

    # ------------------------------------------------- perps: маржа и фандинг
    def _check_margin(self, entry_price: Decimal, quantity: Decimal, lev: Decimal) -> None:
        """Маржи должно хватать: сумма занятой маржи <= equity."""
        need = entry_price * quantity / lev
        used = sum((p.margin_used for p in self.positions), Decimal("0"))
        if used + need > self.equity:
            raise ValueError(
                f"insufficient margin: need={need} used={used} equity={self.equity}"
            )

    def liquidation_price(
        self, entry_price: Decimal, direction: str, lev: Decimal
    ) -> Decimal | None:
        """Оценка цены ликвидации изолированной перпетуал-позиции.

        long  liq = entry * (1 - 1/lev + mm);
        short liq = entry * (1 + 1/lev - mm).
        Сравнивать — с MARK price (см. check_liquidations).

        None — ликвидация невозможна (lev=1 или mm >= 1/lev).
        """
        borrow = Decimal("1") / lev - self.maintenance_margin_pct
        if borrow <= 0:
            return None
        if direction in ("long", "buy"):
            return entry_price * (Decimal("1") - borrow)
        return entry_price * (Decimal("1") + borrow)

    def _check_liquidation(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        direction: str,
        lev: Decimal,
    ) -> None:
        """Стоп обязан сработать РАНЬШЕ ликвидации.

        Оценка цены ликвидации (изолированная маржа перпов, maintenance mm):
        long  liq = entry * (1 - 1/lev + mm);  short liq = entry * (1 + 1/lev - mm).
        """
        liq = self.liquidation_price(entry_price, direction, lev)
        if liq is None:
            return
        if direction in ("long", "buy"):
            if stop_loss <= liq:
                raise ValueError(
                    f"stop {stop_loss} не переживёт ликвидацию {liq} при плече {lev} — уменьшите плечо"
                )
        else:
            if stop_loss >= liq:
                raise ValueError(
                    f"stop {stop_loss} не переживёт ликвидацию {liq} при плече {lev} — уменьшите плечо"
                )

    # ------------------------------------------------- perps: mark и фандинг
    def update_mark_price(self, symbol: str, mark_price: Decimal) -> None:
        """Запомнить mark price символа (обновляет движок каждый тик)."""
        try:
            self._mark_prices[symbol] = Decimal(str(mark_price))
        except Exception:
            logger.debug("Некорректная mark price %s: %r", symbol, mark_price)

    def set_funding_rate(self, symbol: str, rate: Decimal) -> None:
        """Запомнить живую ставку фандинга символа (за интервал)."""
        try:
            self._funding_rates[symbol] = Decimal(str(rate))
        except Exception:
            logger.debug("Некорректная ставка фандинга %s: %r", symbol, rate)

    def _position_funding_rate(self, pos: PaperPosition) -> Decimal:
        """Ставка фандинга позиции: живая с биржи или дефолтная."""
        return self._funding_rates.get(pos.symbol, self.funding_rate)

    def _funding_intervals(self, pos: PaperPosition) -> Decimal:
        """Сколько интервалов фандинга прожила позиция (pro-rata).

        Время — детерминированное: bars_held × длительность timeframe.
        Реальная биржа списывает фандинг дискретно 3 раза в сутки
        (00:00/08:00/16:00 UTC+8 по открытым позициям); здесь —
        пропорциональная аппроксимация, честная в среднем и
        детерминированная при реплее.
        """
        minutes = _TIMEFRAME_MINUTES.get(getattr(pos, "timeframe", "") or "", 60)
        held_minutes = Decimal(pos.bars_held) * Decimal(minutes)
        interval_minutes = self.funding_interval_hours * Decimal("60")
        if held_minutes <= 0 or interval_minutes <= 0:
            return Decimal("0")
        return held_minutes / interval_minutes

    def funding_payment(
        self, pos: PaperPosition, qty: Decimal, fill: Decimal
    ) -> Decimal:
        """Фандинг к уплате за закрываемый объём (знак: + платим, − получаем).

        Начисляется на НОТИОНАЛ (фандинг перпов не зависит от плеча):
        payment = fill * qty * rate * intervals. При положительной ставке
        лонг платит шорту, при отрицательной — наоборот. Вызывается при
        каждом закрытии (в т.ч. частичном) — «забыть» фандинг невозможно.
        В legacy-режиме нулевых издержек (cost_model=None, fee=0) фандинг
        тоже нулевой — механика тестируется на чистых ценах.
        """
        if self.cost_model is None and getattr(self, "fee_pct", Decimal("0")) == 0:
            return Decimal("0")
        rate = self._position_funding_rate(pos)
        if rate == 0 or qty <= 0:
            return Decimal("0")
        intervals = self._funding_intervals(pos)
        if intervals <= 0:
            return Decimal("0")
        payment = fill * qty * rate * intervals
        if pos.direction in ("short", "sell"):
            return -payment
        return payment

    def check_liquidations(self, symbol: str | None = None) -> list[ClosedTrade]:
        """Ликвидировать позиции, чей mark price пробил цену ликвидации.

        Изолированная маржа: каждая позиция живёт/умирает сама по себе.
        Исполнение — по цене ликвидации (консервативная аппроксимация:
        реальное исполнение чуть хуже из-за extra-комиссии ликвидации).
        Без известной mark price символ пропускается (fail-closed: лучше
        пропустить проверку, чем ликвидировать по неверной цене).
        """
        closed: list[ClosedTrade] = []
        for pos in list(self.positions):
            if symbol is not None and pos.symbol != symbol:
                continue
            lev = pos.leverage if pos.leverage > 0 else Decimal("1")
            if lev <= Decimal("1"):
                continue
            mark = self._mark_prices.get(pos.symbol)
            if mark is None:
                continue
            liq = self.liquidation_price(pos.entry_price, pos.direction, lev)
            if liq is None:
                continue
            liquidated = (
                (pos.direction in ("long", "buy") and mark <= liq)
                or (pos.direction in ("short", "sell") and mark >= liq)
            )
            if liquidated:
                logger.warning(
                    "LIQ %s %s mark=%s пробил liq=%s (entry=%s lev=%s)",
                    pos.direction, pos.symbol, mark, liq,
                    pos.entry_price, pos.leverage,
                )
                closed.append(self._close(pos, liq, "liquidation"))
        if closed:
            self.save()
        return closed

    def net_breakeven_price(self, pos: PaperPosition) -> Decimal:
        """Цена выхода, при которой NET PnL оставшегося объёма = 0.

        Учитывает: эффективный вход (slippage), тейкер-комиссию ОБОИХ
        сторон, slippage выхода и накопленный фандинг. Обычный
        «безубыток в точку входа» с комиссиями — это маленький минус;
        эта цена гарантирует честный ноль (и слегка плюс).
        """
        fill = pos.fill_price if pos.fill_price is not None else pos.entry_price
        if self.cost_model is not None:
            f = self.cost_model.taker_fee_rate
            se = self.cost_model.slippage_pct
        else:
            f = self.fee_pct
            se = self.slippage_pct
        # Фандинг на единицу объёма со знаком (шорт при rate>0 получает —
        # его безубыток ниже точки входа).
        fund_unit = self.funding_payment(pos, Decimal("1"), fill)
        one = Decimal("1")
        if pos.direction == "short":
            # fill(1-f) - fund = X(1+se)(1+f)
            return (fill * (one - f) - fund_unit) / ((one + se) * (one + f))
        # long: X(1-se)(1-f) = fill(1+f) + fund
        return (fill * (one + f) + fund_unit) / ((one - se) * (one - f))

    def _pnl_with_fees(
        self, pos: PaperPosition, qty: Decimal, exit_price: Decimal
    ) -> tuple[Decimal, Decimal, Decimal]:
        """(нетто PnL, комиссии, фандинг) закрытия ``qty`` по ``exit_price``.

        Считаем по эффективной цене входа (с slippage), применяем
        slippage на выход и тейкер-комиссию с обеих сторон. Комиссия входа
        делится пропорционально закрытому объёму (для частичных тейков).
        Фандинг — отдельно со знаком (+ заплатили, − получили).

        Использует CostModel если доступен (TZ P0-1), иначе legacy-логику.
        """
        fill = pos.fill_price if pos.fill_price is not None else pos.entry_price
        if self.cost_model is not None:
            exit_fill = self.cost_model.effective_exit_price(exit_price, pos.direction)
            if pos.direction in ("long", "buy"):
                gross = (exit_fill - fill) * qty
            else:
                gross = (fill - exit_fill) * qty
            exit_fee = exit_fill * self.cost_model.taker_fee_rate * qty
            fees = pos.entry_fee_per_unit * qty + exit_fee
        else:
            if pos.direction == "long":
                exit_fill = exit_price * (Decimal("1") - self.slippage_pct)
                gross = (exit_fill - fill) * qty
            else:
                exit_fill = exit_price * (Decimal("1") + self.slippage_pct)
                gross = (fill - exit_fill) * qty
            fees = pos.entry_fee_per_unit * qty + exit_fill * self.fee_pct * qty
        # Фандинг перпов — на нотионал, по времени удержания.
        funding = self.funding_payment(pos, qty, fill)
        return gross - fees - funding, fees, funding

    def _pnl(self, pos: PaperPosition, qty: Decimal, exit_price: Decimal) -> Decimal:
        pnl, _, _ = self._pnl_with_fees(pos, qty, exit_price)
        return pnl
