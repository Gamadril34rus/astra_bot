from pathlib import Path

from scripts.analyze_ironflow_wallet_flow import HORIZONS, _sql_path


def test_forward_horizons_match_at_hold_time_cap() -> None:
    assert HORIZONS == {
        "15m": "close_15m",
        "30m": "close_30m",
        "1h": "close_1h",
        "4h": "close_4h",
        "9h": "close_9h",
    }


def test_sql_path_escapes_single_quotes(tmp_path: Path) -> None:
    path = tmp_path / "wallet's trades.parquet"
    assert _sql_path(path).endswith("wallet''s trades.parquet")
