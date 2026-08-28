"""
ASTRA BOT — Risk Engine
Движок управления рисками
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum

from ..adapters.base import Instrument
from ..core import events, models
from ..core.metrics import (
    DRAWDOWN_PCT,
    EQUITY,
    OPEN_POSITIONS,
    RISK_DECISIONS,
    RISK_STATE,
)
from ..core.state import RiskState

logger = logging.getLogger(__name__)


class RiskDecision(Enum):
    """Решение риск-движка"""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REDUCED = "REDUCED"


@dataclass
class RiskConfig:
    """Конфигурация риска"""
    # Риск на сделку
    risk_per_trade: Decimal = Decimal("0.004")  # 0.4%

    # Дневные лимиты
    daily_loss_limit: Decimal = Decimal("0.02")  # 2%
    weekly_loss_limit: Decimal = Decimal("0.04")  # 4%

    # Просадки
    soft_drawdown: Decimal = Decimal("0.05")  # 5%
    hard_drawdown: Decimal = Decimal("0.08")  # 8%
    emergency_drawdown: Decimal = Decimal("0.10")  # 10%

    # Экспозиция
    max_exposure_pct: Decimal = Decimal("0.30")  # 30%
    max_open_positions: int = 5

    # Portfolio-экспозиция (Этап 5): gross / net / корреляционная группа.
    # None = max_exposure_pct, т.е. по умолчанию поведение НЕ меняется;
    # конфиг может только УЖЕСТОЧАТЬ (survival > returns).
    max_gross_exposure_pct: Decimal | None = None
    max_net_exposure_pct: Decimal | None = None
    max_group_exposure_pct: Decimal | None = None
    # Группы корреляции: символ → группа. Пусто = одна группа «crypto»
    # (все крипто-пары в крахе коррелируют с BTC ≈ 1).
    correlation_groups: dict[str, str] = field(default_factory=dict)
    default_correlation_group: str = "crypto"

    # Волатильность
    high_volatility_multiplier: Decimal = Decimal("0.5")
    extreme_volatility_threshold: Decimal = Decimal("0.15")
    volatility_lookback: int = 20

    # Корреляция
    correlation_limit: Decimal = Decimal("0.7")

    # Бета к BTC (Этап 5): размер позиции делится на max(1, beta) —
    # только уменьшает, никогда не увеличивает. BTC = 1.0 (основной
    # бенчмарк), альты — выше (скачистее). Unknown-символ → default_beta.
    default_beta: Decimal = Decimal("1.5")
    betas: dict[str, Decimal] = field(default_factory=lambda: {
        "BTC": Decimal("1.0"),
        "ETH": Decimal("1.4"),
        "SOL": Decimal("1.8"),
        "XRP": Decimal("1.3"),
        "DOGE": Decimal("1.8"),
        "TON": Decimal("1.5"),
    })

    # Инкременты риска по просадке
    drawdown_adaptation: list[dict] = field(default_factory=lambda: [
        {"drawdown": Decimal("0"), "risk_multiplier": Decimal("1.0")},
        {"drawdown": Decimal("0.03"), "risk_multiplier": Decimal("0.75")},
        {"drawdown": Decimal("0.05"), "risk_multiplier": Decimal("0.5")},
        {"drawdown": Decimal("0.08"), "risk_multiplier": Decimal("0.0")},
    ])


@dataclass
class PositionSizeResult:
    """Результат расчёта размера позиции"""
    accepted: bool
    quantity: Decimal | None = None
    risk_amount: Decimal | None = None
    risk_state: str | None = None
    stop_distance: Decimal | None = None
    reason: str | None = None
    adjusted_size: Decimal | None = None


@dataclass
class RiskCheckResult:
    """Результат проверки риска"""
    approved: bool
    risk_state: str
    daily_loss_used: Decimal
    weekly_loss_used: Decimal
    current_drawdown: Decimal
    max_allowed_loss: Decimal
    reason: str | None = None
    details: dict = field(default_factory=dict)


class RiskEngine:
    """
    Движок управления рисками.

    Проверяет все сделки на соответствие риск-параметрам.
    Содержит критические лимиты, которые нельзя обойти.
    """

    def __init__(self, config: RiskConfig = None):
        self.config = config or RiskConfig()

        # Текущие значения (обновляются из состояния)
        self._daily_pnl: Decimal = Decimal("0")
        self._weekly_pnl: Decimal = Decimal("0")
        self._current_equity: Decimal = Decimal("0")
        self._initial_capital: Decimal = Decimal("0")
        self._high_water_mark: Decimal = Decimal("0")

        # Открытые позиции
        self._open_positions: dict[str, models.Position] = {}
        # Мета открытых позиций (Этап 5): id → {symbol, side, notional}.
        # Позиции могут добавляться по id (str) — для portfolio-лимитов
        # нужен номинал/сторон, поэтому meta передаётся явно.
        self._open_meta: dict[str, dict] = {}

        # История сделок за день/неделю
        self._today_trades: list[dict] = []
        self._week_trades: list[dict] = []

        # Статистика
        self._total_trades: int = 0
        self._total_wins: int = 0
        self._total_losses: int = 0

        # Режим
        self.risk_state = RiskState.NORMAL
        self.trading_enabled = True

    def set_capital(self, equity: Decimal, initial_capital: Decimal):
        """Установить текущий капитал"""
        self._current_equity = equity
        self._initial_capital = initial_capital
        self._update_high_water_mark()
        self._export_metrics()

    def _export_metrics(self) -> None:
        """Обновить Prometheus-гэджи, отражающие состояние risk-engine."""
        EQUITY.labels(account="risk").set(float(self._current_equity))
        DRAWDOWN_PCT.set(float(self.current_drawdown))
        OPEN_POSITIONS.labels(engine="risk").set(len(self._open_positions))
        state_values = {
            RiskState.NORMAL: 0,
            RiskState.REDUCED: 1,
            RiskState.DEFENSIVE: 2,
            RiskState.STOP: 3,
            RiskState.EMERGENCY: 4,
        }
        RISK_STATE.set(state_values.get(self.risk_state, 0))

    @staticmethod
    def _label_reason(reason: str | None) -> str:
        """Схлопнуть человекочитаемую причину в короткий лейбл для метрики."""
        if not reason:
            return "approved"
        lowered = reason.lower()
        if "drawdown" in lowered:
            return "drawdown"
        if "daily" in lowered:
            return "daily_loss"
        if "weekly" in lowered:
            return "weekly_loss"
        if "stop" in lowered and "distance" in lowered:
            return "invalid_stop"
        if "risk" in lowered and "exceed" in lowered:
            return "risk_per_trade"
        if "position" in lowered:
            return "max_positions"
        if "net exposure" in lowered:
            return "net_exposure"
        if "group" in lowered:
            return "group_exposure"
        if "exposure" in lowered:
            return "max_exposure"
        if "disabled" in lowered:
            return "trading_disabled"
        return "other"

    def _update_high_water_mark(self):
        """Обновить high water mark"""
        if self._current_equity > self._high_water_mark:
            self._high_water_mark = self._current_equity

    def restore_from_trades(self, trades, initial_capital: Decimal) -> None:
        """Восстановить состояние risk-движка из персистентных сделок.

        GitHub Actions поднимает свежий процесс на каждой 5-минутной
        сессии, поэтому дневной/недельный PnL, high water mark и
        HALT-статус пересобираются из ``models/paper_trades.jsonl``
        (источник истины уже персистится в CI — новый state-файл не нужен).

        ``trades`` — итерируемый список словарей с полями ``closed_at``
        (миллисекунды), ``pnl``, ``symbol``, ``direction``,
        ``entry_price``, ``quantity``.
        """
        now_ms = datetime.now(UTC).timestamp() * 1000
        day_ago_ms = now_ms - timedelta(hours=24).total_seconds() * 1000
        week_ago_ms = now_ms - timedelta(days=7).total_seconds() * 1000

        self._initial_capital = Decimal(initial_capital)
        self._current_equity = self._initial_capital
        peak = self._initial_capital
        running = self._initial_capital

        parsed: list[tuple[int, dict]] = []
        for t in trades or []:
            try:
                ts = int(float(t.get("closed_at") or 0))
                pnl = Decimal(str(t.get("pnl") or 0))
            except (TypeError, ValueError):
                continue
            if ts <= 0:
                continue
            parsed.append(
                (
                    ts,
                    {
                        "symbol": str(t.get("symbol", "")),
                        "side": str(t.get("direction", "")),
                        "entry_price": Decimal(str(t.get("entry_price") or 0)),
                        "quantity": Decimal(str(t.get("quantity") or 0)),
                        "pnl": pnl,
                        "won": pnl > 0,
                        "timestamp": datetime.fromtimestamp(ts / 1000, tz=UTC).replace(tzinfo=None),
                    },
                )
            )

        # Кривая капитала считается в хронологическом порядке.
        today: list[dict] = []
        week: list[dict] = []
        for ts, rec in sorted(parsed, key=lambda item: item[0]):
            running += rec["pnl"]
            if running > peak:
                peak = running
            if ts > week_ago_ms:
                week.append(rec)
            if ts > day_ago_ms:
                today.append(rec)

        self._today_trades = today
        self._week_trades = week
        self._daily_pnl = sum((t["pnl"] for t in today), Decimal("0"))
        self._weekly_pnl = sum((t["pnl"] for t in week), Decimal("0"))
        self._current_equity = running
        self._high_water_mark = peak
        # Если просадка уже выше порогов — HALT восстанавливается
        # автоматически, даже если процесс только что стартовал.
        self._check_drawdown_state()
        self._export_metrics()

    @property
    def daily_pnl(self) -> Decimal:
        """Текущий PnL за скользящие 24 часа."""
        return self._daily_pnl

    @property
    def weekly_pnl(self) -> Decimal:
        """Текущий PnL за скользящие 7 дней."""
        return self._weekly_pnl

    @property
    def current_drawdown(self) -> Decimal:
        """Текущая просадка в %"""
        if self._high_water_mark <= 0:
            return Decimal("0")
        return (self._high_water_mark - self._current_equity) / self._high_water_mark * Decimal("100")

    @property
    def daily_loss_pct(self) -> Decimal:
        """Потери за день в % от начального капитала"""
        if self._initial_capital <= 0:
            return Decimal("0")
        return abs(self._daily_pnl) / self._initial_capital * Decimal("100")

    @property
    def weekly_loss_pct(self) -> Decimal:
        """Потери за неделю в %"""
        if self._initial_capital <= 0:
            return Decimal("0")
        return abs(self._weekly_pnl) / self._initial_capital * Decimal("100")

    def _get_risk_multiplier(self) -> Decimal:
        """Получить множитель риска на основе просадки.

        ``current_drawdown`` возвращается в процентах (0-100), а
        ``drawdown_adaptation[*].drawdown`` хранится как доля (0-1).
        Возвращаем множитель последнего преодолённого порога.
        """
        dd = self.current_drawdown
        multiplier = Decimal("1")

        for tier in self.config.drawdown_adaptation:
            threshold = Decimal(str(tier["drawdown"])) * Decimal("100")
            if dd >= threshold:
                multiplier = Decimal(str(tier["risk_multiplier"]))
            else:
                break

        return multiplier

    # --------------------------------------------------- portfolio (Этап 5)
    def _beta_for(self, symbol: str) -> Decimal:
        """Бета символа к BTC (статическая оценка, консервативная)."""
        base = symbol.split("-")[0].split("/")[0].upper()
        return self.config.betas.get(base, self.config.default_beta)

    def _group_for(self, symbol: str) -> str:
        """Корреляционная группа символа (по умолчанию одна — crypto)."""
        return self.config.correlation_groups.get(
            symbol, self.config.default_correlation_group
        )

    def _position_notional(self, pos: object) -> Decimal:
        """Номинал позиции: из meta (при str-id) или из самого объекта."""
        meta = self._open_meta.get(
            pos if isinstance(pos, str) else getattr(pos, "id", ""), {}
        )
        if meta.get("notional") is not None:
            try:
                return Decimal(str(meta["notional"]))
            except (InvalidOperation, ValueError):
                return Decimal("0")
        qty = getattr(pos, "quantity", None)
        price = getattr(pos, "entry_price", None)
        if qty is None or price is None:
            return Decimal("0")
        return abs(Decimal(str(qty)) * Decimal(str(price)))

    def _position_side(self, pos: object) -> str:
        meta = self._open_meta.get(
            pos if isinstance(pos, str) else getattr(pos, "id", ""), {}
        )
        side = meta.get("side") or getattr(pos, "direction", "") or ""
        return str(side).lower()

    def _portfolio_exposures(self) -> dict:
        """Gross / net(long-short) / по группам по открытым позициям."""
        gross = Decimal("0")
        net = Decimal("0")
        groups: dict[str, Decimal] = {}
        for pos in self._open_positions.values():
            notional = self._position_notional(pos)
            gross += notional
            side = self._position_side(pos)
            if side == "short":
                net -= notional
            else:
                net += notional
            meta = self._open_meta.get(
                pos if isinstance(pos, str) else getattr(pos, "id", ""), {}
            )
            group = self._group_for(str(meta.get("symbol") or ""))
            groups[group] = groups.get(group, Decimal("0")) + notional
        return {"gross": gross, "net": net, "groups": groups}

    def check_trade(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        proposed_size: Decimal,
        strategy_name: str = "",
    ) -> RiskCheckResult:
        """Проверить сделку и записать решение в метрики."""
        result = self._check_trade_impl(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            proposed_size=proposed_size,
            strategy_name=strategy_name,
        )
        RISK_DECISIONS.labels(
            decision="approved" if result.approved else "rejected",
            reason=self._label_reason(result.reason),
        ).inc()
        self._export_metrics()
        return result

    def _check_trade_impl(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        proposed_size: Decimal,
        strategy_name: str = "",
    ) -> RiskCheckResult:
        """
        Проверить сделку на соответствие риск-параметрам.

        Returns:
            RiskCheckResult с решением
        """
        # 1. Проверка состояния риска
        if not self.trading_enabled:
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=self._daily_pnl,
                weekly_loss_used=self._weekly_pnl,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=Decimal("0"),
                reason="Trading is disabled",
            )

        # 2. Проверка просадки
        if self.current_drawdown >= float(self.config.hard_drawdown) * 100:
            self.risk_state = RiskState.STOP
            self.trading_enabled = False
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=self._daily_pnl,
                weekly_loss_used=self._weekly_pnl,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=Decimal("0"),
                reason=f"Hard drawdown reached: {self.current_drawdown:.2f}%",
            )

        # 3. Проверка дневных потерь
        daily_loss = abs(self._daily_pnl)
        max_daily_loss = self._initial_capital * self.config.daily_loss_limit

        if daily_loss >= max_daily_loss:
            return RiskCheckResult(
                approved=False,
                risk_state=RiskState.REDUCED.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=self._weekly_pnl,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=Decimal("0"),
                reason=f"Daily loss limit reached: {daily_loss:.2f} / {max_daily_loss:.2f}",
            )

        # 4. Проверка недельных потерь
        weekly_loss = abs(self._weekly_pnl)
        max_weekly_loss = self._initial_capital * self.config.weekly_loss_limit

        if weekly_loss >= max_weekly_loss:
            return RiskCheckResult(
                approved=False,
                risk_state=RiskState.DEFENSIVE.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=Decimal("0"),
                reason=f"Weekly loss limit reached: {weekly_loss:.2f} / {max_weekly_loss:.2f}",
            )

        # 5. Расчёт риска по сделке (Этап 5: бета к BTC — риск-бюджет
        # высокобетового символа делится на beta; только уменьшает)
        risk_multiplier = self._get_risk_multiplier()
        beta = self._beta_for(symbol)
        beta = max(Decimal("1"), beta)
        max_risk_per_trade = (
            self._current_equity
            * self.config.risk_per_trade
            * risk_multiplier
        ) / beta

        # 6. Расчёт фактического риска
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=max_risk_per_trade,
                reason="Invalid stop loss distance",
            )

        actual_risk = proposed_size * stop_distance

        if actual_risk > max_risk_per_trade:
            # Можно уменьшить размер
            adjusted_size = max_risk_per_trade / stop_distance
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=max_risk_per_trade,
                reason=f"Risk {actual_risk:.2f} exceeds max {max_risk_per_trade:.2f}. Adjusted size: {adjusted_size:.4f}",
                details={"adjusted_size": adjusted_size},
            )

        # 7. Проверка максимального количества позиций
        if len(self._open_positions) >= self.config.max_open_positions:
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=max_risk_per_trade,
                reason=f"Max positions reached: {len(self._open_positions)}",
            )

        # 8. Проверка gross-экспозиции (Этап 5: max_gross может быть
        # строже max_exposure; по умолчанию совпадает)
        expo = self._portfolio_exposures()
        gross_cap_pct = (
            self.config.max_gross_exposure_pct
            if self.config.max_gross_exposure_pct is not None
            else self.config.max_exposure_pct
        )
        current_exposure = expo["gross"]
        new_exposure = current_exposure + proposed_size * entry_price
        max_exposure = self._current_equity * gross_cap_pct

        if new_exposure > max_exposure:
            allowed_exposure = max_exposure - current_exposure
            if allowed_exposure <= 0:
                return RiskCheckResult(
                    approved=False,
                    risk_state=self.risk_state.value,
                    daily_loss_used=daily_loss,
                    weekly_loss_used=weekly_loss,
                    current_drawdown=self.current_drawdown,
                    max_allowed_loss=max_risk_per_trade,
                    reason=f"Max exposure reached: {current_exposure:.2f} / {max_exposure:.2f}",
                )

            adjusted_size = allowed_exposure / entry_price
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=max_risk_per_trade,
                reason=f"Exposure limit would be exceeded. Adjusted size: {adjusted_size:.4f}",
                details={"adjusted_size": adjusted_size},
            )

        # 8.5. Portfolio-лимиты (Этап 5): net-экспозиция (long − short)
        # и корреляционная группа (по умолчанию все крипто-пары — одна
        # группа: в крахе их корреляция с BTC ≈ 1). Значения по умолчанию
        # равны max_exposure_pct → дефолтное поведение не меняется;
        # конфиг может только ужесточать.
        side_l = str(side).lower()
        new_notional = proposed_size * entry_price
        signed = -new_notional if side_l == "short" else new_notional
        new_net = abs(expo["net"] + signed)
        net_cap = (
            self.config.max_net_exposure_pct
            if self.config.max_net_exposure_pct is not None
            else self.config.max_exposure_pct
        )
        if new_net > self._current_equity * net_cap:
            allowed_net = self._current_equity * net_cap - abs(expo["net"])
            if allowed_net <= 0:
                return RiskCheckResult(
                    approved=False,
                    risk_state=self.risk_state.value,
                    daily_loss_used=daily_loss,
                    weekly_loss_used=weekly_loss,
                    current_drawdown=self.current_drawdown,
                    max_allowed_loss=max_risk_per_trade,
                    reason=f"Net exposure limit reached: {expo['net']:.2f}",
                )
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=max_risk_per_trade,
                reason=f"Net exposure limit would be exceeded. Adjusted size: {allowed_net / entry_price:.4f}",
                details={"adjusted_size": allowed_net / entry_price},
            )

        group = self._group_for(symbol)
        group_cap = (
            self.config.max_group_exposure_pct
            if self.config.max_group_exposure_pct is not None
            else self.config.max_exposure_pct
        )
        new_group = expo["groups"].get(group, Decimal("0")) + new_notional
        if new_group > self._current_equity * group_cap:
            allowed_group = (
                self._current_equity * group_cap
                - expo["groups"].get(group, Decimal("0"))
            )
            if allowed_group <= 0:
                return RiskCheckResult(
                    approved=False,
                    risk_state=self.risk_state.value,
                    daily_loss_used=daily_loss,
                    weekly_loss_used=weekly_loss,
                    current_drawdown=self.current_drawdown,
                    max_allowed_loss=max_risk_per_trade,
                    reason=(
                        f"Correlated group '{group}' exposure limit reached: "
                        f"{expo['groups'].get(group, Decimal('0')):.2f}"
                    ),
                )
            return RiskCheckResult(
                approved=False,
                risk_state=self.risk_state.value,
                daily_loss_used=daily_loss,
                weekly_loss_used=weekly_loss,
                current_drawdown=self.current_drawdown,
                max_allowed_loss=max_risk_per_trade,
                reason=(
                    f"Correlated group '{group}' limit would be exceeded. "
                    f"Adjusted size: {allowed_group / entry_price:.4f}"
                ),
                details={"adjusted_size": allowed_group / entry_price},
            )

        # Все проверки пройдены
        return RiskCheckResult(
            approved=True,
            risk_state=self.risk_state.value,
            daily_loss_used=daily_loss,
            weekly_loss_used=weekly_loss,
            current_drawdown=self.current_drawdown,
            max_allowed_loss=max_risk_per_trade,
            reason=None,
        )

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: Decimal,
        stop_loss: Decimal,
        instrument: Instrument | None = None,
    ) -> PositionSizeResult:
        """
        Рассчитать оптимальный размер позиции.

        Возвращает размер позиции или причину отказа.
        """
        # Расчёт допустимого риска
        risk_multiplier = self._get_risk_multiplier()
        allowed_risk = (
            self._current_equity
            * self.config.risk_per_trade
            * risk_multiplier
        )

        # Расчёт расстояния до стопа
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            return PositionSizeResult(
                accepted=False,
                reason="Stop loss distance must be positive",
            )

        # Расчёт теоретического размера
        theoretical_size = allowed_risk / stop_distance

        # Проверка минимальных требований биржи
        if instrument:
            min_notional = instrument.min_notional
            min_qty = instrument.min_quantity

            # Проверка минимального номинала
            notional = theoretical_size * entry_price
            if notional < min_notional:
                return PositionSizeResult(
                    accepted=False,
                    reason=(
                        f"Position notional {notional:.2f} "
                        f"below minimum {min_notional:.2f}"
                    ),
                )

            # Проверка минимального количества
            if theoretical_size < min_qty:
                return PositionSizeResult(
                    accepted=False,
                    reason=(
                        f"Position size {theoretical_size:.6f} "
                        f"below minimum quantity {min_qty:.6f}"
                    ),
                )

        # Проверка максимального количества позиций
        if len(self._open_positions) >= self.config.max_open_positions:
            return PositionSizeResult(
                accepted=False,
                reason=f"Max positions reached: {len(self._open_positions)}",
            )

        # Проверка экспозиции
        current_exposure = sum(
            abs(p.quantity * p.entry_price)
            for p in self._open_positions.values()
        )
        new_exposure = current_exposure + theoretical_size * entry_price
        max_exposure = self._current_equity * self.config.max_exposure_pct

        if new_exposure > max_exposure:
            allowed_exposure = max_exposure - current_exposure
            if allowed_exposure <= 0:
                return PositionSizeResult(
                    accepted=False,
                    reason="Maximum exposure reached",
                )
            adjusted_size = allowed_exposure / entry_price
            return PositionSizeResult(
                accepted=False,
                reason="Exposure limit exceeded",
                adjusted_size=adjusted_size,
            )

        return PositionSizeResult(
            accepted=True,
            quantity=theoretical_size,
            risk_amount=allowed_risk,
            risk_state=self.risk_state.value,
            stop_distance=stop_distance,
        )

    def record_trade(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        quantity: Decimal,
        pnl: Decimal,
        won: bool,
    ):
        """Записать сделку"""
        trade_data = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "quantity": quantity,
            "pnl": pnl,
            "won": won,
            "timestamp": datetime.utcnow(),
        }

        # Обновление статистики
        self._total_trades += 1
        if won:
            self._total_wins += 1
        else:
            self._total_losses += 1

        # Обновление P&L
        self._daily_pnl += pnl
        self._weekly_pnl += pnl

        # Обновление капитала
        self._current_equity += pnl
        self._update_high_water_mark()

        # Проверка режимов
        self._check_drawdown_state()

        # Добавление в историю
        self._today_trades.append(trade_data)
        self._week_trades.append(trade_data)

        # Очистка старых записей
        self._cleanup_old_trades()

        logger.info(
            f"Trade recorded: {symbol} {side} "
            f"PnL={pnl:.2f}, Won={won}, "
            f"Total PnL={self._daily_pnl:.2f}"
        )
        self._export_metrics()

    def _check_drawdown_state(self):
        """Проверить состояние просадки"""
        dd = self.current_drawdown
        config = self.config

        if dd >= float(config.emergency_drawdown) * 100:
            self.risk_state = RiskState.EMERGENCY
            self.trading_enabled = False
            logger.critical(f"EMERGENCY STOP: Drawdown {dd:.2f}%")

            events.emit(events.EventType.EMERGENCY_STOP, {
                "reason": f"Emergency drawdown: {dd:.2f}%",
                "drawdown": str(dd),
                "risk_state": RiskState.EMERGENCY.value,
            })

        elif dd >= float(config.hard_drawdown) * 100:
            self.risk_state = RiskState.STOP
            self.trading_enabled = False
            logger.warning(f"STOP: Drawdown {dd:.2f}%")

            events.emit(events.EventType.RISK_LIMIT_HIT, {
                "event_type": "DRAWDOWN_THRESHOLD",
                "drawdown": str(dd),
                "limit": str(config.hard_drawdown),
                "action": "TRADING_STOPPED",
            })

        elif dd >= float(config.soft_drawdown) * 100:
            self.risk_state = RiskState.REDUCED
            logger.warning(f"Reduced risk: Drawdown {dd:.2f}%")

        else:
            if self.risk_state in [RiskState.REDUCED, RiskState.DEFENSIVE]:
                if dd < float(config.soft_drawdown) * 50:
                    self.risk_state = RiskState.NORMAL
                    logger.info("Risk state normalized")

    def _cleanup_old_trades(self):
        """Очистить старые записи сделок.

        ВАЖНО: PnL скользящих окон пересчитывается из отфильтрованных
        списков. Раньше ``_daily_pnl``/``_weekly_pnl`` только накапливались
        и никогда не сбрасывались, из-за чего дневной лимит потерь
        срабатывал «навсегда» по мере накопления истории.
        """
        now = datetime.utcnow()

        # Дневные сделки — оставляем за последние 24 часа
        day_ago = now - timedelta(hours=24)
        self._today_trades = [
            t for t in self._today_trades
            if t["timestamp"] > day_ago
        ]

        # Недельные сделки — оставляем за последние 7 дней
        week_ago = now - timedelta(days=7)
        self._week_trades = [
            t for t in self._week_trades
            if t["timestamp"] > week_ago
        ]

        # Пересчёт PnL окон из фактически оставшихся сделок.
        self._daily_pnl = sum(
            (Decimal(str(t["pnl"])) for t in self._today_trades), Decimal("0")
        )
        self._weekly_pnl = sum(
            (Decimal(str(t["pnl"])) for t in self._week_trades), Decimal("0")
        )

    def add_position(
        self,
        position: models.Position | str,
        *,
        symbol: str | None = None,
        side: str | None = None,
        notional: Decimal | None = None,
    ):
        """Добавить позицию (объект позиции или её идентификатор).

        Для позиций, добавленных по id (str), передать meta
        (symbol/side/notional) — она нужна для portfolio-лимитов (Этап 5).
        """
        position_id = position if isinstance(position, str) else position.id
        self._open_positions[position_id] = position
        if symbol is not None or side is not None or notional is not None:
            meta = self._open_meta.get(position_id, {})
            if symbol is not None:
                meta["symbol"] = symbol
            if side is not None:
                meta["side"] = str(side).lower()
            if notional is not None:
                meta["notional"] = str(notional)
            self._open_meta[position_id] = meta
        OPEN_POSITIONS.labels(engine="risk").set(len(self._open_positions))

    def remove_position(self, position_id: str):
        """Удалить позицию"""
        self._open_positions.pop(position_id, None)
        self._open_meta.pop(position_id, None)
        OPEN_POSITIONS.labels(engine="risk").set(len(self._open_positions))

    def get_open_positions(self) -> dict[str, models.Position]:
        """Получить открытые позиции"""
        return self._open_positions.copy()

    def update_position_price(self, position_id: str, current_price: Decimal):
        """Обновить цену позиции"""
        if position_id in self._open_positions:
            pos = self._open_positions[position_id]
            pos.update_price(current_price)

    def to_dict(self) -> dict:
        """Сериализовать состояние"""
        return {
            "risk_state": self.risk_state.value,
            "trading_enabled": self.trading_enabled,
            "current_equity": str(self._current_equity),
            "initial_capital": str(self._initial_capital),
            "high_water_mark": str(self._high_water_mark),
            "current_drawdown": str(self.current_drawdown),
            "daily_pnl": str(self._daily_pnl),
            "daily_loss_pct": str(self.daily_loss_pct),
            "weekly_pnl": str(self._weekly_pnl),
            "risk_multiplier": str(self._get_risk_multiplier()),
            "open_positions": len(self._open_positions),
            "total_trades": self._total_trades,
            "total_wins": self._total_wins,
            "total_losses": self._total_losses,
            "win_rate": (
                self._total_wins / self._total_trades * 100
                if self._total_trades > 0 else 0
            ),
        }


# Глобальный Risk Engine
_risk_engine: RiskEngine | None = None


def get_risk_engine() -> RiskEngine:
    """Получить глобальный Risk Engine"""
    global _risk_engine
    if _risk_engine is None:
        _risk_engine = RiskEngine()
    return _risk_engine


def reset_risk_engine():
    """Сбросить Risk Engine (для тестов)"""
    global _risk_engine
    _risk_engine = RiskEngine()
