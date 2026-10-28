import { Alert, Button, Card, DatePicker, Progress, Select } from "antd";
import dayjs from "dayjs";
import {
  AimOutlined,
  BarChartOutlined,
  ShopOutlined,
  GlobalOutlined,
  TableOutlined,
} from "@ant-design/icons";
import { DashboardLayout } from "../../shared/layouts/DashboardLayout";
import { useSalesData } from "./api/queries";
import { REFUND_SOURCE, TARGET_SOURCE } from "./sourceLinks";
import { SalesTargets } from "./components/SalesTargets";
import { SalesRatings } from "./components/SalesRatings";
import { SalesRegions } from "./components/SalesRegions";
import { PromotionDetails } from "./components/PromotionDetails";
import { SourceState } from "./components/SourceState";
import { ShopTable } from "./components/ShopTable";
import { count, fixed, localTime, money, percent } from "./format";
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
  const kpis = [
    ["销售金额", money(data?.shop_revenue), `${rangeLabel} · ${selectedName}`, "primary"],
    ["店铺访客", count(data?.shop_visitors), "所选日期累计访客", ""],
    ["成交转化率", percent(data?.buyer_conversion), "成交买家 / 店铺访客", ""],
    ["客单价", data?.aov == null ? "—" : `¥${fixed(data.aov)}`, "成交金额 / 成交买家", ""],
    ["退款率", percent(data?.refund_rate), "退款发生期 / 同期支付成交 · 经营金额占比", "danger"],
    ["推广花费", money(data?.promotion_spend), "数仓 6 店 · 官方明星店铺 · 已知值合计", ""],
    ["点击率", percent(data?.promotion_ctr), "已匹配推广点击量 / 曝光量", ""],
    ["ROI", fixed(data?.promotion_roi), "渠道独立 · 支付日统计 · 归因窗口待补证", "success"],
  ];
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
              <i /> 数仓已观察日期 {filters?.meta.observed_through ?? "—"}
              <small>完整截至日未确认 · 非实时数据</small>
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
          <aside className="source-coverage" aria-label="数据覆盖说明">
            <strong>
              {result
                ? "已接入店铺日经营数据 · 上游完整性待确认"
                : error
                  ? "尚未取得当前本地数据"
                  : "正在读取本地已发布数据…"}
            </strong>
            <p>
              {result
                ? `所选时段销售缺少 ${result.missingShopDays} 个店铺日记录，仅汇总已观察值；缺失不补零。`
                : "未收到当前版本前，指标保留为空，不展示旧筛选或样例数据。"}{" "}
              退款、推广、评分及地区按独立来源覆盖展示；基础目标及其覆盖由目标进度区说明，完整日未确认。
            </p>
            <details>
              <summary>查看各店铺日期覆盖及退款缺失明细</summary>
              {filters?.data.shops.map((shop) => {
                const row = shops.find((s) => s.shop_id === shop.shop_id);
                return (
                  <p key={shop.shop_id}>
                    <b>{shop.display_name}</b> · 已入仓 {row?.coverage.observed_days ?? "—"} 天 ·
                    最新 {row?.coverage.latest_observed_date ?? "—"} · 销售缺失日期：
                    {row
                      ? row.coverage.missing_dates
                          .map((s) => s.slice(s.indexOf(":") + 1))
                          .join("、") || "该窗口未观察到缺日"
                      : "待读取"}{" "}
                    · 退款缺失日期：{row?.refund_match?.missing_dates.join("、") || "—"} ·
                    推广最新：
                    {row?.promotion_channels
                      ?.map((c) => c.source.observed_through ?? "—")
                      .join(" / ") || "—"}
                  </p>
                );
              })}
            </details>
            <p>
              本地最近成功：{localTime(result?.meta.last_success_at)} · 最近尝试：
              {result?.meta.last_attempt_status
                ? ({
                    succeeded: "成功",
                    failed: "失败",
                    abandoned: "已中断",
                    running: "进行中",
                    queued: "等待中",
                  }[result.meta.last_attempt_status] ?? result.meta.last_attempt_status)
                : "—"}
              （{result?.meta.last_attempt_at ? localTime(result.meta.last_attempt_at) : "—"}） ·
              来源更新时间原文：
              {result?.meta.source_updated_text ?? "—"}（时区未确认）
            </p>
          </aside>
          <p className="target-scope-note">
            目标进度按 {scope.month || "—"}{" "}
            月累计及经营年度累计计算，不随自定义日期或经营店铺变化。目标采用已确认的飞书基础目标，完成额为数仓成交原值；来源状态及缺口按实际覆盖显示。
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
                店铺级指标随日期与店铺筛选联动；两推广渠道独立、按支付日统计，归因窗口天数待来源补证。
              </p>
            </div>
            <div className="sales-kpis" data-testid="sales-kpis">
              {kpis.map(([title, value, note, state]) => (
                <Card key={title} className={`kpi-card ${state}`}>
                  <span>{title}</span>
                  <strong>{value}</strong>
                  <small>{note}</small>
                </Card>
              ))}
            </div>
            <PromotionDetails data={data} sources={filters?.data.sources} />
            <p className="refund-coverage-note">
              同期经营退款金额占比按销售与退款均有值的同店同日记录计算：分子为所选发生期退款金额，分母为同期支付成交金额。已匹配退款{" "}
              {money(data?.refund_match?.matched_refunds)} ÷ 对应成交金额{" "}
              {money(data?.refund_match?.matched_sales)}；匹配店铺日{" "}
              {data?.refund_match?.matched_days ?? "—"}、销售覆盖率{" "}
              {percent(data?.refund_match?.sales_coverage, 4)}、排除店铺日{" "}
              {data?.refund_match?.excluded_days ?? "—"} 及销售额{" "}
              {money(data?.refund_match?.excluded_sales)}
              。这是发生期与支付期的同期经营占比，不代表同批支付订单的最终退款率；来源覆盖与做单原处理仍待核，缺失不补零。
              <br />
              <SourceState
                source={filters?.data.sources?.shop_refunds ?? data?.refund_match?.source}
                label="飞书退款"
              />
            </p>
            <SalesRatings
              filters={scopeError ? null : filters}
              shop={scope.shop}
              onConflict={invalidateVersion}
            />
          </section>
          <section className="dashboard-section store-split" id="shops">
            <div className="section-heading">
              <div>
                <p className="eyebrow">STORE CONTRIBUTION</p>
                <h2>店铺销售贡献</h2>
              </div>
              <p>点击店铺可快速切换至单店指标。</p>
            </div>
            <div className="store-layout">
              <article className="sales-rank">
                <div className="list-head">
                  <span>店铺</span>
                  <span>销售额 / 占比</span>
                </div>
                {shops.map((shop) => (
                  <button
                    key={shop.shop_id}
                    className={scope.shop === shop.shop_id ? "active" : ""}
                    onClick={() => selectShop(shop.shop_id)}
                  >
                    <span className="rank">{shop.rank ?? "—"}</span>
                    <b>{shop.display_name}</b>
                    <div>
                      <Progress
                        percent={
                          shop.sales_share == null
                            ? 0
                            : Math.min(100, Math.max(0, Number(shop.sales_share) * 100))
                        }
                        showInfo={false}
                      />
                      <strong>
                        {money(shop.shop_revenue, true)}{" "}
                        <small>{percent(shop.sales_share, 1)}</small>
                      </strong>
                    </div>
                  </button>
                ))}
                {!shops.length && (
                  <p className="pending-rank">{error ? "读取失败，请重试" : "等待本地店铺数据"}</p>
                )}
              </article>
              <aside className="data-note">
                <span>数据说明</span>
                <h3>所选时段经营快照</h3>
                <p>
                  销售、访客、成交买家和订单取自数仓。客单价＝成交金额÷成交买家数；转化率＝成交买家数÷店铺访客数，均先合计再计算。访客、买家按日累加，不跨日去重。
                </p>
                <dl>
                  <div>
                    <dt>销售数据</dt>
                    <dd>6 家店铺</dd>
                  </div>
                  <div>
                    <dt>推广数据</dt>
                    <dd>数仓 6 店 + 官方明星店铺</dd>
                  </div>
                  <div>
                    <dt>退款 / 目标</dt>
                    <dd>飞书同期经营退款占比 · 目标入口不可用</dd>
                  </div>
                </dl>
                <p>
                  退款发生额与同期支付成交按同店日配对，缺一方时双方共同排除；推广成交不替代店铺销售。缺数、未知完整日与真实零分别显示。
                </p>
                <a href={REFUND_SOURCE} target="_blank" rel="noreferrer">
                  查看统计退款金额 ↗
                </a>
              </aside>
            </div>
          </section>
          <SalesRegions filters={filters} onConflict={invalidateVersion} />
          <ShopTable shops={shops} selected={scope.shop} onSelect={selectShop} />
          <footer className="sales-footer">
            数据来源：数仓「ods_rpa_pdd_traffic_kanban_overview_f」。本地最近成功：
            {localTime(result?.meta.last_success_at)} · 已观察截至{" "}
            {filters?.meta.observed_through ?? "—"} ·
            完整截至日未知。飞书退款、数仓店铺推广、评分及两类地区已接入；月度目标入口不可用 ·
            本页面不代表实时交易数据。
          </footer>
        </main>
      </div>
    </DashboardLayout>
  );
}
