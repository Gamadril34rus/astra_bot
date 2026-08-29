"""Learning Digest для Telegram: «чему научилась» вместо счётчиков.

Источники (все персистятся в git-сэйве CI, доступны в утреннем отчёте):
- ``models/live_lessons.jsonl`` — уроки с реальных (paper/live) сделок:
  ``takeaway``/``counterfactual``/``recommendation``;
- ``models/research/hypotheses.json`` — lifecycle гипотез: новые
  переходы VALIDATED/INVALIDATED/RETIRED/WEAKENING с причинами;
- ``models/no_trade_outcomes.json`` + ``models/no_trade_observations.jsonl``
  — исходы NO_TRADE на горизонтах 1/3/6/12/24 баров: «отказ был
  оправдан» — будущее движение против гипотетического входа;
- ``models/strategy_stats.json`` — сама база знаний: EV по
  strategy × regime (только с sample size, без «знаний из одной сделки»).

Водяной знак (последнее время дайджеста) — в
``models/telegram_offset.json`` (уже в CI save-state): idempotency,
недубль между отчётами. Файл не найден/повреждён → окно по умолчанию
26 часов (чуть больше суток — покрытие полного цикла).

Чистое чтение + одна атомарная запись watermark; торговому контуру
модуль не мешает (используется только отчётными скриптами/командами).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODELS_DIR = Path("models")
WATERMARK_KEY = "learning_digest_watermark_ms"
DEFAULT_WINDOW_HOURS = 26
TG_MESSAGE_LIMIT = 4096

# Переходы гипотез, заслуживающие отдельной строки в дайджесте.
NOTABLE_STATUSES = {"VALIDATED", "INVALIDATED", "RETIRED", "WEAKENING"}

_STATUS_EMOJI = {
    "VALIDATED": "✅",
    "INVALIDATED": "❌",
    "RETIRED": "",
    "WEAKENING": "⚠️",
}


# ---------------------------------------------------------------- readers
def _read_jsonl(path: Path, tail: int = 400) -> list[dict]:
    """JSONL → список словарей (последние ``tail`` строк; битые — skip)."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.debug("learning_digest: не прочитал %s: %s", path, exc)
        return []
    out: list[dict] = []
    for line in lines[-tail:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("learning_digest: не прочитал %s: %s", path, exc)
        return {}


def recent_lessons(models_dir: Path, since_ms: int, limit: int = 4) -> list[dict]:
    """Уроки с exit_time >= since_ms (свежие первыми)."""
    rows = _read_jsonl(models_dir / "live_lessons.jsonl")
    fresh = [
        r for r in rows
        if int(r.get("exit_time") or 0) >= since_ms and r.get("takeaway")
    ]
    fresh.sort(key=lambda r: int(r.get("exit_time") or 0), reverse=True)
    return fresh[:limit]


def recent_hypothesis_events(
    models_dir: Path, since_ms: int, limit: int = 4
) -> list[dict]:
    """Заметные переходы гипотез с ``at`` >= since_ms (свежие первыми)."""
    data = _read_json(models_dir / "research" / "hypotheses.json")
    since_iso = datetime.fromtimestamp(since_ms / 1000, tz=UTC).isoformat()
    events: list[dict] = []
    for hid, hyp in (data.get("hypotheses") or {}).items():
        for entry in hyp.get("status_log") or []:
            status = str(entry.get("status", ""))
            at = str(entry.get("at", ""))
            if status not in NOTABLE_STATUSES or not at:
                continue
            if at < since_iso:  # ISO-строки UTC сортируются лексикографически
                continue
            events.append(
                {
                    "id": hid,
                    "strategy_id": hyp.get("strategy_id", ""),
                    "description": hyp.get("description", ""),
                    "status": status,
                    "reason": entry.get("reason")
                    or (hyp.get("invalidation_reason") if status == "INVALIDATED" else ""),
                    "at": at,
                }
            )
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


def recent_no_trade_insights(
    models_dir: Path, since_ms: int, limit: int = 3
) -> list[dict]:
    """NO_TRADE с решённым исходом: «отказ был оправдан?».

    Объединяем индекс исходов (bar_time/горизонты) с наблюдениями
    (symbol/reason_code/candidate.direction).
    """
    outcomes = _read_json(models_dir / "no_trade_outcomes.json")
    if not isinstance(outcomes, dict):
        return []
    obs_by_id = {
        str(o.get("id")): o for o in _read_jsonl(models_dir / "no_trade_observations.jsonl")
    }
    insights: list[dict] = []
    for oid, row in outcomes.items():
        try:
            bar_time_ms = int(row.get("bar_time") or 0) * 1000  # сек → мс
        except (TypeError, ValueError):
            continue
        if bar_time_ms < since_ms:
            continue
        horizons = row.get("horizons") or {}
        # Ближайший решённый горизонт (предпочтительно 3 бара).
        h = horizons.get("3") or next(iter(horizons.values()), None)
        if not isinstance(h, dict) or "future_return" not in h:
            continue
        obs = obs_by_id.get(str(oid), {})
        candidate = obs.get("candidate") or {}
        direction = str(candidate.get("direction") or "").lower()
        fr = float(h.get("future_return") or 0.0)
        justified = None
        if direction == "long":
            justified = fr < 0
        elif direction == "short":
            justified = fr > 0
        insights.append(
            {
                "symbol": row.get("symbol") or obs.get("symbol", "?"),
                "reason_code": row.get("reason_code") or obs.get("reason_code", "?"),
                "direction": direction or None,
                "future_return": fr,
                "justified": justified,
                "bar_time_ms": bar_time_ms,
            }
        )
    insights.sort(key=lambda r: r["bar_time_ms"], reverse=True)
    return insights[:limit]


def knowledge_base_lines(models_dir: Path, limit: int = 2) -> list[str]:
    """База знаний: топы по |EV| среди бакетов с sample_size >= 5."""
    data = _read_json(models_dir / "strategy_stats.json")
    rows: list[tuple[float, str, int]] = []
    for key, st in (data.get("buckets") or {}).items():
        try:
            n = int(st.get("sample_size") or 0)
            ev = float(st.get("sum_r") or 0.0) / n if n else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if n < 5:
            continue
        parts = key.split("|")
        label = " × ".join(parts[:2]) if len(parts) >= 2 else key
        rows.append((abs(ev), f"{label}: EV {ev:+.2f}R (n={n})", n))
    rows.sort(key=lambda r: (r[0], r[2]), reverse=True)
    return [label for _, label, _ in rows[:limit]]


# ---------------------------------------------------------------- watermark
def _offset_path(models_dir: Path) -> Path:
    return models_dir / "telegram_offset.json"


def watermark_ms(models_dir: Path) -> int | None:
    data = _read_json(_offset_path(models_dir))
    try:
        return int(data.get(WATERMARK_KEY)) if data.get(WATERMARK_KEY) else None
    except (TypeError, ValueError):
        return None


def save_watermark(models_dir: Path, ms: int) -> None:
    path = _offset_path(models_dir)
    data = _read_json(path)
    data[WATERMARK_KEY] = int(ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- digest
def _fmt_lesson(r: dict) -> str:
    pnl = r.get("pnl_pct")
    pnl_s = f" ({float(pnl):+.1f}%)" if isinstance(pnl, (int, float)) else ""
    direction = r.get("direction", "")
    factor = r.get("influencing_factor", "")
    tail = f" {factor}" if factor else ""
    return (
        f"📉 {r.get('symbol', '?')} {r.get('strategy', '')} {direction} → "
        f"закрытие{pnl_s}{tail}: «{r.get('takeaway', '')}»"
    )


def _fmt_hyp(e: dict) -> str:
    emoji = _STATUS_EMOJI.get(e["status"], "•")
    short_id = e["id"][-8:] if len(e["id"]) > 8 else e["id"]
    strategy = e.get("strategy_id") or ""
    target = f"{strategy} " if strategy else ""
    reason = f" — {e['reason']}" if e.get("reason") else ""
    return f"{emoji} Гипотеза {target}…{short_id}: {e['status']}{reason}"


def _fmt_no_trade(r: dict) -> str:
    direction = f" {r['direction']}" if r.get("direction") else ""
    verdict = ""
    if r.get("justified") is True:
        verdict = " — отказ оправдан"
    elif r.get("justified") is False:
        verdict = " — движение было за гипотезу"
    return (
        f"🚫 NO_TRADE {r['reason_code']}{direction} {r['symbol']}: "
        f"за 3 бара {r['future_return'] * 100:+.1f}%{verdict}"
    )


def build_digest(
    models_dir: Path = DEFAULT_MODELS_DIR,
    now_ms: int | None = None,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    with_knowledge_base: bool = True,
) -> tuple[str, int]:
    """Собрать текст дайджеста. Возвращает ``(text, new_watermark_ms)``.

    Окно — от watermark (если есть) до ``now_ms``; новый watermark =
    ``now_ms`` (вызывать ``save_watermark`` после успешной отправки).
    """
    now = int(now_ms if now_ms is not None else datetime.now(UTC).timestamp() * 1000)
    wm = watermark_ms(models_dir)
    since = wm if wm is not None else now - int(window_hours * 3600 * 1000)

    lines: list[str] = []
    lessons = recent_lessons(models_dir, since)
    lines += [f"• {les}" for les in (_fmt_lesson(r) for r in lessons)]
    hyps = recent_hypothesis_events(models_dir, since)
    lines += [f"• {h}" for h in (_fmt_hyp(e) for e in hyps)]
    nts = recent_no_trade_insights(models_dir, since)
    lines += [f"• {n}" for n in (_fmt_no_trade(r) for r in nts)]

    had_new = bool(lines)
    if with_knowledge_base:
        kb = knowledge_base_lines(models_dir)
        if kb:
            lines.append(f"📊 База знаний: {'; '.join(kb)}")
        else:
            # База ещё не накоплена (n < 5 во всех бакетах).
            lines.append("📊 База знаний: накопление (n < 5 по всем бакетам)")
    if not had_new:
        lines.insert(0, "• Новых знаний нет: сделок и переходов за период не было")

    text = "📚 Чему научилась система:\n" + "\n".join(lines[:12])
    if len(text) > TG_MESSAGE_LIMIT:
        text = text[: TG_MESSAGE_LIMIT - 1] + "…"
    return text, now
