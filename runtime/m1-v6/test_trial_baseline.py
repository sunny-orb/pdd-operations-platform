"""阶段无关试行标准的纯函数测试；只使用合成完整窗口。"""

import unittest
from datetime import date, timedelta
from decimal import Decimal

from trial_baseline import build_calibration, score_observation

DAY = date(2026, 10, 26)
RANGES = [(date(2026, 8, 28), DAY)]


def row(index, category="帽子", **changes):
    return {
        "platform_code": "pdd",
        "shop_id": "a",
        "product_id": str(index),
        "category": category,
        "window_end": date(2026, 10, 12),
        "complete_7d": True,
        "complete_14d": True,
        "source_complete_14d": True,
        "complete_ad_7d": True,
        "uv_7": str(index),
        "gmv_7": str(index),
        "items_7": str(index),
        "fav_7": str(index),
        "cvr_7": str(index),
        "uv_value_7": str(index),
        "ad_roi_7": str(index),
        "ad_ctr_7": str(index),
        "ad_cvr_7": str(index),
        "gmv_growth_7": "2",
        "gmv_prev7": "100",
        "orders_prev7": "5",
        "ad_impressions_7": "2000",
        "roi_invalid_7": 0,
        "ctr_invalid_7": 0,
        "ad_cvr_invalid_7": 0,
        **changes,
    }


def build(rows, **changes):
    return build_calibration(rows, as_of_date=DAY, authorized_ranges=RANGES, **changes)


class TrialBaselineTests(unittest.TestCase):
    def test_category_uses_twelve_unique_complete_keys(self):
        params = build([row(i) for i in range(1, 13)])
        spec = params["standards"]["帽子"]["uv_7"]
        self.assertEqual(spec["bands"], ["3.75", "6.5", "9.25", "10.9"])
        self.assertEqual(spec["n"], 12)
        self.assertEqual(spec["reference"], "帽子")

    def test_repeated_windows_do_not_increase_sample_count(self):
        rows = [row(i) for i in range(1, 12)]
        rows += [row(1, window_end=date(2026, 10, 11), uv_7="1000")]
        self.assertIsNone(build(rows)["standards"]["帽子"]["uv_7"])

    def test_same_product_in_different_shops_is_distinct(self):
        rows = [row(i, shop_id="a") for i in range(1, 7)]
        rows += [row(i, shop_id="b") for i in range(1, 7)]
        self.assertEqual(build(rows)["standards"]["帽子"]["uv_7"]["n"], 12)

    def test_global_hard_fallback_requires_twenty_four(self):
        rows = [row(i, category=str(i % 3)) for i in range(1, 25)]
        self.assertEqual(build(rows)["standards"]["0"]["uv_7"]["reference"], "全品类")
        self.assertIsNone(build(rows[:-1])["standards"]["0"]["uv_7"])

    def test_cutoff_and_unreceived_windows_are_excluded(self):
        rows = [row(i) for i in range(1, 13)]
        rows[0]["window_end"] = DAY - timedelta(days=13)
        self.assertIsNone(build(rows)["standards"]["帽子"]["uv_7"])
        rows[0] = row(1, complete_7d=False)
        self.assertIsNone(build(rows)["standards"]["帽子"]["uv_7"])
        rows[0] = row(1, window_end=date(2026, 8, 30))
        self.assertIsNone(build(rows)["standards"]["帽子"]["uv_7"])

    def test_duplicate_key_and_window_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "重复"):
            build([row(1), row(1)])

    def test_favorites_stays_pending_until_additivity_verified(self):
        params = build([row(i) for i in range(1, 13)])
        self.assertIsNone(params["standards"]["帽子"]["fav_7"])
        result = score_observation(row(30, window_end=DAY), params)
        self.assertEqual(result["signals"]["fav_7"]["points"], None)
        self.assertGreaterEqual(result["pending_weight"], 10)
        self.assertFalse(result["formal_scaling_eligible"])

    def test_growth_unknown_source_completeness_stays_pending(self):
        params = build([row(i) for i in range(1, 13)], favorites_additive=True)
        result = score_observation(
            row(30, window_end=DAY, source_complete_14d=False), params
        )
        self.assertEqual(result["signals"]["gmv_growth_7"]["points"], None)
        self.assertEqual(result["pending_weight"], 14)

    def test_decimal_threshold_does_not_round_up(self):
        params = build([row(i) for i in range(1, 13)], favorites_additive=True)
        result = score_observation(
            row(30, window_end=DAY, uv_7="3.749999999999999999"), params
        )
        self.assertEqual(result["signals"]["uv_7"]["points"], "0")
        exact = score_observation(row(30, window_end=DAY, uv_7="3.75"), params)
        self.assertEqual(exact["signals"]["uv_7"]["points"], "3")
        self.assertEqual(
            Decimal(exact["verified_score"]),
            sum(
                Decimal(s["points"])
                for s in exact["signals"].values()
                if s["points"] is not None
            ),
        )

    def test_current_day_must_match_calibration(self):
        params = build([row(i) for i in range(1, 13)])
        with self.assertRaisesRegex(ValueError, "评分日"):
            score_observation(row(30), params)

    def test_unknown_category_keeps_unscored_record(self):
        params = build([row(i) for i in range(1, 25)] + [row(30, category=None)])
        result = score_observation(row(30, category=None, window_end=DAY), params)
        self.assertEqual(result["score_status"], "unscored")
        self.assertIsNone(result["verified_score"])

    def test_growth_window_must_stay_inside_authorized_range(self):
        rows = [row(i, window_end=date(2026, 9, 5)) for i in range(1, 13)]
        params = build(rows)
        self.assertIsNone(params["growth_floors"]["帽子"])

    def test_missing_product_days_does_not_block_valid_ad_window(self):
        params = build([row(i) for i in range(1, 25)])
        result = score_observation(row(30, window_end=DAY, complete_7d=False), params)
        self.assertEqual(result["signals"]["ad_roi_7"]["points"], "8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
