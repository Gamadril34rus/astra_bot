#!/usr/bin/env python3
"""Срез двухнедельного обзора ASTRA (инструмент будущего среза, 13.09.2026).

Только ЧТЕНИЕ торгового состояния: ни один файл в ``models/`` не создаётся,
не перезаписывается и не удаляется. В режиме ``--git-ref`` файлы читаются
через ``git show <ref>:<path>`` — рабочая копия и индекс не трогаются
(``git fetch`` скрипт НЕ выполняет: если нужен свежий master, сделайте
``git fetch`` перед запуском среза).

Запуск:
    python scripts/measure_two_week_review.py
    python scripts/measure_two_week_review.py --git-ref origin/master
    python scripts/measure_two_week_review.py --days 14 --as-of 2026-09-12T06:13Z

Секции отчёта:
  (1) бакеты фигур/новых стратегий и всех прочих: n, PF, mean/median R,
      разбивка |ANY|/режимные + пометка двойного счёта |ANY|;
  (2) агрегат kill-switch: кого вырезал и кого вернул бы при
      дедуплицированном n;
  (3) ``paper_ledger.jsonl``: наличие и сверка Σfee/realized с брокером
      реплеем (штатным ``TradeLedger.replay()``);
  (4) ``htf_shadow_bans.jsonl``: объём, разбивка по стратегиям/направлениям;
  (5) ``paper_trades.jsonl`` за окно: входов/день, доли ``exit_reason``,
      R стоп-выходов.

Деньги — ``Decimal`` (никакого float там, где суммируются комиссии/PnL).
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.core.ledger import TradeLedger

# Фигуры и «новые» стратегии (список владельца, ТЗ среза §1).
NEW_STRATEGY_NAMES: tuple[str, ...] = (
    "rounded_top",
    "rounded_bottom",
    "ob_swing",
    "breaker_block",
    "maicross",
)

STATS_FILE = "strategy_stats.json"
TRADES_FILE = "paper_trades.jsonl"
LEDGER_FILE = "paper_ledger.jsonl"
POSITIONS_FILE = "paper_positions.json"
SHADOW_FILE = "htf_shadow_bans.jsonl"

PAPER_TF_RE = ("5m", "15m", "1h", "4h", "1d")
MS_PER_DAY = 86_400_000


# --------------------------------------------------------------------------- utils
def _dec(value: Any) -> Decimal:
    """Деньги: строго Decimal (float/str/int → Decimal через str)."""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _money(value: Decimal) -> str:
    return f"{value:.8f}"


def _ratio(value: float) -> str:
    if value == float("inf"):
        return "inf"
    return f"{value:.2f}"


def _r(value: float) -> str:
    return f"{value:+.3f}"


def _pct(part: int, total: int) -> str:
    return f"{100.0 * part / total:.1f}%" if total else "—"


def _rel_to_repo(path: Path) -> str:
    """Путь для ``git show``: относительно корня репозитория, posix."""
    resolved = path if path.is_absolute() else (Path.cwd() / path)
    try:
        return resolved.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _git(args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


class StateSource:
    """Источник state-файлов: рабочая копия ``models/`` ИЛИ git-ref (read-only)."""

    def __init__(self, models_dir: Path, git_ref: str | None = None) -> None:
        self.models_dir = models_dir
        self.git_ref = git_ref
        self.repo_path = _rel_to_repo(models_dir)
        self.sha: str | None = None
        if git_ref:
            sha = _git(["rev-parse", git_ref])
            self.sha = sha.strip() if sha else None

    def describe(self) -> str:
        if self.git_ref:
            sha = (self.sha or "?")[:12]
            return f"git {self.git_ref} @ {sha} ({self.repo_path}/, только чтение)"
        return f"{self.models_dir}/ рабочей копии (только чтение)"

    def read_text(self, name: str) -> str | None:
        if self.git_ref:
            return _git(["show", f"{self.git_ref}:{self.repo_path}/{name}"])
        path = self.models_dir / name
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def read_json(self, name: str) -> dict[str, Any] | None:
        text = self.read_text(name)
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def read_jsonl(self, name: str) -> list[dict[str, Any]] | None:
        text = self.read_text(name)
        if text is None:
            return None
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def exists(self, name: str) -> bool:
        if self.git_ref:
            return _git(["cat-file", "-e", f"{self.git_ref}:{self.repo_path}/{name}"]) is not None
        return (self.models_dir / name).is_file()


# ---------------------------------------------------------------- (1) бакеты stats
def parse_bucket_key(key: str) -> tuple[str, str, str]:
    """``strategy|regime|timeframe`` (timeframe может быть пустым)."""
    parts = key.split("|")
    name = parts[0].strip() if parts else ""
    regime = parts[1].strip() if len(parts) > 1 else ""
    timeframe = parts[2].strip() if len(parts) > 2 else ""
    return name, regime, timeframe


def bucket_kind(regime: str) -> str:
    if regime.upper() == "ANY":
        return "any"
    if regime.startswith(("T:", "V:", "L:")):
        return "axes"
    return "regime"


def bucket_metrics(row: dict[str, Any]) -> dict[str, float]:
    n = float(row.get("sample_size") or 0)
    wins = float(row.get("wins_sum_r") or 0.0)
    losses = float(row.get("losses_sum_r") or 0.0)
    total = float(row.get("sum_r") or 0.0)
    pf = float("inf") if losses == 0 and wins > 0 else (wins / abs(losses) if losses else 0.0)
    return {"n": n, "wins": wins, "losses": losses, "sum_r": total, "pf": pf}


def median_r_by_strategy(trades: Iterable[dict[str, Any]]) -> dict[str, float]:
    """Медиана net-R по стратегии из paper_trades (в strategy_stats её нет)."""
    acc: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        name = str(t.get("strategy") or "")
        if not name or t.get("r_multiple") is None:
            continue
        acc[name].append(float(t["r_multiple"]))
    return {k: statistics.median(v) for k, v in acc.items()}


def section_buckets(
    stats: dict[str, Any] | None,
    trades: list[dict[str, Any]],
    out: list[str],
) -> None:
    out.append("")
    out.append("(1) БАКЕТЫ ФИГУР И НОВЫХ СТРАТЕГИЙ (+ все остальные)")
    if not stats:
        out.append(f"  {STATS_FILE}: НЕТ файла в источнике — секция пуста.")
        return
    buckets = stats.get("buckets") or {}
    if not isinstance(buckets, dict):
        out.append(f"  {STATS_FILE}: структура без 'buckets' — секция пуста.")
        return
    out.append(f"  источник: {STATS_FILE} (updated={stats.get('updated')}), бакетов {len(buckets)}")
    out.append(
        "  метрики: n=sample_size, PF=wins_sum_r/|losses_sum_r| (как "
        "StrategyRegimeStats.profit_factor), mean R=sum_r/n;"
    )
    out.append(
        "  median R — из paper_trades.jsonl (в strategy_stats распределения нет), "
        "по всем сделкам файла."
    )
    out.append(
        "  ПОМЕТКА ДВОЙНОГО СЧЁТА |ANY|: каждая сделка попадает и в режимный бакет, "
        "и в |ANY| того же ТФ,"
    )
    out.append(
        "  поэтому n_all = n_regime + n_any — суммировать все бакеты подряд нельзя."
    )
    out.append("")

    per_name: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n_all": 0.0, "w_all": 0.0, "l_all": 0.0, "n_any": 0.0, "n_reg": 0.0,
                 "w_any": 0.0, "l_any": 0.0, "buckets": 0, "tf": set()}
    )
    for key, row in buckets.items():
        if not isinstance(row, dict):
            continue
        name, regime, timeframe = parse_bucket_key(str(key))
        if not name:
            continue
        m = bucket_metrics(row)
        agg = per_name[name]
        agg["buckets"] += 1
        agg["n_all"] += m["n"]
        agg["w_all"] += m["wins"]
        agg["l_all"] += m["losses"]
        if timeframe:
            agg["tf"].add(timeframe)
        if bucket_kind(regime) == "any":
            agg["n_any"] += m["n"]
            agg["w_any"] += m["wins"]
            agg["l_any"] += m["losses"]
        else:
            agg["n_reg"] += m["n"]

    medians = median_r_by_strategy(trades)
    header = (
        f"  {'стратегия':<22} {'бакетов':>7} {'n|ANY':>7} {'n|реж':>7} {'n|все':>7} "
        f"{'PF|ANY':>7} {'meanR|ANY':>10} {'medianR|paper':>13}  помета"
    )
    out.append("  --- 1a. фигуры и новые стратегии (список из ТЗ) ---")
    out.append(header)
    for name in NEW_STRATEGY_NAMES:
        agg = per_name.get(name)
        if agg is None or agg["buckets"] == 0:
            out.append(f"  {name:<22} {'—':>7}  НЕТ бакетов (не торговала / имя иное)")
            continue
        out.append(_bucket_row(name, agg, medians.get(name)))
    out.append("")
    out.append("  --- 1b. все остальные стратегии (по имени, агрегат бакетов) ---")
    out.append(header)
    for name in sorted(per_name, key=lambda k: (-per_name[k]["n_all"], k)):
        if name in NEW_STRATEGY_NAMES:
            continue
        out.append(_bucket_row(name, per_name[name], medians.get(name)))

    traded = {str(t.get("strategy") or "") for t in trades if t.get("strategy")}
    missing = sorted(n for n in traded if n not in per_name)
    if missing:
        out.append("")
        out.append(
            f"  ВНИМАНИЕ: есть сделки в paper_trades, но нет бакетов в stats: {', '.join(missing)}"
        )
    out.append("")
    out.append(
        "  помета '|ANY| двоит': n|все = n|ANY + n|реж; для kill-switch движок "
        "суммирует ВСЕ бакеты (см. секцию 2)."
    )


def _bucket_row(name: str, agg: dict[str, Any], median: float | None) -> str:
    n_any = agg["n_any"]
    pf_any = float("inf") if agg["l_any"] == 0 and agg["w_any"] > 0 else (
        agg["w_any"] / abs(agg["l_any"]) if agg["l_any"] else 0.0
    )
    mean_any = (agg["w_any"] + agg["l_any"]) / n_any if n_any else 0.0
    med = f"{median:+.3f}" if median is not None else "—"
    flag = "|ANY| двоит" if n_any and agg["n_reg"] else ""
    return (
        f"  {name:<22} {agg['buckets']:>7} {n_any:>7.0f} {agg['n_reg']:>7.0f} "
        f"{agg['n_all']:>7.0f} {_ratio(pf_any):>7} {_r(mean_any):>10} {med:>13}  {flag}"
    )


# ------------------------------------------------------------ (2) kill-switch
def kill_switch_agg(buckets: dict[str, Any], *, any_only: bool) -> dict[str, dict[str, float]]:
    """Правило движка (trading_engine.apply_kill_switches_from_stats) как есть.

    ``any_only=True`` — дедуплицированный вариант: только бакеты режима ``ANY``
    (сделка учтена один раз, без наложения режимных бакетов).
    """
    agg: dict[str, dict[str, float]] = {}
    for key, row in buckets.items():
        if not isinstance(row, dict):
            continue
        name, regime, _tf = parse_bucket_key(str(key))
        if not name:
            continue
        is_any = bucket_kind(regime) == "any"
        if any_only and not is_any:
            continue  # дедуп: только |ANY| (иначе — все бакеты, как в движке)
        m = bucket_metrics(row)
        a = agg.setdefault(name, {"n": 0.0, "w": 0.0, "l": 0.0})
        a["n"] += m["n"]
        a["w"] += m["wins"]
        a["l"] += m["losses"]
    return agg


def kill_verdict(a: dict[str, float] | None) -> tuple[bool, float, float]:
    """(сработало бы правило, PF, n). Правило движка: n>=5 и PF<1.0, где PF — по суммам R."""
    if a is None or a["n"] <= 0:
        return False, 0.0, 0.0
    if a["l"] < 0:
        pf = a["w"] / abs(a["l"])
    else:
        pf = float("inf") if a["w"] > 0 else 0.0
    return (a["n"] >= 5 and a["l"] < 0 and pf < 1.0), pf, a["n"]


def section_kill_switch(stats: dict[str, Any] | None, out: list[str]) -> None:
    out.append("")
    out.append("(2) АГРЕГАТ KILL-SWITCH (правило движка: n>=5 и PF<1.0 → вырезать)")
    if not stats or not isinstance(stats.get("buckets"), dict):
        out.append(f"  {STATS_FILE}: НЕТ данных — секция пуста.")
        return
    buckets = stats["buckets"]
    asis = kill_switch_agg(buckets, any_only=False)
    dedup = kill_switch_agg(buckets, any_only=True)
    out.append(
        "  'как движок' = сумма ВСЕХ бакетов имени (|ANY| + режимные, n задвоен);"
    )
    out.append(
        "  'дедуп' = только бакеты |ANY| (та же выборка сделок без разбивки по режимам)."
    )
    out.append("")
    out.append(
        f"  {'стратегия':<22} {'n|как':>7} {'PF|как':>7} {'KILL':>6}   "
        f"{'n|дедуп':>8} {'PF|дедуп':>9}  вердикт"
    )
    for name in sorted(asis, key=lambda k: (kill_verdict(asis[k])[0] is False, -asis[k]["n"])):
        kill_a, pf_a, n_a = kill_verdict(asis.get(name))
        kill_d, pf_d, n_d = kill_verdict(dedup.get(name))
        if kill_a and kill_d:
            verdict = "вырезал бы (и при дедуп. n тоже)"
        elif kill_a and not kill_d:
            verdict = f"вырезал бы АВАНСОМ: при дедуп. n={n_d:.0f} правило не срабатывает"
        elif not kill_a and kill_d:
            verdict = "наоборот: дедуп. n вырезал бы, а движок — нет"
        else:
            verdict = "не трогает"
        out.append(
            f"  {name:<22} {n_a:>7.0f} {_ratio(pf_a):>7} "
            f"{'ДА' if kill_a else 'нет':>6}   {n_d:>8.0f} {_ratio(pf_d):>9}  {verdict}"
        )
    out.append("")
    out.append(
        "  ФАКТИЧЕСКИ вырезанные в сессиях в state не сохраняются (список strategy — "
        "рантайм пайплайна);"
    )
    out.append(
        "  строки 'KILL-SWITCH <name>: n=... PF=...' видны только в логе сессии → "
        "владелец смотрит в Actions."
    )


# ------------------------------------------------------------ (3) ledger сверка
def _ledger_replay(text: str, initial_cash: Decimal) -> tuple[Any, Counter, list[str]]:
    """Реплей штатным TradeLedger (файл-копия во временном каталоге, не state)."""
    with tempfile.TemporaryDirectory(prefix="astra_ledger_review_") as tmp:
        path = Path(tmp) / LEDGER_FILE
        path.write_text(text, encoding="utf-8")
        snapshot = TradeLedger(path).replay(initial_cash=initial_cash)
        kinds: Counter = Counter()
        ids: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            kinds[str(row.get("kind") or "?")] += 1
            if row.get("event_id"):
                ids.append(str(row["event_id"]))
        return snapshot, kinds, ids


def _fees_all_rows(text: str) -> Decimal:
    """Σ fee по ВСЕМ строкам — 'старый' способ (двойной счёт). Для диагностики."""
    total = Decimal("0")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += _dec(row.get("fee"))
    return total


def section_ledger(
    source: StateSource,
    trades: list[dict[str, Any]],
    positions_state: dict[str, Any] | None,
    out: list[str],
) -> None:
    out.append("")
    out.append("(3) PAPER_LEDGER.JSONL: НАЛИЧИЕ И СВЕРКА С БРОКЕРОМ (РЕПЛЕЙ)")
    text = source.read_text(LEDGER_FILE)
    if text is None:
        out.append(f"  {LEDGER_FILE}: НЕТ файла в источнике (CI сохраняет его после сессии с ledger).")
        out.append("  Сверка невозможна. Причина отсутствия файла в логе сессии → владелец смотрит в Actions.")
        return
    cash = _dec((positions_state or {}).get("initial_capital"))
    snapshot, kinds, ids = _ledger_replay(text, cash)
    dups = {eid: n for eid, n in Counter(ids).items() if n > 1}
    broker_fees = sum((_dec(t.get("fees")) for t in trades), Decimal("0"))
    broker_realized = _dec((positions_state or {}).get("realized_pnl"))
    all_rows_fee = _fees_all_rows(text)
    out.append(f"  строк: {snapshot.events}, kinds: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    out.append(f"  дубли event_id (dedup-целостность): {len(dups)}" + (f" {list(dups)[:3]}" if dups else ""))
    out.append("")
    fee_delta = snapshot.fees - broker_fees
    out.append(
        f"  Σfee(replay, только kind=fee) = {_money(snapshot.fees)}   |   "
        f"Σ fees(paper_trades, брокер) = {_money(broker_fees)}"
    )
    if fee_delta == 0:
        out.append("  ВЕРДИКТ Σfee: СХОДИТСЯ")
    else:
        out.append(f"  ВЕРДИКТ Σfee: РАСХОЖДЕНИЕ {_money(fee_delta)}")
    out.append(
        f"  (справочно) Σfee по ВСЕМ строкам = {_money(all_rows_fee)} — так считал "
        "replay до фикса B2 (order/fill дублировали fee)"
    )
    realized_delta = snapshot.realized_pnl - broker_realized
    out.append(
        f"  realized(replay) = {_money(snapshot.realized_pnl)}   |   "
        f"realized_pnl(paper_positions) = {_money(broker_realized)}"
    )
    if realized_delta == 0:
        out.append("  ВЕРДИКТ realized: СХОДИТСЯ")
    else:
        out.append(f"  ВЕРДИКТ realized: РАСХОЖДЕНИЕ {_money(realized_delta)}")
    state_pos = _signed_positions(positions_state)
    replayed = {k: v for k, v in snapshot.positions.items() if v != 0}
    if replayed == state_pos:
        out.append(f"  ВЕРДИКТ инвентарь: СХОДИТСЯ ({len(replayed)} позиций)")
    else:
        out.append(f"  ВЕРДИКТ инвентарь: РАСХОЖДЕНИЕ replay={_pos_str(replayed)} state={_pos_str(state_pos)}")
    out.append(
        f"  cash(replay) = {_money(snapshot.cash)} при initial_capital={_money(cash)} "
        "(cash меняется только закрытиями; funding брокер учитывает отдельно)"
    )


def _signed_positions(positions_state: dict[str, Any] | None) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for p in (positions_state or {}).get("positions") or []:
        if not isinstance(p, dict):
            continue
        symbol = str(p.get("symbol") or "")
        qty = _dec(p.get("quantity"))
        if p.get("direction") == "short":
            qty = -qty
        if symbol and qty != 0:
            result[symbol] = result.get(symbol, Decimal("0")) + qty
    return {k: v for k, v in result.items() if v != 0}


def _pos_str(positions: dict[str, Decimal]) -> str:
    if not positions:
        return "{}"
    items = ", ".join(f"{k}:{_money(v)}" for k, v in sorted(positions.items()))
    return "{" + items + "}"


# ------------------------------------------------------------ (4) shadow bans
def section_shadow(source: StateSource, out: list[str]) -> None:
    out.append("")
    out.append("(4) HTF_SHADOW_BANS.JSONL: ОБЪЁМ И РАЗБИВКА (SHADOW-фильтр, входы не блокирует)")
    rows = source.read_jsonl(SHADOW_FILE)
    if rows is None:
        out.append(
            f"  {SHADOW_FILE}: НЕТ файла в источнике — журнал пуст (best-effort, пишется "
            "только при гипотетических запретах)."
        )
        return
    out.append(f"  строк: {len(rows)}")
    if not rows:
        return
    ts = [int(r["ts"]) for r in rows if r.get("ts")]
    bars = {int(r["bar_time"]) for r in rows if r.get("bar_time")}
    if ts:
        out.append(
            f"  период: {_iso_ms(min(ts))} … {_iso_ms(max(ts))}, уникальных bar_time: {len(bars)}"
        )
    by_strategy = Counter(str(r.get("strategy") or "?") for r in rows)
    by_direction = Counter(str(r.get("direction") or "?") for r in rows)
    by_bias = Counter(str(r.get("htf_bias") or "?") for r in rows)
    by_symbol = Counter(str(r.get("symbol") or "?") for r in rows)
    out.append("  по стратегиям: " + ", ".join(f"{k}={v}" for k, v in by_strategy.most_common()))
    out.append("  по направлениям (запрещённый вход): " + ", ".join(
        f"{k}={v} ({_pct(v, len(rows))})" for k, v in by_direction.most_common()
    ))
    out.append("  по htf_bias: " + ", ".join(f"{k}={v}" for k, v in by_bias.most_common()))
    out.append("  топ-5 символов: " + ", ".join(f"{k}={v}" for k, v in by_symbol.most_common(5)))
    out.append(
        "  помета: дедуп в рамках процесса по (symbol, bar_time, strategy, direction); "
        "rotation cap 5000 строк."
    )


# ------------------------------------------------------------ (5) сделки окна
def section_trades(
    trades: list[dict[str, Any]],
    days: int,
    as_of_ms: int | None,
    out: list[str],
) -> None:
    out.append("")
    out.append(f"(5) PAPER_TRADES.JSONL ЗА ОКНО ({days} дней)")
    if not trades:
        out.append(f"  {TRADES_FILE}: НЕТ сделок (или нет файла) в источнике.")
        out.append("  COOLDOWN-блокировки: по state не проверяются — владелец смотрит в Actions.")
        return
    closed = [t for t in trades if t.get("closed_at")]
    if not closed:
        out.append("  в файле нет сделок с closed_at — секция пуста.")
        return
    end_ms = as_of_ms if as_of_ms is not None else max(int(t["closed_at"]) for t in closed)
    start_ms = end_ms - days * MS_PER_DAY
    window = [t for t in closed if start_ms <= int(t["closed_at"]) <= end_ms]
    out.append(f"  окно: {_iso_ms(start_ms)} … {_iso_ms(end_ms)}; сделок в окне: {len(window)}")
    if not window:
        out.append("  в окне нет сделок.")
        return
    span_days = max(
        1.0, (max(int(t["closed_at"]) for t in window) - min(int(t["closed_at"]) for t in window))
        / MS_PER_DAY,
    )
    out.append(
        f"  фактический охват окна: {span_days:.2f} дней; "
        f"входов/день = {len(window) / days:.2f} (по календарным {days} дн.) / "
        f"{len(window) / span_days:.2f} (по охвату)"
    )
    reasons = Counter(str(t.get("exit_reason") or "?") for t in window)
    out.append("  доли exit_reason:")
    for reason, n in reasons.most_common():
        out.append(f"    {reason:<18} {n:>4}  {_pct(n, len(window)):>6}")
    stops = [t for t in window if "stop" in str(t.get("exit_reason") or "").lower()]
    out.append(f"  стоп-выходов (exit_reason содержит 'stop'): {len(stops)} ({_pct(len(stops), len(window))})")
    rs = [float(t["r_multiple"]) for t in stops if t.get("r_multiple") is not None]
    if rs:
        out.append(
            f"    R: mean {_r(statistics.fmean(rs))}, median {_r(statistics.median(rs))}, "
            f"min {_r(min(rs))}, max {_r(max(rs))}, хуже -1R: "
            f"{sum(1 for x in rs if x < -1.0)}/{len(rs)} (fees и проскальзывание/гэп сверх уровня)"
        )
    pnl = sum((_dec(t.get("pnl")) for t in window), Decimal("0"))
    fees = sum((_dec(t.get("fees")) for t in window), Decimal("0"))
    out.append(f"  Σpnl(окно) = {_money(pnl)}, Σfees(окно) = {_money(fees)} (деньги в Decimal)")
    out.append(
        "  COOLDOWN-блокировки: логи Actions не видны из репозитория — "
        "владелец смотрит в Actions."
    )


def _iso_ms(ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


# ------------------------------------------------------------------- отчёт/CLI
def render_report(source: StateSource, days: int, as_of_ms: int | None) -> str:
    out: list[str] = []
    out.append("=" * 100)
    out.append("ASTRA: СРЕЗ ДВУХНЕДЕЛЬНОГО ОБЗОРА")
    out.append("=" * 100)
    out.append(f"  источник: {source.describe()}")
    out.append("  режим: ТОЛЬКО ЧТЕНИЕ (файлы state не создаются/не изменяются)")
    for name in (STATS_FILE, TRADES_FILE, LEDGER_FILE, POSITIONS_FILE, SHADOW_FILE):
        out.append(f"    {'есть' if source.exists(name) else 'НЕТ ':>4}  {name}")

    stats = source.read_json(STATS_FILE)
    trades = source.read_jsonl(TRADES_FILE) or []
    positions_state = source.read_json(POSITIONS_FILE)

    section_buckets(stats, trades, out)
    section_kill_switch(stats, out)
    section_ledger(source, trades, positions_state, out)
    section_shadow(source, out)
    section_trades(trades, days, as_of_ms, out)

    out.append("")
    out.append("=" * 100)
    out.append(
        "Пометки: 'видел в коде' — утверждения о правилах (kill-switch, PF, дедуп банов) "
        "сверены с кодом; числа — из state."
    )
    out.append(
        "COOLDOWN/прочие рантайм-события в state не пишутся — смотреть лог сессии в Actions "
        "(владелец смотрит в Actions)."
    )
    return "\n".join(out)


def _parse_as_of(value: str | None) -> int | None:
    if not value:
        return None
    from datetime import UTC, datetime

    text = value.strip().replace("Z", "+00:00")
    if len(text) == 10:
        text += "T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Срез двухнедельного обзора (только чтение state)")
    parser.add_argument("--models-dir", default="models", help="каталог state (по умолчанию models/)")
    parser.add_argument(
        "--git-ref",
        default=None,
        help="читать state из git-ref (например origin/master) через git show; fetch не выполняется",
    )
    parser.add_argument("--days", type=int, default=14, help="окно секции (5), по умолчанию 14 дней")
    parser.add_argument("--as-of", default=None, help="конец окна (ISO, например 2026-09-12T06:13Z)")
    args = parser.parse_args(argv)

    source = StateSource(Path(args.models_dir), git_ref=args.git_ref)
    print(render_report(source, days=args.days, as_of_ms=_parse_as_of(args.as_of)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
