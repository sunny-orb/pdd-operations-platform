/** 仅测试导入的少量合成契约输入；正式页面不引用此文件。 */
import type { FiltersResponse, SalesMeta, SalesMetrics, ShopItem } from "./api/contracts";
export const testMeta: SalesMeta = {
  data_version: "synthetic-v1",
  observed_from: "2026-09-01",
  observed_through: "2026-10-26",
  completeness_state: "unknown",
  complete_through: null,
  source_updated_text: null,
  source_updated_at: null,
  last_attempt_status: "succeeded",
  last_attempt_at: "2026-10-28T00:00:00Z",
  last_success_at: "2026-10-28T00:00:00Z",
  request_id: "synthetic-request",
};
export const testMetrics: SalesMetrics = {
  shop_revenue: "30.50",
  shop_visitors: "110",
  buyers: "5",
  orders: "17",
  buyer_conversion: "0.045454545455",
  aov: "6.100000000000",
  refund_rate: null,
  promotion_spend: null,
  promotion_ctr: null,
  promotion_roi: null,
  field_states: { refund_rate: "not_connected" },
  coverage: {
    observed_days: 2,
    latest_observed_date: "2026-10-26",
    missing_dates: [],
    field_null_counts: {},
    reason_code: "UNKNOWN",
    window_start: "2026-10-01",
    window_end: "2026-10-27",
  },
};
export const testShops: ShopItem[] = [
  {
    ...testMetrics,
    shop_id: "spigf",
    shop_name: "合成官方店",
    display_name: "官方店",
    rank: 1,
    sales_share: "1",
  },
  {
    ...testMetrics,
    shop_id: "spihw",
    shop_name: "合成户外店",
    display_name: "户外店",
    rank: 2,
    sales_share: "0",
    shop_revenue: "0.00",
    aov: null,
  },
];
export const testFilters: FiltersResponse = {
  meta: testMeta,
  data: {
    shops: testShops.map((s) => ({
      shop_id: s.shop_id,
      shop_name: s.shop_name,
      display_name: s.display_name,
    })),
    months: [
      { month: "2026-09", date_from: "2026-09-01", date_to: "2026-09-30" },
      { month: "2026-10", date_from: "2026-10-01", date_to: "2026-10-27" },
    ],
    default_month: "2026-10",
    recommended_date_from: "2026-10-01",
    recommended_date_to: "2026-10-27",
    calendar_through: "2026-10-27",
    observed_from: "2026-09-01",
    observed_through: "2026-10-26",
    capabilities: { shop_daily: true },
  },
};
export function syntheticResponse(path: string) {
  return path.includes("/filters")
    ? testFilters
    : path.includes("/shops")
      ? { meta: testMeta, data: { items: testShops, missing_shop_days: 0 } }
      : {
          meta: testMeta,
          data: {
            ...testMetrics,
            ...(path.includes("shop_id=spihw") ? { shop_revenue: "0.00", aov: null } : {}),
          },
        };
}

export const syntheticNames = ["官方店", "户外店", "服饰店", "雨具店", "箱包店", "青少年服饰店"];
export const syntheticAllShops = syntheticNames.map((display_name, i) => ({
  ...testMetrics,
  shop_id: ["spigf", "spihw", "spifs", "spiyj", "spixb", "spitztx"][i],
  shop_name: `合成${display_name}`,
  display_name,
  rank: i + 1,
  sales_share: i === 0 ? "1" : "0",
  shop_revenue: i === 0 ? "30.50" : "0.00",
  shop_visitors: i === 0 ? "110" : "0",
  buyers: i === 0 ? "5" : "0",
  orders: i === 0 ? "17" : "0",
  buyer_conversion: i === 0 ? "0.045454545455" : null,
  aov: i === 0 ? "6.100000000000" : null,
}));
