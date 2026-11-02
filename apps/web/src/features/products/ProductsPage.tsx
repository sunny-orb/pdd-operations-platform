import { useState } from "react";
import { Alert, Button, Spin, Tooltip } from "antd";
import {
  BarChartOutlined,
  BulbOutlined,
  LineChartOutlined,
  TableOutlined,
  WarningOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { DashboardLayout } from "../../shared/layouts/DashboardLayout";
import { board } from "./contracts";
import { ApiError } from "./api";
import { useDebounced, useProducts, useResource } from "./data";
import { Filters } from "./Filters";
import { Scores } from "./Scores";
import { Days } from "./Days";
import { Brief, Health, Metrics, Risks, Trends } from "./Overview";
import { useFilterScrollAnchor, useOverviewPresentation } from "./useOverviewPresentation";
import { localTime } from "./format";
import "./prototype.css";
import "./products.css";

/** 商品整页：原型六区，三套独立URL筛选；只消费本地已发布API。 */
export function ProductsPage() {
  const state = useProducts();
  const main = state.scope("main");
  const keyword = useDebounced(main.keyword);
  const requestScope = { ...main, keyword };
  const result = useResource("overview", board, requestScope, state);
  const [active, setActive] = useState(["revenue"]);
  const presentation = useOverviewPresentation(result.data, requestScope, state.version);
  const { data, scope: shownScope } = presentation;
  const updating = result.isFetching || keyword !== main.keyword;
  const unavailable = updating || !!result.error;
  const { ref: filterAnchorRef, capture: captureFilterPosition } = useFilterScrollAnchor(
    result.data,
    !updating,
  );
  const meta = state.metadata.data;
  const toggle = (key: string) =>
    setActive((keys) =>
      keys.includes(key)
        ? keys.length === 1
          ? keys
          : keys.filter((k) => k !== key)
        : [...keys, key],
    );
  return (
    <DashboardLayout
      currentPage="products"
      context="商品数据 · 本页独立分析"
      sections={[
        { id: "product-brief", title: "经营结论", icon: <BulbOutlined /> },
        { id: "product-scores", title: "新品评分", icon: <ThunderboltOutlined /> },
        { id: "overview", title: "经营总览", icon: <BarChartOutlined /> },
        { id: "product-diagnosis", title: "经营诊断", icon: <LineChartOutlined /> },
        { id: "risk", title: "风险清单", icon: <WarningOutlined /> },
        { id: "product-days", title: "商品明细", icon: <TableOutlined /> },
      ]}
    >
      <div className="m1-products-scope">
        <main className="m1-dashboard">
          <header className="topbar">
            <div className="brand">
              <span className="mark">数</span>
              <div>
                <b>拼多多商品数据看板</b>
                <small>店铺商品 · 同比经营追踪</small>
              </div>
            </div>
            <Tooltip
              trigger={["hover", "focus"]}
              title={
                <div>
                  <p>最近同步成功：{localTime(meta?.success_at)}；同步时间不代表业务数据日期。</p>
                  <p>
                    完整截至日：{meta?.complete_through ?? "尚未确认"}。
                    {meta?.complete_through == null &&
                      "尚未取得上游每日齐数的确认依据：日终采集完成标记、应交付范围和补录修订截止规则；不代表已经发现漏数。"}
                  </p>
                  <p>运营、品类、等级按当前商品资料分组；查看历史日期时，不代表当时的归属。</p>
                  <p>热卖榜按已接收记录排序；源库对账一致不等于上游已完成日终全部采集。</p>
                  {meta?.sources.map((s) => (
                    <p key={s.dataset}>
                      {s.table} · 观察截至 {s.observed_through ?? "资料快照"} · 接收{" "}
                      {localTime(s.received_at)}
                    </p>
                  ))}
                  <p>最近任务：{meta?.latest_attempt.status ?? "未接收"}</p>
                </div>
              }
            >
              <div className="updated" tabIndex={0}>
                数据截止：{meta?.date_to ?? "—"}
              </div>
            </Tooltip>
          </header>
          {state.metadata.error && (
            <Alert
              type="error"
              showIcon
              title="商品数据尚未接通"
              description="已保留页面字段与布局；当前无可用本地发布版本，不显示模拟经营数字。"
              action={<Button onClick={() => void state.metadata.refetch()}>重试连接</Button>}
            />
          )}
          {meta?.latest_attempt.status === "failed" && (
            <Alert
              type="warning"
              showIcon
              title="最近接收失败，当前仍展示上次成功发布的数据"
              description={`成功时间 ${localTime(meta.success_at)}；完整日未知。`}
            />
          )}
          <Brief
            data={data}
            updating={unavailable}
            onSelect={(patch) => {
              state.change("main", { ...patch, page: 1 });
              document
                .getElementById("overview")
                ?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
          />
          <Health data={data} />
          <Scores state={state} scope={state.scope("score")} />
          <section id="overview" className="m1-overview">
            <div
              className="m1-main-filters"
              ref={filterAnchorRef}
              onPointerDownCapture={captureFilterPosition}
              onKeyDownCapture={(event) => {
                if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key))
                  captureFilterPosition();
              }}
            >
              <Filters
                name="main"
                scope={main}
                state={state}
                onBeforeChange={captureFilterPosition}
                status={
                  updating ? (
                    <>
                      <Spin size="small" delay={150} />
                      <span>
                        {data ? "正在更新筛选结果，暂显示上次结果。" : "正在加载筛选结果…"}
                      </span>
                    </>
                  ) : result.error ? (
                    <>
                      <span>
                        {result.error instanceof ApiError && result.error.status === 422
                          ? result.error.message
                          : "结果加载失败。"}
                        {data ? "仍显示上次结果。" : "请重试。"}
                      </span>
                      <Button
                        size="small"
                        onClick={() => {
                          captureFilterPosition();
                          void result.refetch();
                        }}
                      >
                        重试
                      </Button>
                    </>
                  ) : null
                }
              />
            </div>
            <div aria-busy={updating}>
              <div className="product-bar">
                <div>
                  <span>商品信息</span>
                  <b>
                    {shownScope.products.length
                      ? `已选 ${shownScope.products.length} 款`
                      : "全部商品"}
                  </b>
                  <small>{data?.product_count ?? "—"} 个店铺商品链接</small>
                </div>
                <div>
                  <span>店铺名称</span>
                  <b>{data?.selection.shop_name ?? (shownScope.shop ? "所选店铺" : "全部店铺")}</b>
                </div>
                <div>
                  <span>款式对应运营</span>
                  <b>{shownScope.operator || "全部运营"}</b>
                </div>
                <div>
                  <span>品类</span>
                  <b>{shownScope.category || "全部品类"}</b>
                </div>
                <div>
                  <span>产品等级</span>
                  <b>{shownScope.level || "全部等级"}</b>
                </div>
              </div>
              <Metrics data={data} active={active} toggle={toggle} />
            </div>
          </section>
          <Trends data={data} active={active} />
          <Risks data={data} />
          <Days state={state} scope={state.scope("detail")} />
          <footer className="m1-source-footer">
            来源：数仓商品日经营、商品推广、商品上架资料。原值不额外扣退款或做单。收藏为累计新增收藏量（未扣取消），不代表净增或跨日去重人数；推广沿来源已归因的支付日汇总，不重算点击归因；完整日仍待补证。
          </footer>
        </main>
      </div>
    </DashboardLayout>
  );
}
