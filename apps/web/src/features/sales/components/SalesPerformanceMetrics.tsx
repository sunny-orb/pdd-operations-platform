import { useState } from "react";
import type { FiltersResponse, SalesResult, SalesTrendMetric, Scope } from "../api/contracts";
import { StableContent } from "../../../shared/components/StableContent";
import { SalesKpis } from "./SalesKpis";
import { SalesPerformanceTrend } from "./SalesPerformanceTrend";

/** 八卡与趋势共用指标选择，切换时只更新经营指标区。 */
export function SalesPerformanceMetrics({
  result,
  pending,
  scope,
  sources,
  shopName,
  rangeLabel,
  onConflict,
}: {
  result: SalesResult | null;
  pending: boolean;
  scope: Scope;
  sources?: FiltersResponse["data"]["sources"];
  shopName: string;
  rangeLabel: string;
  onConflict: () => void;
}) {
  const [metrics, setMetrics] = useState<SalesTrendMetric[]>([
    "shop_revenue",
    "shop_visitors",
    "buyer_conversion",
  ]);
  const toggleMetric = (key: SalesTrendMetric) =>
    setMetrics((current) =>
      current.includes(key)
        ? current.length > 1
          ? current.filter((metric) => metric !== key)
          : current
        : [...current, key],
    );

  return (
    <>
      <StableContent pending={pending} ready={!!result} label="正在更新经营数据…">
        <SalesKpis
          data={result?.performance}
          shops={result?.shops ?? []}
          shopId={scope.shop}
          shopName={shopName}
          rangeLabel={rangeLabel}
          sources={sources}
          selectedMetrics={metrics}
          onMetricChange={toggleMetric}
        />
      </StableContent>
      <SalesPerformanceTrend
        scope={scope}
        version={result?.meta.data_version}
        shopName={shopName}
        rangeLabel={rangeLabel}
        metrics={metrics}
        onConflict={onConflict}
      />
    </>
  );
}
