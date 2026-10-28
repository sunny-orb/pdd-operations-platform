import { Button, Empty, Tag } from "antd";
import type { Overview } from "./contracts";
import { formatValue } from "./format";
export function Brief({
  data,
  openDetails,
}: {
  data: Overview | undefined;
  openDetails: () => void;
}) {
  return (
    <section className="m5-decision" id="aftersales-decision" aria-label="主风险与优先行动">
      <article className="m5-decision-card m5-main-risk">
        <div className="m5-card-caption">
          主风险 <Tag>{data?.decision.status ?? "待核验"}</Tag>
        </div>
        <h2>{data?.decision.title ?? "等待售后报告"}</h2>
        <strong>{formatValue(data?.decision.rate, "percent")}</strong>
        <p>
          实际入库金额 {formatValue(data?.decision.amount, "currency")} · 金额占比{" "}
          {formatValue(data?.decision.amount_share, "percent")}
        </p>
        <p>较上期 {formatValue(data?.decision.change_pp, "points")}</p>
        <small>{data?.decision.note ?? "正在读取本地报告状态"}</small>
        <Button type="link" onClick={openDetails}>
          查看风险明细 →
        </Button>
      </article>
      {data?.priorities.length ? (
        data.priorities.slice(0, 3).map((item, index) => (
          <article className="m5-decision-card m5-priority" key={item.id}>
            <div className="m5-card-caption">
              优先行动 {index + 1} · {item.kind}
            </div>
            <h3>{item.title}</h3>
            <p>{item.detail}</p>
            <small>{item.verification}</small>
            <Button type="link" onClick={openDetails}>
              查看整改清单 →
            </Button>
          </article>
        ))
      ) : (
        <article className="m5-decision-card m5-empty-action">
          <h3>优先行动</h3>
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可核查行动" />
          <p>缺少相容来源时不生成风险分、根因或人工处理进度。</p>
        </article>
      )}
    </section>
  );
}
