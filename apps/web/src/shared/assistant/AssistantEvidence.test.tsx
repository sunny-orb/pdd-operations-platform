// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantEntry, AssistantWorkspace } from "./AssistantWorkspace";
import { AssistantPageContext } from "./AssistantContext";
import { startPanelSession } from "../layouts/panelLayout";
import type { AssistantReport, BusinessReport, PageContext } from "./contracts";

let endSession: () => void;
beforeEach(() => {
  sessionStorage.clear();
  endSession = startPanelSession();
  HTMLDialogElement.prototype.show = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  vi.stubGlobal("matchMedia", () => ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  endSession();
  vi.unstubAllGlobals();
});

const sales: BusinessReport = {
  kind: "dashboard",
  domain: "sales",
  capability: "trend",
  title: "店铺销售",
  source: "已发布销售",
  period: { date_from: "2026-11-20", date_to: "2026-11-23" },
  scope: {},
  data_version: "sv1",
  versions: { sales: "sv1" },
  facts: [
    {
      key: "revenue",
      label: "成交额",
      value: "9007199254740993.01",
      unit: "currency",
      status: "available",
      reason: null,
    },
  ],
  tables: [],
  notes: [],
  series: [
    {
      key: "revenue",
      label: "成交额",
      unit: "currency",
      points: [
        { date: "2026-11-20", value: "100" },
        { date: "2026-11-21", value: null },
        { date: "2026-11-23", value: "0" },
      ],
    },
  ],
};
const definitions: BusinessReport = {
  ...sales,
  domain: "definitions",
  capability: "rule_definitions",
  title: "规则与适用范围",
  source: "推广生效规则",
  period: null,
  facts: [],
  series: [],
  tables: [
    {
      title: "推广阈值",
      columns: [{ key: "condition", label: "判断条件", unit: "text" }],
      rows: [{ condition: "ROI低于2，且点击至少100次" }],
      total: 1,
      offset: 0,
    },
  ],
};
async function showAnswer(
  question: string,
  reports: (AssistantReport | BusinessReport)[],
  evidence = "本次查询依据",
  page = { path: "/", title: "运营看板中心", domain: "all" as PageContext["domain"] },
) {
  vi.stubGlobal("fetch", (url: string) =>
    Promise.resolve({
      ok: true,
      json: async () =>
        url.endsWith("/models")
          ? { models: ["m"] }
          : {
              answer: "**已按本次依据回答。**",
              status: "answered",
              subject: null,
              candidates: [],
              reports,
              evidence,
              model: "m",
            },
    }),
  );
  render(
    <MemoryRouter initialEntries={[page.path]}>
      <AssistantWorkspace>
        <AssistantPageContext page={page.title} subject={null} context={{ domain: page.domain }} />
        <AssistantEntry />
      </AssistantWorkspace>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
  const input = await screen.findByRole("textbox", { name: "输入运营问题" });
  fireEvent.change(input, { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  await screen.findByText("已按本次依据回答。");
}

it("阈值解释把多份规则及经营报告放入一个默认收起的依据入口", async () => {
  await showAnswer("推广异常的阈值是多少？", [
    definitions,
    { ...definitions, source: "商品生效规则" },
    sales,
  ]);
  const summary = await screen.findByText("数据与规则依据");
  const details = summary.closest("details")!;
  expect(details.open).toBe(false);
  expect(document.querySelectorAll(".assistant-report")).toHaveLength(0);
  expect(screen.queryAllByRole("img", { name: /趋势/ })).toHaveLength(0);
  fireEvent.click(summary);
  expect(details.open).toBe(true);
  await within(details).findByText("已发布销售");
  expect(details.querySelectorAll("details")).toHaveLength(0);
  expect(within(details).getAllByText("ROI低于2，且点击至少100次")).toHaveLength(2);
  expect(within(details).getByText("¥9,007,199,254,740,993.01")).toBeTruthy();
  expect(within(details).getByText("已发布销售")).toBeTruthy();
  expect(within(details).getByText("2026-11-20 至 2026-11-23")).toBeTruthy();
});

it("不同 SOP 即使修订号相同也各自保留版本和可点击的原文", async () => {
  const documents = ["refunds", "quality"].map((key) => ({
    ...definitions,
    capability: "business_knowledge",
    title: key,
    source: "原文 " + key,
    scope: { document_key: key, document_url: "https://kocotree.feishu.cn/docx/" + key },
    versions: { ["sop_" + key]: "7" },
    data_version: "7",
  }));
  await showAnswer("这两篇 SOP 各怎么要求？", documents);
  fireEvent.click(await screen.findByText("数据与规则依据"));
  for (const key of ["refunds", "quality"]) {
    const link = await screen.findByRole("link", { name: "原文 " + key });
    expect(link.getAttribute("href")).toBe("https://kocotree.feishu.cn/docx/" + key);
  }
  expect(screen.getAllByText(/飞书修订号.*7/)).toHaveLength(2);
});

it.each(["成交额是多少？", "数据哪天截止？", "这项指标怎么算？", "你能查什么？"])(
  "纯查数或说明不因附带时序而自动出图：%s",
  async (question) => {
    await showAnswer(question, [sales]);
    await screen.findByText("数据与规则依据");
    expect(screen.queryAllByRole("img", { name: /趋势/ })).toHaveLength(0);
    expect(document.querySelectorAll(".assistant-report")).toHaveLength(0);
  },
);

it("自然下滑追问用一个主图辅助，多域依据只去重完全相同报告", async () => {
  await showAnswer("这款哪几天开始下滑？", [
    sales,
    structuredClone(sales),
    {
      ...sales,
      domain: "promotion_store",
      title: "推广分析",
      source: "已发布推广",
      capability: "summary",
    },
    { ...sales, capability: "summary" },
  ]);
  const summary = await screen.findByText("数据与规则依据");
  expect(screen.getAllByRole("img", { name: /趋势/ })).toHaveLength(1);
  expect(screen.getAllByRole("combobox", { name: "趋势指标" })).toHaveLength(1);
  fireEvent.click(summary);
  const details = summary.closest("details")!;
  await within(details).findByText("推广分析");
  expect(details.querySelectorAll(".assistant-evidence-report")).toHaveLength(3);
  expect(within(details).getByText("推广分析")).toBeTruthy();
  expect(within(details).getAllByText("店铺销售")).toHaveLength(2);
});

it.each([[], [null, null], ["12", null], ["bad", "12"]].map((values) => ({ values })))(
  "没有两个有效数值点不留空图壳：$values",
  async ({ values }) => {
    await showAnswer("看看成交趋势", [
      {
        ...sales,
        series: [
          {
            ...sales.series[0],
            points: values.map((value, index) => ({ date: `2026-11-${20 + index}`, value })),
          },
        ],
      },
    ]);
    await screen.findByText("数据与规则依据");
    expect(screen.queryAllByRole("img", { name: /趋势/ })).toHaveLength(0);
    expect(screen.queryByRole("combobox", { name: "趋势指标" })).toBeNull();
    expect(document.querySelectorAll(".assistant-report")).toHaveLength(0);
  },
);

it("规则定义即使附带数列也不会当作经营趋势", async () => {
  await showAnswer("展示规则阈值趋势", [{ ...definitions, series: sales.series }]);
  await screen.findByText("数据与规则依据");
  expect(screen.queryAllByRole("img", { name: /趋势/ })).toHaveLength(0);
});

it("解释规则里的趋势窗口时不展示顺带取回的经营图", async () => {
  await showAnswer("规则里的7天趋势是怎么判断的？", [definitions, sales]);
  await screen.findByText("数据与规则依据");
  expect(screen.queryAllByRole("img", { name: /趋势/ })).toHaveLength(0);
});

it.each(["画图说明这款为什么命中流量规则", "用图说明这款为什么命中流量规则"])(
  "明确要求用图解释规则时可用实际经营序列：%s",
  async (question) => {
    await showAnswer(question, [definitions, sales]);
    await screen.findByText("数据与规则依据");
    expect(screen.getAllByRole("img", { name: /趋势/ })).toHaveLength(1);
  },
);

it("明确要求画图但只有规则定义时也不补造经营图", async () => {
  await showAnswer("画图说明这款为什么命中流量规则", [definitions]);
  await screen.findByText("数据与规则依据");
  expect(screen.queryAllByRole("img", { name: /趋势/ })).toHaveLength(0);
});

it("多域取数时主图采用问题明确提到的指标", async () => {
  await showAnswer("这款投产哪几天开始下滑？", [
    sales,
    {
      ...sales,
      domain: "promotion_product",
      title: "商品推广",
      source: "已发布推广",
      series: [
        {
          key: "roi",
          label: "投产",
          unit: "ratio",
          points: [
            { date: "2026-11-20", value: "2" },
            { date: "2026-11-21", value: "1" },
          ],
        },
      ],
    },
  ]);
  await screen.findByText("数据与规则依据");
  expect(screen.getByRole("img", { name: "投产趋势" })).toBeTruthy();
  expect(screen.queryByRole("img", { name: "成交额趋势" })).toBeNull();
});

it("中文指标简称选择可绘制的对应指标，空首列不充当默认曲线", async () => {
  await showAnswer("访客下降是从哪天开始的？", [
    {
      ...sales,
      series: [
        {
          key: "empty",
          label: "订单数",
          unit: "number",
          points: [{ date: "2026-11-20", value: null }],
        },
        ...sales.series,
        {
          key: "visitors",
          label: "访客数",
          unit: "number",
          points: [
            { date: "2026-11-20", value: "200" },
            { date: "2026-11-21", value: "100" },
          ],
        },
      ],
    },
  ]);
  await screen.findByText("数据与规则依据");
  expect(screen.getByRole("img", { name: "访客数趋势" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "订单数" })).toBeNull();
});

it("没有报告和来源时不显示空依据", async () => {
  await showAnswer("你好", [], "");
  expect(screen.queryByText("数据与规则依据")).toBeNull();
  expect(document.querySelectorAll(".assistant-report")).toHaveLength(0);
});

const forecast: BusinessReport = {
  ...sales,
  capability: "sales_forecast",
  title: "六店月末 GMV 预测",
  source: "六店销售日报、渠道月目标",
  data_version: "inputs-cd64a710",
  versions: { forecast_sop: "125", forecast_inputs: "inputs-cd64a710" },
  notes: [
    "月末中心估计 = 累计 GMV + 剩余天数 × 加权日均。",
    "加权日均 = 近 2 日销售日均 × 75% + 近 4 日销售日均 × 25%。",
    "回算采用当前已发布的日报。",
  ],
};
const knowledge: BusinessReport = {
  ...definitions,
  capability: "business_knowledge",
  title: "公共预测 SOP",
  source: "月度 GMV 预测方法",
  data_version: "125",
  versions: { forecast_sop: "125" },
  tables: [],
  notes: ["第三节 月末预测"],
};

it.each([
  { page: { path: "/aftersales", title: "售后分析", domain: "aftersales" }, reports: [knowledge] },
  { page: { path: "/sales", title: "店铺销售", domain: "sales" }, reports: [forecast, knowledge] },
] as const)(
  "在$page.title查看预测或规则时，首层保留来源和一份飞书修订号",
  async ({ page, reports }) => {
    await showAnswer(
      "这个月的预测方法是什么？",
      [
        ...reports,
        { ...definitions, versions: { sales: "unrelated", forecast_sop: "other-version" } },
      ],
      undefined,
      page,
    );
    const summary = await screen.findByText("数据与规则依据");
    expect(screen.queryByText("第三节 月末预测")).toBeNull();
    fireEvent.click(summary);
    const evidence = within(summary.closest("details")!);
    await evidence.findByText("第三节 月末预测");
    expect(evidence.getAllByText(/飞书修订号.*125/)).toHaveLength(1);
    expect(evidence.queryByText(/other-version|inputs-cd64a710|数据版本/)).toBeNull();
    const method = evidence.getByRole("region", { name: "公共预测 SOP数据" });
    expect(within(method).getByText("月度 GMV 预测方法")).toBeTruthy();
    expect(within(method).queryByRole("button")).toBeNull();
    if (reports.includes(forecast)) {
      expect(evidence.getByText("六店销售日报、渠道月目标")).toBeTruthy();
      expect(evidence.getByText("2026-11-20 至 2026-11-23")).toBeTruthy();
      expect(evidence.getByText(forecast.notes[0])).toBeTruthy();
      expect(evidence.getByText(forecast.notes[1])).toBeTruthy();
      expect(evidence.queryByText(forecast.notes[2])).toBeNull();
    }
  },
);

it("展开销售依据先看少量事实，键盘查看完整分页、单位和各自日期上的逐日数据", async () => {
  const longNote = "各来源的覆盖说明。".repeat(30);
  const rows = Array.from({ length: 14 }, (_, index) => ({ name: "店铺" + (index + 1) }));
  const table = {
    title: "店铺分页",
    columns: [{ key: "name", label: "店铺", unit: "text" as const }],
    rows,
    offset: 20,
    total: 56,
  };
  await showAnswer("销售汇总", [
    {
      ...sales,
      scope: { category: ["女装", "童装"], shop_id: "甲店" },
      facts: [
        ...sales.facts,
        ...["订单数", "访客", "店铺数", "尾项"].map((label, i) => ({
          key: "metric" + i,
          label,
          value: String(i),
          unit: "number" as const,
          status: "observed",
          reason: null,
        })),
      ],
      tables: [table, { ...table, title: "另一分页" }],
      notes: ["成交按支付日统计。", longNote],
      series: [
        ...sales.series,
        {
          key: "refund",
          label: "退款率",
          unit: "percent",
          points: [
            { date: "2026-11-21", value: "0.125" },
            { date: "2026-11-22", value: null },
          ],
        },
      ],
    },
  ]);
  const summary = await screen.findByText("数据与规则依据");
  expect(screen.queryAllByText("店铺14")).toHaveLength(0);
  fireEvent.click(summary);
  const evidence = within(summary.closest("details")!);
  await evidence.findByText("已发布销售");
  expect(evidence.getByText(/女装.*童装/)).toBeTruthy();
  expect(evidence.getByText(/甲店/)).toBeTruthy();
  expect(evidence.getByText("¥9,007,199,254,740,993.01")).toBeTruthy();
  expect(evidence.queryByText("尾项")).toBeNull();
  expect(evidence.queryByText("店铺14")).toBeNull();
  expect(evidence.queryByText("退款率逐日明细")).toBeNull();
  expect(evidence.queryByText(longNote)).toBeNull();
  const button = evidence.getByRole("button", { name: "查看明细", expanded: false });
  button.focus();
  await userEvent.keyboard("{Enter}");
  expect(evidence.getAllByText("店铺14")).toHaveLength(2);
  expect(evidence.getAllByText("显示第21—34条，共56条")).toHaveLength(2);
  expect(evidence.getByText("尾项")).toBeTruthy();
  expect(evidence.getByText(longNote)).toBeTruthy();
  expect(evidence.getByText("12.50%").closest("tr")?.textContent).toContain("2026-11-21");
  expect(evidence.getByText("2026-11-22").closest("tr")?.textContent).toContain("—");
  expect(evidence.getByText("¥0.00").closest("tr")?.textContent).toContain("2026-11-23");
  await userEvent.keyboard(" ");
  expect(evidence.queryByText("店铺14")).toBeNull();
});

it("同名跨店单款的来源、日期和缺记录覆盖直接可见，详细表按报告独立展开", async () => {
  const product: AssistantReport = {
    kind: "product_performance",
    product: { listing_id: "s/a", shop_id: "s", name: "春风", shop_name: "甲店" },
    period: {
      date_from: "2026-11-01",
      date_to: "2026-11-03",
      previous_from: "2026-10-29",
      previous_to: "2026-10-31",
    },
    data_version: "product-hash",
    evidence: {
      source: "商品日经营",
      rules: {
        version: "rule-test",
        status: "partial",
        date_to: "2026-11-03",
        findings: [{ label: "流量下滑", basis: "访客低于前期" }],
        checks: [{ key: "roi", label: "投产", state: "unavailable", reason: "缺少投放记录" }],
      },
    },
    coverage: {
      current: { observed_days: 2, expected_days: 3, missing_dates: ["2026-11-02"] },
      previous: { observed_days: 3, expected_days: 3, missing_dates: [] },
      complete_through: "2026-11-01",
    },
    metrics: [
      {
        key: "revenue",
        label: "成交额",
        unit: "currency",
        current: "0",
        previous: null,
        status: "partial",
        reason: "只有两天有记录",
      },
    ],
    series: [{ date: "2026-11-03", values: { revenue: "0" } }],
  };
  await showAnswer(
    "这些商品的经营结果",
    [
      product,
      {
        ...product,
        product: { ...product.product, listing_id: "t/a", shop_id: "t", shop_name: "乙店" },
      },
    ],
    undefined,
    { path: "/products", title: "商品数据", domain: "products" },
  );
  const summary = await screen.findByText("数据与规则依据");
  fireEvent.click(summary);
  const evidence = within(summary.closest("details")!);
  const [shopA, shopB] = await evidence.findAllByRole("region", { name: "商品经营数据" });
  expect(shopA.textContent).toContain("春风");
  expect(shopA.textContent).toContain("甲店");
  expect(shopB.textContent).toContain("乙店");
  expect(shopA.textContent).toContain("商品日经营");
  expect(shopA.textContent).toContain("2/3 天");
  expect(shopA.textContent).toContain("完整截至日 2026-11-01");
  expect(shopA.textContent).toContain("流量下滑");
  expect(shopA.textContent).toContain("¥0.00");
  expect(shopA.textContent).toContain("—");
  expect(shopA.textContent).not.toContain("product-hash");
  expect(within(shopA).queryByText("逐日明细")).toBeNull();
  fireEvent.click(within(shopA).getByRole("button", { name: "查看明细" }));
  expect(within(shopA).getByRole("table", { name: "逐日明细" })).toBeTruthy();
  expect(shopA.textContent).toContain("缺少投放记录");
  expect(shopA.textContent).toContain("本期无记录日期 2026-11-02");
  expect(within(shopB).queryByText("逐日明细")).toBeNull();
});

it.each(["投产趋势怎么样？", "退款趋势怎么样？"])(
  "只有成交额时不画与所问指标无关的图：%s",
  async (question) => {
    await showAnswer(question, [sales]);
    await screen.findByText("数据与规则依据");
    expect(screen.queryAllByRole("img", { name: /趋势/ })).toHaveLength(0);
  },
);

it.each([
  {
    question: "净投产趋势",
    gross: { key: "roi", label: "实际投产比" },
    net: { key: "net_roi", label: "净投产比" },
  },
  {
    question: "净交易额趋势",
    gross: { key: "revenue", label: "交易额" },
    net: { key: "net_revenue", label: "净交易额" },
  },
])("同份报告的净指标图优先于原始值：$question", async ({ question, gross, net }) => {
  const points = [
    { date: "2026-11-20", value: "2" },
    { date: "2026-11-21", value: "1" },
  ];
  await showAnswer(question, [
    {
      ...sales,
      series: [
        { ...gross, unit: "ratio", points },
        { ...net, unit: "ratio", points },
      ],
    },
  ]);
  await screen.findByText("数据与规则依据");
  expect(screen.getByRole("img", { name: net.label + "趋势" })).toBeTruthy();
});
