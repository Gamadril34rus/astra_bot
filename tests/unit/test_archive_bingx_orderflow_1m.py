from __future__ import annotations

import unittest
from decimal import Decimal

from scripts.archive_bingx_orderflow_1m import aggregate_group, parse_row, validate_page


class BingXNativeTakerFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.open_ms = 1_760_004_000_000
        self.row = [
            self.open_ms, "100", "102", "99", "101", "12.5",
            self.open_ms + 59_999, "1262.5", 345, "7.5", "758.0",
        ]

    def test_parses_all_documented_fields(self) -> None:
        parsed = parse_row(self.row)
        self.assertEqual(parsed["open_time"], self.open_ms)
        self.assertEqual(parsed["trade_count"], 345)
        self.assertEqual(parsed["quote_volume"], Decimal("1262.5"))
        self.assertEqual(parsed["taker_buy_base_volume"], Decimal("7.5"))
        self.assertEqual(parsed["taker_buy_quote_volume"], Decimal("758.0"))

    def test_rejects_ohlcv_only_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "all 11 documented fields"):
            parse_row(self.row[:6])

    def test_rejects_taker_buy_above_total(self) -> None:
        bad = list(self.row)
        bad[9] = "12.6"
        with self.assertRaisesRegex(ValueError, "taker-buy base volume exceeds"):
            parse_row(bad)

    def test_validates_contiguous_page(self) -> None:
        second = list(self.row)
        second[0] += 60_000
        second[6] += 60_000
        parsed = validate_page([second, self.row], self.open_ms, 2)
        self.assertEqual(
            [item["open_time"] for item in parsed],
            [self.open_ms, self.open_ms + 60_000],
        )

    def test_rejects_missing_page_bar(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "expected 2 1m bars"):
            validate_page([self.row], self.open_ms, 2)

    def test_aggregates_15m_flow_without_loss(self) -> None:
        rows = []
        for index in range(15):
            row = list(self.row)
            row[0] = self.open_ms + index * 60_000
            row[6] = row[0] + 59_999
            row[5] = "1"
            row[7] = "100"
            row[8] = 10
            row[9] = "0.4"
            row[10] = "40"
            rows.append(parse_row(row))
        aggregate = aggregate_group(rows, 15)
        self.assertEqual(aggregate["volume"], Decimal("15"))
        self.assertEqual(aggregate["quote_volume"], Decimal("1500"))
        self.assertEqual(aggregate["trade_count"], 150)
        self.assertEqual(aggregate["taker_buy_base_volume"], Decimal("6.0"))
        self.assertEqual(aggregate["taker_buy_quote_volume"], Decimal("600"))
        self.assertEqual(aggregate["open"], Decimal("100"))
        self.assertEqual(aggregate["close"], Decimal("101"))

    def test_aggregates_1h_flow_without_loss(self) -> None:
        hour_start = self.open_ms - (self.open_ms % 3_600_000)
        rows = []
        for index in range(60):
            row = list(self.row)
            row[0] = hour_start + index * 60_000
            row[6] = row[0] + 59_999
            row[5] = "1"
            row[7] = "100"
            row[8] = 10
            row[9] = "0.4"
            row[10] = "40"
            rows.append(parse_row(row))
        aggregate = aggregate_group(rows, 60)
        self.assertEqual(aggregate["volume"], Decimal("60"))
        self.assertEqual(aggregate["quote_volume"], Decimal("6000"))
        self.assertEqual(aggregate["trade_count"], 600)
        self.assertEqual(aggregate["taker_buy_base_volume"], Decimal("24.0"))
        self.assertEqual(aggregate["taker_buy_quote_volume"], Decimal("2400"))

    def test_rejects_incomplete_aggregate(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "expected 15 source bars"):
            aggregate_group([parse_row(self.row)], 15)


if __name__ == "__main__":
    unittest.main()
