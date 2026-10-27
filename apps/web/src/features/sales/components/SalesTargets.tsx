import { Card, Progress, Button } from "antd";
import { detailPath, targetsSchema, useSalesDetail } from "../api/details";
import { money, percent } from "../format";
export function SalesTargets({
  month,
  version,
  onConflict,
}: {
  month: string;
  version?: string;
  onConflict: () => void;
}) {
  const { data, error, retry } = useSalesDetail(
    detailPath("targets", version, { month }),
    targetsSchema,
    onConflict,
  );
  const d = data?.data;
  return (
    <section id="targets" className="target-grid" aria-label="目标完成进度">
      {[
        {
          kind: "monthly",
          title: "本月 GMV 目标进度",
          badge: "目标跟踪",
          actual: d?.month_actual,
          target: d?.month_target,
          rate: d?.month_ratio,
          note: `月累计 GMV · ${month || "—"}${d?.month_missing_days ? ` · 缺${d.month_missing_days}店铺日` : ""}`,
          footer: "月度目标",
        },
        {
          kind: "annual",
          title: "年度 GMV 目标进度",
          badge: "年度累计",
          actual: d?.annual_actual,
          target: d?.annual_target,
          rate: d?.annual_ratio,
          note: `2026.02–2027.01 累计 GMV${d?.annual_missing_days ? ` · 缺${d.annual_missing_days}店铺日` : ""}`,
          footer: "年度目标",
        },
      ].map((x) => (
        <Card key={x.kind} className={`target-card ${x.kind}-target`}>
          <div className="target-title">
            <span>{x.title}</span>
            <b>{x.badge}</b>
          </div>
          <div className="target-metric">
            <strong>{money(x.actual)}</strong>
            <span>{x.note}</span>
          </div>
          <Progress
            percent={x.rate == null ? 0 : Math.min(100, Number(x.rate) * 100)}
            showInfo={false}
            strokeColor={x.kind === "annual" ? "#15805d" : undefined}
            aria-label={`${x.footer}${x.target == null ? "来源不可用" : ""}`}
          />
          <div className="target-footer">
            <span>
              {x.footer} {money(x.target)}
            </span>
            <strong>{percent(x.rate)}</strong>
          </div>
        </Card>
      ))}
      <Card className="target-card target-summary">
        <span>本月目标缺口</span>
        <strong>{money(d?.gap)}</strong>
        <p>
          {error ?? "目标原入口当前不可用；已展示真实月/年累计，缺口与达成率等待有效目标输入。"}
        </p>
        {error && (
          <Button size="small" onClick={retry}>
            重试目标数据
          </Button>
        )}
        <a href="https://bi.kktree.cn/" target="_blank" rel="noreferrer">
          查看店铺销售原始数据 ↗
        </a>
      </Card>
    </section>
  );
}
