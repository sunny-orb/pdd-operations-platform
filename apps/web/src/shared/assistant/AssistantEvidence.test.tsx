// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantEntry, AssistantWorkspace } from "./AssistantWorkspace";
import { AssistantPageContext } from "./AssistantContext";
import { startPanelSession } from "../layouts/panelLayout";
import type { BusinessReport } from "./contracts";

let endSession: () => void;
beforeEach(() => {
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
async function showAnswer(question: string, reports: BusinessReport[], evidence = "本次查询依据") {
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
    <MemoryRouter>
      <AssistantWorkspace>
        <AssistantPageContext page="运营看板中心" subject={null} context={{ domain: "all" }} />
        <AssistantEntry />
      </AssistantWorkspace>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
  await screen.findByRole("option", { name: "m" });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: question } });
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
  expect(details.querySelectorAll("details")).toHaveLength(0);
  expect(within(details).getAllByText("ROI低于2，且点击至少100次")).toHaveLength(2);
  expect(within(details).getByText("¥9,007,199,254,740,993.01")).toBeTruthy();
  expect(within(details).getByText("已发布销售")).toBeTruthy();
  expect(within(details).getByText("2026-11-20 至 2026-11-23")).toBeTruthy();
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
