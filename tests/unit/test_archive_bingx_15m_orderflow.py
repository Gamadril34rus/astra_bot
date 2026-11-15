from __future__ import annotations

import unittest

from scripts.archive_bingx_15m_orderflow import parse_row, validate_page


class BingXOrderFlowKlineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.interval_ms = 15 * 60_000
        self.open_ms = 1_760_000_000_000
        self.row = [
            self.open_ms, "100", "102", "99", "101", "12.5",
            self.open_ms + self.interval_ms - 1, "1262.5", 345,
            "7.5", "758.0",
        ]

    def test_parses_all_documented_orderflow_fields(self) -> None:
        parsed = parse_row(self.row, self.interval_ms)
        self.assertEqual(parsed["open_time"], self.open_ms)
        self.assertEqual(parsed["close_time"], self.open_ms + self.interval_ms - 1)
        self.assertEqual(parsed["trade_count"], 345)
        self.assertEqual(str(parsed["quote_volume"]), "1262.5")
        self.assertEqual(str(parsed["taker_buy_base_volume"]), "7.5")
        self.assertEqual(str(parsed["taker_buy_quote_volume"]), "758.0")

    def test_rejects_ohlcv_only_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "all 11 documented fields"):
            parse_row(self.row[:6], self.interval_ms)

    def test_rejects_taker_buy_volume_above_total(self) -> None:
        bad = list(self.row)
        bad[9] = "12.6"
        with self.assertRaisesRegex(ValueError, "exceeds total volume"):
            parse_row(bad, self.interval_ms)

    def test_validates_complete_aligned_page(self) -> None:
        second = list(self.row)
        second[0] += self.interval_ms
        second[6] += self.interval_ms
        parsed = validate_page([second, self.row], self.open_ms, 2, self.interval_ms)
        self.assertEqual([row["open_time"] for row in parsed],
                         [self.open_ms, self.open_ms + self.interval_ms])

    def test_rejects_missing_page_candle(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "expected 2 bars"):
            validate_page([self.row], self.open_ms, 2, self.interval_ms)


if __name__ == "__main__":
    unittest.main()
