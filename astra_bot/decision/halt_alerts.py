"""Персистентный dedup HALT-алертов: раз в сутки на ключ.

Actions-контур поднимает свежий процесс каждые 5 минут, поэтому
in-memory dedup (set на TradingEngine) терялся, и HALT-алерт
повторялся КАЖДУЮ сессию, пока лимит пробит. Теперь дата отправки
хранится в ``models/halt_alerts.json`` (часть Save-state в bot.yml):

* тот же ключ в тот же день (UTC) — повтор НЕ шлём;
* новый день — алерт доступен снова;
* если отправка не удалась (сеть/Telegram) — ключ НЕ помечаем,
  следующая сессия попробует ещё раз (но не чаще раза в сутки).

Файл крошится/отсутствует → «ничего не отправлялось» (fail-open для
алерта: лучше повтор, чем тишина).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = "models/halt_alerts.json"
# Ключи старше этого срока удаляем при записи — файл не должен
# расти бесконечно (TZ §29, state size gate).
MAX_AGE_DAYS = 30


def _today(now: datetime | None = None) -> str:
    """Текущая дата UTC (ISO) — окном dedup является календарный день UTC."""
    return (now or datetime.now(UTC)).date().isoformat()


def load_sent(path: str | Path = DEFAULT_PATH) -> dict[str, str]:
    """Вернуть ``{ключ: дата}`` — какие алерты уже были отправлены и когда.

    Повреждённый или отсутствующий файл не фатален: считаем, что
    ничего не отправлялось.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.debug("halt_alerts: не читается %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def already_sent_today(
    path: str | Path = DEFAULT_PATH,
    key: str = "",
    now: datetime | None = None,
) -> bool:
    """True, если алерт с этим ключом уже отправляли сегодня (UTC)."""
    return load_sent(path).get(key) == _today(now)


def mark_sent(
    path: str | Path = DEFAULT_PATH,
    key: str = "",
    now: datetime | None = None,
) -> bool:
    """Отметить, что алерт с ключом отправлен сегодня.

    Возвращает, удалось ли записать файл. Если нет — алерт может
    повториться в следующей сессии (лучше дубль, чем тишина).
    """
    sent = load_sent(path)
    sent[key] = _today(now)
    # Уборка старых ключей, чтобы файл не рос бесконечно.
    try:
        cutoff = (datetime.now(UTC) - timedelta(days=MAX_AGE_DAYS)).date().isoformat()
        sent = {k: v for k, v in sent.items() if v >= cutoff}
    except Exception:
        pass
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(sent, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        logger.warning("halt_alerts: не сохранилось %s: %s", p, exc)
        return False
