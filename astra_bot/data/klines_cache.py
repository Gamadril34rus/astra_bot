"""Локальный кэш закрытых баров (PR #78, часть 4 — чистая телеметрия).

Зачем это нужно. До сих пор «история баров» собиралась реконструкцией из
``models/no_trade_outcomes.json``: непрерывные серии получались длиной
медиана 31 бар, p90 108, максимум 192 (5m). Следствия, уже проявившиеся в
исследованиях:

* мультисуточные горизонты недоступны в принципе;
* «не тронуто» на длинных окнах оказывалось цензурой (конца серии нет), а не
  свойством рынка — первая версия геометрии достижимости на этом споткнулась.

Кэш пишет каждый увиденный ДВИЖКОМ закрытый бар ровно один раз (append-only
JSONL на пару символ×ТФ), поэтому через N дней история становится настоящей,
а ``scripts/track_c_replay.py``/``lever_scan`` смогут считать достижимость по
фактическим барам, а не по склейке.

Гарантии (всё — чтобы телеметрия не могла сломать торговлю):
  * только запись; никаких решений, никаких исключений наружу;
  * пишутся только закрытые бары (``close_time <= now``), незакрытая свеча —
    никогда;
  * дедуп по ``open_time``: за раз дописываются только бары новее последнего
    записанного, то есть стоимость вызова O(новых баров), а не O(истории);
  * рост ограничен ``keep_days`` — усечение раз в ``prune_every_writes`` записей;
  * файлы лежат в ``models/klines_cache/`` и не должны попадать в git
    (строка в ``.gitignore``).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Поле одной строки кэша: компактно и самодокументируемо.
_FIELDS = ("t", "o", "h", "l", "c", "v")


@dataclass(frozen=True)
class KlineCacheConfig:
    enabled: bool = True
    dir: Path = Path("models/klines_cache")
    timeframes: tuple[str, ...] = ("5m",)
    keep_days: int = 45
    prune_every_writes: int = 4000

    @classmethod
    def from_engine_config(cls, cfg: Any) -> KlineCacheConfig:
        tfs = getattr(cfg, "klines_cache_timeframes", None) or ("5m",)
        if isinstance(tfs, str):
            tfs = tuple(p.strip() for p in tfs.split(",") if p.strip())
        return cls(
            enabled=bool(getattr(cfg, "klines_cache_enabled", True)),
            dir=Path(getattr(cfg, "klines_cache_dir", "models/klines_cache")),
            timeframes=tuple(str(t) for t in tfs),
            keep_days=int(getattr(cfg, "klines_cache_keep_days", 45)),
        )


def _safe_name(symbol: str, timeframe: str) -> str:
    return f"{str(symbol).replace('/', '-').replace(':', '-')}_{timeframe}.jsonl"


class KlineCache:
    """Append-only кэш баров. Потоко-небезопасен, как и весь движок (один цикл)."""

    def __init__(self, config: KlineCacheConfig | None = None, **kwargs: Any) -> None:
        self.cfg = config or KlineCacheConfig(**kwargs)
        self._last: dict[tuple[str, str], int] = {}
        self._writes_since_prune = 0
        self._dir_created = False
        self.errors = 0

    # ------------------------------------------------------------- internally
    def _path(self, symbol: str, timeframe: str) -> Path:
        return self.cfg.dir / _safe_name(symbol, timeframe)

    def _seed_last(self, path: Path) -> int:
        """Последний записанный open_time — из хвоста файла (иначе дубли)."""
        from collections import deque

        try:
            with path.open("r", encoding="utf-8") as fh:
                tail = list(deque(fh, maxlen=1))
            if not tail:
                return 0
            return int(json.loads(tail[0]).get("t", 0))
        except Exception:
            return 0

    # ------------------------------------------------------------------ public
    def store(self, symbol: str, timeframe: str, candles: Iterable[Any]) -> int:
        """Дописать закрытые бары, которых ещё нет в кэше. Возвращает число записей."""
        if not self.cfg.enabled or timeframe not in self.cfg.timeframes:
            return 0
        try:
            key = (symbol, timeframe)
            if key not in self._last:
                path = self._path(symbol, timeframe)
                self._last[key] = self._seed_last(path) if path.exists() else 0
            last = self._last.get(key, 0)
            rows: list[str] = []
            newest = last
            now_s = int(time.time())
            for c in candles:
                try:
                    t = int(getattr(c, "open_time", 0) or 0)
                    if t <= last:
                        continue
                    # Единицы open_time в репозитории разнородны (секунды в models.Candle,
                    # миллисекунды в части фидов) — определяем по порядку
                    # величины и СРАВНИВАЕМ В ТЕХ ЖЕ ЕДИНИЦАХ, иначе проверка
                    # «бар закрыт» превратится в «не пишем ничего» или наоборот.
                    now = now_s * 1000 if t > 1_000_000_000_000 else now_s
                    close_time = int(getattr(c, "close_time", 0) or 0)
                    if close_time and close_time > now:
                        continue  # незакрытая свеча — не пишем никогда
                    row = {
                        _FIELDS[0]: t,
                        _FIELDS[1]: float(c.open),
                        _FIELDS[2]: float(c.high),
                        _FIELDS[3]: float(c.low),
                        _FIELDS[4]: float(c.close),
                        _FIELDS[5]: float(getattr(c, "volume", 0.0) or 0.0),
                    }
                except (TypeError, ValueError, AttributeError):
                    continue
                rows.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                newest = max(newest, t)
            if not rows:
                return 0
            if not self._dir_created:
                self.cfg.dir.mkdir(parents=True, exist_ok=True)
                self._dir_created = True
            path = self._path(symbol, timeframe)
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(rows) + "\n")
            self._last[key] = newest
            self._writes_since_prune += len(rows)
            if self._writes_since_prune >= self.cfg.prune_every_writes:
                self._prune_all()
            return len(rows)
        except Exception as exc:  # телеметрия не имеет права ронять тик
            self.errors += 1
            logger.debug("klines_cache %s/%s: %s", symbol, timeframe, exc)
            return 0

    def store_context(self, candles_by_tf: Mapping[str, Any], symbol: str) -> int:
        """Склад: ``MarketContext.candles`` — dict {ТФ: [Candle]}."""
        total = 0
        for tf, candles in (candles_by_tf or {}).items():
            total += self.store(symbol, str(tf), list(candles))
        return total

    def read(self, symbol: str, timeframe: str, tail: int = 0) -> list[dict[str, Any]]:
        """Прочитать серию (для скриптов/анализа). Пусто — если кэша ещё нет.

        ``tail > 0`` — только последние N баров (так лабораторный скрипт читает
        свежий срез, не поднимая весь файл).
        """
        path = self._path(symbol, timeframe)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        out.sort(key=lambda r: int(r.get("t", 0)))
        return out[-tail:] if tail and len(out) > tail else out

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.enabled,
            "dir": str(self.cfg.dir),
            "series": {f"{s}|{tf}": n for (s, tf), n in self._last.items()},
            "errors": self.errors,
        }

    # -------------------------------------------------------------- retention
    def _prune_all(self) -> None:
        self._writes_since_prune = 0
        cutoff = int(time.time()) - self.cfg.keep_days * 86_400
        for (symbol, timeframe), _last in list(self._last.items()):
            self._prune_one(symbol, timeframe, cutoff)

    def _prune_one(self, symbol: str, timeframe: str, cutoff: int) -> None:
        path = self._path(symbol, timeframe)
        if not path.exists():
            return
        try:
            rows = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            kept = [line for line in rows if int(json.loads(line).get("t", 0)) >= cutoff]
            if len(kept) != len(rows):
                tmp = path.with_suffix(".jsonl.tmp")
                tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
                tmp.replace(path)
                if kept:
                    self._last[(symbol, timeframe)] = int(json.loads(kept[-1]).get("t", 0))
        except Exception as exc:  # pragma: no cover
            self.errors += 1
            logger.debug("klines_cache prune: %s", exc)
