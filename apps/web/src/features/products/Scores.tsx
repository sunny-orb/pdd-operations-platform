import { useState } from "react";
import { Empty, Modal, Pagination, Table } from "antd";
import { card, scorePage, type ScoreCard } from "./contracts";
import { useResource, type ProductsState } from "./data";
import { Filters } from "./Filters";
import { State } from "./State";
import { formatSignal as format } from "./format";
import type { Scope } from "./scope";

export function Scores({ state, scope }: { state: ProductsState; scope: Scope }) {
  const queryScope = {
    ...scope,
    level: "新品",
    from: state.metadata.data?.date_from ?? "",
    to: state.metadata.data?.date_to ?? "",
  };
  const result = useResource("scores", scorePage, queryScope, state);
  const [selected, setSelected] = useState<string | null>(null);
  const data = result.data;
  return (
    <section className="new-potential-section" id="product-scores">
      <div className="new-potential-heading">
        <div>
          <span>新品潜力四档评分 · 放量决策</span>
          <h2>查看表现档位与放量建议</h2>
          <p>只纳入商品资料明确标记为“新品”的商品。评分区独立筛选，点击卡片查看十项指标。</p>
        </div>
        <div className="potential-meta">
          <b>{data?.total ?? "—"} 款新品</b>
          <small>近7日截至 {data?.end ?? "—"}</small>
          <small>当前资料归属 · 完整日未知</small>
        </div>
      </div>
      <details className="potential-guide">
        <summary>评分与放量口径</summary>
        <p>
          十项权重合计100分；基础、良好、优秀、突出分别计单项25%、50%、75%、100%。缺项不计零、不重分权重；缺少相容四档线时保留原值。收藏定义、广告归因及完整日未知会限制评分和放量。75分不是爆款概率，不自动调整预算。
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
              完整验证 <b>{data.groups.complete}</b>
            </span>
            <span>
              待验证 <b>{data.groups.pending}</b>
            </span>
            <span>
              暂未评分 <b>{data.groups.unscored}</b>
            </span>
          </div>
          <div className="new-potential-grid">
            {data.items.map((item) => (
              <ScoreTile
                key={item.listing_id}
                item={item}
                open={() => setSelected(item.listing_id)}
              />
            ))}
          </div>
          {!data.items.length && (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选没有新品记录" />
          )}
          <Pagination
            current={scope.page}
            pageSize={12}
            total={data.total}
            showSizeChanger={false}
            showLessItems
            responsive
            size="small"
            onChange={(page) => state.change("score", { page })}
            showTotal={(total) => `共 ${total} 款 · 标准未接入`}
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
}
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
        <p className="tier-card-status pending">暂未评分 · 缺少相容四档标准</p>
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
function ScoreDialog({
  state,
  selected,
  close,
  scope,
}: {
  state: ProductsState;
  selected: string;
  close: () => void;
  scope: Scope;
}) {
  const result = useResource(`scores/${selected}`, card.nullable(), scope, state);
  const item = result.data;
  const columns = [
    {
      title: "评分指标 / 历史参照",
      key: "label",
      width: 205,
      render: (_: unknown, r: ScoreCard["signals"][number]) => (
        <>
          <b>{r.label}</b>
          <small>{r.reference_group ?? "历史参照未接入"}</small>
          <small>样本数 {r.sample_count ?? "—"}</small>
        </>
      ),
    },
    { title: "权重", dataIndex: "weight", width: 65, render: (v: number) => `${v}分` },
    {
      title: "近7日当前值",
      key: "value",
      width: 120,
      render: (_: unknown, r: ScoreCard["signals"][number]) => format(r.value, r.type),
    },
    ...(
      [
        ["basic", "基础 ≥25%"],
        ["good", "良好 ≥50%"],
        ["excellent", "优秀 ≥75%"],
        ["outstanding", "突出 ≥100%"],
      ] as const
    ).map(([key, title]) => ({
      title,
      key,
      width: 100,
      render: (_: unknown, r: ScoreCard["signals"][number]) => format(r[key], r.type),
    })),
    {
      title: "当前档位",
      dataIndex: "grade",
      width: 90,
      render: (v: string | null) => v ?? "待验证",
    },
    {
      title: "实际得分",
      dataIndex: "points",
      width: 90,
      render: (v: string | null) => (v == null ? "—" : `${format(v)}分`),
    },
  ];
  return (
    <Modal
      open
      title="新品潜力四档评分 · 指标详情"
      onCancel={close}
      footer={null}
      width={1180}
      getContainer={false}
      className="m1-score-dialog"
      destroyOnHidden
    >
      <State
        pending={!!state.version && result.isPending}
        error={result.error}
        retry={() => void result.refetch()}
      />
      {result.isSuccess && !item && (
        <Empty description="当前版本没有该新品对象，请关闭后重新筛选" />
      )}
      {item && (
        <>
          <div className="potential-modal-heading">
            <h2>{item.product_name ?? item.product_id}</h2>
            <p>
              {item.shop_name} · {item.category ?? "品类待匹配"} · {item.operator ?? "运营待匹配"} ·{" "}
              {item.start} 至 {item.end}
            </p>
          </div>
          <div className="tier-score-modal-summary">
            <div>
              <span>已计分得分</span>
              <b>{format(item.score)} / 100</b>
              <small>相容标准未接入</small>
            </div>
            <div>
              <span>待验证权重</span>
              <b>{item.pending_weight} 分</b>
              <small>10 项指标待验证</small>
            </div>
            <div>
              <span>完整评分</span>
              <b>{format(item.complete_score)}</b>
              <small>暂未评分</small>
            </div>
            <div>
              <span>达标 / 有效指标</span>
              <b>— / —</b>
              <small>不以缺项作零分</small>
            </div>
          </div>
          <div className="potential-detail-note">
            <b>放量判断：暂未评分</b>
            <p>{item.reason}。当前原值仅代表已接收观察记录，完整日未知。</p>
          </div>
          <Table
            rowKey="code"
            size="small"
            pagination={false}
            scroll={{ x: 1070 }}
            columns={columns}
            dataSource={item.signals}
          />
          <div className="m1-score-notes">
            <p>
              <b>四档计分：</b>
              基础/良好/优秀/突出分别获得本项权重的25%/50%/75%/100%；没有标准时不猜测分档。
            </p>
            <p>
              <b>历史参照：</b>
              评分标准尚未完成商品身份与适用范围核验；不将历史商品试算线扩为全品类通用。冲锋衣临时ROI线须有适用标准才可启用。
            </p>
            <p>
              <b>推广门槛：</b>
              近7日曝光不足2,000时，广告三项不计分；归因日期/窗口待核，源比率仅作参考。
            </p>
            <p>
              <b>增长基数：</b>
              近7日交易倍数需相邻两个7日观察齐全、前期订单不少于5且金额正基数，历史基数线还需相容标准。收藏日值可加性待核。
            </p>
            <p>75分是执行门槛，不代表爆款概率；本页不自动调整预算或生成处理进度。</p>
          </div>
        </>
      )}
    </Modal>
  );
}
