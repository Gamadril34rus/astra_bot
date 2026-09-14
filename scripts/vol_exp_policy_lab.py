"""Замер политик выхода VOL_EXPANSION на существующем paper-логе (только чтение).

Вопрос: что изменилось бы, если правило `VOL_EXPANSION` (safety-контур,
TR бара >= 2 x медиана TR окна) сделать (1) с абсолютным полом по проценту
цены, (2) направленным — резать только спайк ПРОТИВ позиции, (3) вообще
выключить. Барового ряда в репозитории нет, поэтому пересчитать сам триггер
нельзя — оцениваем только развитие цены ПОСЛЕ отменённого срыва, тремя
сценариями (брейкет), как это уже делает ``scripts/measure_rr_reach.py``:

  stop  — пессимистичный: позиция доходит до MAE_CUT (-0.9R, exit_plan.py:72);
  take  — оптимистичный: позиция доходит до plan-тейка (+rr R);
  pop   — нейтральный: позиция даёт средний R по популяции сделок того же
          таймфрейма, закрытых НЕ этим правилом (эмпирика «что бывает с
          позицией, которую не тронули»).

Дополнительно (без сценариев) считается ``sizing`` — пересчёт P&L к единому
1R: правило тут ни при чём, но распределение убытка зависит от того, что
1R в сделках скачет в разы (риск-бюджет не выдерживается маржой).

Отмена срабатывания под policy определяется по НАБЛЮДЕННОМУ ходу цены за
сделку (``|exit/entry - 1|``) — это нижняя граница TR триггер-бара: если ход
больше порога, бар-спайк превысил бы и абсолютный пол, и правило осталось бы
в силе. Отсюда «пол» (floor) трактуется консервативно.

Запуск:
    python scripts/vol_exp_policy_lab.py [путь_к_jsonl] [--since ISO_UTC]

Только чтение: ничего не пишет, конфигурацию не меняет (окно фиксации правил).
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MAE_CUT_R = 0.9  # exit_plan.ExitPlanParams.mae_cut_r (смартовый дефолт)
DEFAULT_PATH = "models/paper_trades.jsonl"
FLOORS_PCT = (0.2, 0.3, 0.4, 0.6, 1.0)  # абсолютный пол TR, % от цены


@dataclass
class T:
    """Сделка в терминах политики выхода."""

    symbol: str
    strategy: str
    timeframe: str
    exit_reason: str
    closed_at: int
    pnl: float
    fees: float
    r: float
    mae_r: float
    mfe_r: float
    risk_usd: float  # долларовой размер 1R (вход -> initial_stop) x qty
    rr: float  # расстояние до plan-тейка в R
    move_pct: float  # |exit/entry - 1| * 100 — нижняя граница TR бара

    @property
    def stop_scn(self) -> float:
        return -MAE_CUT_R * self.risk_usd

    @property
    def take_scn(self) -> float:
        return self.rr * self.risk_usd


def _f(row: dict, key: str, default: float = 0.0) -> float:
    v = row.get(key)
    return default if v is None else float(v)


def load(path: Path) -> list[T]:
    out: list[T] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if not row.get("closed_at"):
            continue
        entry = _f(row, "effective_entry") or _f(row, "entry_price")
        qty = _f(row, "quantity")
        stop = _f(row, "initial_stop")
        take = _f(row, "initial_take")
        if not (entry and qty and stop) or entry == stop:
            continue
        risk_usd = abs(stop - entry) * qty
        rr = abs(take - entry) / abs(stop - entry) if take else 0.0
        # ход цены за сделку в % (без знака): нижняя граница TR спайк-бара
        exit_price = _f(row, "exit_price") or entry
        move_pct = abs(exit_price / entry - 1.0) * 100.0
        out.append(
            T(
                symbol=row.get("symbol", "?"),
                strategy=row.get("strategy", "?"),
                timeframe=row.get("timeframe") or "?",
                exit_reason=row.get("exit_reason") or "?",
                closed_at=int(row["closed_at"]),
                pnl=_f(row, "pnl"),
                fees=_f(row, "fees"),
                r=_f(row, "r_multiple"),
                mae_r=_f(row, "mae_r"),
                mfe_r=_f(row, "mfe_r"),
                risk_usd=risk_usd,
                rr=rr,
                move_pct=move_pct,
            )
        )
    return out


def pop_mean_r(trades: list[T], tf: str) -> float:
    """Средний R по сделкам того же ТФ, закрытым НЕ VOL_EXPANSION."""
    s = [t.r for t in trades if t.timeframe == tf and t.exit_reason != "VOL_EXPANSION"]
    return statistics.mean(s) if s else 0.0


def apply_policy(
    trades: list[T], policy: str, floor_pct: float, by_tf: dict[str, float]
) -> list[tuple[T, float, float, float]]:
    """Для каждой сделки -> (t, outcome_stop, outcome_take, outcome_pop).

    outcome_* = P&L в долларах при данной политике; равны факту там, где
    политика не отменяет срабатывание.
    """
    res: list[tuple[T, float, float, float]] = []
    for t in trades:
        cancels = False
        if t.exit_reason == "VOL_EXPANSION":
            if policy == "off":
                cancels = True
            elif policy == "floor":
                # отменяем, только если заведомо маленький ход (нижняя граница
                # TR не достигает пола) — иначе правило сработало бы и так
                cancels = t.move_pct < floor_pct
            elif policy == "directional":
                # спайк в нашу пользу (сделка закрылась в плюс) — не режем
                cancels = t.pnl > 0
        if not cancels:
            res.append((t, t.pnl, t.pnl, t.pnl))
            continue
        base = by_tf.get(t.timeframe, 0.0)
        res.append((t, t.stop_scn, t.take_scn, base * t.risk_usd))
    return res


def summarize(rows: list[tuple[T, float, float, float]]) -> dict[str, float]:
    n = len(rows)
    cut = sum(1 for t, *_ in rows if t.exit_reason == "VOL_EXPANSION")
    out = {
        "n": n,
        "vol_exp": cut,
        "stopped": sum(1 for t, a, _, _ in rows if t.exit_reason == "VOL_EXPANSION" and a != t.pnl),
    }
    for key, idx in (("stop", 1), ("take", 2), ("pop", 3)):
        vals = [v[idx] for v in rows]
        out[f"{key}_total"] = sum(vals)
        out[f"{key}_delta"] = sum(vals) - sum(t.pnl for t, *_ in rows)
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = Path(args[0] if args else DEFAULT_PATH)
    since = None
    for a in sys.argv[1:]:
        if a.startswith("--since"):
            raw = a.split("=", 1)[1] if "=" in a else sys.argv[sys.argv.index(a) + 1]
            since = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000
    raw = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    all_tr = load(path)
    dropped = len([r for r in raw if r.get("closed_at")]) - len(all_tr)
    def _span(rows: list[T]) -> str:
        if not rows:
            return "—"
        f = datetime.fromtimestamp(min(r.closed_at for r in rows) / 1000, UTC).strftime("%d.%m")
        l = datetime.fromtimestamp(max(r.closed_at for r in rows) / 1000, UTC).strftime("%d.%m %H:%M")
        return f"{f} -> {l} UTC"

    windows = [(f"все сделки с геометрией лога [{_span(all_tr)}]", all_tr)]
    if since:
        windows.append(("окно с " + datetime.fromtimestamp(since / 1000, UTC).strftime("%d.%m %H:%M"),
                        [t for t in all_tr if t.closed_at >= since]))

    print(f"источник: {path}  закрытых сделок с geometry: {len(all_tr)} "
          f"(без initial_stop/initial_take — не участвуют: {dropped})")
    print(f"допущения: MAE_CUT=-{MAE_CUT_R}R (пессимистичный сценарий), "
          f"оптимистичный = плановый тейк, нейтральный = средний R популяции того же ТФ")

    for title, tr in windows:
        if not tr:
            continue
        base_total = sum(t.pnl for t in tr)
        by_tf = {tf: pop_mean_r([x for x in tr if x.timeframe == tf], tf) for tf in {t.timeframe for t in tr}}
        print(f"\n{'='*78}\n{title}: сделок {len(tr)}, факт P&L {base_total:+.2f} U, "
              f"средний R {statistics.mean(t.r for t in tr):+.3f}")
        ve = [t for t in tr if t.exit_reason == "VOL_EXPANSION"]
        if ve:
            print(f"  бакет VOL_EXPANSION: n={len(ve)} сумма {sum(t.pnl for t in ve):+.2f} U "
                  f"({100*sum(t.pnl for t in ve)/base_total:.0f}% убытка), "
                  f"среднее {statistics.mean(t.pnl for t in ve):+.2f} U, "
                  f"медиана {statistics.median(t.pnl for t in ve):+.2f} U, "
                  f"средний R {statistics.mean(t.r for t in ve):+.3f}")
            print("  средний R популяции по ТФ: " + ", ".join(f"{k}: {v:+.3f}" for k, v in sorted(by_tf.items())))
            print(f"  1R в бакете: мин {min(t.risk_usd for t in ve):.1f} / "
                  f"медиана {statistics.median(t.risk_usd for t in ve):.1f} / "
                  f"макс {max(t.risk_usd for t in ve):.1f} U "
                  f"(разброс x{max(t.risk_usd for t in ve)/max(0.1,min(t.risk_usd for t in ve)):.1f})")

        det = [t for t in tr if t.exit_reason == "VOL_EXPANSION"]
        if det:
            print("\n  разбор бакета (ход цены = нижняя граница TR триггер-бара):")
            print(f"    {'символ':<11}{'pnl':>8}{'R':>7}{'ход%':>7}{'1R,U':>7}{'тейк,R':>7}"
                  f"{'если не резать: stop':>22}{'pop':>8}{'take':>8}")
            for t in sorted(det, key=lambda x: x.closed_at):
                b = by_tf.get(t.timeframe, 0.0)
                print(f"    {t.symbol:<11}{t.pnl:>8.2f}{t.r:>7.2f}{t.move_pct:>7.2f}{t.risk_usd:>7.1f}"
                      f"{t.rr:>7.2f}{t.stop_scn:>22.2f}{b * t.risk_usd:>8.2f}{t.take_scn:>8.2f}")
            print(f"    {'ИТОГО бакет':<11}{sum(t.pnl for t in det):>8.2f}{'':>7}{'':>7}"
                  f"{sum(t.risk_usd for t in det):>7.1f}{'':>7}{sum(t.stop_scn for t in det):>22.2f}"
                  f"{sum(by_tf.get(t.timeframe, 0.0) * t.risk_usd for t in det):>8.2f}"
                  f"{sum(t.take_scn for t in det):>8.2f}")

        print(f"\n  {'политика':<26}{'срезано':>9}{'stop (песс.):':>16}{'pop (нейтр.)':>15}{'take (оптим.)':>16}")
        for label, policy, floor in (
            [("факт (VOL_EXP как есть)", "keep", 0.0)]
            + [(f"пол TR >= {x}% цены", "floor", x) for x in FLOORS_PCT]
            + [
                ("резать только против позиции", "directional", 0.0),
                ("VOL_EXPANSION выключен", "off", 0.0),
            ]
        ):
            if policy == "keep":
                s = {"n": len(tr), "vol_exp": len([t for t in tr if t.exit_reason == "VOL_EXPANSION"]),
                     "stopped": 0, "stop_total": base_total, "take_total": base_total,
                     "pop_total": base_total, "stop_delta": 0.0, "take_delta": 0.0, "pop_delta": 0.0}
            else:
                s = summarize(apply_policy(tr, policy, floor, by_tf))
            print(f"  {label:<26}{s['stopped']:>7}/{s['vol_exp'] or 0}"
                  f"{s['stop_total']:>16.2f}{s['pop_total']:>15.2f}{s['take_total']:>16.2f}")

        # отдельная политика: риск-нормирование (не про триггер)
        med_risk = statistics.median(t.risk_usd for t in tr)
        resized = sum(t.pnl * (med_risk / t.risk_usd) for t in tr)
        print(f"\n  sizing@медиана-1R ({med_risk:.1f} U): P&L окна {resized:+.2f} U "
              f"(факт {base_total:+.2f}) — эффект без изменения правил выхода")

    raw_rows = _raw(path)
    print(f"\n{'='*78}\nДополнительные срезы (все {len([r for r in raw_rows if r.get('closed_at')])} закрытых сделок)")
    for rr in (1.0, 1.5, 2.3):
        r_space(raw_rows, rr)
    power(raw_rows, int(since) if since else None)
    print("\n  перезаходы в тот же символ (churn):")
    churn(raw_rows)
    scoped = [r for r in raw_rows if r.get("closed_at")]
    print("\n  политика «кулдаун на вход после forced-выхода» (статический контрфакт):")
    for minutes in (20, 30, 60):
        cooldown(scoped, minutes, ("VOL_EXPANSION",),
                 f"VOL_EXP -> блок на {minutes} мин")
    cooldown(scoped, 20, ("VOL_EXPANSION", "mae_cut", "regime_exit"),
             "любой forced -> блок 20 мин")
    return 0




# ----------------------------------------------------------------------------------
# R-пространство: вся эпоха без геометрии (метод measure_rr_reach.py) + power + churn
# ----------------------------------------------------------------------------------


def _raw(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and l.strip()[0] == "{"]


def r_space(rows: list[dict], rr_assumed: float) -> None:
    """Что было бы с суммой R, если не резать спайк-выходы (касание тейка = mfe)."""
    have = [r for r in rows if r.get("closed_at") and r.get("r_multiple") is not None]
    base = sum(float(r["r_multiple"]) for r in have)
    ve = [r for r in have if r.get("exit_reason") == "VOL_EXPANSION"]
    print(f"\n  R-пространство (все сделки с r_multiple: {len(have)}, из них VOL_EXP {len(ve)}), "
          f"тейк принят как {rr_assumed}R при отсутствии initial_take:")
    for tag, policy in (("факт", "keep"), ("VOL_EXP выключен", "off"),
                       ("только против позиции", "directional")):
        tot = 0.0
        n_cut = 0
        for r in have:
            rr = rr_assumed
            if r.get("initial_take") and r.get("effective_entry") and r.get("initial_stop"):
                ee, ist, itk = (float(r[k]) for k in ("effective_entry", "initial_stop", "initial_take"))
                if ist != ee:
                    rr = abs(itk - ee) / abs(ist - ee)
            is_ve = r.get("exit_reason") == "VOL_EXPANSION"
            keeps = (not is_ve) or policy == "keep" or (policy == "directional" and float(r["pnl"]) <= 0)
            if keeps:
                tot += float(r["r_multiple"])
                continue
            n_cut += 1
            mfe = float(r.get("mfe_r") or 0.0)
            tot += rr if mfe >= rr else -MAE_CUT_R
        d = tot - base
        print(f"    {tag:<24} sum R = {tot:+8.1f}  (факт {base:+.1f}, дельта {d:+.1f}R)  срезано/возвращено: {n_cut}")


def power(rows: list[dict], since_ms: int | None, boot: int = 4000) -> None:
    """Бутстрэп: можно ли на этом объёме вообще обнаружить эффект правила."""
    src = [r for r in rows if r.get("closed_at") and r.get("r_multiple") is not None]
    if since_ms:
        src = [r for r in src if int(r["closed_at"]) >= since_ms]
    a = [float(r["r_multiple"]) for r in src if r.get("exit_reason") == "VOL_EXPANSION"]
    b = [float(r["r_multiple"]) for r in src if r.get("exit_reason") != "VOL_EXPANSION"]
    if len(a) < 3:
        print(f"\n  power: в окне всего {len(a)} VOL_EXP-сделок — статистически неотличимо ни от чего")
        return
    obs = statistics.mean(a) - statistics.mean(b)
    pool = a + b
    rng = random.Random(7)
    stats = []
    for _ in range(boot):
        sa = [rng.choice(a) for _ in a]
        sb = [rng.choice(b) for _ in b]
        stats.append(statistics.mean(sa) - statistics.mean(sb))
    stats.sort()
    lo, hi = stats[int(0.025 * boot)], stats[int(0.975 * boot)]
    print(f"\n  power (бутстрэп {boot}): средний R бакета {statistics.mean(a):+.3f} vs остальные "
          f"{statistics.mean(b):+.3f}; дельта {obs:+.3f}R, 95% CI [{lo:+.3f}, {hi:+.3f}] "
          f"-> {'значимо' if lo*hi > 0 else 'НЕ значимо (CI содержит 0)'}")
    sd = statistics.pstdev(pool)
    n_per_group = int(2 * (1.96 + 0.84) ** 2 * sd * sd / 0.25**2)
    print(f"    sigma(R) = {sd:.2f}; чтобы различить сдвиг 0.25R с power 0.8 нужно "
          f"~{n_per_group} сделок в бакете и столько же в контроле (сейчас {len(a)})")


def cooldown(rows: list[dict], minutes: int, reasons: tuple[str, ...], label: str) -> None:
    """Статический контрфакт: запрет любого нового входа в символ на N минут после forced-выхода."""
    closes = [r for r in rows if r.get("exit_reason") in reasons and r.get("closed_at")]
    blocked: dict[str, dict] = {}
    for c in closes:
        tc = int(c["closed_at"])
        for r in rows:
            if r.get("symbol") != c.get("symbol") or not r.get("opened_at"):
                continue
            if tc < int(r["opened_at"]) <= tc + minutes * 60_000:
                blocked[r.get("id") or f"{r.get('symbol')}{r.get('opened_at')}"] = r
    b = list(blocked.values())
    tot = sum(float(r["pnl"]) for r in rows if r.get("closed_at"))
    lost = sum(float(r["pnl"]) for r in b)
    fees = sum(float(r.get("fees") or 0) for r in b)
    rs = [float(r["r_multiple"]) for r in b if r.get("r_multiple") is not None]
    print(f"    {label:<38} выходов {len(closes):>3} | блокируется входов {len(b):>3} | "
          f"их P&L {lost:+8.2f} U (fees {fees:>5.2f}, meanR {statistics.mean(rs) if rs else 0:+.3f}) | "
          f"P&L окна {tot:+.2f} -> {tot - lost:+.2f} U")


def churn(rows: list[dict], window_min: int = 30) -> None:
    """Повторные входы в тот же символ сразу после выхода — налог на «срез и перезаход»."""
    by_sym: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("closed_at"):
            by_sym.setdefault(r.get("symbol", "?"), []).append(r)
    for sym in by_sym:
        by_sym[sym].sort(key=lambda x: int(x["opened_at"]))
    for tag, reason in (("VOL_EXPANSION", "VOL_EXPANSION"), ("stop_loss (контроль)", "stop_loss")):
        closes = [r for r in rows if r.get("exit_reason") == reason and r.get("closed_at")]
        follow: dict[str, dict] = {}
        for c in closes:
            tc = int(c["closed_at"])
            for r in by_sym.get(c.get("symbol", "?"), []):
                if tc < int(r["opened_at"]) <= tc + window_min * 60_000:
                    follow[r.get("id") or f"{r.get('symbol')}{r.get('opened_at')}"] = r
        f = list(follow.values())
        if closes and f:
            wins = sum(1 for r in f if float(r["pnl"]) > 0)
            rs = [float(r["r_multiple"]) for r in f if r.get("r_multiple") is not None]
            print(f"    {tag:<24} выходов {len(closes):>3} -> перезаходов ≤{window_min} мин: "
                  f"{len(f):>3} ({100*len(f)/len(closes):>3.0f}%), WR {100*wins/len(f):>3.0f}%, "
                  f"meanR {statistics.mean(rs):+.3f}, P&L {sum(float(r['pnl']) for r in f):+8.2f} U, "
                  f"fees {sum(float(r.get('fees') or 0) for r in f):>6.2f} U")
        elif closes:
            print(f"    {tag:<24} выходов {len(closes):>3} -> перезаходов ≤{window_min} мин: 0")


if __name__ == "__main__":
    raise SystemExit(main())
