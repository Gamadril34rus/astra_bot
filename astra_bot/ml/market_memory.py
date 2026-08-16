"""Persistent semantic memory built from closed trade lessons.

Memory is deliberately compact and explainable. It stores counts and outcomes
for combinations of chart patterns/regimes/news context and exposes smoothed
historical win-rate features for future decisions.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


class MarketMemory:
    def __init__(self, path: Path = Path("models/market_memory.json")) -> None:
        self.path = path
        self.data: dict[str, Any] = {"patterns": {}, "updated": None}
        self.load()

    @staticmethod
    def _bucket(value: float, edges: tuple[float, ...]) -> int:
        for i, edge in enumerate(edges):
            if value < edge:
                return i
        return len(edges)

    @classmethod
    def pattern_key(cls, features: dict[str, float]) -> str:
        candle = "+".join(name for name in (
            "bullish_engulfing", "bearish_engulfing", "candle_hammer",
            "candle_shooting_star", "candle_doji", "candle_inside_bar",
            "morning_star", "evening_star",
        ) if features.get(name, 0.0) > 0.5) or "NONE"
        structure = int(round(float(features.get("structure_bias", 0.0))))
        breakout = "UP" if features.get("breakout_up", 0.0) > 0.5 else "DOWN" if features.get("breakout_down", 0.0) > 0.5 else "NONE"
        rsi = cls._bucket(float(features.get("rsi_14", features.get("rsi", 50.0))), (30.0, 45.0, 55.0, 70.0))
        vol = cls._bucket(float(features.get("atr_pct", 0.0)), (1.0, 2.0, 4.0, 7.0))
        channel = cls._bucket(float(features.get("channel_position_50", 0.0)), (-1.0, -0.25, 0.25, 1.0))
        news = cls._bucket(float(features.get("news_sentiment", 0.0)), (-0.35, -0.05, 0.05, 0.35))
        return f"c={candle}|s={structure}|b={breakout}|r={rsi}|v={vol}|ch={channel}|n={news}"

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {"patterns": {}, "updated": None}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def observe(self, lesson: dict[str, Any]) -> None:
        features = lesson.get("features") or {}
        key = self.pattern_key(features)
        row = self.data.setdefault("patterns", {}).setdefault(key, {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "pnl": 0.0,
            "recommendations": {},
        })
        row["trades"] += 1
        if lesson.get("outcome") == "win":
            row["wins"] += 1
        elif lesson.get("outcome") == "loss":
            row["losses"] += 1
        row["pnl"] += float(lesson.get("pnl", 0.0) or 0.0)
        recommendation = str(lesson.get("recommendation") or "UNKNOWN")
        row.setdefault("recommendations", {})[recommendation] = row.setdefault("recommendations", {}).get(recommendation, 0) + 1

    def build_from_lessons(self, lessons_path: Path = Path("models/lessons.jsonl")) -> int:
        if not lessons_path.exists():
            return 0
        self.data = {"patterns": {}, "updated": None}
        count = 0
        with lessons_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    lesson = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.observe(lesson)
                count += 1
        from datetime import UTC, datetime
        self.data["updated"] = datetime.now(tz=UTC).isoformat()
        self.save()
        return count

    def features_for(self, features: dict[str, float]) -> dict[str, float]:
        key = self.pattern_key(features)
        row = self.data.get("patterns", {}).get(key)
        if not row:
            return {
                "memory_pattern_seen": 0.0,
                "memory_pattern_win_rate": 0.5,
                "memory_pattern_count_log": 0.0,
                "memory_pattern_pnl": 0.0,
            }
        trades = max(int(row.get("trades", 0)), 0)
        wins = max(int(row.get("wins", 0)), 0)
        # Beta(1,1) smoothing prevents a single lucky trade from dominating.
        win_rate = (wins + 1.0) / (trades + 2.0)
        import math
        return {
            "memory_pattern_seen": 1.0,
            "memory_pattern_win_rate": float(win_rate),
            "memory_pattern_count_log": float(math.log1p(trades)),
            "memory_pattern_pnl": float(row.get("pnl", 0.0)),
        }

    def best_lessons(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = []
        for key, row in self.data.get("patterns", {}).items():
            trades = int(row.get("trades", 0))
            if trades < 5:
                continue
            wr = (int(row.get("wins", 0)) + 1) / (trades + 2)
            rows.append({"pattern": key, "trades": trades, "win_rate": wr, "pnl": float(row.get("pnl", 0.0))})
        rows.sort(key=lambda x: (x["win_rate"], x["pnl"]), reverse=True)
        return rows[:limit]
