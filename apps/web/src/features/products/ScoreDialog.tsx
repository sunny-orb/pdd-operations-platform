import { Button, Empty, Modal } from "antd";
import { card, type ScoreCard } from "./contracts";
import { useResource, type ProductsState } from "./data";
import { State } from "./State";
import { formatSignal as format } from "./format";
import type { Scope } from "./scope";
import "./ScoreDialog.css";

type Signal = ScoreCard["signals"][number];
const tiers = [
  ["basic", "基础", "25%"],
  ["good", "良好", "50%"],
  ["excellent", "优秀", "75%"],
  ["outstanding", "突出", "100%"],
] as const;

/** 仅按接口是否计分及档位设置展示状态，不在前端重算档位或总分。 */
function signalTone(signal: Signal) {
  if (signal.points == null) return "inactive";
  return signal.grade === "未达基础" ? "missed" : "met";
}

export function ScoreDialog({
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
  return (
    <Modal
      open
      centered
      title={
        <div className="m1-score-detail-heading">
          <span>新品四档评分 · 放量决策</span>
          <h2>{item ? (item.product_name ?? item.product_id) : "新品评分详情"}</h2>
          {item && (
            <>
              <p>
                商品ID：{item.product_id} · {item.category ?? "品类未填写"} ·{" "}
                {item.shop_name ?? item.shop_id} · {item.operator ?? "运营未填写"}
              </p>
              <p>
                评分期间：{item.start} 至 {item.end}
              </p>
            </>
          )}
        </div>
      }
      onCancel={close}
      closable={{ "aria-label": "关闭评分详情" }}
      footer={
        <div className="m1-score-detail-footer">
          <span>
            75分是执行门槛，不代表爆款概率。加预算前仍需核对库存、保本投产和近期转化；本页不自动调整预算。
          </span>
          <Button type="primary" autoInsertSpace={false} onClick={close}>
            关闭
          </Button>
        </div>
      }
      width={1220}
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
      {item && <ScoreDetails item={item} />}
    </Modal>
  );
}

function ScoreDetails({ item }: { item: ScoreCard }) {
  const counted = item.signals.filter((signal) => signal.points != null);
  const met = counted.filter((signal) => !/^0(?:\.0*)?$/.test(signal.points!)).length;
  // 合并重复说明，保留所有接口原因；避免同一句技术说明占满十行表格。
  const explanations = new Map<string, string[]>();
  for (const signal of item.signals) {
    if (signal.reason)
      explanations.set(signal.reason, [...(explanations.get(signal.reason) ?? []), signal.label]);
  }
  return (
    <>
      <div className="m1-score-detail-summary">
        <div>
          <span>已计分</span>
          <b>{format(item.score)} 分</b>
          <small>已计分权重 {counted.reduce((sum, signal) => sum + signal.weight, 0)} / 100</small>
        </div>
        <div>
          <span>待验证权重</span>
          <b>{format(item.pending_weight)} 分</b>
          <small>{item.signals.length - counted.length} 项暂未计分，不按0分处理</small>
        </div>
        <div>
          <span>完整综合评分</span>
          <b>{item.complete_score == null ? "—" : `${format(item.complete_score)} / 100`}</b>
          <small>{item.complete_score == null ? "缺项不折算成满分" : item.decision_label}</small>
        </div>
        <div>
          <span>有效基础达标信号</span>
          <b>
            {met} / {counted.length} 项
          </b>
          <small>
            {[item.season, item.lifecycle].filter(Boolean).join(" · ") || "只统计已计分指标"}
          </small>
        </div>
      </div>
      <div className="m1-score-detail-note">
        <b>{item.decision_label}</b>
        <span>{item.action}</span>
      </div>
      <div className="m1-score-detail-note">
        <b>四档规则</b>
        <span>
          基础、良好、优秀、突出分别得本项权重的25%、50%、75%、100%。达到哪档就拿哪档分；有效值为0或低于基础线得0分。暂未计分的权重不分给其他项，部分得分不折算成满分。
        </span>
      </div>
      <div className="m1-score-detail-note">
        <b>历史参照</b>
        <span>
          当前使用试行参照，优先参考同品类；样本不足时参考全品类。每项实际参照和样本数见表内，详细说明可在下方展开。
        </span>
      </div>
      <div
        className="m1-score-detail-table-wrap"
        tabIndex={0}
        role="region"
        aria-label="评分指标明细，可横向滚动"
      >
        <table className="m1-score-detail-table" aria-label="评分指标明细">
          <colgroup>
            <col className="m1-score-detail-reference-col" />
            <col className="m1-score-detail-weight-col" />
            <col />
            {tiers.map(([key]) => (
              <col key={key} />
            ))}
            <col />
            <col />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">评分指标 / 历史参照</th>
              <th scope="col">权重</th>
              <th scope="col">近7日当前值</th>
              {tiers.map(([key, label, weight]) => (
                <th scope="col" key={key}>
                  {label} ≥<small>{weight}权重</small>
                </th>
              ))}
              <th scope="col">当前档位</th>
              <th scope="col">实际得分</th>
            </tr>
          </thead>
          <tbody>
            {item.signals.map((signal) => (
              <tr key={signal.code} className={`m1-score-detail-${signalTone(signal)}`}>
                <th scope="row">
                  <b>{signal.label}</b>
                  <small>
                    {signal.reference_group ?? "暂无历史参照"} · 样本 {format(signal.sample_count)}{" "}
                    款
                  </small>
                </th>
                <td>{signal.weight}</td>
                <td>{format(signal.value, signal.type)}</td>
                {tiers.map(([key]) => (
                  <td key={key}>{format(signal[key], signal.type)}</td>
                ))}
                <td>
                  <span className="m1-score-detail-grade">
                    {signal.points == null ? "暂未计分" : (signal.grade ?? "—")}
                  </span>
                </td>
                <td>
                  {signal.points == null ? "—" : `${format(signal.points)} / ${signal.weight}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="m1-score-detail-note">
        <b>增长与口径</b>
        <span>
          增长项需两个完整7日，前7日至少5单且交易额达到历史基数线。当前增长评分尚未启用；近7日曝光不足2,000时，推广三项不计分。
        </span>
      </div>
      <details className="m1-score-detail-explanations">
        <summary>评分说明</summary>
        <p>原版评分标准尚未接入，当前仍是此前试行结果，暂不据此增加预算。{item.reason}。</p>
        <p>
          试行参照使用已接收的当期60日，训练窗口截止评分日前14日。每个店铺商品先取完整7日特征的中位数；同品类至少12款，否则改用至少24款的全品类参照，仍不足则不计分。季节与阶段不猜测。
        </p>
        <p>
          收藏为7日新增累计，未扣取消，也不是跨日去重人数；推广成交沿来源支付日汇总，不重新归因。来源完整日及参照效果仍待核对。
        </p>
        <ul>
          {[...explanations].map(([reason, labels]) => (
            <li key={reason}>
              <b>{labels.join("、")}：</b>
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}
