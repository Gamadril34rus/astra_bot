"""
State Manager — Git-based persistence for GitHub Actions (Block 2).

Since GitHub Actions doesn't preserve state, we use git commits as storage.
- data/state.json — current positions, balance, daily PnL
- data/trades.db — SQLite with all trades (for ML training)
- data/model.joblib — trained ML model (or via Releases for >50MB)
- data/features_cache.pkl — cache of computed features
- data/weights.json — adaptive strategy weights

Also syncs with legacy models/ folder for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_STATE_FILE = DEFAULT_DATA_DIR / "state.json"
DEFAULT_TRADES_DB = DEFAULT_DATA_DIR / "trades.db"
DEFAULT_WEIGHTS_FILE = DEFAULT_DATA_DIR / "weights.json"
DEFAULT_MODEL_FILE = DEFAULT_DATA_DIR / "model.joblib"
DEFAULT_FEATURES_CACHE = DEFAULT_DATA_DIR / "features_cache.pkl"


class StateManager:
    """Git-based persistence manager."""

    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "state.json"
        self.trades_db = self.data_dir / "trades.db"
        self.weights_file = self.data_dir / "weights.json"
        self.model_file = self.data_dir / "model.joblib"
        self.features_cache = self.data_dir / "features_cache.pkl"
        self._init_trades_db()
        self._init_weights()

    def _init_trades_db(self) -> None:
        """Initialize SQLite trades DB (Block 2.1, 7.1)."""
        try:
            conn = sqlite3.connect(str(self.trades_db))
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    symbol TEXT,
                    side TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    quantity REAL,
                    pnl REAL,
                    pnl_pct REAL,
                    strategy TEXT,
                    ml_confidence REAL,
                    regime TEXT,
                    timeframe TEXT,
                    exit_reason TEXT,
                    all_features_json TEXT,
                    created_at TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    trades INTEGER,
                    wins INTEGER,
                    losses INTEGER,
                    pnl REAL,
                    equity_end REAL
                )
                """
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Failed to init trades.db: %s", exc)

    def _init_weights(self) -> None:
        """Initialize adaptive weights file (Block 7.3)."""
        if not self.weights_file.exists():
            default_weights = {
                "strategy_weights": {
                    "trend_following": 0.35,
                    "mean_reversion": 0.20,
                    "breakout": 0.25,
                    "momentum": 0.20,
                    "scalp5m": 0.25,
                    "scalp": 0.20,
                    "pullback": 0.20,
                    "ts_momentum": 0.15,
                },
                "disabled_strategies": [],
                "best_hours": [8, 9, 14, 15, 16],
                "worst_hours": [0, 1, 2, 3, 4],
                "best_regime": "TRENDING_UP",
                "worst_regime": "VOLATILE",
                "last_adaptation": datetime.now(timezone.utc).isoformat(),
            }
            try:
                self.weights_file.write_text(
                    json.dumps(default_weights, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning("Failed to init weights.json: %s", exc)

    # -------------------- state.json --------------------
    def load_state(self) -> dict[str, Any]:
        """Load state.json or return default."""
        if not self.state_file.exists():
            return self._default_state()
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load state.json: %s", exc)
            return self._default_state()

    def _default_state(self) -> dict[str, Any]:
        return {
            "positions": [],
            "balance": 2000.0,
            "daily_pnl": 0.0,
            "daily_trades": 0,
            "daily_wins": 0,
            "daily_losses": 0,
            "last_report_date": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def save_state(self, state: dict[str, Any]) -> None:
        """Save state.json atomically."""
        try:
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_file)
            logger.info("State saved to %s", self.state_file)
            # Also sync to legacy models/demo_state.json for morning_report compatibility
            legacy = Path("models/demo_state.json")
            try:
                legacy.parent.mkdir(parents=True, exist_ok=True)
                legacy.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        except Exception as exc:
            logger.error("Failed to save state: %s", exc)

    # -------------------- trades.db --------------------
    def save_trade(self, trade: dict[str, Any]) -> None:
        """Save single trade to SQLite (Block 7.1)."""
        try:
            conn = sqlite3.connect(str(self.trades_db))
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO trades
                (id, timestamp, symbol, side, entry_price, exit_price, quantity, pnl, pnl_pct,
                 strategy, ml_confidence, regime, timeframe, exit_reason, all_features_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(trade.get("id", "")),
                    str(trade.get("timestamp") or trade.get("closed_at", "")),
                    str(trade.get("symbol", "")),
                    str(trade.get("direction") or trade.get("side", "")),
                    float(trade.get("entry_price", 0.0) or 0.0),
                    float(trade.get("exit_price", 0.0) or 0.0),
                    float(trade.get("quantity", 0.0) or 0.0),
                    float(trade.get("pnl", 0.0) or 0.0),
                    float(trade.get("pnl_pct", 0.0) or 0.0),
                    str(trade.get("strategy", "")),
                    float(trade.get("ml_confidence") or trade.get("confidence", 0.0) or 0.0),
                    str(trade.get("regime", "")),
                    str(trade.get("timeframe", "")),
                    str(trade.get("exit_reason", "")),
                    json.dumps(trade.get("features") or trade.get("all_features") or {}, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Failed to save trade to DB: %s", exc)

    def save_trades(self, trades: list[dict[str, Any]]) -> int:
        """Save multiple trades, return count."""
        count = 0
        for t in trades:
            try:
                self.save_trade(t)
                count += 1
            except Exception:
                continue
        return count

    def get_recent_trades(self, hours: int = 24) -> list[dict[str, Any]]:
        """Get trades from last N hours."""
        try:
            conn = sqlite3.connect(str(self.trades_db))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            # Since timestamp is stored as ISO or ms, we try to filter by created_at
            cur.execute(
                """
                SELECT * FROM trades ORDER BY created_at DESC LIMIT 1000
                """
            )
            rows = cur.fetchall()
            conn.close()
            # Filter by hours if timestamp available
            result = []
            now = datetime.now(timezone.utc)
            for r in rows:
                try:
                    d = dict(r)
                    # Try to parse created_at
                    created_str = d.get("created_at", "")
                    if created_str:
                        created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                        age_hours = (now - created).total_seconds() / 3600
                        if age_hours <= hours:
                            result.append(d)
                    else:
                        result.append(d)
                except Exception:
                    result.append(dict(r))
            return result
        except Exception as exc:
            logger.warning("Failed to get recent trades: %s", exc)
            return []

    # -------------------- weights.json --------------------
    def load_weights(self) -> dict[str, Any]:
        if not self.weights_file.exists():
            self._init_weights()
        try:
            return json.loads(self.weights_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_weights(self, weights: dict[str, Any]) -> None:
        try:
            weights["last_adaptation"] = datetime.now(timezone.utc).isoformat()
            tmp = self.weights_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(weights, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.weights_file)
        except Exception as exc:
            logger.error("Failed to save weights: %s", exc)

    # -------------------- git persistence helpers --------------------
    @staticmethod
    def git_commit_and_push(files: list[str], message: str | None = None) -> bool:
        """
        Commit and push with conflict resolution (Block 2.2).
        - git pull --rebase
        - git push --force-with-lease fallback to push
        """
        try:
            msg = message or f"chore(ci): bot state {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')} [skip ci]"
            subprocess.run(["git", "config", "user.name", "astra-bot"], check=False)
            subprocess.run(["git", "config", "user.email", "bot@users.noreply.github.com"], check=False)

            # Add files (force for ignored)
            for f in files:
                p = Path(f)
                if p.exists():
                    subprocess.run(["git", "add", "-f", f], check=False)

            # Check if there's anything to commit
            result = subprocess.run(["git", "diff", "--cached", "--quiet"])
            if result.returncode == 0:
                logger.info("No changes to commit")
                return True

            subprocess.run(["git", "commit", "-m", msg], check=False)

            # Pull --rebase with conflict resolution
            pull_result = subprocess.run(["git", "pull", "--rebase", "origin", "master"], capture_output=True, text=True)
            if pull_result.returncode != 0:
                logger.warning("git pull --rebase failed: %s, trying to continue", pull_result.stderr)

            # Push with lease, fallback to normal push
            push_result = subprocess.run(
                ["git", "push", "--force-with-lease", "origin", "master"], capture_output=True, text=True
            )
            if push_result.returncode != 0:
                logger.warning("force-with-lease push failed, trying normal push: %s", push_result.stderr)
                push2 = subprocess.run(["git", "push", "origin", "master"], capture_output=True, text=True)
                if push2.returncode != 0:
                    logger.error("git push failed: %s", push2.stderr)
                    return False
            logger.info("Git push successful")
            return True
        except Exception as exc:
            logger.error("Git commit/push failed: %s", exc)
            return False


# Global instance
_state_manager: StateManager | None = None


def get_state_manager() -> StateManager:
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager
