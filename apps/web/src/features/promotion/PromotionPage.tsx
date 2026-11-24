import { useCallback, useState } from "react";
import { Alert, Button, Spin } from "antd";
import {
  BarChartOutlined,
  BulbOutlined,
  FilterOutlined,
  LineChartOutlined,
  TableOutlined,
} from "@ant-design/icons";
import { DashboardLayout } from "../../shared/layouts/DashboardLayout";
import type { DashboardSectionLink } from "../../shared/components/DashboardSections";
import { usePromotion } from "./data";
import { Filters } from "./Filters";
import { Details } from "./Details";
import { Metrics, MissingDays, TopDates, Trends } from "./Overview";
import { StoreNet } from "./StoreNet";
import { Diagnosis } from "./Diagnosis";
import { formatMetric, formatValue, roiExclusionNote } from "./format";
import { toggleMetric, type MetricKey } from "./state";
import { AssistantPageContext } from "../../shared/assistant/AssistantContext";
import "./prototype.css";
import "./promotion.css";

/** 推广双模式整页：共享导航，独立筛选与服务端聚合，不读取经营快照。 */
export function PromotionPage() {
  const state = usePromotion();
  const product = state.mode === "product";
  const [storeSelected, setStoreSelected] = useState<MetricKey[]>(["spend", "revenue"]);
  const [productSelected, setProductSelected] = useState<MetricKey[]>(["revenue", "spend"]);
  const [missingDatesOpen, setMissingDatesOpen] = useState(false);
  const selected = product ? productSelected : storeSelected;
  const toggle = useCallback(
    (key: MetricKey) =>
      product
        ? setProductSelected((values) => toggleMetric(values, key, "product"))
        : setStoreSelected((values) => toggleMetric(values, key, "store")),
    [product],
  );
  const meta = state.meta.data?.data;
  const data = state.overview.data?.data;
  // 当前筛选的已发布聚合包含记录时，唯一所选商品才是本页实际统计对象。
  const singleProduct =
    product && state.scope.products.length === 1 && data && data.current_record_count !== "0"
      ? state.selectedProducts.find(
          (row) =>
            row.listing_id === state.scope.products[0] &&
            (!state.scope.shop || row.shop_id === state.scope.shop),
        )
      : undefined;
  const roiNote = product ? roiExclusionNote(data) : "";
  const errors = [state.overview.error, state.days.error].filter(Boolean);
  const syncFailed = meta?.sources.some((source) => {
    const attempt = source.latest_attempt;
    return (
      attempt !== null &&
      typeof attempt === "object" &&
      "status" in attempt &&
      attempt.status === "failed"
    );
  });
  const showStatus =
    state.meta.isPending ||
    state.meta.error ||
    (state.invalid && state.version) ||
    (!state.invalid && errors.length > 0) ||
    syncFailed;
  const sections: DashboardSectionLink[] = product
    ? [
        { id: "promotion-filters", title: "筛选范围", icon: <FilterOutlined /> },
        { id: "promotion-metrics", title: "推广指标", icon: <BarChartOutlined /> },
        { id: "promotion-trend", title: "趋势与漏斗", icon: <LineChartOutlined /> },
        { id: "promotion-top-dates", title: "低投产日期", icon: <LineChartOutlined /> },
        { id: "promotion-diagnosis", title: "SOP经营诊断", icon: <BulbOutlined /> },
        { id: "promotion-days", title: "每日推广明细", icon: <TableOutlined /> },
      ]
    : [
        { id: "promotion-filters", title: "筛选范围", icon: <FilterOutlined /> },
        { id: "promotion-metrics", title: "推广指标", icon: <BarChartOutlined /> },
        { id: "promotion-trend", title: "核心指标趋势", icon: <LineChartOutlined /> },
        { id: "promotion-days", title: "店铺渠道明细", icon: <TableOutlined /> },
      ];
  return (
    <DashboardLayout currentPage="promotion" sections={sections} context="推广分析 · 本页独立分析">
      <AssistantPageContext
        page="推广分析"
        subject={
          singleProduct
            ? {
                listing_id: singleProduct.listing_id,
                product_id: singleProduct.product_id,
                shop_id: singleProduct.shop_id,
                name: singleProduct.product_name,
                shop_name:
                  state.shopOptions.find((shop) => shop.value === singleProduct.shop_id)?.label ??
                  null,
              }
            : null
        }
        context={
          data && !state.invalid
            ? {
                date_from: state.scope.from,
                date_to: state.scope.to,
                shop_id: state.scope.shop || undefined,
                promotion_version: product ? state.version : undefined,
              }
            : {}
        }
      />
      <div className="m2-promotion-scope">
        <div className="promotion-modes" aria-label="推广分析模式">
          <Button aria-pressed={!product} onClick={() => state.setMode("store")}>
            店铺推广总览
          </Button>
          <Button aria-pressed={product} onClick={() => state.setMode("product")}>
            商品推广明细
          </Button>
          {product && (
            <a
              className="m2-product-source-link"
              href="https://kocotree.feishu.cn/base/XGzTbd9xJaASV3soYnycMmf0nY3?table=tbl3KOoiJ1uu7zbS&view=vew4DGsevS"
              target="_blank"
              rel="noreferrer"
            >
              查看商品上架表 ↗
            </a>
          )}
        </div>
        <main className={product ? "product-promotion" : "store-promotion"}>
          <section className="hero">
            <div>
              <p className="eyebrow">{product ? "推广效率 · 多店铺" : "店铺推广 · 分渠道统计"}</p>
              <h1>{product ? "拼多多推广数据看板" : "拼多多店铺推广看板"}</h1>
              <p>
                {product
                  ? "花费、投产、流量效率与风险日跟踪"
                  : "店铺商品推广 + 官方店明星店铺 · 分渠道核对"}
              </p>
            </div>
            <div className="snapshot">
              <b>数据截至：{meta?.date_to ?? "—"}</b>
              {product && (
                <span>
                  来源：数仓 · 共 {formatValue(data?.dataset_record_count ?? meta?.observed_rows)}{" "}
                  条记录
                </span>
              )}
            </div>
          </section>
          {showStatus && (
            <div className="m2-status" aria-live="polite">
              {state.meta.isPending && <Spin size="small" aria-label="正在读取推广来源…" />}
              {state.meta.error && (
                <Alert
                  type="error"
                  showIcon
                  title="推广数据暂时无法加载"
                  description="请稍后重试。"
                  action={<Button onClick={() => void state.meta.refetch()}>重试连接</Button>}
                />
              )}
              {state.invalid && state.version && (
                <Alert type="warning" showIcon title={state.invalid} />
              )}
              {!state.invalid && errors.length > 0 && (
                <Alert
                  type="error"
                  showIcon
                  title="推广数据暂时无法加载，请重试"
                  action={
                    <Button
                      onClick={() => {
                        void state.meta.refetch();
                        void state.overview.refetch();
                        void state.days.refetch();
                        void state.options.refetch();
                      }}
                    >
                      重试查询
                    </Button>
                  }
                />
              )}
              {syncFailed && !state.meta.error && (
                <Alert
                  type="warning"
                  showIcon
                  title="本次更新未完成"
                  description={
                    meta?.date_to
                      ? `当前显示上次更新的数据，数据截至 ${meta.date_to}。`
                      : "请稍后重试。"
                  }
                />
              )}
            </div>
          )}
          <Filters state={state} />
          <section className="callout" aria-label={product ? "推广经营结论" : undefined}>
            <div>
              {product && <span>推广经营结论</span>}
              <strong>
                {!data
                  ? state.meta.error || errors.length > 0
                    ? "推广数据暂时无法加载"
                    : "正在读取推广数据…"
                  : data.current_record_count === "0"
                    ? "所选期间暂无推广数据"
                    : !product
                      ? (data.coverage.missing_count ?? 0) > 0
                        ? "部分日期暂无推广数据。"
                        : "所选期间每天均有推广记录。"
                      : "推广效率需结合周期变化与商品利润底线判断"}
                {!product &&
                  data &&
                  data.current_record_count !== "0" &&
                  (data.coverage.missing_count ?? 0) > 0 && (
                    <>
                      {" "}
                      <a href="#promotion-missing-days" onClick={() => setMissingDatesOpen(true)}>
                        查看日期
                      </a>
                    </>
                  )}
              </strong>
              {!product && <p>查看单个商品，请切换「商品推广明细」。</p>}
            </div>
            {product && data && data.current_record_count !== "0" && (
              <p>
                当前范围 {formatValue(data.coverage.observed_days?.toString())} 天数据 ·{" "}
                投产比（ROI） <b>{formatMetric(data.metrics.roi)}</b> · 近7天诊断与行动建议见下方
                SOP 分析
                {roiNote && <small className="m2-roi-note">{roiNote}</small>}
              </p>
            )}
          </section>
          <Metrics data={data} selected={selected} toggle={toggle} mode={state.mode} />
          <Trends data={data} selected={selected} product={product} />
          {product ? (
            <>
              <TopDates data={data} />
              <Diagnosis state={state} />
            </>
          ) : (
            <>
              {!state.invalid && (
                <StoreNet
                  data={data}
                  shop={state.scope.shop}
                  channel={state.scope.channel}
                  failed={!!state.overview.error || !!state.meta.error}
                />
              )}
              <MissingDays data={data} open={missingDatesOpen} onToggle={setMissingDatesOpen} />
            </>
          )}
          <Details state={state} />
          {product && (
            <footer>
              指标口径：推广成交沿用来源已归因的支付日汇总，不重算点击归因；支付日不代表当天点击。实际投产比
              = 交易额 ÷ 总花费；净投产比 = 净交易额 ÷ 总花费；CTR = 点击 ÷ 曝光；点击转化率 =
              成交笔数 ÷ 点击量。
            </footer>
          )}
        </main>
      </div>
    </DashboardLayout>
  );
}
