import { Card, Progress, Button } from "antd";
import { detailPath, targetsSchema, useSalesDetail } from "../api/details";
import { money, percent } from "../format";
import { TARGET_SOURCE } from "../sourceLinks";
import "./sales-targets.css";

/** 目标三卡仅格式化接口值；说明放在卡片外，展开不改变三卡高度。 */
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
    <section id="targets" className="sales-targets" aria-label="目标完成进度">
      <div className="target-grid">
        {[
          {
            kind: "monthly",
            title: "本月 GMV 目标进度",
            badge: "目标跟踪",
            actual: d?.month_actual,
            target: d?.month_target,
            rate: d?.month_ratio,
            note: "月累计 GMV",
            footer: "月度目标",
          },
          {
            kind: "annual",
            title: "年度 GMV 目标进度",
            badge: "年度累计",
            actual: d?.annual_actual,
            target: d?.annual_target,
            rate: d?.annual_ratio,
            note: "2026.02–2027.01 累计 GMV",
            footer: "年度目标",
          },
        ].map((x) => (
          <Card key={x.kind} className={`target-card ${x.kind}-target`}>
            <div className="target-title">
              <span>{x.title}</span>
              <b>{x.badge}</b>
            </div>
            <div className="target-metric">
              <strong>{x.actual === null ? "暂无数据" : money(x.actual)}</strong>
              <span>{x.note}</span>
            </div>
            {x.rate == null ? (
              <div className="target-progress target-progress-empty" aria-hidden="true" />
            ) : (
              <Progress
                className="target-progress"
                size={{ height: 9 }}
                percent={Math.min(100, Number(x.rate) * 100)}
                showInfo={false}
                strokeColor={x.kind === "annual" ? "var(--sales-green)" : "var(--sales-blue)"}
                railColor="#e9eef5"
                aria-label={`${x.footer}完成率`}
                aria-valuetext={percent(x.rate, 2)}
              />
            )}
            <div className="target-footer">
              <span>
                {x.footer} {money(x.target)}
              </span>
              <strong>{x.actual === null ? "暂无数据" : percent(x.rate, 2)}</strong>
            </div>
          </Card>
        ))}
        <Card className="target-card target-summary">
          <span>本月目标缺口</span>
          <strong>{d?.month_actual === null ? "暂无数据" : money(d?.gap)}</strong>
          <p>
            {error ??
              (!d
                ? "正在读取目标进度…"
                : d.month_actual === null
                  ? "本月累计期间暂无销售数据。"
                  : d.month_target === null
                    ? "暂未取得本月目标，完成率与缺口暂不可用。"
                    : "本月目标减去已累计销售金额，已达标时缺口为0。")}
          </p>
          {d?.target_source?.state === "failed_retained" && (
            <p>目标最近更新失败，当前显示上次成功数据。</p>
          )}
          {error && (
            <Button size="small" onClick={retry}>
              重试目标数据
            </Button>
          )}
          <a href="https://bi.kktree.cn/" target="_blank" rel="noreferrer">
            查看店铺销售原始数据 ↗
          </a>
        </Card>
      </div>
      {d && (
        <details className="target-details">
          <summary>查看目标口径与数据说明</summary>
          {d.monthly_window && (
            <p>
              本月累计：{d.monthly_window.start} 至 {d.monthly_window.end}；不随自定义日期区间变化。
            </p>
          )}
          {d.annual_window && (
            <p>
              年度累计：{d.annual_window.start} 至 {d.annual_window.end}。
            </p>
          )}
          {[
            {
              label: "本月",
              actual: d.month_actual,
              missing: d.month_missing_days,
              target: d.month_target,
            },
            {
              label: "年度",
              actual: d.annual_actual,
              missing: d.annual_missing_days,
              target: d.annual_target,
            },
          ].map((period) => (
            <div key={period.label}>
              <p>
                {period.label}销售记录：
                {period.actual === null
                  ? "累计期间无销售记录。"
                  : period.missing == null
                    ? "尚未取得记录覆盖情况。"
                    : period.missing > 0
                      ? `缺少 ${period.missing} 条店铺日销售记录，已累计其余记录的金额（同一家店同一天计 1 条）。`
                      : "累计期间各店每日均有销售记录。"}
              </p>
              {period.target === null ? (
                <p>
                  尚未取得可用的{period.label}目标，{period.label}完成率暂不计算。
                </p>
              ) : Number(period.target) === 0 ? (
                <p>{period.label}目标为0，不计算完成率。</p>
              ) : null}
            </div>
          ))}
          <p>
            完成额累计已有销售金额，不补零、不重复扣退款；完成率＝完成额÷目标；缺口＝目标－完成额，最低为0。
            整个累计期间无销售记录时，完成额、完成率和缺口显示“暂无数据”。
          </p>
          {d.target_coverage && (
            <p>
              {d.target_basis_label ?? "原版 GMV 目标"} · 财年已接{" "}
              {d.target_coverage.available_months.length}/12 月
              {d.target_coverage.missing_months.length
                ? `，缺 ${d.target_coverage.missing_months.join("、")}`
                : ""}
              ；固定六店渠道范围，不按单店分摊。
            </p>
          )}
          <a href={TARGET_SOURCE} target="_blank" rel="noreferrer">
            查看原版 GMV 目标来源 ↗
          </a>
        </details>
      )}
    </section>
  );
}
