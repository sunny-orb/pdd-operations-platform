// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter } from "react-router-dom";
import { AssistantPageContext, AnalyzeProduct } from "./AssistantContext";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { startPanelSession } from "../layouts/panelLayout";
import { AssistantEntry, AssistantWorkspace } from "./AssistantWorkspace";

// jsdom 尚未实现原生 dialog 的显示方法；响应式与焦点另用浏览器核验。
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
});
afterEach(() => {
  cleanup();
  endSession();
  vi.unstubAllGlobals();
});

const product = { listing_id: "s/A", shop_id: "s", name: "测试商品甲", shop_name: "甲店" };
const other = { listing_id: "t/B", shop_id: "t", name: "测试商品乙", shop_name: "乙店" };
const period = { date_from: "2026-11-01", date_to: "2026-11-20", product_version: "pv1" };
const answer = {
  answer: "成交额下降了两成。",
  status: "answered",
  subject: product,
  reports: [],
  candidates: [],
  evidence: "已发布依据",
  model: "openai-codex:gpt-6.2-astra",
};
function renderAssistant(selected = true) {
  render(
    <MemoryRouter initialEntries={["/products"]}>
      <AssistantWorkspace>
        <AssistantPageContext
          page="商品数据"
          subject={selected ? product : null}
          context={period}
        />
        <AnalyzeProduct subject={other} context={period} />
        <AssistantEntry />
        <Link to="/promotion">推广页</Link>
      </AssistantWorkspace>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
}

it("收起、切页后保留草稿，显式选择才把对话范围切到推广", () => {
  renderAssistant();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "再看看推广" } });
  fireEvent.click(screen.getByRole("button", { name: "收起助手" }));
  fireEvent.click(screen.getByRole("link", { name: "推广页" }));
  fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("再看看推广");
  expect(screen.getByText("商品数据")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "使用当前页范围 · 推广分析" }));
  expect(screen.getByText("推广分析")).toBeTruthy();
});

it("停止后重新请求真实回答，追问带上已完成的对话", async () => {
  const request = vi
    .fn()
    .mockImplementationOnce(() => new Promise(() => {}))
    .mockResolvedValue({ ok: true, json: async () => answer });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({
          ok: true,
          json: async () => ({
            models: ["openai-codex:gpt-6.2-sol", "openai-codex:gpt-6.2-astra"],
          }),
        })
      : request(url, options),
  );

  renderAssistant();
  await screen.findByRole("option", { name: "GPT-6.2 Astra" });
  fireEvent.change(screen.getByRole("combobox", { name: "回答模型" }), {
    target: { value: "openai-codex:gpt-6.2-astra" },
  });
  fireEvent.click(screen.getByRole("button", { name: /这款近 7 天表现怎样/ }));
  expect(JSON.parse(request.mock.calls[0][1].body).model).toBe("openai-codex:gpt-6.2-astra");
  fireEvent.click(screen.getByRole("button", { name: "停止" }));
  fireEvent.click(screen.getByRole("button", { name: "重新回答" }));
  expect(await screen.findByText("成交额下降了两成。")).toBeTruthy();
  expect(request.mock.calls[0][1].signal.aborted).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "再看推广" }));
  expect(request.mock.calls[1][0]).toBe("/api/v1/assistant/stop");
  expect(JSON.parse(request.mock.calls[3][1].body).history[0].answer).toBe("成交额下降了两成。");
  await screen.findAllByText("成交额下降了两成。");
});

it("助手宽度随边界方向变化，收起、切页、再展开仍保留", () => {
  renderAssistant();
  const separator = () => screen.getByRole("separator", { name: "调整助手宽度" });
  fireEvent.keyDown(separator(), { key: "ArrowLeft" });
  expect(separator().getAttribute("aria-valuenow")).toBe("448");
  fireEvent.click(screen.getByRole("button", { name: "收起助手" }));
  fireEvent.click(screen.getByRole("link", { name: "推广页" }));
  fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
  expect(separator().getAttribute("aria-valuenow")).toBe("448");
  fireEvent.doubleClick(separator());
  expect(separator().getAttribute("aria-valuenow")).toBe("440");
});

it("同名候选续用原问题，跨店商品切换与新对话清除旧对象", async () => {
  const request = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...answer,
        answer: "请选择商品",
        status: "need_selection",
        subject: null,
        candidates: [product, other],
      }),
    })
    .mockResolvedValueOnce({ ok: true, json: async () => answer })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ...answer, subject: other, answer: "已查询乙款" }),
    })
    .mockResolvedValueOnce({ ok: true, json: async () => answer });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({
          ok: true,
          json: async () => ({ models: ["m"], business_data_enabled: true }),
        })
      : request(url, options),
  );
  renderAssistant(false);
  const ask = (q: string) => {
    fireEvent.change(screen.getByRole("textbox"), { target: { value: q } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  };
  ask("测试商品当前期间的表现？");
  fireEvent.click(await screen.findByRole("button", { name: /甲店.*s\/A/ }));
  expect(await screen.findByText(answer.answer)).toBeTruthy();
  const payload = (i: number) => JSON.parse(request.mock.calls[i][1].body);
  expect(payload(1).context.candidate_confirmed).toBe(true);
  expect(payload(1)).toMatchObject({
    question: "测试商品当前期间的表现？",
    context: { ...period, listing_id: "s/A", shop_id: "s" },
    history: [],
  });
  fireEvent.click(screen.getByRole("button", { name: "分析这款" }));
  expect(await screen.findByText("已查询乙款")).toBeTruthy();
  expect(payload(2)).toMatchObject({
    context: { listing_id: "t/B", shop_id: "t", product_version: "pv1" },
    history: [],
  });
  fireEvent.click(screen.getByRole("button", { name: "新对话" }));
  expect(screen.queryByText("已查询乙款")).toBeNull();
  ask("测试商品今天怎么样？");
  expect(payload(3).context.listing_id).toBeUndefined();
  expect(payload(3).history).toEqual([]);
  await screen.findByText(answer.answer);
});

it("跨页追问沿用实际报告的期间与两个领域的版本", async () => {
  const report = {
    kind: "product_performance",
    product,
    versions: { product_version: "pv1", promotion_version: "adv2" },
    period: {
      date_from: "2026-11-14",
      date_to: "2026-11-20",
      previous_from: "2026-11-07",
      previous_to: "2026-11-13",
    },
    metrics: [],
    series: [],
    coverage: {
      current: { observed_days: 0, expected_days: 7, missing_dates: [] },
      previous: { observed_days: 0, expected_days: 7, missing_dates: [] },
      complete_through: null,
    },
    data_version: "pv1",
    evidence: { source: "测试依据" },
  };
  const request = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => ({ ...answer, reports: [report] }) });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({
          ok: true,
          json: async () => ({ models: ["m"], business_data_enabled: true }),
        })
      : request(url, options),
  );
  renderAssistant();
  fireEvent.click(screen.getByRole("button", { name: "这款近 7 天表现怎样？" }));
  await screen.findByText(answer.answer);
  fireEvent.click(screen.getByRole("link", { name: "推广页" }));
  fireEvent.click(screen.getByRole("button", { name: "再看推广" }));
  expect(JSON.parse(request.mock.calls[1][1].body)).toMatchObject({
    context: {
      ...report.versions,
      listing_id: product.listing_id,
      shop_id: product.shop_id,
      date_from: report.period.date_from,
      date_to: report.period.date_to,
    },
  });
  await screen.findAllByText(answer.answer);
});

it("切换商品时取消正在生成的一问，迟到结果不会覆盖新商品", async () => {
  let finish!: (r: unknown) => void;
  const request = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((r) => {
          finish = r;
        }),
    )
    .mockResolvedValue({
      ok: true,
      json: async () => ({ ...answer, subject: other, answer: "新商品回答" }),
    });
  let stopped;
  vi.stubGlobal("fetch", (url: string, options: RequestInit) => {
    if (url.endsWith("/models"))
      return Promise.resolve({
        ok: true,
        json: async () => ({ models: ["m"], business_data_enabled: true }),
      });
    if (url.endsWith("/stop")) {
      stopped = JSON.parse(String(options.body)).generation;
      return Promise.resolve({ ok: true });
    }
    return request(url, options);
  });
  renderAssistant();
  fireEvent.click(screen.getByRole("button", { name: "这款近 7 天表现怎样？" }));
  fireEvent.click(screen.getByRole("button", { name: "分析这款" }));
  await screen.findByText("新商品回答");
  expect(request.mock.calls[0][1].signal.aborted).toBe(true);
  expect(stopped).toBe(JSON.parse(request.mock.calls[0][1].body).generation);
  finish({ ok: true, json: async () => answer });
  await Promise.resolve();
  expect(screen.queryByText(answer.answer)).toBeNull();
  expect(screen.getByText("新商品回答")).toBeTruthy();
});

it("最终结果到达前显示流式正文，停止后保留已显示文字", async () => {
  let channel!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      channel = controller;
    },
  });
  vi.stubGlobal("fetch", (url: string) =>
    url.endsWith("/models")
      ? Promise.resolve({
          ok: true,
          json: async () => ({ models: ["m"], business_data_enabled: true }),
        })
      : url.endsWith("/stop")
        ? Promise.resolve({ ok: true })
        : Promise.resolve(
            new Response(stream, { headers: { "content-type": "text/event-stream" } }),
          ),
  );
  renderAssistant();
  fireEvent.click(screen.getByRole("button", { name: /这款近 7 天表现怎样/ }));
  channel.enqueue(new TextEncoder().encode('data: {"type":"delta","text":"访客减少，"}\n\n'));
  expect(await screen.findByText("访客减少，")).toBeTruthy();
  expect(screen.getByRole("button", { name: "停止" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "停止" }));
  expect(screen.getByText("访客减少，")).toBeTruthy();
  expect(screen.getByText("已停止回答")).toBeTruthy();
  channel.enqueue(
    new TextEncoder().encode("data: " + JSON.stringify({ type: "done", result: answer }) + "\n\n"),
  );
  channel.close();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(screen.queryByText(answer.answer)).toBeNull();
});
