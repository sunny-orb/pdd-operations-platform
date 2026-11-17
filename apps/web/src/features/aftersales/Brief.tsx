import { Button } from "antd";
import { formatFixedDecimal, isNegative } from "../../shared/format/decimal";
import type { Overview } from "./contracts";
import { Explanation } from "./Explanation";

type DetailTarget = "aftersales-risk-overview" | "aftersales-action-list";

export function Brief({
  data,
  openDetails,
}: {
  data: Overview | undefined;
  openDetails: (target: DetailTarget) => void;
}) {
  const decision = data?.decision;
  const rate = formatFixedDecimal(decision?.rate, 1, 2);
  const amount = formatFixedDecimal(decision?.amount, 2, 0, true);
  const amountShare = formatFixedDecimal(decision?.amount_share, 1, 2);
  const change = formatFixedDecimal(decision?.change_pp, 1);
  const status =
    !decision?.status || decision.status === "pending_validation" ? "待验证" : decision.status;

  return (
    <section className="m5-decision" id="aftersales-decision" aria-label="主风险与优先行动">
      <article className="m5-decision-card m5-main-risk">
        <div className="m5-card-caption">
          <span>主风险</span>
          <span className={`m5-decision-badge${status === "预警 P1" ? " is-risk" : ""}`}>
            {status}
          </span>
        </div>
        <h3>{decision?.title ?? "等待售后报告"}</h3>
        <strong>{rate === null ? "—" : `${rate}%`}</strong>
        <p>总退款率 · 影响金额 {amount ?? "—"}</p>
        <p title={decision?.note}>
          {change === null
            ? "较上期 —"
            : change === "0.0"
              ? "较上期持平"
              : `较上期${isNegative(decision?.change_pp) ? "下降" : "上升"} ${change.replace(/^-/, "")} 个百分点`}
          {"，退款金额占比 "}
          {amountShare === null ? "—" : `${amountShare}%`}
        </p>
        <Button type="link" onClick={() => openDetails("aftersales-risk-overview")}>
          查看风险明细 →
        </Button>
      </article>
      {[1, 2, 3].map((slot) => {
        const item = data?.priorities[slot - 1];
        return (
          <article className="m5-decision-card m5-priority" key={slot}>
            <div className="m5-card-caption">
              <span>优先行动 {slot}</span>
            </div>
            <h3>{item?.title || "暂无优先行动"}</h3>
            <p>{item?.detail || "—"}</p>
            {item?.verification && (
              <Explanation label="验证指标">
                <p>{item.verification}</p>
              </Explanation>
            )}
            <Button type="link" onClick={() => openDetails("aftersales-action-list")}>
              查看整改清单 →
            </Button>
          </article>
        );
      })}
    </section>
  );
}
