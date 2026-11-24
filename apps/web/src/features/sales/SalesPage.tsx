import { Alert, Button, DatePicker, Select } from "antd";
import dayjs from "dayjs";
import {
  AimOutlined,
  BarChartOutlined,
  ShopOutlined,
  GlobalOutlined,
  TableOutlined,
} from "@ant-design/icons";
import { AssistantPageContext } from "../../shared/assistant/AssistantContext";
import { DashboardLayout } from "../../shared/layouts/DashboardLayout";
import { useSalesData } from "./api/queries";
import { TARGET_SOURCE } from "./sourceLinks";
import { SalesTargets } from "./components/SalesTargets";
import { SalesRatings } from "./components/SalesRatings";
import { SalesRegions } from "./components/SalesRegions";
import { PromotionDetails } from "./components/PromotionDetails";
import { SalesPerformanceMetrics } from "./components/SalesPerformanceMetrics";
import { SalesNotes } from "./components/SalesNotes";
import { ShopTable } from "./components/ShopTable";
import { SalesCoverage } from "./components/SalesCoverage";
import { localTime } from "./format";
import { SalesContribution } from "./components/SalesContribution";
import "./prototype.css";
import "./sales.css";

/** 原型正文API适配；所有正式指标由后端提供，共享导航保持原组件。 */
export function SalesPage() {
  const {
    filters,
    scope,
    result,
    error,
    scopeError,
    changeScope,
    resetScope,
    retry,
    invalidateVersion,
  } = useSalesData();
  const selectedName =
    filters?.data.shops.find((s) => s.shop_id === scope.shop)?.display_name ?? "全部店铺";
  const rangeLabel = scopeError
    ? "链接范围无效"
    : scope.from
      ? scope.from === scope.to
        ? scope.from
        : `${scope.from} 至 ${scope.to}`
      : "等待本地数据范围";
  const data = result?.performance;
  const shops = result?.shops ?? [];
  const selectShop = (id: string) => changeScope({ shop: scope.shop === id ? "" : id });
  const dateChange = (value: string, which: "from" | "to") => {
    if (!value) return;
    if (which === "from") changeScope({ from: value, ...(value > scope.to ? { to: value } : {}) });
    else changeScope({ to: value, ...(value < scope.from ? { from: value } : {}) });
  };
  return (
    <DashboardLayout
      sections={[
        { id: "targets", title: "目标进度", icon: <AimOutlined /> },
        { id: "performance", title: "经营表现", icon: <BarChartOutlined /> },
        { id: "shops", title: "店铺贡献", icon: <ShopOutlined /> },
        { id: "regions", title: "地区交易", icon: <GlobalOutlined /> },
        { id: "product-regions", title: "产品地区", icon: <GlobalOutlined /> },
        { id: "shop-detail", title: "店铺明细", icon: <TableOutlined /> },
      ]}
      currentPage="sales"
      context="店铺销售 · 本页独立分析"
    >
      <AssistantPageContext
        page="店铺销售"
        subject={null}
        context={
          filters && !scopeError
            ? {
                domain: "sales",
                shop_id: scope.shop || undefined,
                month: scope.month,
                date_from: scope.from,
                date_to: scope.to,
                versions: { sales: filters.meta.data_version },
              }
            : {}
        }
      />
      <div className="m3-sales-scope">
        <main className="sales-dashboard">
          <section className="sales-intro">
            <div>
              <p className="eyebrow">STORE SALES · MONTHLY SNAPSHOT</p>
              <h1>店铺销售看板</h1>
              <p>
                按日期查看各店铺销售贡献，并将销售、流量、转化、退款与推广效率放在同一经营视图中。
              </p>
            </div>
            <div className="data-status">
              <i /> 数据截止：{filters?.meta.observed_through ?? "—"}
            </div>
          </section>
          <section className="sales-filters" aria-label="数据筛选">
            <div className="prototype-select-label">
              <span>月份</span>
              <Select
                variant="borderless"
                size="small"
                aria-label="月份"
                disabled={!filters}
                value={scope.month || undefined}
                placeholder="待接入"
                options={filters?.data.months.map((item) => ({
                  value: item.month,
                  label: `${Number(item.month.slice(5))}月（${item.month.slice(0, 4)}年）`,
                }))}
                onChange={(month) => {
                  const option = filters!.data.months.find((item) => item.month === month)!;
                  changeScope({ month, from: option.date_from, to: option.date_to });
                }}
              />
            </div>
            <div className="sales-date-range" role="group" aria-label="经营数据日期筛选">
              <span>日期</span>
              <DatePicker
                variant="borderless"
                size="small"
                aria-label="经营数据开始日期"
                format="YYYY/MM/DD"
                value={scope.from ? dayjs(scope.from) : null}
                disabled={!filters}
                allowClear={false}
                minDate={filters ? dayjs(filters.data.months[0].date_from) : undefined}
                maxDate={filters ? dayjs(filters.data.calendar_through) : undefined}
                onChange={(value) => dateChange(value?.format("YYYY-MM-DD") ?? "", "from")}
              />
              <span>至</span>
              <DatePicker
                variant="borderless"
                size="small"
                aria-label="经营数据结束日期"
                format="YYYY/MM/DD"
                value={scope.to ? dayjs(scope.to) : null}
                disabled={!filters}
                allowClear={false}
                minDate={filters ? dayjs(filters.data.months[0].date_from) : undefined}
                maxDate={filters ? dayjs(filters.data.calendar_through) : undefined}
                onChange={(value) => dateChange(value?.format("YYYY-MM-DD") ?? "", "to")}
              />
            </div>
            <span>经营数据：{rangeLabel}</span>
            <a href={TARGET_SOURCE} target="_blank" rel="noreferrer">
              查看月度目标台账 ↗
            </a>
          </section>
          {error && (
            <Alert
              type="error"
              showIcon
              title={scopeError ? "链接范围无效" : "本地数据读取失败"}
              description={error}
              action={
                <Button onClick={scopeError ? resetScope : retry}>
                  {scopeError ? "清除无效范围" : "重新读取本地数据"}
                </Button>
              }
            />
          )}
          <SalesCoverage result={result} error={error} />
          <p className="target-scope-note">
            目标进度按 {scope.month || "—"}{" "}
            月累计及经营年度累计计算，不随自定义日期或经营店铺变化。目标采用已确认的飞书原版 GMV
            目标，完成额为数仓成交原值；来源状态及缺口按实际覆盖显示。
          </p>
          <SalesTargets
            month={scope.month}
            version={
              !scopeError && filters?.meta.input_versions ? filters.meta.data_version : undefined
            }
            onConflict={invalidateVersion}
            statusKey={JSON.stringify(filters?.data.sources?.sales_targets)}
          />
          <section id="performance" className="dashboard-section sales-section">
            <div className="section-heading">
              <div>
                <p className="eyebrow">PERFORMANCE OVERVIEW</p>
                <div className="performance-title-row">
                  <h2>{scope.shop ? `${selectedName}经营表现` : "全店经营表现"}</h2>
                  <div className="shop-filter">
                    <span>店铺筛选</span>
                    <Select
                      variant="borderless"
                      size="small"
                      aria-label="店铺筛选"
                      disabled={!filters}
                      value={scope.shop}
                      options={[
                        { value: "", label: "全部店铺" },
                        ...(filters?.data.shops.map((shop) => ({
                          value: shop.shop_id,
                          label: shop.display_name,
                        })) ?? []),
                      ]}
                      onChange={(shop) => changeScope({ shop })}
                    />
                  </div>
                  {scope.shop && (
                    <Button className="reset-filter" onClick={() => changeScope({ shop: "" })}>
                      显示全部店铺
                    </Button>
                  )}
                </div>
              </div>
              <p>
                {rangeLabel} ·
                汇总所选日期与店铺的销售、流量、退款和推广数据；推广成交按支付日统计。
              </p>
            </div>
            <SalesPerformanceMetrics
              result={result}
              scope={scope}
              sources={filters?.data.sources}
              shopName={selectedName}
              rangeLabel={rangeLabel}
              onConflict={invalidateVersion}
            />
            <SalesNotes data={data} shops={filters?.data.shops} shopId={scope.shop} />
            <PromotionDetails
              data={data}
              sources={filters?.data.sources}
              shops={filters?.data.shops}
              shopName={selectedName}
            />
            <SalesRatings
              filters={scopeError ? null : filters}
              shop={scope.shop}
              onConflict={invalidateVersion}
            />
          </section>
          <SalesContribution
            shops={shops}
            sources={filters?.data.sources}
            selected={scope.shop}
            onSelect={selectShop}
            error={error}
          />
          <SalesRegions filters={filters} onConflict={invalidateVersion} />
          <ShopTable shops={shops} selected={scope.shop} onSelect={selectShop} />
          <footer className="sales-footer">
            数据来源：数仓「ods_rpa_pdd_traffic_kanban_overview_f」。本地最近成功：
            {localTime(result?.meta.last_success_at)} · 已观察截至{" "}
            {filters?.meta.observed_through ?? "—"} ·
            完整截至日未知。飞书退款、数仓店铺推广及历史补齐、评分及两类地区已接入；GMV目标来源与状态见目标进度区
            · 本页面不代表实时交易数据。
          </footer>
        </main>
      </div>
    </DashboardLayout>
  );
}
