// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter } from "react-router-dom";
import { AssistantPageContext, AnalyzeProduct } from "./AssistantContext";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { startPanelSession } from "../layouts/panelLayout";
import { AssistantEntry, AssistantWorkspace } from "./AssistantWorkspace";
import type { PageContext } from "./contracts";

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

it.each(["t", ""])(
  "追问优先沿用服务端确认店铺 %j，全部店铺的空字符串不回退旧范围",
  async (shop) => {
    const query = { domain: "sales", shop_id: shop, versions: { sales: "sv2" }, month: "2026-11" };
    const request = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ...answer, subject: null, query_context: query }),
    });
    vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
      url.endsWith("/models")
        ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
        : request(url, options),
    );
    render(
      <MemoryRouter initialEntries={["/sales"]}>
        <AssistantWorkspace>
          <AssistantPageContext
            page="店铺销售"
            subject={null}
            context={{
              domain: "sales",
              shop_id: "s",
              month: "2026-10",
              versions: { sales: "sv1" },
            }}
          />
          <AssistantEntry />
        </AssistantWorkspace>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
    fireEvent.click(screen.getByRole("button", { name: "本月GMV目标达成怎么样？" }));
    await screen.findByText(answer.answer);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "再看评级" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    expect(JSON.parse(request.mock.calls[1][1].body).context).toMatchObject({
      ...query,
      filter_shop_id: shop,
    });
    await screen.findAllByText(answer.answer);
  },
);

it("同页报告与品类范围变化也提供显式切换，跨域追问保留四维多选", async () => {
  const initial: PageContext = {
    domain: "products",
    shop_ids: ["s", "t"],
    category_values: ["衣", "帽"],
    operator_values: ["甲", "乙"],
    level_values: ["A", "B"],
  };
  const query = {
    ...initial,
    domain: "aftersales",
    report_id: "report-2",
    category: "衣",
    versions: { aftersales: "av2" },
  };
  const request = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ ...answer, subject: null, query_context: query }),
  });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
      : request(url, options),
  );
  render(
    <MemoryRouter initialEntries={["/products"]}>
      <AssistantWorkspace>
        <AssistantPageContext page="商品数据" subject={null} context={initial} />
        <AssistantEntry />
      </AssistantWorkspace>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
  fireEvent.click(screen.getByRole("button", { name: "当前期间商品整体表现怎么样？" }));
  await screen.findByText(answer.answer);
  expect(screen.getByRole("button", { name: "使用当前页范围 · 商品数据" })).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "还有哪些问题" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  expect(JSON.parse(request.mock.calls[1][1].body).context).toMatchObject(query);
  await screen.findAllByText(answer.answer);
  fireEvent.click(screen.getByRole("button", { name: "使用当前页范围 · 商品数据" }));
  expect(screen.queryByText(answer.answer)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "当前期间商品整体表现怎么样？" }));
  expect(JSON.parse(request.mock.calls[2][1].body).context).toMatchObject(initial);
  expect(JSON.parse(request.mock.calls[2][1].body).context.report_id).toBeUndefined();
  await screen.findByText(answer.answer);
});

it("单款追问通用看板后保留已选款与完整对话，明确无证据时清空对象", async () => {
  const dashboard = {
    kind: "dashboard",
    domain: "sales",
    capability: "summary",
    title: "店铺销售",
    source: "已发布销售",
    period: null,
    scope: {},
    data_version: "sv1",
    versions: { sales: "sv1" },
    facts: [],
    tables: [],
    notes: [],
    series: [],
  };
  const request = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...answer,
        subject: null,
        reports: [dashboard],
        query_context: { domain: "sales", shop_id: "", versions: { sales: "sv1" } },
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...answer,
        answer: "查不到这款的更多证据",
        status: "no_evidence",
        subject: null,
      }),
    });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
      : request(url, options),
  );
  renderAssistant();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "看看全部店铺销售" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  await screen.findByText(answer.answer);
  expect(screen.getByText("测试商品甲")).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "这款呢" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  expect(JSON.parse(request.mock.calls[1][1].body)).toMatchObject({
    context: { listing_id: "s/A", shop_id: "s", filter_shop_id: "" },
    history: [{ question: "看看全部店铺销售", answer: answer.answer }],
  });
  await screen.findByText("查不到这款的更多证据");
  expect(screen.queryByText("测试商品甲")).toBeNull();
});

it("单款七天切到销售三十天后，继续追问采用最近确认期间", async () => {
  const performance = {
    kind: "product_performance",
    product,
    period: {
      date_from: "2026-11-14",
      date_to: "2026-11-20",
      previous_from: "2026-11-07",
      previous_to: "2026-11-13",
    },
    metrics: [],
    series: [],
    data_version: "pv1",
    evidence: { source: "商品经营" },
    coverage: {
      current: { observed_days: 7, expected_days: 7, missing_dates: [] },
      previous: { observed_days: 7, expected_days: 7, missing_dates: [] },
      complete_through: "2026-11-20",
    },
  };
  const request = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ...answer, answer: "七天商品经营", reports: [performance] }),
    })
    .mockResolvedValue({
      ok: true,
      json: async () => ({
        ...answer,
        answer: "三十天店铺销售",
        query_context: {
          domain: "sales",
          versions: { sales: "sv1" },
          date_from: "2026-10-22",
          date_to: "2026-11-20",
        },
      }),
    });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
      : request(url, options),
  );
  renderAssistant();
  const ask = (value: string) => {
    fireEvent.change(screen.getByRole("textbox"), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  };
  ask("看看这款近7天");
  await screen.findByText("七天商品经营");
  ask("店铺近30天销售呢");
  await screen.findByText("三十天店铺销售");
  ask("退款呢");
  expect(JSON.parse(request.mock.calls[2][1].body).context).toMatchObject({
    domain: "sales",
    date_from: "2026-10-22",
    date_to: "2026-11-20",
  });
  await screen.findAllByText("三十天店铺销售");
});
