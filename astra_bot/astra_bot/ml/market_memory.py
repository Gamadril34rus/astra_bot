"""Persistent market memory for trade lessons and research findings."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class MarketMemory:
    def __init__(self, path: Path = Path("models/market_memory.json")) -> None:
        self.path = path
        self.data: dict[str, Any] = {"patterns": {}, "research": {}, "updated": None}
        self.load()

    @staticmethod
    def _bucket(value: float, edges: tuple[float, ...]) -> int:
        for i, edge in enumerate(edges):
            if value < edge:
                return i
        return len(edges)

    @classmethod
    def pattern_key(cls, features: dict[str, float]) -> str:
        candle = (
            "+".join(
                name
                for name in (
                    "bullish_engulfing",
                    "bearish_engulfing",
                    "candle_hammer",
                    "candle_shooting_star",
                    "candle_doji",
                    "candle_inside_bar",
                    "morning_star",
                    "evening_star",
                )
                if features.get(name, 0.0) > 0.5
            )
            or "NONE"
        )
        structure = round(float(features.get("structure_bias", 0.0)))
        breakout = (
            "UP"
            if features.get("breakout_up", 0.0) > 0.5
            else "DOWN" if features.get("breakout_down", 0.0) > 0.5 else "NONE"
        )
        rsi = cls._bucket(
            float(features.get("rsi_14", features.get("rsi", 50.0))), (30.0, 45.0, 55.0, 70.0)
        )
        vol = cls._bucket(float(features.get("atr_pct", 0.0)), (1.0, 2.0, 4.0, 7.0))
        channel = cls._bucket(
            float(features.get("channel_position_50", 0.0)), (-1.0, -0.25, 0.25, 1.0)
        )
        news = cls._bucket(float(features.get("news_sentiment", 0.0)), (-0.35, -0.05, 0.05, 0.35))
        return f"c={candle}|s={structure}|b={breakout}|r={rsi}|v={vol}|ch={channel}|n={news}"

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("patterns", {})
            self.data.setdefault("research", {})
        except Exception:
            self.data = {"patterns": {}, "research": {}, "updated": None}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def observe(self, lesson: dict[str, Any]) -> None:
        features = lesson.get("features") or {}
        key = self.pattern_key(features)
        row = self.data.setdefault("patterns", {}).setdefault(
            key,
            {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0.0,
                "recommendations": {},
            },
        )
        row["trades"] += 1
        if lesson.get("outcome") == "win":
            row["wins"] += 1
        elif lesson.get("outcome") == "loss":
            row["losses"] += 1
        row["pnl"] += float(lesson.get("pnl", 0.0) or 0.0)
        recommendation = str(lesson.get("recommendation") or "UNKNOWN")
        row.setdefault("recommendations", {})[recommendation] = (
            row.setdefault("recommendations", {}).get(recommendation, 0) + 1
        )

    def observe_research(self, row: dict[str, Any]) -> None:
        """Store observations as research evidence, independently of trades."""
        timeframe = row.get("timeframe", "?")
        regime = row.get("market_regime", "?")
        events = row.get("events") or ["baseline"]
        forward = row.get("forward") or row.get("forward_returns") or {}
        for event in events:
            key = f"{event}|{timeframe}|{regime}"
            target = self.data.setdefault("research", {}).setdefault(
                key,
                {
                    "observations": 0,
                    "positive": {},
                    "negative": {},
                    "returns": {},
                    "max_up": {},
                    "max_down": {},
                },
            )
            target["observations"] += 1
            for horizon, value in forward.items():
                if isinstance(value, dict):
                    ret = value.get("return")
                    max_up = value.get("max_up")
                    max_down = value.get("max_down")
                else:
                    ret, max_up, max_down = value, None, None
                if not isinstance(ret, (int, float)):
                    continue
                target["positive"][horizon] = int(target["positive"].get(horizon, 0)) + int(ret > 0)
                target["negative"][horizon] = int(target["negative"].get(horizon, 0)) + int(ret < 0)
                values = target["returns"].setdefault(horizon, [])
                if len(values) < 2000:
                    values.append(float(ret))
                up_values = target["max_up"].setdefault(horizon, [])
                if isinstance(max_up, (int, float)) and len(up_values) < 2000:
                    up_values.append(float(max_up))
                down_values = target["max_down"].setdefault(horizon, [])
                if isinstance(max_down, (int, float)) and len(down_values) < 2000:
                    down_values.append(float(max_down))

    def import_research(
        self,
        path: Path = Path("models/research_observations.jsonl"),
        *,
        rebuild: bool = True,
    ) -> int:
        """Import research observations.

        The merged JSONL file is a cumulative source of truth. Rebuilding by
        default prevents every monthly job from counting all old observations
        again and keeps the derived memory deterministic.
        """
        if not path.exists():
            return 0
        if rebuild:
            self.data["research"] = {}
        count = 0
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("record_type") != "market_research":
                    continue
                self.observe_research(row)
                count += 1
        self.data["updated"] = datetime.now(tz=UTC).isoformat()
        self.save()
        return count

    def build_from_lessons(self, lessons_path: Path = Path("models/lessons.jsonl")) -> int:
        if not lessons_path.exists():
            return 0
        research = self.data.get("research", {})
        self.data = {"patterns": {}, "research": research, "updated": None}
        count = 0
        with lessons_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    lesson = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.observe(lesson)
                count += 1
        self.data["updated"] = datetime.now(tz=UTC).isoformat()
        self.save()
        return count

    def research_features_for(
        self, features: dict[str, float], timeframe: str = "1h", regime: str = "unknown"
    ) -> dict[str, float]:
        """Expose historical research as model features, not deterministic rules."""
        result = {"research_seen": 0.0, "research_samples": 0.0, "research_positive_rate_1h": 0.5}
        candidates = [
            event
            for event in (
                "breakout_up",
                "breakout_down",
                "retest_support",
                "retest_resistance",
                "bullish_engulfing",
                "bearish_engulfing",
                "candle_hammer",
                "candle_shooting_star",
                "volume_spike",
                "atr_expansion",
                "trend_acceleration",
                "trend_deceleration",
            )
            if features.get(event, 0.0) > 0.5
        ]
        for event in candidates:
            key = f"{event}|{timeframe}|{regime}"
            row = self.data.get("research", {}).get(key)
            if not row or int(row.get("observations", 0)) <= 0:
                continue
            samples = int(row["observations"])
            pos = int(row.get("positive", {}).get("1h", 0))
            neg = int(row.get("negative", {}).get("1h", 0))
            result["research_seen"] = 1.0
            result["research_samples"] = max(result["research_samples"], float(samples))
            result["research_positive_rate_1h"] = (pos + 1.0) / (pos + neg + 2.0)
            break
        return result

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
        return {
            "memory_pattern_seen": 1.0,
            "memory_pattern_win_rate": float((wins + 1.0) / (trades + 2.0)),
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
            rows.append(
                {
                    "pattern": key,
                    "trades": trades,
                    "win_rate": wr,
                    "pnl": float(row.get("pnl", 0.0)),
                }
            )
        rows.sort(key=lambda x: (x["win_rate"], x["pnl"]), reverse=True)
        return rows[:limit]
