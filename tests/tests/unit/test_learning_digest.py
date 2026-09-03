"""Learning Digest: «чему научилась» для TG (уроки/гипотезы/NO_TRADE/EV)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from astra_bot.ml.learning_digest import (
    build_digest,
    save_watermark,
    watermark_ms,
)

H = 3600 * 1000


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _make_models(tmp_path: Path, now: int) -> Path:
    d = tmp_path / "models"
    (d / "research").mkdir(parents=True)

    lessons = [
        {
            "trade_id": "t1", "symbol": "BTC-USDT", "direction": "long",
            "exit_time": now - 1 * H, "strategy": "scalp",
            "pnl_pct": -1.2, "outcome": "loss", "influencing_factor": "STOP_LOSS",
            "takeaway": "следовало подождать подтверждения",
        },
        {
            "trade_id": "t2", "symbol": "ETH-USDT", "direction": "short",
            "exit_time": now - 3 * 24 * H, "strategy": "scalp",
            "pnl_pct": -0.5, "takeaway": "старый урок (вне окна)",
        },
    ]
    (d / "live_lessons.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lessons),
        encoding="utf-8",
    )

    hyps = {
        "updated": _iso(now),
        "hypotheses": {
            "exit-abcdef123456": {
                "id": "exit-abcdef123456",
                "strategy_id": "scalp",
                "description": "Exit BREAKEVEN trigger 1.0R",
                "status": "INVALIDATED",
                "invalidation_reason": "OOS expectancy < 0",
                "status_log": [
                    {"at": _iso(now - 5 * 24 * H), "status": "DISCOVERED", "reason": "создана"},
                    {"at": _iso(now - 4 * 24 * H), "status": "TESTING", "reason": ""},
                    {"at": _iso(now - 2 * H), "status": "INVALIDATED",
                     "reason": "OOS expectancy < 0"},
                ],
            }
        },
    }
    (d / "research" / "hypotheses.json").write_text(
        json.dumps(hyps, ensure_ascii=False), encoding="utf-8"
    )

    outcomes = {
        "obs-1": {
            "bar_time": (now - 2 * H) // 1000,  # секунды
            "symbol": "SOL-USDT",
            "reason_code": "LOW_EV",
            "market_regime": "TREND",
            "horizons": {
                "1": {"future_return": -0.004, "max_up": 0.001, "max_down": -0.005},
                "3": {"future_return": -0.008, "max_up": 0.002, "max_down": -0.009},
            },
            "computed_at": _iso(now - 1 * H),
        }
    }
    (d / "no_trade_outcomes.json").write_text(
        json.dumps(outcomes, ensure_ascii=False), encoding="utf-8"
    )
    obs = [
        {
            "id": "obs-1", "symbol": "SOL-USDT", "bar_time": (now - 2 * H) // 1000,
            "timestamp": now - 2 * H, "market_regime": "TREND",
            "regime_confidence": 0.5, "reason_code": "LOW_EV", "reasons": [],
            "candidate": {"strategy": "scalp", "direction": "long",
                          "ev_r": 0.1, "confidence": 0.2},
            "features": {}, "result": None,
        }
    ]
    (d / "no_trade_observations.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in obs),
        encoding="utf-8",
    )

    stats = {
        "updated": _iso(now),
        "buckets": {
            "scalp|TREND|1h": {"sample_size": 14, "sum_r": 5.88, "wins": 9,
                               "losses": 5},
            "other|FLAT|5m": {"sample_size": 2, "sum_r": 1.0, "wins": 2,
                              "losses": 0},
        },
    }
    (d / "strategy_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False), encoding="utf-8"
    )
    return d


class TestDigest:
    def test_all_sections_present(self, tmp_path):
        now = _now_ms()
        d = _make_models(tmp_path, now)
        text, wm = build_digest(d, now_ms=now)
        assert wm == now
        # Урок (только свежий; старый вне окна).
        assert "следовало подождать подтверждения" in text
        assert "старый урок" not in text
        # Переход гипотезы с причиной.
        assert "INVALIDATED" in text
        assert "OOS expectancy < 0" in text
        # NO_TRADE: long + движение вниз → отказ оправдан.
        assert "NO_TRADE LOW_EV long SOL-USDT" in text
        assert "отказ оправдан" in text
        # База знаний: только бакет с n >= 5.
        assert "scalp × TREND: EV +0.42R (n=14)" in text
        assert "other" not in text

    def test_empty_dir_no_new_knowledge(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        text, _ = build_digest(d, now_ms=_now_ms())
        assert "Новых знаний нет" in text
        assert "накопление" in text

    def test_watermark_roundtrip_no_duplicates(self, tmp_path):
        now = _now_ms()
        d = _make_models(tmp_path, now)
        _, wm1 = build_digest(d, now_ms=now)
        save_watermark(d, wm1)
        assert watermark_ms(d) == wm1
        # Повторный дайджест через час: всё уже «старее» watermark.
        text2, _ = build_digest(d, now_ms=now + H)
        assert "следовало подождать подтверждения" not in text2
        assert "Новых знаний нет" in text2

    def test_no_trade_against_hypothesis(self, tmp_path):
        """Long-кандидат, но рынок пошёл вверх → «движение было за гипотезу»."""
        now = _now_ms()
        d = _make_models(tmp_path, now)
        outcomes = json.loads((d / "no_trade_outcomes.json").read_text())
        outcomes["obs-1"]["horizons"]["3"]["future_return"] = 0.012
        (d / "no_trade_outcomes.json").write_text(
            json.dumps(outcomes), encoding="utf-8"
        )
        text, _ = build_digest(d, now_ms=now)
        assert "движение было за гипотезу" in text

    def test_length_capped(self, tmp_path):
        now = _now_ms()
        d = _make_models(tmp_path, now)
        rows = [
            {
                "trade_id": f"t{i}", "symbol": "BTC-USDT", "direction": "long",
                "exit_time": now - i * 60_000, "strategy": "scalp",
                "pnl_pct": -0.1, "takeaway": "длинный текст урока " * 5,
            }
            for i in range(60)
        ]
        (d / "live_lessons.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in rows),
            encoding="utf-8",
        )
        text, _ = build_digest(d, now_ms=now)
        assert len(text) <= 4096

    def test_watermark_preserves_other_keys(self, tmp_path):
        d = tmp_path / "models"
        d.mkdir()
        (d / "telegram_offset.json").write_text(
            json.dumps({"other_key": 42}), encoding="utf-8"
        )
        save_watermark(d, 123)
        data = json.loads((d / "telegram_offset.json").read_text())
        assert data["learning_digest_watermark_ms"] == 123
        assert data["other_key"] == 42
