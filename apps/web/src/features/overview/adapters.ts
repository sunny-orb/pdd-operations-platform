import { z } from "zod";
import { ApiError, getJson } from "../../shared/api/client";
import { meta as productMeta, board } from "../products/contracts";
import {
  metadata as promotionMeta,
  overview as promotionOverview,
  diagnosis,
} from "../promotion/contracts";
import { filtersSchema, performanceSchema, shopsSchema } from "../sales/api/contracts";
import { targetsSchema } from "../sales/api/details";
import {
  metadata as aftersalesMeta,
  overview as aftersalesOverview,
} from "../aftersales/contracts";
import type { DomainSpec } from "./data";
import {
  actionFromObservation,
  aftersalesLink,
  formatMetric,
  localTime,
  productLink,
  promotionLink,
  type DomainCard,
  type DomainMeta,
  type DomainPayload,
  type Observation,
} from "./model";

const token = z.object({ data_version: z.string().nullable() });
/** 负责域仍保有业务计算权；中心只校验同一发布版并挑选原字段。 */
async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  signal: AbortSignal,
  version?: string,
) {
  const response = z
    .object({ data: schema, meta: token })
    .parse(await getJson(`/api/v1/${path}`, signal));
  if (version && response.meta.data_version !== version)
    throw new ApiError("发布版本已变化，请重新读取。", 409);
  return response;
}
const query = (version: string, date: string) =>
  new URLSearchParams({ data_version: version, date_from: date, date_to: date });
const text = (value: unknown): string | null => (typeof value === "string" ? value : null);
const empty = (card: DomainCard): DomainPayload => ({ card, observations: [], actions: [] });
const dailyNote = "最新观察日；完整截至日未知，不能视为完整日。";

export const productsSpec: DomainSpec<z.infer<typeof productMeta>> = {
  id: "products",
  async meta(signal) {
    const { data: m } = await request("products/meta", productMeta, signal);
    return {
      id: "products",
      version: m.data_version,
      date: m.date_to || null,
      completeThrough: m.complete_through,
      success: m.success_at,
      attempt: m.latest_attempt.finished_at ?? m.latest_attempt.started_at ?? null,
      attemptStatus: m.latest_attempt.status ?? null,
      status: m.completeness,
      notes: m.sources.map(
        (s) => `${s.dataset}：观察截至 ${s.observed_through ?? "未知"} · ${s.status}`,
      ),
      raw: m,
    };
  },
  async load(m, signal) {
    const base: DomainCard = {
      title: "商品经营",
      heading: "商品数据看板",
      value: null,
      valueType: "currency",
      valueLabel: `${m.date ?? "尚无观察日"} GMV`,
      pairs: [],
      note: dailyNote,
      link: m.date ? productLink(m.date) : "/products",
      status: "观察 · 待核验",
    };
    if (!m.date) return empty(base);
    const { data: d } = await request(
      `products/overview?${query(m.version, m.date)}`,
      board.pick({ metrics: true, risks: true, brief: true }),
      signal,
      m.version,
    );
    const metric = d.metrics.revenue;
    const observations: Observation[] = d.risks.map((r) => ({
      id: `products:${r.listing_id}:${r.alert.label}`,
      domain: "products",
      level: "observation",
      sourcePriority: r.alert.priority,
      title: `${r.product_name ?? r.product_id} · ${r.alert.label}`,
      detail: `${r.alert.reasons.join("；")}。${r.alert.basis}`,
      scope: `${r.shop_name ?? r.shop_id} · ${d.brief.start} 至 ${d.brief.end}（风险观察窗）`,
      impact: r.alert.expected_loss,
      impactKind: "规则估算影响，非已实现损失",
      link: productLink(m.date!, r),
    }));
    return {
      card: {
        ...base,
        value: metric?.value ?? null,
        pairs: [
          { label: "日环比", value: metric?.mom ?? null, type: "percent" },
          { label: "同比", value: metric?.yoy ?? null, type: "percent" },
          { label: "异常数", value: null, type: "number", note: "M6-01 待确认" },
          { label: "最近更新", value: m.date, type: "text" },
        ],
        note: `来源：商品经营日报。${metric?.reason ?? dailyNote} 风险沿负责域 ${d.brief.start} 至 ${d.brief.end} 观察窗。`,
      },
      observations,
      actions: observations.map((o, i) =>
        actionFromObservation(
          o,
          d.risks[i].alert.action,
          d.risks[i].alert.reasons.join("；") || "待核查原因",
          "在对应商品风险窗复核交易额、流量与转化；完整性确认后验证变化",
        ),
      ),
    };
  },
};

type PromotionMeta = z.infer<typeof promotionMeta>;
function promotionSources(m: PromotionMeta) {
  return m.sources.map((s) => {
    const a =
      s.latest_attempt && typeof s.latest_attempt === "object"
        ? (s.latest_attempt as Record<string, unknown>)
        : {};
    return {
      success: text(s.last_success_at),
      attempt: text(a.finished_at) ?? text(a.started_at),
      attemptStatus: text(a.status),
      note: `${text(s.dataset) ?? "推广来源"}：观察截至 ${text(s.observed_through) ?? "未知"} · ${text(s.status) ?? "未知"}`,
    };
  });
}
export const promotionSpec: DomainSpec<PromotionMeta> = {
  id: "promotion",
  async meta(signal) {
    const { data: m, meta } = await request("promotion/meta?mode=product", promotionMeta, signal);
    const sources = promotionSources(m);
    // 不从多源拼接业务日期；主源已由负责域给出date_to。
    return {
      id: "promotion",
      version: meta.data_version ?? "",
      date: m.date_to,
      completeThrough: m.complete_through,
      success: sources[0]?.success ?? null,
      attempt: sources[0]?.attempt ?? null,
      attemptStatus: sources[0]?.attemptStatus ?? null,
      status: m.completeness,
      notes: sources.map((s) => s.note),
      raw: m,
    };
  },
  async load(m, signal) {
    const base: DomainCard = {
      title: "推广效率",
      heading: "推广数据看板",
      value: null,
      valueType: "ratio",
      valueLabel: `${m.date ?? "尚无观察日"} ROI`,
      pairs: [],
      note: dailyNote,
      link: m.date ? promotionLink(m.date) : "/promotion?mode=product",
      status: "观察 · 待核验",
    };
    if (!m.date) return empty(base);
    const p = query(m.version, m.date);
    p.set("mode", "product");
    const [{ data: d }, { data: di }] = await Promise.all([
      request(
        `promotion/overview?${p}`,
        promotionOverview.pick({ metrics: true }),
        signal,
        m.version,
      ),
      request(
        `promotion/diagnosis?${p}`,
        diagnosis.pick({
          current_window: true,
          previous_window: true,
          flagged_products: true,
          status: true,
        }),
        signal,
        m.version,
      ),
    ]);
    const window = `${di.current_window.start ?? "未知"} 至 ${di.current_window.end ?? "未知"}`;
    const observations: Observation[] = di.flagged_products.map((r) => ({
      id: `promotion:${r.object_key}`,
      domain: "promotion",
      level: "observation",
      sourcePriority: r.priority || null,
      title: `${r.label} · 推广观察`,
      detail:
        [...r.findings.map((f) => f.evidence), ...r.clues.map((c) => c.evidence), ...r.notes].join(
          "；",
        ) || "归因与记录完整性待核验",
      scope: `${r.shop_name ?? r.shop_id ?? "未提供店铺"} · ${window}（诊断窗）`,
      impact: r.current.spend?.value ?? null,
      impactKind: "诊断窗花费，非损失金额",
      link: promotionLink(m.date!, r),
    }));
    return {
      card: {
        ...base,
        value: d.metrics.roi?.value ?? null,
        pairs: [
          { label: "花费", value: d.metrics.spend?.value ?? null, type: "currency" },
          { label: "交易额", value: d.metrics.revenue?.value ?? null, type: "currency" },
          {
            label: "CTR / CVR",
            value: `${formatMetric(d.metrics.ctr?.value, "percent")} / ${formatMetric(d.metrics.cvr?.value, "percent")}`,
            type: "text",
          },
          { label: "最近更新", value: m.date, type: "text" },
        ],
        note: `来源：商品推广 · 最新观察单日。诊断另按 ${window}，不等于当日结果；归因/完整性待核验。`,
      },
      observations,
      actions: observations.map((o, i) => {
        const r = di.flagged_products[i];
        return actionFromObservation(
          o,
          [...r.findings.map((f) => f.action), ...r.clues.map((c) => c.action)]
            .filter(Boolean)
            .join("；") || "按源诊断复核证据",
          r.findings.map((f) => f.rule).join("；") || "源域规则线索，待验证",
          "在诊断窗复核花费、归因交易额和 ROI；不将相关变化视为因果",
        );
      }),
    };
  },
};

type SalesRaw = z.infer<typeof filtersSchema>;
export const salesSpec: DomainSpec<SalesRaw> = {
  id: "sales",
  async meta(signal) {
    const r = filtersSchema.parse(await getJson("/api/v1/sales/filters", signal));
    const m = r.meta;
    return {
      id: "sales",
      version: m.data_version,
      scopeKey: `${r.data.default_month}:${r.data.calendar_through}`,
      date: m.observed_through,
      completeThrough: m.complete_through,
      success: m.last_success_at,
      attempt: m.last_attempt_at,
      attemptStatus: m.last_attempt_status,
      status: m.completeness_state,
      notes: [
        `店铺日销售：观察截至 ${m.observed_through ?? "未知"}；月目标按 ${r.data.default_month} 经营月。`,
      ],
      raw: r,
    };
  },
  async load(m, signal) {
    const month = m.raw.data.default_month;
    const base: DomainCard = {
      title: "店铺销售 GMV",
      heading: `${month} 累计 GMV`,
      value: null,
      valueType: "currency",
      valueLabel: `${month} 累计 GMV`,
      pairs: [],
      note: "来源：数仓店铺销售。目标来源未齐备；店铺页尚不支持中心URL筛选，进入后选择范围。",
      link: "/sales",
      status: "目标跟踪",
    };
    const t = targetsSchema.parse(
      await getJson(
        `/api/v1/sales/targets?${new URLSearchParams({ month, data_version: m.version })}`,
        signal,
      ),
    );
    if (t.meta.data_version !== m.version) throw new ApiError("店铺发布版本变化", 409);
    let daily: string | null = null,
      shopsText = "尚无最新观察日";
    if (m.date) {
      const p = query(m.version, m.date);
      const [performance, shops] = await Promise.all([
        getJson(`/api/v1/sales/performance?${p}`, signal).then((v) => performanceSchema.parse(v)),
        getJson(`/api/v1/sales/shops?${p}`, signal).then((v) => shopsSchema.parse(v)),
      ]);
      if (performance.meta.data_version !== m.version || shops.meta.data_version !== m.version)
        throw new ApiError("店铺发布版本变化", 409);
      daily = performance.data.shop_revenue;
      const observed = shops.data.items.filter(
        (s) => s.coverage.observed_days > 0 && s.coverage.latest_observed_date === m.date,
      ).length;
      shopsText = `最新日含 ${observed}/${m.raw.data.shops.length} 店铺`;
    }
    return empty({
      ...base,
      value: t.data.month_actual,
      pairs: [
        { label: "最新入仓日 GMV", value: daily, type: "currency" },
        { label: "月度目标", value: t.data.month_target, type: "currency" },
        { label: "目标达成", value: t.data.month_ratio, type: "percent" },
        { label: "最近更新", value: m.date, type: "text" },
      ],
      note: `${base.note} 本月缺 ${t.data.month_missing_days ?? "未知"} 个店铺日；${shopsText}。`,
    });
  },
};

export const aftersalesSpec: DomainSpec<z.infer<typeof aftersalesMeta>> = {
  id: "aftersales",
  async meta(signal) {
    const { data: m } = await request("aftersales/meta", aftersalesMeta, signal);
    return {
      id: "aftersales",
      version: m.data_version ?? "",
      scopeKey: m.default_report_id ?? "",
      date: m.observed_to,
      completeThrough: m.complete_through,
      success: m.last_success_at,
      attempt: m.last_attempt_at,
      attemptStatus: m.sources.some((s) => s.status === "failed_retained" || s.error_code)
        ? "failed"
        : m.sources.some((s) => s.status === "new_input_pending_report")
          ? "pending_report"
          : null,
      status: m.status,
      notes: m.sources.map(
        (s) =>
          `${s.label}：${s.status} · 观察截至 ${s.observed_to ?? "未知"}；${s.error_code ? `错误：${s.error_code}；` : ""}${s.note}`,
      ),
      raw: m,
    };
  },
  async load(m, signal) {
    const report = m.raw.default_report_id;
    const base: DomainCard = {
      title: "售后退款",
      heading: "退货分析看板",
      value: null,
      valueType: "percent",
      valueLabel: "总退款率",
      pairs: [],
      note: "来源：售后真实报告；金额和比率按源域核验门禁。",
      link: report ? aftersalesLink(report) : "/aftersales",
      status: "观察 · 待核验",
    };
    if (!report) return empty(base);
    const { data: d } = await request(
      `aftersales/overview?${new URLSearchParams({ report_id: report, data_version: m.version })}`,
      aftersalesOverview,
      signal,
      m.version,
    );
    const metric = (key: string) => d.metrics.find((x) => x.key === key);
    const amount = d.risk_metrics.find((x) => x.key === "total_amount");
    const scope = d.report ? `${d.report.period_start} 至 ${d.report.period_end}` : "报告期间未知";
    const observations: Observation[] = d.priorities.map((r) => ({
      id: `aftersales:${r.id}`,
      domain: "aftersales",
      level: "observation",
      sourcePriority: null,
      title: r.title,
      detail: r.detail,
      scope: `${scope} · 报告范围，未提供对象映射`,
      impact: null,
      impactKind: "未提供对应已核金额",
      link: aftersalesLink(report),
    }));
    const actions = d.actions.map((r) => ({
      id: `aftersales:action:${r.id}`,
      issue: r.object,
      impact: r.amount,
      impactKind: "报告源金额，按核验门禁",
      hypothesis: r.hypothesis,
      suggestion: r.action,
      assignee: "未分配" as const,
      due: "未登记" as const,
      progress: "建议，未登记" as const,
      verification: r.verification,
      link: aftersalesLink(report),
    }));
    return {
      card: {
        ...base,
        value: metric("total_rate")?.value ?? null,
        valueLabel: `总退款率（${scope}）`,
        pairs: [
          { label: "退款总额", value: amount?.value ?? null, type: "currency" },
          { label: "售后退款", value: metric("inbound_amount")?.value ?? null, type: "currency" },
          { label: "异常数", value: null, type: "number", note: "M6-01 待确认" },
          {
            label: "最近更新",
            value: d.report?.generated_at ? localTime(d.report.generated_at) : null,
            type: "text",
            note: "报告生成时间",
          },
        ],
        note: `${base.note} ${d.scope_note} ${d.quality_note}`,
      },
      observations,
      actions,
    };
  },
};

export type OverviewMeta = DomainMeta;
