"""离线验证复用引擎的数学边界；全部输入为合成数据，不构成业务验收。"""

import unittest

import pandas as pd
from viral_tiers import FEATURES, GRADES, score_frame


def calibration():
    """返回专用于边界测试的四线，禁止作为真实评分标准。"""
    base = {"bands": [1.0, 2.0, 3.0, 4.0], "n": 30}
    table = {"cell": {}, "parent": {}, "fallback": {"验证期": base}, "global": base}
    floor = {**table, "fallback": {}, "global": {"bands": [100.0], "n": 30}}
    return {"tables": {feature: table for feature in FEATURES}, "growth_floor": floor}


def feature_row(**changes):
    """返回已聚合的合成特征；此函数不读取数据库、历史文件或上游。"""
    return {
        **{feature: 8.0 for feature in FEATURES},
        "platform_code": "synthetic",
        "shop_id": "synthetic-shop",
        "product_id": "synthetic-product",
        "category": "冲锋衣",
        "season": "旺季",
        "lifecycle": "验证期",
        "age_days": 21,
        "ad_impressions_7": 2000,
        "gmv_prev7": 200,
        "orders_prev7": 5,
        **changes,
    }


class ReuseBoundaryChecks(unittest.TestCase):
    def calculate(self, **changes):
        return score_frame(pd.DataFrame([feature_row(**changes)]), calibration()).iloc[
            0
        ]

    def test_all_four_boundaries_include_equality(self):
        for tier, value in enumerate([0.5, 1.0, 2.0, 3.0, 4.0]):
            with self.subTest(value=value):
                result = self.calculate(uv_7=value)
                self.assertEqual(result.grade_uv_7, GRADES[tier])
                self.assertEqual(result.points_uv_7, 12 * tier / 4)

    def test_incomplete_promotion_does_not_rescale(self):
        self.assertEqual(self.calculate().complete_score, 100)
        result = self.calculate(ad_impressions_7=1999)
        self.assertEqual(result.verified_score, 84)
        self.assertEqual(result.pending_weight, 16)
        self.assertTrue(pd.isna(result.complete_score))

    def test_missing_feature_is_pending(self):
        result = self.calculate(uv_7=None)
        self.assertEqual(result.pending_weight, 12)
        self.assertTrue(pd.isna(result.points_uv_7))

    def test_zero_is_valid_and_scores_zero(self):
        result = self.calculate(uv_7=0)
        self.assertEqual(result.active_uv_7, 1)
        self.assertEqual(result.points_uv_7, 0)

    def test_quality_flag_blocks_only_affected_metric(self):
        result = self.calculate(roi_invalid_7=1)
        self.assertEqual(result.pending_weight, 8)
        self.assertTrue(pd.isna(result.points_ad_roi_7))

    def test_growth_prerequisites_and_no_growth(self):
        for changes in ({"orders_prev7": 4}, {"gmv_prev7": 99}, {"age_days": 7}):
            with self.subTest(changes=changes):
                self.assertEqual(self.calculate(**changes).pending_weight, 14)
        result = self.calculate(gmv_growth_7=0.52)
        self.assertEqual(result.points_gmv_growth_7, 0)
        self.assertEqual(result.pending_weight, 0)

    def test_roi_borrows_only_softshell_context(self):
        params = calibration()
        params["tables"]["ad_roi_7"] = {
            **params["tables"]["ad_roi_7"],
            "cell": {"软壳衣|旺季|验证期": {"bands": [5.0, 6.0, 7.0, 8.0], "n": 30}},
        }
        result = score_frame(pd.DataFrame([feature_row()]), params).iloc[0]
        self.assertEqual(result.tier_1_ad_roi_7, 5)
        self.assertEqual(result.tier_1_uv_7, 1)

    def test_two_shops_remain_two_rows_when_already_separated(self):
        frame = pd.DataFrame(
            [feature_row(shop_id="shop-a"), feature_row(shop_id="shop-b")]
        )
        result = score_frame(frame, calibration())
        self.assertEqual(result.shop_id.tolist(), ["shop-a", "shop-b"])
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
