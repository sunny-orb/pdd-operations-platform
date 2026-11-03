import { memo, useLayoutEffect, useRef, useState } from "react";
import { Empty, Pagination, Spin } from "antd";
import { scorePage, type ScoreCard } from "./contracts";
import { useResource, type ProductsState } from "./data";
import { Filters } from "./Filters";
import { State } from "./State";
import { ScoreDialog } from "./ScoreDialog";
import { formatSignal as format } from "./format";
import type { Scope } from "./scope";

export const Scores = memo(function Scores({
  state,
  scope,
}: {
  state: ProductsState;
  scope: Scope;
}) {
  const queryScope = {
    ...scope,
    level: "新品",
    from: state.metadata.data?.date_from ?? "",
    to: state.metadata.data?.date_to ?? "",
  };
  const result = useResource("scores", scorePage, queryScope, state);
  const [selected, setSelected] = useState<string | null>(null);
  const data = result.data;
  const turningPage = result.isPlaceholderData;
  const grid = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    // 新页从卡片列表顶部开始，保持整个看板的滚动位置。
    if (!turningPage && grid.current) grid.current.scrollTop = 0;
  }, [scope.page, turningPage]);
  return (
    <section className="new-potential-section" id="product-scores">
      <div className="new-potential-heading">
        <div>
          <span>新品潜力四档评分 · 放量决策</span>
          <h2>查看表现档位与放量建议</h2>
          <p>只纳入商品资料明确标记为“新品”的商品。评分区独立筛选，点击卡片查看十项指标。</p>
        </div>
        <div className="new-potential-meta" role="region" aria-label="新品评分概况">
          <b>
            {data?.total ?? "—"} / {data?.total_count ?? "—"} 款新品
          </b>
          <small>
            已计算 {data?.scored_count ?? "—"} 款 · 评分截至：
            {data?.end ?? "—"}
          </small>
        </div>
      </div>
      <details className="potential-guide">
        <summary>评分与放量口径</summary>
        <p>
          新品按当前商品资料认定；数量依次为筛选后的款数和全部新品款数。已计算数量按全部新品统计，包含部分计分，不代表十项都已计分。评分截至日是本次评分的统计结束日期。
        </p>
        <p>
          <b>分数怎么看：</b>
          十项权重合计100分；基础、良好、优秀、突出分别拿到本项权重的25%、50%、75%、100%。例如交易额占18分，达到优秀得13.5分。指标有效但没达到基础线，才记0分。
        </p>
        <p>
          <b>部分得分怎么算：</b>
          暂不能判断的指标不计分，也不把权重分给其他项。已计分60分、待验证16分，不能相加成76分，也不能折算成满分100分的成绩；十项都能判断后，才有完整评分。
        </p>
        <p>
          <b>推广与增长：</b>
          推广三项共16分，近7日曝光至少2,000且字段、分母有效才参与评分；曝光达标不等于自动得分。增长项需比较相邻两个完整7日，前7日至少5单且交易额达到历史基数线；基数合格但没有增长，增长项得0分。
        </p>
        <p>
          <b>放量前先看：</b>
          SOP要求完整评分至少75分，投产和支付转化达到良好、点击转化达到基础，且交易倍数大于1。再由运营确认库存、保本投产和近期转化；每次加预算10%～20%，观察3天，每天复评。投产跌破良好线或保本线、转化下降或缺货时，停止加量并回撤新增预算。75分不是75%的爆款概率，本页不自动调整预算。
        </p>
        <p>
          <b>当前使用说明：</b>
          {data?.status === "trial_active" &&
            "当前仍显示此前试行结果，原版评分标准尚未接入；增长评分和正式放量判断暂未启用，不据现有试行分加预算。"}
          收藏按7日新增累计、未扣取消；推广沿来源支付日汇总。来源完整日尚未确认，当前参照的效果仍待核对。
        </p>
      </details>
      <Filters name="score" scope={queryScope} state={state} />
      <State
        pending={!!state.version && result.isPending}
        error={result.error}
        retry={() => void result.refetch()}
      />
      {data && (
        <>
          <div className="m1-score-groups">
            <span>
              完整评分 <b>{data.groups.complete}</b>
            </span>
            <span>
              待验证 <b>{data.groups.pending}</b>
            </span>
            <span>
              暂未评分 <b>{data.groups.unscored}</b>
            </span>
          </div>
          <Spin spinning={turningPage} tip={`正在加载第 ${scope.page} 页…`}>
            <div
              className="new-potential-grid"
              ref={grid}
              aria-busy={turningPage}
              inert={turningPage}
            >
              {data.items.map((item) => (
                <ScoreTile
                  key={item.listing_id}
                  item={item}
                  open={() => setSelected(item.listing_id)}
                />
              ))}
            </div>
          </Spin>
          {!data.items.length && (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选没有新品记录" />
          )}
          <Pagination
            disabled={turningPage}
            current={scope.page}
            pageSize={12}
            total={data.total}
            showSizeChanger={false}
            showLessItems
            responsive
            size="small"
            onChange={(page) => state.change("score", { page })}
            showTotal={(total) =>
              `共 ${total} 款 · ${data.status === "trial_active" ? "新试行标准已启用 · 待验证" : data.status === "pending_recompute" ? "输入已更新 · 评分待重算" : "标准未接入"}`
            }
          />
        </>
      )}
      {selected && (
        <ScoreDialog
          state={state}
          selected={selected}
          close={() => setSelected(null)}
          scope={queryScope}
        />
      )}
    </section>
  );
});
function ScoreTile({ item, open }: { item: ScoreCard; open: () => void }) {
  return (
    <article
      className="new-potential-card tier-score-card unverified"
      role="button"
      tabIndex={0}
      aria-label={`查看${item.product_name ?? item.product_id}评分档位`}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      }}
    >
      <div className="new-potential-card-heading">
        <div>
          <span>{item.category ?? "未匹配品类"}</span>
          <small>
            {item.shop_name ?? item.shop_id} · {item.operator ?? "运营未匹配"}
          </small>
        </div>
        <b>查看档位 →</b>
      </div>
      <div className="tier-card-body">
        <h3>{item.product_name ?? "未命名商品"}</h3>
        <small>商品ID：{item.product_id}</small>
        <p className="tier-card-status pending">{item.decision_label}</p>
        <p>
          已计分 {format(item.score)} 分 · 待验证权重 {item.pending_weight} 分
        </p>
        <p>
          近7日交易 {format(item.revenue, "currency")} · 访客 {format(item.visitors)} · 支付{" "}
          {format(item.units)} 件
        </p>
        <p>
          曝光 {format(item.ad_impressions)} · ROI {format(item.ad_roi, "ratio")}
        </p>
        <p className="tier-card-context">
          {item.season ?? "季节待验证"} · {item.lifecycle ?? "生命周期待验证"} · {item.action}
        </p>
        <p>
          {item.coverage === "partial"
            ? "近7日缺日或缺字段，当前值为已观察部分"
            : "已观察原值 · 完整日未知"}
        </p>
      </div>
    </article>
  );
}
