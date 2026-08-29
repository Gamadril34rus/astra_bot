"""
ASTRA BOT — Paper execution engine.

Принимает финальное решение от ``DecisionPipeline`` и исполняет его
на виртуальном счёте. Поддерживает:

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

logger = logging.getLogger(__name__)


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
        fee_pct: Decimal = Decimal("0.001"),
        slippage_pct: Decimal = Decimal("0.001"),
    ):
        self.state_path = state_path
        self.trades_path = trades_path
        self.initial_capital = initial_capital
        self.positions: list[PaperPosition] = []
        self.realized_pnl: Decimal = Decimal("0")
        # Реальные издержки исполнения (master prompt §24): тейкер-комиссия
        # и slippage на каждую сторону. База 0.1%/0.1% совпадает с baseline
        # в scripts/run_full_research_audit.py, чтобы live-paper цифры были
        # сравнимы с audit.
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
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
    ) -> PaperPosition:
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
        if direction == "long":
            fill = entry_price * (Decimal("1") + self.slippage_pct)
        else:
            fill = entry_price * (Decimal("1") - self.slippage_pct)
        pos = PaperPosition(
            id=str(uuid.uuid4()),
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profits=tps,
            strategy=strategy,
            notes=notes or {},
            fill_price=fill,
            entry_fee_per_unit=fill * self.fee_pct,
            risk_distance=abs(entry_price - stop_loss),
            regime=regime,
            timeframe=timeframe,
            regime_axes=regime_axes,
        )
        pos.tp_filled = [False] * len(tps)
        pos.initial_quantity = qty
        self.positions.append(pos)
        logger.info(
            "OPEN %s %s qty=%s entry=%s stop=%s tp1=%s",
            direction, symbol, quantity, entry_price, stop_loss,
            tps[0] if tps else "-",
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
                pnl, fees = self._pnl_with_fees(pos, part_qty, tp)
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
                # После первого тейка включаем трейлинг и двигаем стоп в БУ.
                if i == 0:
                    pos.trailing_activated = True
                    pos.stop_loss = pos.entry_price
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
        pnl, fees = self._pnl_with_fees(pos, qty, price)
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

    def _pnl_with_fees(
        self, pos: PaperPosition, qty: Decimal, exit_price: Decimal
    ) -> tuple[Decimal, Decimal]:
        """Нетто PnL и издержки закрытия ``qty`` по цене ``exit_price``.

        Считаем по эффективной цене входа (с slippage), применяем
        slippage на выход и тейкер-комиссию с обеих сторон. Комиссия входа
        делится пропорционально закрытому объёму (для частичных тейков).
        """
        fill = pos.fill_price if pos.fill_price is not None else pos.entry_price
        if pos.direction == "long":
            exit_fill = exit_price * (Decimal("1") - self.slippage_pct)
            gross = (exit_fill - fill) * qty
        else:
            exit_fill = exit_price * (Decimal("1") + self.slippage_pct)
            gross = (fill - exit_fill) * qty
        fees = pos.entry_fee_per_unit * qty + exit_fill * self.fee_pct * qty
        return gross - fees, fees

    def _pnl(self, pos: PaperPosition, qty: Decimal, exit_price: Decimal) -> Decimal:
        pnl, _ = self._pnl_with_fees(pos, qty, exit_price)
        return pnl
