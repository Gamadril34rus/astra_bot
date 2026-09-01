"""
ASTRA BOT — Unified Cost Model.

Единый источник истины для комиссий и slippage. Используется всеми
контурами: PaperBroker, legacy PaperTradingEngine, Backtester, ExecutionEngine.

Модель:
- maker/taker комиссия (%) — на каждую сторону (вход + выход);
- slippage (%) — на каждую сторону, по худшей стороне;
- funding rate (опционально, для деривативов).

Инвариант: суммарные издержки round-trip ≥ 2 × notional × min_fee_rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal


Side = Literal["long", "short", "buy", "sell"]


@dataclass(frozen=True)
class CostModel:
    """Единая модель торговых издержек.

    Все ставки — в долях (0.001 = 0.1%).
    ``frozen=True`` защищает от случайного изменения после создания.
    """

    # Тейкер-комиссия (используется для paper/demo — мы всегда taker).
    taker_fee_rate: Decimal = Decimal("0.001")  # 0.1%
    # Мейкер-комиссия (для бэктестера — limit-ордера).
    maker_fee_rate: Decimal = Decimal("0.001")  # 0.1%
    # Slippage — проскальзывание на сторону (вход ИЛИ выход).
    slippage_pct: Decimal = Decimal("0.001")  # 0.1%
    # Funding rate (8h) — для perpetual futures, опционально.
    funding_rate: Decimal = Decimal("0")

    # --- Минимальная ставка для инварианта (самая низкая из maker/taker).
    @property
    def min_fee_rate(self) -> Decimal:
        return min(self.taker_fee_rate, self.maker_fee_rate)

    def __post_init__(self) -> None:
        """Запрещаем нулевые комиссии — это главная причина P0-1."""
        if self.taker_fee_rate < 0 or self.maker_fee_rate < 0:
            raise ValueError("Fee rates must be non-negative")
        if self.slippage_pct < 0:
            raise ValueError("Slippage must be non-negative")
        # Taker fee must be > 0 to ensure every trade pays fees.
        # This is the core invariant that catches legacy paper_engine (fees=0).
        if self.taker_fee_rate <= 0:
            raise ValueError(
                "taker_fee_rate must be > 0. "
                "Zero-fee trading distorts PnL (TZ P0-1)."
            )

    # --- Effective fill price ---

    def effective_entry_price(
        self, price: Decimal, direction: Side
    ) -> Decimal:
        """Цена входа с учётом slippage (худшая сторона).

        long  → цена уходит вверх (мы покупаем дороже);
        short → цена уходит вниз (мы продаём дешевле).
        """
        if direction in ("long", "buy"):
            return price * (Decimal("1") + self.slippage_pct)
        else:
            return price * (Decimal("1") - self.slippage_pct)

    def effective_exit_price(
        self, price: Decimal, direction: Side
    ) -> Decimal:
        """Цена выхода с учётом slippage (худшая сторона).

        long  → выход дешевле;
        short → выход дороже.
        """
        if direction in ("long", "buy"):
            return price * (Decimal("1") - self.slippage_pct)
        else:
            return price * (Decimal("1") + self.slippage_pct)

    # --- Fee amounts ---

    def entry_fee(
        self,
        price: Decimal,
        quantity: Decimal,
        direction: Side,
        *,
        is_maker: bool = False,
    ) -> Decimal:
        """Комиссия за вход. Считается от effective price (со slippage)."""
        fee_rate = self.maker_fee_rate if is_maker else self.taker_fee_rate
        fill = self.effective_entry_price(price, direction)
        return fill * quantity * fee_rate

    def exit_fee(
        self,
        price: Decimal,
        quantity: Decimal,
        direction: Side,
        *,
        is_maker: bool = False,
    ) -> Decimal:
        """Комиссия за выход. Считается от effective exit price."""
        fee_rate = self.maker_fee_rate if is_maker else self.taker_fee_rate
        fill = self.effective_exit_price(price, direction)
        return fill * quantity * fee_rate

    def round_trip_fees(
        self,
        entry_price: Decimal,
        exit_price: Decimal,
        quantity: Decimal,
        direction: Side,
        *,
        is_maker: bool = False,
    ) -> Decimal:
        """Полная комиссия round-trip (вход + выход)."""
        return (
            self.entry_fee(entry_price, quantity, direction, is_maker=is_maker)
            + self.exit_fee(exit_price, quantity, direction, is_maker=is_maker)
        )

    def round_trip_slippage(
        self,
        entry_price: Decimal,
        exit_price: Decimal,
        quantity: Decimal,
        direction: Side,
    ) -> Decimal:
        """Полный slippage-cost round-trip (вход + выход).

        Считается как разница между mid-price PnL и effective PnL.
        """
        if direction in ("long", "buy"):
            entry_slip = self.effective_entry_price(entry_price, direction) - entry_price
            exit_slip = exit_price - self.effective_exit_price(exit_price, direction)
        else:
            entry_slip = entry_price - self.effective_entry_price(entry_price, direction)
            exit_slip = self.effective_exit_price(exit_price, direction) - exit_price
        return (entry_slip + exit_slip) * quantity

    def net_pnl(
        self,
        entry_price: Decimal,
        exit_price: Decimal,
        quantity: Decimal,
        direction: Side,
    ) -> tuple[Decimal, Decimal]:
        """(net_pnl, total_costs) — PnL после всех издержек.

        net_pnl = gross_pnl - round_trip_fees - round_trip_slippage
        total_costs = round_trip_fees + round_trip_slippage
        """
        if direction in ("long", "buy"):
            gross = (
                self.effective_exit_price(exit_price, direction)
                - self.effective_entry_price(entry_price, direction)
            ) * quantity
        else:
            gross = (
                self.effective_entry_price(entry_price, direction)
                - self.effective_exit_price(exit_price, direction)
            ) * quantity
        fees = self.round_trip_fees(entry_price, exit_price, quantity, direction)
        return gross - fees, fees

    # --- Invariant ---

    @staticmethod
    def check_round_trip_invariant(
        entry_fee: Decimal,
        exit_fee: Decimal,
        entry_notional: Decimal,
        exit_notional: Decimal,
        min_fee_rate: Decimal,
    ) -> bool:
        """Инвариант: КАЖДАЯ сторона charged ≥ notional × min_fee_rate.

        Round-trip = 2 стороны. Проверяем каждую отдельно:
        - entry_fee ≥ entry_notional × min_fee_rate
        - exit_fee  ≥ exit_notional  × min_fee_rate

        Это ловит случай, когда одна из сторон = 0 (legacy paper_engine).
        Возвращает True если инвариант выполнен.
        """
        if min_fee_rate <= 0:
            return True
        entry_ok = entry_notional <= 0 or entry_fee >= entry_notional * min_fee_rate
        exit_ok = exit_notional <= 0 or exit_fee >= exit_notional * min_fee_rate
        return entry_ok and exit_ok

    def assert_invariant(
        self,
        entry_price: Decimal,
        exit_price: Decimal,
        quantity: Decimal,
        direction: Side,
    ) -> None:
        """Бросает ValueError если инвариант round-trip комиссий нарушен.

        Проверяет что КАЖДАЯ сторона сделки имеет комиссию ≥ notional × min_fee_rate.
        """
        e_fee = self.entry_fee(entry_price, quantity, direction)
        x_fee = self.exit_fee(exit_price, quantity, direction)
        e_notional = self.effective_entry_price(entry_price, direction) * quantity
        x_notional = self.effective_exit_price(exit_price, direction) * quantity

        if not self.check_round_trip_invariant(
            e_fee, x_fee, e_notional, x_notional, self.min_fee_rate
        ):
            raise ValueError(
                f"Cost invariant violated: "
                f"entry_fee={e_fee} (notional={e_notional}), "
                f"exit_fee={x_fee} (notional={x_notional}), "
                f"min_fee_rate={self.min_fee_rate}. "
                f"Each side must be charged ≥ notional × min_fee_rate."
            )


# --- Default instance for convenience ---
DEFAULT_COST_MODEL = CostModel()


# --- Convenience: create CostModel from a flat fee_pct + slippage_pct ---
def cost_model_from_flat(
    fee_pct: Decimal = Decimal("0.001"),
    slippage_pct: Decimal = Decimal("0.001"),
) -> CostModel:
    """Создать CostModel из плоских параметров (для обратной совместимости
    с PaperBroker(fee_pct=, slippage_pct=))."""
    return CostModel(
        taker_fee_rate=fee_pct,
        maker_fee_rate=fee_pct,
        slippage_pct=slippage_pct,
    )
