import { Alert, Button, Card, Spin, Tag } from "antd";
import { Link } from "react-router-dom";
import type { DomainState } from "./data";
import { domains, formatMetric, type DomainId, type Field } from "./model";

const fallbackPairs: Record<DomainId, string[]> = {
  sales: ["最新入仓日 GMV", "月度目标", "目标达成", "最近更新"],
  products: ["日环比", "同比", "观察异常对象", "最近更新"],
  promotion: ["花费", "交易额", "CTR / CVR", "最近更新"],
  aftersales: ["退款总额", "售后退款", "观察异常对象", "最近更新"],
};

function fieldDisplay(field: Field) {
  const change = field.label === "日环比" || field.label === "同比";
  const value =
    field.type === "text"
      ? (field.value ?? "—")
      : formatMetric(field.value, field.type, field.type === "percent" ? 1 : undefined);
  const positive = field.value != null && !field.value.startsWith("-") && /[1-9]/.test(field.value);
  return {
    label: field.label === "观察异常对象" ? "异常数" : field.label,
    value: change && positive && value !== "—" ? `+${value}` : value,
    unit: field.label === "观察异常对象" && field.value != null ? " 项" : "",
    className:
      field.value == null
        ? undefined
        : field.label === "目标达成" || (change && positive)
          ? "m6-up"
          : change && field.value.startsWith("-")
            ? "m6-down"
            : undefined,
  };
}

/** 四域共用的经营卡；只格式化负责域字段，不取数、不推定风险级别。 */
export function DomainCard({ state }: { state: DomainState }) {
  const card = state.data?.card;
  const domain = domains.find((d) => d.id === state.id)!;
  const pairs: Field[] = card?.pairs.length
    ? card.pairs
    : fallbackPairs[state.id].map((label) => ({ label, value: null, type: "text" as const }));
  return (
    <Card id={`domain-${state.id}`} className={`m6-domain-card m6-${state.id}`}>
      <div className="m6-card-head">
        <span>{card?.title ?? domain.label}</span>
        <Tag>{card?.status ?? (state.loading ? "读取中" : "暂无数据")}</Tag>
      </div>
      <h3>{card?.heading ?? `${domain.label}看板`}</h3>
      <div className="m6-metric-line">
        <strong>
          {card
            ? `${state.id === "promotion" ? "ROI " : ""}${formatMetric(card.value, card.valueType)}`
            : "—"}
        </strong>
        <span>{card?.valueLabel ?? "暂无数据"}</span>
      </div>
      <div className="m6-metric-pairs">
        {pairs.map((field) => {
          const display = fieldDisplay(field);
          return (
            <p key={field.label}>
              <span>{display.label}</span>
              <b className={display.className}>
                {display.value}
                {display.unit}
              </b>
            </p>
          );
        })}
      </div>
      {state.loading && (
        <div className="m6-card-state">
          <Spin size="small" /> 正在读取数据
        </div>
      )}
      {state.error && (
        <Alert
          type="error"
          showIcon
          title={state.error}
          action={
            <Button size="small" onClick={state.refresh}>
              重试
            </Button>
          }
        />
      )}
      <footer>
        <small>{card?.sourceNote ?? "来源及期间见下方口径说明"}</small>
        <Link to={card?.link ?? domain.link}>
          {state.id === "sales" ? "查看数据" : "进入处理"} →
        </Link>
      </footer>
    </Card>
  );
}
