import { Alert, Badge, Button, Select, Space, Tag, Typography } from "antd";
import {
  AppstoreOutlined,
  BarChartOutlined,
  FundOutlined,
  BulbOutlined,
  AlertOutlined,
  TableOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import type {
  Diagnosis,
  Filters,
  MetricKey,
  Mode,
  Options,
  Overview,
  Product,
  Scenario,
  Scope,
  Trend,
} from "./api/contracts";
import { useProductQuery, ProductDataVersionContext, ProductBootstrapContext } from "./api/queries";
import { createVersionCoordinator } from "./state/version";
import {
  buildDefaultFilters,
  readFilters,
  sameCatalogFilters,
  updateFilters,
  writeFilters,
} from "./state/filters";
import { BriefSection } from "./sections/BriefSection";
import { ScoresSection } from "./sections/ScoresSection";
import { OverviewSection } from "./sections/OverviewSection";
import { TrendSection, type Comparison } from "./sections/TrendSection";
import { DiagnosisSection } from "./sections/DiagnosisSection";
import { AlertsSection } from "./sections/AlertsSection";
import { DaysSection } from "./sections/DaysSection";
import type { DashboardSectionLink } from "../../shared/components/DashboardSections";
import { DashboardLayout } from "../../shared/layouts/DashboardLayout";
import "./products.css";

const pageSections: DashboardSectionLink[] = [
  { id: "product-brief", title: "经营核心结论", icon: <AppstoreOutlined /> },
  { id: "product-scores", title: "评分与放量建议", icon: <BarChartOutlined /> },
  { id: "overview", title: "全店指标与趋势", icon: <FundOutlined /> },
  { id: "product-diagnosis", title: "经营诊断", icon: <BulbOutlined /> },
  { id: "risk", title: "异常与行动建议", icon: <AlertOutlined /> },
  { id: "product-days", title: "商品明细底表", icon: <TableOutlined /> },
];

const scenarios: { value: Scenario; label: string }[] = [
  { value: "normal", label: "正常样例" },
  { value: "partial", label: "部分缺数" },
  { value: "empty", label: "无匹配结果" },
  { value: "error", label: "请求失败" },
  { value: "pending", label: "评分 / 分析待生成" },
  { value: "not-connected", label: "未接入" },
];
const metricNames: Record<MetricKey, string> = {
  revenue: "交易金额",
  visitors: "商品访客数",
  favorites: "收藏用户数",
  buyers: "支付买家数",
  units: "支付件数",
  aov: "客单价",
  conversion: "支付转化率",
};

/** 商品页只协调UI筛选和数据版本；真实经营计算全部由后端完成。 */
export function ProductsPage() {
  const [params, setParams] = useSearchParams();
  useEffect(() => {
    const before = document.title;
    document.title = "商品数据看板 · 运营平台";
    return () => {
      document.title = before;
    };
  }, []);
  const mode: Mode =
    import.meta.env.DEV && params.get("preview") === "1"
      ? "preview"
      : import.meta.env.DEV && params.get("data") === "offline"
        ? "offline"
        : "api";
  const scenario = scenarios.find((s) => s.value === params.get("scenario"))?.value ?? "normal";
  const client = useQueryClient();
  const [versionToken, setVersionToken] = useState(0);
  const [versionNotice, setVersionNotice] = useState(false);
  const [versionLocked, setVersionLocked] = useState(false);
  const versionCoordinator = useRef(createVersionCoordinator());
  const [metric, setMetric] = useState<MetricKey>("revenue");
  const [comparison, setComparison] = useState<Comparison>("year");
  const rawOverview = readFilters(params, "overview");
  const rawScores = readFilters(params, "scores");
  const rawDays = readFilters(params, "days");
  const requestOptions = { mode, scenario, versionToken };
  const optionsQuery = useProductQuery<Options>("filters", "overview", rawOverview, requestOptions);
  const dataVersion = optionsQuery.data?.meta.dataVersion;
  const queriesReady = !versionLocked && (mode === "offline" || optionsQuery.isSuccess);
  const options = optionsQuery.data?.data ?? null;
  const reuseDayOptions = mode === "api" && sameCatalogFilters(rawOverview, rawDays);
  const scoreOptionsQuery = useProductQuery<Options>(
    "filters",
    "scores",
    rawScores,
    { ...requestOptions, dataVersion },
    queriesReady,
  );
  const dayOptionsQuery = useProductQuery<Options>(
    "filters",
    "days",
    rawDays,
    { ...requestOptions, dataVersion },
    queriesReady && !reuseDayOptions,
  );
  const rawScoreOptions = scoreOptionsQuery.data?.data;
  const scoreOptions = useMemo(
    () =>
      rawScoreOptions
        ? {
            ...rawScoreOptions,
            shops: rawScoreOptions.shops.map((shop) => ({
              ...shop,
              label: options?.shops.find((s) => s.value === shop.value)?.label ?? shop.label,
            })),
          }
        : null,
    [rawScoreOptions, options],
  );
  const dayOptions = reuseDayOptions ? options : (dayOptionsQuery.data?.data ?? null);
  const withDates = (f: Filters, scope: Scope, available: Options | null): Filters =>
    !params.has(`${scope}.dateFrom`) && !params.has(`${scope}.dateTo`) && available
      ? { ...f, dateFrom: available.dateRange.from, dateTo: available.dateRange.to }
      : f;
  const overviewFilters = withDates(rawOverview, "overview", options);
  const scoreFilters = rawScores;
  // 推荐日期是全局发布窗口；独立明细目录返回前即可使用，避免先查空日期再重查。
  const dayFilters = withDates(rawDays, "days", dayOptions ?? options);
  const overviewQuery = useProductQuery<Overview>(
    "overview",
    "overview",
    overviewFilters,
    { ...requestOptions, dataVersion },
    queriesReady,
  );
  const trendQuery = useProductQuery<Trend>(
    "trend",
    "overview",
    overviewFilters,
    {
      ...requestOptions,
      dataVersion,
      metric,
      comparison: overviewFilters.products.length ? comparison : "year",
    },
    queriesReady,
  );
  const diagnosisQuery = useProductQuery<Diagnosis>(
    "diagnosis",
    "overview",
    overviewFilters,
    { ...requestOptions, dataVersion, metric },
    queriesReady,
  );
  const setScope = useCallback(
    (scope: Scope, patch: Partial<Filters>) => {
      setParams((previous) => {
        let current = readFilters(previous, scope);
        const available =
          scope === "overview" ? options : scope === "days" ? dayOptions : scoreOptions;
        if (
          !previous.has(`${scope}.dateFrom`) &&
          !previous.has(`${scope}.dateTo`) &&
          scope !== "scores" &&
          available
        )
          current = {
            ...current,
            dateFrom: available.dateRange.from,
            dateTo: available.dateRange.to,
          };
        return writeFilters(previous, scope, updateFilters(current, patch));
      });
    },
    [setParams, options, dayOptions, scoreOptions],
  );
  const changeOverview = useCallback(
    (patch: Partial<Filters>) => setScope("overview", patch),
    [setScope],
  );
  const changeScores = useCallback(
    (patch: Partial<Filters>) => setScope("scores", patch),
    [setScope],
  );
  const changeDays = useCallback((patch: Partial<Filters>) => setScope("days", patch), [setScope]);
  const reset = (scope: Scope) => {
    const available = scope === "overview" ? options : scope === "days" ? dayOptions : scoreOptions;
    setParams((previous) =>
      writeFilters(previous, scope, {
        ...buildDefaultFilters(),
        dateFrom: scope !== "scores" ? (available?.dateRange.from ?? "") : "",
        dateTo: scope !== "scores" ? (available?.dateRange.to ?? "") : "",
      }),
    );
  };
  const chooseProduct = (product: Product) => {
    changeOverview({ shop: product.shopId, products: [product.key] });
    document.getElementById("overview")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const switchMode = (nextMode: Mode) => {
    setParams((previous) => {
      const next = new URLSearchParams();
      if (previous.has("focus")) next.set("focus", previous.get("focus")!);
      if (nextMode === "preview") next.set("preview", "1");
      if (nextMode === "offline") next.set("data", "offline");
      return next;
    });
    client.removeQueries({ queryKey: ["products"] });
  };
  useEffect(() => {
    return client.getQueryCache().subscribe((event) => {
      const query = event.query;
      if (query.queryKey[0] !== "products" || event.type !== "updated") return;
      const result = versionCoordinator.current.handle({
        action: event.action.type,
        endpoint: String(query.queryKey[1]),
        epoch: (query.queryKey[4] as { versionToken?: number })?.versionToken ?? 0,
        status: (query.state.error as { status?: number } | null)?.status,
      });
      if (result === "lock") {
        setVersionLocked(true);
        return;
      }
      if (result !== "reload") return;
      setVersionNotice(true);
      void client.cancelQueries({ queryKey: ["products"] });
      client.removeQueries({ queryKey: ["products"] });
      setVersionToken(versionCoordinator.current.epoch);
    });
  }, [client]);
  const focus = params.get("focus");
  useEffect(() => {
    if (focus !== "risk") return;
    const timer = setTimeout(
      () => document.getElementById("risk")?.scrollIntoView({ block: "start" }),
      350,
    );
    return () => clearTimeout(timer);
  }, [focus]);
  const rangeLabel = overviewFilters.dateFrom
    ? `${overviewFilters.dateFrom} 至 ${overviewFilters.dateTo || overviewFilters.dateFrom}`
    : "日期范围尚未接入";
  const pageKey = `${mode}-${scenario}-${versionToken}`;
  return (
    <div className="products-page">
      <DashboardLayout currentPage="products" sections={pageSections}>
        <main className="product-main">
          <div className="product-page-heading">
            <div className="product-title">
              <span className="product-mark">
                <DatabaseOutlined />
              </span>
              <div>
                <h1>拼多多商品数据看板</h1>
                <span className="muted">店铺商品 · 同比经营追踪</span>
              </div>
            </div>
            <Space orientation="vertical" align="end" size={4}>
              <Badge
                status={mode === "preview" ? "warning" : options ? "success" : "default"}
                text={
                  mode === "preview"
                    ? "开发样例 · 非真实经营数据"
                    : options
                      ? "本地数据库 · 真实经营数据"
                      : optionsQuery.isError
                        ? "数据加载失败"
                        : "正在连接数据库"
                }
              />
              <span className="muted">
                {mode === "preview" ? "样例截止" : "已观察数据截至"}：
                {optionsQuery.data?.meta.dataThrough ?? "—"}
                {mode === "api" && (
                  <> · 完整日截至：{optionsQuery.data?.meta.completeThrough ?? "尚未确认"}</>
                )}
              </span>
            </Space>
          </div>
          {import.meta.env.DEV && mode !== "api" && (
            <div className="preview-toolbar">
              <Space wrap>
                <ExperimentOutlined />
                <strong>开发交互预览</strong>
                <Select
                  aria-label="数据模式"
                  value={mode}
                  options={[
                    { value: "api", label: "真实数据库" },
                    { value: "offline", label: "未接入模式" },
                    { value: "preview", label: "开发样例" },
                  ]}
                  onChange={(value: Mode) => switchMode(value)}
                />
                {mode === "preview" && (
                  <Select
                    aria-label="样例状态场景"
                    value={scenario}
                    options={scenarios}
                    onChange={(value) =>
                      setParams((previous) => {
                        const next = new URLSearchParams(previous);
                        next.set("scenario", value);
                        return next;
                      })
                    }
                  />
                )}
              </Space>
              <Typography.Text type="secondary">
                仅前端验证 · 未连接数据库 / 数仓 · 未调用AI
              </Typography.Text>
            </div>
          )}
          {mode === "preview" && (
            <Alert
              className="preview-banner"
              type="warning"
              showIcon
              title="开发样例 · 非真实经营数据"
              description="以下商品、金额、评分与诊断均为合成样例，仅验证页面布局和交互；切换筛选不会查询数仓，评分不代表真实模型结果。"
            />
          )}
          <div className="page-scope">
            <Tag color="blue" variant="filled">
              当前范围
            </Tag>
            <span>{rangeLabel}</span>
            <span className="muted">粒度：店铺 × 商品ID × 日期（非SKU）</span>
            <span className="muted">
              {mode === "preview"
                ? "来源：小型合成开发样例"
                : mode === "api"
                  ? "来源：PostgreSQL已发布数据 · 当前商品资料归属"
                  : "来源：尚未接入"}
            </span>
          </div>
          {mode === "api" && options && (
            <Alert
              showIcon
              type="warning"
              title="按数仓原值展示，不额外扣退款和做单；来源完整日尚未确认。"
              description={`已观察数据不代表全部到齐。数仓网页更新时间以网页为准，本批未登记；最近入库成功：${optionsQuery.data?.meta.lastSuccessAt ?? "暂无记录"}。`}
            />
          )}
          {mode === "api" && optionsQuery.data?.meta.syncStatus === "failed" && (
            <Alert
              type="error"
              showIcon
              title="最近一次入库失败，保留上次成功发布数据"
              description={
                optionsQuery.data.meta.lastError?.message ??
                "请根据任务记录排查来源，页面刷新不会重新拉数。"
              }
            />
          )}
          {mode === "api" && optionsQuery.data?.meta.syncStatus === "running" && (
            <Alert type="info" showIcon title="来源正在更新，当前展示上次成功发布数据" />
          )}
          {versionNotice && (
            <Alert showIcon type="info" title="数据版本已更新，当前条件已统一重新加载。" />
          )}
          {optionsQuery.isError && (
            <Alert
              type="error"
              showIcon
              title="筛选数据加载失败"
              description={optionsQuery.error.message}
              action={<Button onClick={() => void optionsQuery.refetch()}>重试</Button>}
            />
          )}
          {versionLocked ? (
            <Alert
              type="warning"
              showIcon
              title="数据版本连续变化，请稍后重新加载"
              action={
                <Button
                  onClick={() => {
                    versionCoordinator.current.reset();
                    setVersionLocked(false);
                    client.removeQueries({ queryKey: ["products"] });
                    setVersionToken(versionCoordinator.current.epoch);
                  }}
                >
                  重新加载
                </Button>
              }
            />
          ) : (
            <ProductBootstrapContext.Provider
              value={{
                ready: optionsQuery.isSuccess,
                error: mode === "api" ? optionsQuery.error : null,
                notConnected: optionsQuery.data?.availability === "not_connected",
                retry: () => {
                  void optionsQuery.refetch();
                },
              }}
            >
              <ProductDataVersionContext.Provider value={dataVersion}>
                <div key={pageKey}>
                  <BriefSection
                    mode={mode}
                    scenario={scenario}
                    filters={overviewFilters}
                    versionToken={versionToken}
                    onProduct={chooseProduct}
                  />
                  {overviewQuery.data?.data && (
                    <Alert
                      className="health-banner"
                      type={overviewQuery.data.data.health.tone}
                      showIcon
                      title={
                        <>
                          <b>增长健康度</b> · {overviewQuery.data.data.health.title}
                        </>
                      }
                      description={overviewQuery.data.data.health.description}
                    />
                  )}
                  <ScoresSection
                    mode={mode}
                    scenario={scenario}
                    filters={scoreFilters}
                    onChange={changeScores}
                    options={scoreOptions}
                    versionToken={versionToken}
                    scoreVersion={scoreOptionsQuery.data?.meta.scoreVersion}
                    optionsError={scoreOptionsQuery.error}
                  />
                  <OverviewSection
                    query={overviewQuery}
                    filters={overviewFilters}
                    options={options}
                    onChange={changeOverview}
                    onReset={() => reset("overview")}
                    metric={metric}
                    onMetric={setMetric}
                  />
                  <div className="analysis-grid">
                    <TrendSection
                      query={trendQuery}
                      label={metricNames[metric]}
                      comparison={overviewFilters.products.length ? comparison : "year"}
                      hasProducts={overviewFilters.products.length > 0}
                      onComparison={setComparison}
                    />
                    <DiagnosisSection query={diagnosisQuery} />
                  </div>
                  <AlertsSection
                    filters={overviewFilters}
                    mode={mode}
                    scenario={scenario}
                    versionToken={versionToken}
                  />
                  <DaysSection
                    mode={mode}
                    scenario={scenario}
                    filters={dayFilters}
                    onChange={changeDays}
                    onReset={() => reset("days")}
                    options={dayOptions}
                    optionsError={reuseDayOptions ? optionsQuery.error : dayOptionsQuery.error}
                    versionToken={versionToken}
                  />
                </div>
              </ProductDataVersionContext.Provider>
            </ProductBootstrapContext.Provider>
          )}
          <div className="product-footer">
            <span>
              数据完整性：{optionsQuery.data?.meta.isPreview ? "仅样例覆盖" : "尚未核实"} ·
              最近同步成功：
              {mode === "preview"
                ? "样例无真实同步"
                : (optionsQuery.data?.meta.lastSuccessAt ?? "尚无记录")}
            </span>
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => void client.invalidateQueries({ queryKey: ["products"] })}
            >
              刷新当前页面
            </Button>
            <span>页面刷新不会同步上游</span>
          </div>
        </main>
      </DashboardLayout>
    </div>
  );
}
