import { useState } from "react";
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
import { Metrics, MissingDays, StoreNet, TopDates, Trends } from "./Overview";
import { Diagnosis } from "./Diagnosis";
import { SourceState } from "./SourceState";
import { formatMetric, formatValue } from "./format";
import { toggleMetric, type MetricKey } from "./state";
import "./prototype.css";
import "./promotion.css";

/** 推广双模式整页：共享导航，独立筛选与服务端聚合，不读取经营快照。 */
export function PromotionPage() {
  const state = usePromotion();
  const product = state.mode === "product";
  const [storeSelected, setStoreSelected] = useState<MetricKey[]>(["spend", "revenue"]);
  const [productSelected, setProductSelected] = useState<MetricKey[]>(["revenue", "spend"]);
  const selected = product ? productSelected : storeSelected;
  const toggle = (key: MetricKey) =>
    product
      ? setProductSelected((values) => toggleMetric(values, key, "product"))
      : setStoreSelected((values) => toggleMetric(values, key, "store"));
  const meta = state.meta.data?.data;
  const data = state.overview.data?.data;
  const errors = [state.overview.error, state.days.error, state.options.error].filter(Boolean);
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
      <div className="m2-promotion-scope">
        <div className="promotion-modes" aria-label="推广分析模式">
          <Button aria-pressed={!product} onClick={() => state.setMode("store")}>
            店铺推广总览
          </Button>
          <Button aria-pressed={product} onClick={() => state.setMode("product")}>
            商品推广明细
          </Button>
        </div>
        <main className={product ? "product-promotion" : "store-promotion"}>
          <section className="hero">
            <div>
              <p className="eyebrow">{product ? "推广效率 · 多店铺" : "店铺推广 · 数仓接入"}</p>
              <h1>{product ? "拼多多推广数据看板" : "拼多多店铺推广看板"}</h1>
              <p>
                {product
                  ? "花费、投产、流量效率与风险日跟踪"
                  : "店铺商品推广 + 官方店明星店铺 · 分渠道核对"}
              </p>
              {product && (
                <a
                  className="m2-hero-source-link"
                  href="https://kocotree.feishu.cn/base/XGzTbd9xJaASV3soYnycMmf0nY3?table=tbl3KOoiJ1uu7zbS&view=vew4DGsevS"
                  target="_blank"
                  rel="noreferrer"
                >
                  查看商品上架表 ↗
                </a>
              )}
            </div>
            <div className="snapshot">
              <b>
                {product ? "数据截至" : "统计观察截至"}：{meta?.date_to ?? "—"}
              </b>
              <span>
                {product
                  ? `来源：数仓商品推广明细 · 已同步 ${formatValue(data?.dataset_record_count ?? meta?.observed_rows)} 条记录`
                  : "历史保留来源，未知缺日不补零"}
              </span>
              <span>完整截至日：{meta?.complete_through ?? "未知"}</span>
            </div>
          </section>
          <SourceState data={meta} version={state.version} />
          <div className="m2-status" aria-live="polite">
            {state.meta.isPending && <Spin size="small" aria-label="正在读取推广来源…" />}
            {state.meta.error && (
              <Alert
                type="error"
                showIcon
                title="推广数据暂未接通"
                description="暂无可用本地发布版本，页面保留展示字段，不显示模拟经营数据。"
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
                title="当前推广查询未完成"
                description={errors[0]?.message}
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
          </div>
          <Filters state={state} />
          <section className="callout">
            <div>
              {product && <span>推广经营结论</span>}
              <strong>
                {product
                  ? data?.current_record_count && data.current_record_count !== "0"
                    ? "推广效率需结合周期变化与商品利润底线判断"
                    : "当前筛选范围暂无推广记录"
                  : (data?.coverage.missing_count ?? 0) > 0
                    ? `存在 ${data!.coverage.missing_count} 个店铺渠道日缺失，以下为已知记录合计`
                    : data?.current_record_count === "0"
                      ? "所选店铺或渠道暂无适用记录"
                      : data
                        ? "所选范围已有记录，源业务完整性仍待核验"
                        : "当前范围尚无可用发布结果"}
              </strong>
              {!product && (
                <p>店铺总览不按商品ID拆分；商品、品类、运营及等级筛选请切换「商品推广明细」。</p>
              )}
            </div>
            {product && (
              <p>
                当前范围 {formatValue(data?.current_record_count)} 条记录 · 交易额 / 花费 ROI{" "}
                <b>{formatMetric(data?.metrics.roi)}</b> · 近7天诊断与行动建议见下方 SOP 分析
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
              <StoreNet data={data} />
              <MissingDays data={data} />
            </>
          )}
          <Details state={state} />
          <footer>
            {product
              ? "指标口径：推广成交沿用来源已归因的支付日汇总，不重算点击归因；支付日不代表当天点击。实际投产比 = 交易额 ÷ 总花费；净投产比 = 净交易额 ÷ 总花费；CTR = 点击 ÷ 曝光；点击转化率 = 成交笔数 ÷ 点击量。"
              : "店铺商品推广：ods_rpa_pdd_product_promotion_overview_f；官方店明星店铺：pdd_star_shop。沿用来源已归因的支付日汇总，仅日粒度，不含当天；两渠道独立、不重复成交，比率由相容分子分母合计后计算。缺渠道日不发布合计ROI/CVR，明星店铺缺净额不拼完整净额。支付日不代表当天点击。"}
          </footer>
        </main>
      </div>
    </DashboardLayout>
  );
}
