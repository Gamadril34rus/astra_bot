#!/usr/bin/env python3
"""Проверка «Простой книги торговли» (Simple Trading Book, корень проекта).

Прогоняет стратегию ``book_breakout`` (пробой → ретест → подтверждающая
свеча, стоп за экстремумом ретеста, measured move) по правилам книги на
реальных свечах Binance BTC/USDT за последние N лет (по умолчанию 2 года)
на таймфреймах 1h и 4h.

Что делает:
1. Загружает свечи из ``data/BTCUSDT_1h.csv`` и ``data/BTCUSDT_4h.csv``
   (формат Binance klines: open_time_ms, open, high, low, close, volume,
   close_time_ms, quote_volume, ...).
2. Обрезает окно [конец - N лет, конец) и гоняет событийный бэктестер
   ``astra_bot.backtester.BacktestEngine`` со стратегией из книги.
3. Считает метрики (win-rate, profit factor, expectancy, просадка,
   Sharpe, сравнение с buy&hold, разбивки по годам/направлениям/причинам
   выхода) и складывает отчёт в ``reports/book_2y/``:
   summary.md / summary.json / trades_*.csv / equity_*.csv / equity_*.png.

Бумажная проверка правил книги: реальные деньги и боевые ордера скрипт
не использует. Результаты — не гарантия будущей прибыли.

Пример:
    python scripts/backtest_book_2y.py --years 2 --capital 10000
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.backtester.engine import BacktestConfig, BacktestEngine, BacktestResult
from astra_bot.strategies.book_breakout import BookBreakoutStrategy

LOGGER = logging.getLogger("backtest_book_2y")

# Свечей в году для годовой нормировки Sharpe (Binance торгует 24/7).
BARS_PER_YEAR = {"1h": 365 * 24, "4h": 365 * 6}

# Файлы данных: таймфрейм -> имя CSV в data/.
DEFAULT_FILES = {"1h": "BTCUSDT_1h.csv", "4h": "BTCUSDT_4h.csv"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Бэктест стратегии из «Простой книги торговли» за N лет"
    )
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.004)
    parser.add_argument("--end", default=None, help="конец окна, YYYY-MM-DD (по умолчанию сегодня)")
    parser.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"))
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "reports" / "book_2y"))
    parser.add_argument(
        "--use-risk-adaptation",
        action="store_true",
        help="включить адаптивное снижение риска бота по просадке "
        "(по умолчанию выключено — проверяются чистые правила книги)",
    )
    return parser.parse_args()


def load_klines_csv(path: Path) -> pd.DataFrame:
    """Загрузить Binance-формат CSV и проверить целостность."""
    df = pd.read_csv(path)
    required = ["open_time", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: нет колонок {missing}")
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time")
    df = df.reset_index(drop=True)
    # Дыры в данных: предупреждаем, если шаг свечей непостоянен.
    steps = df["open_time"].diff().dropna()
    if len(steps) > 1 and steps.nunique() > 2:
        LOGGER.warning("%s: обнаружены пропуски свечей (%d разных шагов)", path.name, steps.nunique())
    return df


def df_to_candles(df: pd.DataFrame, window_start_ms: int, window_end_ms: int) -> list[dict]:
    """Окно [start, end) в формате свечей бэктестера (open_time в мс)."""
    w = df[(df["open_time"] >= window_start_ms) & (df["open_time"] < window_end_ms)]
    candles = []
    for row in w.itertuples(index=False):
        candles.append(
            {
                "open_time": int(row.open_time),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
                "quote_volume": float(getattr(row, "quote_volume", row.volume * row.close)),
            }
        )
    return candles


def run_timeframe(
    symbol: str,
    timeframe: str,
    candles: list[dict],
    initial_capital: Decimal,
    risk_per_trade: float,
    start_dt: datetime,
    end_dt: datetime,
    use_risk_adaptation: bool = False,
) -> tuple[dict, BacktestResult]:
    """Один прогон бэктестера на таймфрейме. Возвращает (сводка, результат)."""
    risk_config: dict = {"risk_per_trade": str(risk_per_trade)}
    if not use_risk_adaptation:
        # Чистая проверка правил книги: фиксированный риск на сделку,
        # без адаптивного снижения/остановки по просадке.
        risk_config["drawdown_adaptation"] = [{"drawdown": 0, "risk_multiplier": 1.0}]
    config = BacktestConfig(
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_dt,
        end_date=end_dt,
        initial_capital=initial_capital,
        max_open_positions=1,  # одна позиция одновременно — как в книге
        risk_config=risk_config,
    )
    engine = BacktestEngine(config)
    engine.add_strategy("book_breakout", BookBreakoutStrategy())
    engine.load_candles(candles)
    result = engine.run()

    trades = [t for t in result.trades if t.result in ("won", "lost")]
    closed = [t for t in trades if t.exit_time is not None]

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_profit = sum((t.pnl for t in wins), Decimal("0"))
    gross_loss = abs(sum((t.pnl for t in losses), Decimal("0")))

    # Годовые разбивки по времени выхода.
    by_year: dict[int, dict[str, float]] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in closed:
        y = t.exit_time.year
        by_year[y]["trades"] += 1
        if t.pnl > 0:
            by_year[y]["wins"] += 1
        by_year[y]["pnl"] += float(t.pnl)

    by_exit = Counter(t.exit_reason for t in closed)
    by_side = Counter(t.side for t in trades)
    side_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        side_pnl[t.side] += float(t.pnl)

    hold_hours = [
        (t.exit_time - t.entry_time).total_seconds() / 3600.0
        for t in closed
        if t.exit_time is not None and t.entry_time is not None
    ]

    # PnL на эквити-кривой (unrealized включён).
    eq = pd.DataFrame(result.equity_curve)
    eq["datetime"] = pd.to_datetime(eq["timestamp"] * 1_000_000, utc=True) if eq["timestamp"].max() < 10_000_000_000 else pd.to_datetime(eq["timestamp"], unit="ms", utc=True)
    eq["drawdown_pct"] = (eq["equity"].cummax() - eq["equity"]) / eq["equity"].cummax() * 100

    # Sharpe по per-bar доходностям, годовая нормировка под таймфрейм.
    rets = eq["equity"].pct_change().dropna()
    sharpe = (
        float(rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR.get(timeframe, 8760)))
        if len(rets) > 1 and rets.std() > 0
        else 0.0
    )

    first_close = Decimal(str(candles[0]["close"]))
    last_close = Decimal(str(candles[-1]["close"]))
    buy_hold_pct = float((last_close / first_close - 1) * 100)
    return_pct = float(result.return_pct)

    return {
        "timeframe": timeframe,
        "window": {
            "start": str(start_dt.date()),
            "end": str(end_dt.date()),
            "candles": len(candles),
        },
        "metrics": {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
            "profit_factor": round(float(gross_profit / gross_loss), 3) if gross_loss > 0 else None,
            "net_pnl_usdt": round(float(result.net_profit), 2),
            "total_fees_usdt": round(float(result.total_fees), 2),
            "total_slippage_usdt": round(float(result.total_slippage), 2),
            "avg_win_usdt": round(float(result.avg_win), 2),
            "avg_loss_usdt": round(float(result.avg_loss), 2),
            "expectancy_usdt": round(float(result.net_profit / len(trades)), 2) if trades else 0.0,
            "largest_win_usdt": round(float(result.largest_win), 2),
            "largest_loss_usdt": round(float(result.largest_loss), 2),
            "max_drawdown_pct": round(float(eq["drawdown_pct"].max()), 2),
            "return_pct": round(return_pct, 2),
            "buy_hold_pct": round(buy_hold_pct, 2),
            "sharpe_annual": round(sharpe, 3),
            "avg_hold_hours": round(float(np.mean(hold_hours)), 1) if hold_hours else 0.0,
            "max_hold_hours": round(float(np.max(hold_hours)), 1) if hold_hours else 0.0,
        },
        "by_year": {str(y): v for y, v in sorted(by_year.items())},
        "by_exit_reason": dict(by_exit),
        "by_side": dict(by_side),
        "pnl_by_side": {k: round(v, 2) for k, v in side_pnl.items()},
        "final_equity_usdt": round(float(result.final_equity), 2),
    }, result


def save_artifacts(
    out_dir: Path,
    result: dict,
    trades: list,
    equity: list[dict],
    timeframe: str,
):
    """Сохранить сделки, эквити-кривую и картинку."""
    out_dir.mkdir(parents=True, exist_ok=True)

    trades_df = pd.DataFrame(
        [
            {
                "id": t.id,
                "side": t.side,
                "entry_time": t.entry_time.isoformat() if t.entry_time else "",
                "exit_time": t.exit_time.isoformat() if t.exit_time else "",
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price) if t.exit_price is not None else "",
                "stop_loss": float(t.stop_loss),
                "take_profit": float(t.take_profit),
                "quantity": float(t.quantity),
                "pnl_usdt": float(t.pnl),
                "fees_usdt": float(t.fees),
                "result": t.result,
                "exit_reason": t.exit_reason,
                "strategy": t.strategy_name,
            }
            for t in trades
        ]
    )
    trades_df.to_csv(out_dir / f"trades_{timeframe}.csv", index=False)

    eq_df = pd.DataFrame(equity)
    eq_df.to_csv(out_dir / f"equity_{timeframe}.csv", index=False)

    if len(eq_df) > 2:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        ts = eq_df["timestamp"]
        ts_dt = (
            pd.to_datetime(ts * 1_000_000, utc=True)
            if ts.max() < 10_000_000_000
            else pd.to_datetime(ts, unit="ms", utc=True)
        )
        fig, ax = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
        ax[0].plot(ts_dt, eq_df["equity"], lw=1.2, color="#1f77b4")
        ax[0].set_title(f"Книга (book_breakout) — эквити {timeframe}, {result['window']['start']} → {result['window']['end']}")
        ax[0].grid(alpha=0.3)
        dd = (eq_df["equity"].cummax() - eq_df["equity"]) / eq_df["equity"].cummax() * 100
        ax[1].fill_between(ts_dt, dd, 0, color="#d62728", alpha=0.5)
        ax[1].set_title("Просадка, %")
        ax[1].grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(out_dir / f"equity_{timeframe}.png", dpi=110)
        plt.close(fig)


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Риск-движок логирует каждое изменение множителя — в бэктесте это шум.
    logging.getLogger("astra_bot.engines.risk_engine").setLevel(logging.ERROR)
    logging.getLogger("astra_bot.backtester.engine").setLevel(logging.WARNING)

    end_dt = (
        datetime.fromisoformat(args.end).replace(tzinfo=UTC)
        if args.end
        else datetime.now(tz=UTC)
    )
    start_dt = end_dt - timedelta(days=int(args.years * 365.25))
    window_start_ms = int(start_dt.timestamp() * 1000)
    window_end_ms = int(end_dt.timestamp() * 1000)

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries: dict[str, dict] = {}
    for timeframe, filename in DEFAULT_FILES.items():
        path = data_dir / filename
        if not path.exists():
            LOGGER.error(
                "Нет файла %s. Положите свечи Binance %s в data/%s "
                "(например, из github.com/RonaldLu66/binance-btc-monitor "
                "или выгрузкой /api/v3/klines).",
                path, timeframe, filename,
            )
            continue

        df = load_klines_csv(path)
        candles = df_to_candles(df, window_start_ms, window_end_ms)
        if not candles:
            LOGGER.error("%s: в окне %s → %s нет свечей", filename, start_dt.date(), end_dt.date())
            continue

        LOGGER.info(
            "%s %s: %d свечей (%s → %s)",
            args.symbol, timeframe, len(candles),
            datetime.fromtimestamp(candles[0]["open_time"] / 1000, tz=UTC).date(),
            datetime.fromtimestamp(candles[-1]["open_time"] / 1000, tz=UTC).date(),
        )

        summary, engine_result = run_timeframe(
            args.symbol, timeframe, candles,
            Decimal(str(args.capital)), args.risk_per_trade, start_dt, end_dt,
            use_risk_adaptation=args.use_risk_adaptation,
        )
        summaries[timeframe] = summary

        save_artifacts(
            out_dir, summary, engine_result.trades, engine_result.equity_curve, timeframe
        )
        LOGGER.info(
            "%s: сделок=%d win=%s%% PF=%s net=%s USDT dd=%s%%",
            timeframe,
            summary["metrics"]["total_trades"],
            summary["metrics"]["win_rate_pct"],
            summary["metrics"]["profit_factor"],
            summary["metrics"]["net_pnl_usdt"],
            summary["metrics"]["max_drawdown_pct"],
        )

    if not summaries:
        LOGGER.error("Ни один таймфрейм не прогнан — отчёт не создан.")
        return 1

    report = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "symbol": args.symbol,
        "strategy": "book_breakout (пробой → ретест → подтверждение, Simple Trading Book)",
        "capital_usdt": args.capital,
        "risk_per_trade": args.risk_per_trade,
    "risk_adaptation": "on" if args.use_risk_adaptation else "off (чистые правила книги)",
        "window": {"start": str(start_dt.date()), "end": str(end_dt.date()), "years": args.years},
        "timeframes": summaries,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    LOGGER.info("Отчёт: %s", out_dir / "summary.md")
    print(render_markdown(report))
    return 0


def render_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append("# Проверка «Простой книги торговли» — бэктест")
    lines.append("")
    lines.append(
        f"- Пара: **{report['symbol']}** · Окно: **{report['window']['start']} → {report['window']['end']}** "
        f"({report['window']['years']} года) · Стартовый капитал: **{report['capital_usdt']:,.0f} USDT**"
    )
    lines.append(
        f"- Стратегия: **{report['strategy']}** · Риск на сделку: {report['risk_per_trade'] * 100:.2f}% "
        "· Комиссия 0.1% + проскальзывание 0.1% · одна позиция одновременно · "
        f"адаптация риска: {report['risk_adaptation']}"
    )
    lines.append("")
    lines.append("## Итоги по таймфреймам")
    lines.append("")
    lines.append("| Метрика | 1h | 4h |")
    lines.append("|---|---|---|")
    tfs = report["timeframes"]

    def m(tf: str, key: str, fmt: str = "{:.2f}") -> str:
        v = tfs.get(tf, {}).get("metrics", {}).get(key)
        if v is None:
            return "—"
        if isinstance(v, float):
            return fmt.format(v)
        return str(v)

    rows = [
        ("Свечей в окне", "window", "candles", "{:,}"),
        ("Сделок", "metrics", "total_trades", "{:,}"),
        ("Win-rate, %", "metrics", "win_rate_pct", "{:.2f}"),
        ("Profit factor", "metrics", "profit_factor", "{:.2f}"),
        ("Чистый PnL, USDT", "metrics", "net_pnl_usdt", "{:,.2f}"),
        ("Доходность стратегии, %", "metrics", "return_pct", "{:.2f}"),
        ("Buy & hold за окно, %", "metrics", "buy_hold_pct", "{:.2f}"),
        ("Макс. просадка, %", "metrics", "max_drawdown_pct", "{:.2f}"),
        ("Средний выигрыш, USDT", "metrics", "avg_win_usdt", "{:,.2f}"),
        ("Средний убыток, USDT", "metrics", "avg_loss_usdt", "{:,.2f}"),
        ("Expectancy, USDT/сделка", "metrics", "expectancy_usdt", "{:,.2f}"),
        ("Sharpe (годовых)", "metrics", "sharpe_annual", "{:.2f}"),
        ("Средний холд, часов", "metrics", "avg_hold_hours", "{:.1f}"),
        ("Комиссии всего, USDT", "metrics", "total_fees_usdt", "{:,.2f}"),
    ]
    for label, section, key, fmt in rows:
        if section == "window":
            a = tfs.get("1h", {}).get("window", {}).get(key)
            b = tfs.get("4h", {}).get("window", {}).get(key)
            lines.append(f"| {label} | {fmt.format(a) if a is not None else '—'} | {fmt.format(b) if b is not None else '—'} |")
        else:
            lines.append(f"| {label} | {m('1h', key, fmt)} | {m('4h', key, fmt)} |")

    for tf in ("1h", "4h"):
        t = tfs.get(tf)
        if not t:
            continue
        lines.append("")
        lines.append(f"## {tf}: причины выхода")
        lines.append("")
        for reason, count in sorted(t["by_exit_reason"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {reason}: {count}")
        lines.append("")
        lines.append(f"## {tf}: по годам (по времени выхода)")
        lines.append("")
        lines.append("| Год | Сделок | Win-rate | PnL, USDT |")
        lines.append("|---|---|---|---|")
        for year, v in sorted(t["by_year"].items()):
            wr = v["wins"] / v["trades"] * 100 if v["trades"] else 0
            lines.append(f"| {year} | {v['trades']} | {wr:.1f}% | {v['pnl']:,.2f} |")

    lines.append("")
    lines.append("> Проверка правил книги на истории, без реальных денег. "
                 "Прошлые результаты не гарантируют будущую прибыль.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
