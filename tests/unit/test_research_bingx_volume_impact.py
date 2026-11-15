from __future__ import annotations

import unittest

import pandas as pd

from scripts.research_bingx_volume_impact import (
    add_volume_features,
    choose_volume_absorption_release,
    choose_volume_impact_continuation,
    choose_volume_regime_router,
)


class BingXVolumeImpactTests(unittest.TestCase):
    def test_hourly_context_is_never_from_a_future_close(self) -> None:
        base = 1_760_000_400_000
        m15 = pd.DataFrame([
            {"open_time": base + i * 900_000, "decision_time": base + (i + 1) * 900_000,
             "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i,
             "volume": 10, "quote_volume": 10}
            for i in range(4)
        ])
        hour_start = base - 3_600_000
        h1 = pd.DataFrame([
            {"open_time": hour_start, "decision_time": base,
             "open": 100, "high": 105, "low": 98, "close": 103,
             "volume": 100, "quote_volume": 100},
            {"open_time": base, "decision_time": base + 3_600_000,
             "open": 103, "high": 106, "low": 101, "close": 104,
             "volume": 100, "quote_volume": 100},
        ])
        features = add_volume_features(m15, h1)
        self.assertTrue((features["h1_source_decision_time"] <= features["decision_time"]).all())
        self.assertEqual(int(features.iloc[0]["h1_source_decision_time"]), base)
        self.assertEqual(int(features.iloc[2]["h1_source_decision_time"]), base)
        self.assertEqual(int(features.iloc[3]["h1_source_decision_time"]), base + 3_600_000)

    def test_trend_continuation_requires_volume_and_hourly_trend_alignment(self) -> None:
        rows = []
        for i in range(6):
            rows.append({
                "atr14": 1.0, "h1_adx14": 28.0, "h1_ema20": 105.0, "h1_ema50": 100.0,
                "h1_ema20_rising": True, "h1_ema20_falling": False,
                "h1_volume_ratio_prior": 1.3, "h1_price_move_atr": 0.2,
                "volume_ratio_prior": 1.8, "body_atr": 0.3, "close_location": 0.85,
                "low": 99.0 - i * 0.1, "high": 102.0 + i * 0.1,
                "close": 100.0 + i,
            })
        rows[-1]["close"] = 104.0
        signal = choose_volume_impact_continuation(pd.DataFrame(rows), 4)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["family"], "VOLUME_IMPACT_CONTINUATION")
        self.assertEqual(signal["side"], 1)

    def test_absorption_proxy_requires_high_volume_low_hourly_impact_then_break(self) -> None:
        rows = [
            {"atr14": 1.0, "h1_adx14": 15.0, "h1_volume_ratio_prior": 1.8,
             "h1_price_move_atr": 0.1, "volume_ratio_prior": 1.7,
             "body_atr": 0.3, "close_location": 0.5, "close": 100.0,
             "low": 99.0, "high": 102.0},
            {"atr14": 1.0, "h1_adx14": 15.0, "h1_volume_ratio_prior": 1.8,
             "h1_price_move_atr": 0.1, "volume_ratio_prior": 1.7,
             "body_atr": 0.3, "close_location": 0.5, "close": 100.0,
             "low": 99.5, "high": 102.0},
            {"atr14": 1.0, "h1_adx14": 15.0, "h1_volume_ratio_prior": 1.8,
             "h1_price_move_atr": 0.1, "volume_ratio_prior": 1.7,
             "body_atr": 0.3, "close_location": 0.5, "close": 100.0,
             "low": 99.4, "high": 102.0},
            {"atr14": 1.0, "h1_adx14": 15.0, "h1_volume_ratio_prior": 1.8,
             "h1_price_move_atr": 0.1, "volume_ratio_prior": 1.7,
             "body_atr": 0.3, "close_location": 0.85, "close": 103.0,
             "low": 102.0, "high": 103.2},
        ]
        rows.append(dict(rows[-1]))
        signal = choose_volume_absorption_release(pd.DataFrame(rows), 3)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["family"], "VOLUME_ABSORPTION_RELEASE")
        self.assertEqual(signal["side"], 1)

    def test_router_stays_flat_in_neutral_adx_band(self) -> None:
        frame = pd.DataFrame([{"h1_adx14": 20.0}, {"h1_adx14": 20.0}])
        self.assertIsNone(choose_volume_regime_router(frame, 1))


if __name__ == "__main__":
    unittest.main()
