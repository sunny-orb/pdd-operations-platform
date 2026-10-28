import { Card, Progress, Button } from "antd";
import { detailPath, targetsSchema, useSalesDetail } from "../api/details";
import { money, percent } from "../format";
import { TARGET_SOURCE } from "../sourceLinks";
export function SalesTargets({
  month,
  version,
  onConflict,
  statusKey,
}: {
  month: string;
  version?: string;
  onConflict: () => void;
  statusKey?: string;
}) {
  const { data, error, retry } = useSalesDetail(
    detailPath("targets", version, { month }),
    targetsSchema,
    onConflict,
    statusKey,
  );
  const d = data?.data;
  return (
    <section id="targets" className="target-grid" aria-label="目标完成进度">
      {[
        {
          kind: "monthly",
          title: "本月 GMV 目标进度",
          badge: d?.target_basis_label ?? "基础目标",
          actual: d?.month_actual,
          target: d?.month_target,
          rate: d?.month_ratio,
          note: `月累计 GMV · ${month || "—"}${d?.month_missing_days ? ` · 缺${d.month_missing_days}店铺日` : ""}`,
          footer: "月度目标",
        },
        {
          kind: "annual",
          title: "年度 GMV 目标进度",
          badge: "年度累计 · 基础目标",
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
            aria-label={`${x.footer}${x.target == null ? "尚无相容目标" : "基础目标 · 观察参考进度"}`}
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
          {error ?? d?.note ?? "正在读取基础目标及数仓完成额；未取得目标时达成率与缺口保留缺项。"}
        </p>
        {d?.target_coverage && (
          <p>
            基础目标 · 财年已接 {d.target_coverage.available_months.length}/12 月
            {d.target_coverage.missing_months.length
              ? `，缺 ${d.target_coverage.missing_months.join("、")}`
              : ""}
            ；固定六店渠道范围，不按单店分摊。
          </p>
        )}
        {error && (
          <Button size="small" onClick={retry}>
            重试目标数据
          </Button>
        )}
        <a href="https://bi.kktree.cn/" target="_blank" rel="noreferrer">
          查看店铺销售原始数据 ↗
        </a>
        <a href={TARGET_SOURCE} target="_blank" rel="noreferrer">
          查看基础目标来源 ↗
        </a>
      </Card>
    </section>
  );
}
