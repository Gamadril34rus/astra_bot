"""
Готовность к реальному счёту.

Бот НЕ должен переходить на реальные деньги, пока не докажет стабильность
на демо. Здесь собираются объективные метрики и вычисляется итоговый вердикт
(0..100) с порогом готовности. Когда порог пройден — Telegram присылает
уведомление «я готов к реальному счёту».

Критерии (как настоящий риск-менеджмент):
* 30+ торговых дней на демо;
* win-rate не ниже 55% и profit-factor ≥ 1.3;
* максимальная просадка ≤ 8%;
* нет серии из 6+ убытков подряд;
* доля прибыльных дней ≥ 55%;
* Sharpe ≥ 1.0;
* минимум 200 закрытых сделок.

Ни один метрика не гарантирует прибыль (рынок всегда может удивить), но это
разумный минимум, чтобы не слить депозит в первой же неделе.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).resolve().parents[2] / "models" / "readiness.json"

READINESS_THRESHOLD = 85  # из 100


@dataclass
class DayStat:
    date: str
    trades: int
    wins: int
    pnl: float
    equity_end: float


@dataclass
class ReadinessState:
    days: list[dict] = field(default_factory=list)
    max_drawdown_pct: float = 0.0
    longest_loss_streak: int = 0
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_pnl: float = 0.0
    notified_ready: bool = False
    first_trade_date: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _path() -> Path:
    return Path(os.environ.get("READINESS_FILE", str(_STATE_PATH)))


def load() -> ReadinessState:
    p = _path()
    if not p.exists():
        return ReadinessState()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return ReadinessState(**{k: v for k, v in data.items()
                                  if k in ReadinessState.__dataclass_fields__})
    except Exception as exc:
        logger.warning("Не смог прочитать readiness: %s", exc)
        return ReadinessState()


def save(state: ReadinessState) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def record_day(
    *,
    day: date | None = None,
    trades: int = 0,
    wins: int = 0,
    pnl: float = 0.0,
    equity_end: float = 0.0,
) -> ReadinessState:
    """Записать итог торгового дня и пересчитать агрегаты."""
    state = load()
    day = day or date.today()
    ds = day.isoformat()

    if state.first_trade_date is None and trades > 0:
        state.first_trade_date = ds

    # Заменяем запись за сегодня, если перезаписываем итог дня.
    state.days = [d for d in state.days if d["date"] != ds]
    if trades > 0 or pnl != 0:
        state.days.append({
            "date": ds, "trades": trades, "wins": wins,
            "pnl": pnl, "equity_end": equity_end,
        })
    # Ограничиваем историю 180 днями.
    state.days = state.days[-180:]

    # Пересчёт агрегатов (заодно пересчитывает max-drawdown и streak).
    _recompute_aggregates(state)

    # Max drawdown по дневным эквити.
    peak = 0.0
    max_dd = 0.0
    for d in state.days:
        eq = d["equity_end"] or 0.0
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)
    state.max_drawdown_pct = round(max_dd, 2)

    # Самая длинная серия убыточных дней.
    streak = 0
    max_streak = 0
    for d in state.days:
        if d["pnl"] < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    state.longest_loss_streak = max_streak

    save(state)
    return state


def _recompute_aggregates(state: ReadinessState) -> ReadinessState:
    """Пересчитать max-drawdown и streak по дням (на случай загрузки сырого файла)."""
    peak = 0.0
    max_dd = 0.0
    for d in state.days:
        eq = d.get("equity_end") or 0.0
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)
    state.max_drawdown_pct = round(max_dd, 2)
    streak = 0
    max_streak = 0
    for d in state.days:
        if d.get("pnl", 0) < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    state.longest_loss_streak = max_streak
    state.total_trades = sum(d.get("trades", 0) for d in state.days)
    state.total_wins = sum(d.get("wins", 0) for d in state.days)
    state.total_losses = state.total_trades - state.total_wins
    state.total_pnl = sum(d.get("pnl", 0) for d in state.days)
    return state


def evaluate(state: ReadinessState | None = None) -> dict:
    """Вернуть вердикт готовности с разбивкой по критериям."""
    state = _recompute_aggregates(state or load())
    days = state.days

    trading_days = len(days)
    win_rate = (state.total_wins / state.total_trades * 100) if state.total_trades else 0.0
    gross_profit = sum(d["pnl"] for d in days if d["pnl"] > 0)
    gross_loss = abs(sum(d["pnl"] for d in days if d["pnl"] < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
    profitable_days = sum(1 for d in days if d["pnl"] > 0)
    profitable_day_pct = (profitable_days / trading_days * 100) if trading_days else 0.0
    rets = [d["pnl"] for d in days if d["pnl"] != 0]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    sharpe = (mean / (var ** 0.5)) if var > 0 else 0.0

    checks = [
        ("30+ дней на демо", trading_days >= 30, 15),
        ("200+ сделок", state.total_trades >= 200, 15),
        (f"Win-rate ≥ 55% (сейчас {win_rate:.0f}%)", win_rate >= 55, 20),
        (f"Profit Factor ≥ 1.3 (сейчас {profit_factor:.2f})", profit_factor >= 1.3, 15),
        (f"Просадка ≤ 8% (сейчас {state.max_drawdown_pct:.1f}%)", state.max_drawdown_pct <= 8.0, 15),
        (f"Дней в плюсе ≥ 55% (сейчас {profitable_day_pct:.0f}%)", profitable_day_pct >= 55, 10),
        (f"Серия убытков < 6 дней (сейчас {state.longest_loss_streak})", state.longest_loss_streak < 6, 5),
        (f"Sharpe ≥ 1.0 (сейчас {sharpe:.2f})", sharpe >= 1.0, 5),
    ]

    score = sum(weight for _, passed, weight in checks if passed)
    ready = score >= READINESS_THRESHOLD

    return {
        "ready": ready,
        "score": score,
        "threshold": READINESS_THRESHOLD,
        "trading_days": trading_days,
        "total_trades": state.total_trades,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "max_drawdown_pct": state.max_drawdown_pct,
        "profitable_day_pct": round(profitable_day_pct, 1),
        "longest_loss_streak": state.longest_loss_streak,
        "sharpe": round(sharpe, 2),
        "total_pnl": round(state.total_pnl, 2),
        "checks": [{"name": n, "passed": p, "weight": w} for n, p, w in checks],
        "notified": state.notified_ready,
    }


def should_notify_ready() -> bool:
    """Если только что перешли порог и ещё не уведомляли — True."""
    state = load()
    verdict = evaluate(state)
    if verdict["ready"] and not state.notified_ready:
        state.notified_ready = True
        save(state)
        return True
    return False


def format_report() -> str:
    v = evaluate()
    status = "✅ ГОТОВ к реальному счёту" if v["ready"] else "🚫 Ещё не готов к реальному счёту"
    lines = [
        "🎯 *ГОТОВНОСТЬ К РЕАЛЬНОМУ СЧЁТУ*",
        f"Вердикт: {status}",
        f"Балл: {v['score']} / {v['threshold']}",
        "",
        f"Дней на демо: {v['trading_days']}  |  Сделок: {v['total_trades']}",
        f"Win-rate: {v['win_rate']}%  |  PF: {v['profit_factor']}  |  Sharpe: {v['sharpe']}",
        f"Просадка: {v['max_drawdown_pct']}%  |  Дней в плюсе: {v['profitable_day_pct']}%",
        f"Серия убытков макс: {v['longest_loss_streak']}  |  PnL: {v['total_pnl']:+.2f} ₽",
        "",
        "*Чек-лист:*",
    ]
    for c in v["checks"]:
        lines.append(f"  {'✅' if c['passed'] else '⬜'} {c['name']}")
    if not v["ready"]:
        lines += [
            "",
            "Переход на реальный счёт будет возможен после выполнения всех "
            "критериев. Бот сообщит, когда будет готов.",
        ]
    return "\n".join(lines)
