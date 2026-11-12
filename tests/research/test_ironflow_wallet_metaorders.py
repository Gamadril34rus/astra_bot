import importlib.util
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "research" / "ironflow_wallet_metaorders.py"
spec = importlib.util.spec_from_file_location("ironflow_wallet_metaorders", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class MetaorderGroupingTests(unittest.TestCase):
    def make_row(self, oid, minute, direction="Open Long", side="buy", tx_hash="0xabc"):
        return {
            "oid": oid,
            "ts": (datetime(2026, 11, 12, 12, 0, tzinfo=UTC) + timedelta(minutes=minute)).isoformat(),
            "side": side,
            "direction": direction,
            "q_notional": 100.0,
            "size": 1.0,
            "vwap": 100.0,
            "fill_count": 2,
            "fees": 0.1,
            "realized_pnl": 1.0,
            "tx_hash": tx_hash,
        }

    def test_groups_by_oid_sequence_and_direction(self):
        rows = [self.make_row(i, i * 5) for i in range(10)]
        rows += [self.make_row(100 + i, i * 5, direction="Close Short") for i in range(2)]
        parents, twap = mod.build_metaorders(rows, 30, 10, 24)
        self.assertEqual(twap, 0)
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0]["child_orders"], 10)
        self.assertEqual(parents[0]["fill_rows"], 20)
        self.assertEqual(parents[0]["q_notional_usd"], 1000.0)
        self.assertEqual(parents[0]["direction"], "Open Long")

    def test_gap_splits_parent(self):
        rows = [self.make_row(i, i * 5 if i < 5 else 60 + (i - 5) * 5) for i in range(10)]
        parents, _ = mod.build_metaorders(rows, 30, 10, 24)
        self.assertEqual(parents, [])

    def test_zero_hash_is_excluded_as_twap_candidate(self):
        rows = [self.make_row(i, i * 2, tx_hash="0x" + "0" * 64) for i in range(10)]
        parents, twap = mod.build_metaorders(rows, 30, 10, 24)
        self.assertEqual(parents, [])
        self.assertEqual(twap, 10)


if __name__ == "__main__":
    unittest.main()
