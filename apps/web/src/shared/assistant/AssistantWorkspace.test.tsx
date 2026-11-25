// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter } from "react-router-dom";
import { AssistantPageContext, AnalyzeProduct } from "./AssistantContext";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { startPanelSession } from "../layouts/panelLayout";
import { AssistantEntry, AssistantWorkspace } from "./AssistantWorkspace";
import type { PageContext } from "./contracts";

// jsdom 尚未实现原生 dialog 的显示方法；响应式与焦点另用浏览器核验。
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
  model: "bailian:deepseek-v4.1-flash-1017",
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

it("首次提问前切页跟随推广页，重新打开仍保留草稿", () => {
  renderAssistant();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "再看看推广" } });
  fireEvent.click(screen.getByRole("button", { name: "收起助手" }));
  fireEvent.click(screen.getByRole("link", { name: "推广页" }));
  fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("再看看推广");
  expect(screen.queryByRole("button", { name: "改用当前页" })).toBeNull();
  expect(screen.getByRole("button", { name: "当前期间推广投产和花费怎么样？" })).toBeTruthy();
});

it.each([true, false])(
  "售后页跨域讨论商品规则后快捷提问仍跟随页面，新对话恢复售后建议：explanation=%s",
  async (withExplanation) => {
    vi.stubGlobal("fetch", (url: string) =>
      Promise.resolve({
        ok: true,
        json: async () =>
          url.endsWith("/models")
            ? { models: ["m"] }
            : {
                ...answer,
                subject: null,
                answer: "商品严重流量下滑规则已说明",
                query_context: {
                  domain: withExplanation ? "aftersales" : "products",
                  versions: {},
                  ...(withExplanation ? { explanation: { view: "products" } } : {}),
                },
              },
      }),
    );
    render(
      <MemoryRouter initialEntries={["/aftersales"]}>
        <AssistantWorkspace>
          <AssistantPageContext
            page="售后分析"
            subject={null}
            context={{ domain: "aftersales", report_id: "report-2" }}
          />
          <AssistantEntry />
        </AssistantWorkspace>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
    expect(
      within(screen.getByLabelText("建议问题")).getByRole("button", {
        name: "当前报告退款情况怎么样？",
      }),
    ).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "商品严重流量下滑阈值" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText("商品严重流量下滑规则已说明");
    const suggestions = within(screen.getByLabelText("建议问题"));
    expect(suggestions.queryByRole("button", { name: "当前期间哪些商品有经营风险？" })).toBeNull();
    expect(suggestions.getByRole("button", { name: "当前报告退款情况怎么样？" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "新对话" }));
    expect(
      within(screen.getByLabelText("建议问题")).getByRole("button", {
        name: "当前报告退款情况怎么样？",
      }),
    ).toBeTruthy();
  },
);

it.each([
  { domain: "aftersales", suggestion: "当前报告退款情况怎么样？" },
  { domain: "promotion_store", suggestion: "当前期间推广投产和花费怎么样？" },
])("选款讨论$domain后仍显示当前商品的快捷问题", async ({ domain, suggestion }) => {
  vi.stubGlobal("fetch", (url: string) =>
    Promise.resolve({
      ok: true,
      json: async () =>
        url.endsWith("/models")
          ? { models: ["m"] }
          : {
              ...answer,
              answer: "跨域问题已回答",
              query_context: { domain, versions: {} },
            },
    }),
  );
  renderAssistant();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "看看这个业务的情况" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  await screen.findByText("跨域问题已回答");
  const suggestions = within(screen.getByLabelText("建议问题"));
  expect(suggestions.queryByRole("button", { name: suggestion })).toBeNull();
  expect(suggestions.getByRole("button", { name: "近期表现" })).toBeTruthy();
  expect(screen.getByLabelText(/测试商品甲 · 甲店/)).toBeTruthy();
});

it("单款商品推广主题继续提供单款追问并发送原问题", async () => {
  const request = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      ...answer,
      query_context: {
        domain: "products",
        versions: {},
        explanation: { view: "promotion_product" },
      },
    }),
  });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
      : request(url, options),
  );
  renderAssistant();
  fireEvent.click(screen.getByRole("button", { name: "这款近 7 天表现怎样？" }));
  await screen.findByText(answer.answer);
  fireEvent.click(
    within(screen.getByLabelText("建议问题")).getByRole("button", { name: "异常在哪" }),
  );
  expect(JSON.parse(request.mock.calls[1][1].body).question).toBe("这款的异常主要在哪？");
  await screen.findAllByText(answer.answer);
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
            models: ["bailian:deepseek-v4.1-flash-1017"],
          }),
        })
      : request(url, options),
  );

  renderAssistant();
  expect(screen.queryByRole("combobox", { name: "回答模型" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /这款近 7 天表现怎样/ }));
  expect(JSON.parse(request.mock.calls[0][1].body)).not.toHaveProperty("model");
  fireEvent.click(screen.getByRole("button", { name: "停止" }));
  fireEvent.click(screen.getByRole("button", { name: "重新回答" }));
  expect(await screen.findByText("成交额下降了两成。")).toBeTruthy();
  expect(request.mock.calls[0][1].signal.aborted).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "再看推广" }));
  expect(request.mock.calls[1][0]).toBe("/api/v1/assistant/stop");
  expect(JSON.parse(request.mock.calls[3][1].body).history[0].answer).toBe("成交额下降了两成。");
  await screen.findAllByText("成交额下降了两成。");
});

it.each(["no_evidence", "need_selection"])(
  "未解决的 %s 问题随短追问传递，完成后继续保留已答历史",
  async (status) => {
    const request = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ...answer, answer: "请明确要解释的规则", status, subject: null }),
      })
      .mockResolvedValue({
        ok: true,
        json: async () => ({ ...answer, answer: "已读取生效规则", subject: null }),
      });
    vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
      url.endsWith("/models")
        ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
        : request(url, options),
    );
    renderAssistant(false);
    const ask = (value: string) => {
      fireEvent.change(screen.getByRole("textbox"), { target: { value } });
      fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    };
    ask("推广低投产的阈值是多少？");
    await screen.findByText("请明确要解释的规则");
    ask("我问你呢");
    expect(JSON.parse(request.mock.calls[1][1].body).history).toEqual([
      {
        page: "商品数据",
        question: "推广低投产的阈值是多少？",
        answer: "请明确要解释的规则",
        status,
      },
    ]);
    await screen.findByText("已读取生效规则");
    ask("这个阈值适用于哪些样本？");
    expect(JSON.parse(request.mock.calls[2][1].body).history).toEqual([
      {
        page: "商品数据",
        question: "推广低投产的阈值是多少？",
        answer: "请明确要解释的规则",
        status,
      },
      {
        page: "商品数据",
        question: "我问你呢",
        answer: "已读取生效规则",
        status: "answered",
      },
    ]);
    await screen.findAllByText("已读取生效规则");
  },
);

it("未解决问题与已答历史一起只传最近六项", async () => {
  let sequence = 0;
  const request = vi.fn().mockImplementation(async () => {
    sequence += 1;
    return {
      ok: true,
      json: async () => ({
        ...answer,
        answer: `第${sequence}次回答`,
        status: sequence % 2 ? "no_evidence" : "answered",
        subject: null,
      }),
    };
  });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
      : request(url, options),
  );
  renderAssistant(false);
  for (let index = 1; index <= 8; index += 1) {
    fireEvent.change(screen.getByRole("textbox"), { target: { value: `第${index}个问题` } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText(`第${index}次回答`);
  }
  expect(JSON.parse(request.mock.calls[7][1].body).history).toEqual([
    { page: "商品数据", question: "第2个问题", answer: "第2次回答", status: "answered" },
    { page: "商品数据", question: "第3个问题", answer: "第3次回答", status: "no_evidence" },
    { page: "商品数据", question: "第4个问题", answer: "第4次回答", status: "answered" },
    { page: "商品数据", question: "第5个问题", answer: "第5次回答", status: "no_evidence" },
    { page: "商品数据", question: "第6个问题", answer: "第6次回答", status: "answered" },
    { page: "商品数据", question: "第7个问题", answer: "第7次回答", status: "no_evidence" },
  ]);
});

it("已选商品缺少规则时保留选款与未答问题，并携带服务端解释主题", async () => {
  const request = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...answer,
        answer: "尚未取得该规则",
        status: "no_evidence",
        subject: null,
        query_context: {
          domain: "products",
          versions: {},
          explanation: { view: "promotion_product", report_id: null },
        },
      }),
    })
    .mockResolvedValue({ ok: true, json: async () => answer });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
      : request(url, options),
  );
  renderAssistant();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "这款异常阈值是多少？" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  await screen.findByText("尚未取得该规则");
  expect(screen.getByLabelText(/测试商品甲 · 甲店/)).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "我问你呢" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  expect(JSON.parse(request.mock.calls[1][1].body)).toMatchObject({
    context: {
      listing_id: "s/A",
      shop_id: "s",
      explanation: { view: "promotion_product", report_id: null },
    },
    history: [{ question: "这款异常阈值是多少？", status: "no_evidence" }],
  });
  await screen.findByText(answer.answer);
});

it("未答追问的新解释主题不被更早已答主题覆盖", async () => {
  const request = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...answer,
        answer: "商品规则已说明",
        query_context: { domain: "products", versions: {}, explanation: { view: "products" } },
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...answer,
        answer: "推广规则尚未取得",
        status: "no_evidence",
        subject: null,
        query_context: {
          domain: "products",
          versions: {},
          explanation: { view: "promotion_product" },
        },
      }),
    })
    .mockResolvedValue({ ok: true, json: async () => answer });
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
  ask("商品异常怎么判？");
  await screen.findByText("商品规则已说明");
  ask("推广阈值呢？");
  await screen.findByText("推广规则尚未取得");
  ask("我问你呢");
  expect(JSON.parse(request.mock.calls[2][1].body).context.explanation).toEqual({
    view: "promotion_product",
  });
  await screen.findByText(answer.answer);
});

it("新报告经营结果显式清除旧解释主题，追问阈值不带旧报告引用", async () => {
  const request = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...answer,
        subject: null,
        answer: "旧报告规则已解释",
        query_context: {
          domain: "aftersales",
          report_id: "old-report",
          versions: { aftersales: "av2" },
          explanation: { view: "aftersales", report_id: "old-report" },
        },
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...answer,
        subject: null,
        answer: "新报告经营已查询",
        query_context: {
          domain: "aftersales",
          report_id: "new-report",
          versions: { aftersales: "av2" },
          explanation: null,
        },
      }),
    })
    .mockResolvedValue({ ok: true, json: async () => ({ ...answer, subject: null }) });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
      : request(url, options),
  );
  render(
    <MemoryRouter initialEntries={["/aftersales"]}>
      <AssistantWorkspace>
        <AssistantPageContext
          page="售后分析"
          subject={null}
          context={{
            domain: "aftersales",
            report_id: "new-report",
            versions: { aftersales: "av2" },
          }}
        />
        <AssistantEntry />
      </AssistantWorkspace>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
  const ask = (value: string) => {
    fireEvent.change(screen.getByRole("textbox"), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  };
  ask("解释旧报告规则");
  await screen.findByText("旧报告规则已解释");
  ask("查新报告经营");
  await screen.findByText("新报告经营已查询");
  expect(screen.queryByRole("button", { name: "改用当前页" })).toBeNull();
  ask("它的阈值呢");
  const context = JSON.parse(request.mock.calls[2][1].body).context;
  expect(context.report_id).toBe("new-report");
  expect(context.explanation).toBeUndefined();
  await screen.findByText(answer.answer);
});

it("明确换款无结果后不从旧经营报告恢复商品和七天期间", async () => {
  const report = {
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
      json: async () => ({
        ...answer,
        reports: [report],
        query_context: {
          domain: "products",
          versions: { products: "pv1" },
          date_from: "2026-11-14",
          date_to: "2026-11-20",
          explanation: { view: "products" },
        },
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...answer,
        answer: "没有找到新款",
        status: "no_evidence",
        subject: null,
        subject_cleared: true,
        query_context: { domain: "products", versions: { products: "pv1" } },
      }),
    })
    .mockResolvedValue({ ok: true, json: async () => ({ ...answer, subject: null }) });
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
  ask("这款近七天呢？");
  await screen.findByText(answer.answer);
  ask("换成不存在的新款");
  await screen.findByText("没有找到新款");
  ask("我问你呢");
  const body = JSON.parse(request.mock.calls[2][1].body);
  expect(body.context.listing_id).toBeUndefined();
  expect(body.context.date_from).toBeUndefined();
  expect(body.context.date_to).toBeUndefined();
  expect(body.context.explanation).toBeUndefined();
  expect(body.history).toEqual([
    {
      page: "商品数据",
      question: "换成不存在的新款",
      answer: "没有找到新款",
      status: "no_evidence",
    },
  ]);
  await screen.findAllByText(answer.answer);
});

it("切换商品和新对话都清除未解决问题", async () => {
  const request = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ...answer, answer: "尚未取得甲款规则", status: "no_evidence" }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...answer,
        answer: "尚未取得乙款规则",
        status: "no_evidence",
        subject: other,
      }),
    })
    .mockResolvedValue({ ok: true, json: async () => ({ ...answer, subject: null }) });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
      : request(url, options),
  );
  renderAssistant();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "这款阈值是多少？" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  await screen.findByText("尚未取得甲款规则");
  fireEvent.click(screen.getByRole("button", { name: "分析这款" }));
  expect(JSON.parse(request.mock.calls[1][1].body).history).toEqual([]);
  await screen.findByText("尚未取得乙款规则");
  fireEvent.click(screen.getByRole("button", { name: "新对话" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "我问你呢" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  expect(JSON.parse(request.mock.calls[2][1].body).history).toEqual([]);
  await screen.findByText(answer.answer);
});

it.each(["stopped", "error"])("%s 流式半段不进入后续追问历史", async (state) => {
  let channel!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      channel = controller;
    },
  });
  const request = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ...answer, answer: "需要继续核对阈值", status: "no_evidence" }),
    })
    .mockResolvedValueOnce(
      new Response(stream, { headers: { "content-type": "text/event-stream" } }),
    )
    .mockResolvedValue({ ok: true, json: async () => answer });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
      : url.endsWith("/stop")
        ? Promise.resolve({ ok: true })
        : request(url, options),
  );
  renderAssistant();
  const ask = (value: string) => {
    fireEvent.change(screen.getByRole("textbox"), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  };
  ask("低投产阈值是多少？");
  await screen.findByText("需要继续核对阈值");
  ask("我问你呢");
  channel.enqueue(new TextEncoder().encode('data: {"type":"delta","text":"未完成的阈值解释"}\n\n'));
  await screen.findByText("未完成的阈值解释");
  if (state === "stopped") {
    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    channel.close();
    await screen.findByText("已停止回答");
  } else {
    channel.error(new Error("连接中断"));
    await screen.findByText("连接中断");
  }
  expect(document.querySelector(".assistant-turn:last-of-type .assistant-evidence")).toBeNull();
  ask("继续解释");
  expect(JSON.parse(request.mock.calls[2][1].body).history).toEqual([
    {
      page: "商品数据",
      question: "低投产阈值是多少？",
      answer: "需要继续核对阈值",
      status: "no_evidence",
    },
  ]);
  await screen.findByText(answer.answer);
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
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "刚才那款的推广呢" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
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

it("跨域追问保留四维多选，页面快捷提问自动使用当前筛选", async () => {
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
  expect(screen.getByRole("switch", { name: "跟随页面" }).getAttribute("aria-checked")).toBe(
    "true",
  );
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "还有哪些问题" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  expect(JSON.parse(request.mock.calls[1][1].body).context).toMatchObject(query);
  await screen.findAllByText(answer.answer);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "看看新品" } });
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("看看新品");
  expect(screen.getAllByText(answer.answer)).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "当前期间商品整体表现怎么样？" }));
  expect(JSON.parse(request.mock.calls[2][1].body).context).toMatchObject(initial);
  expect(JSON.parse(request.mock.calls[2][1].body).context.report_id).toBeUndefined();
  expect(JSON.parse(request.mock.calls[2][1].body).history).toEqual([]);
  await waitFor(() => expect(screen.getAllByText(answer.answer)).toHaveLength(3));
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
        subject_cleared: true,
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
  expect(screen.getByLabelText(/测试商品甲 · 甲店/)).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "这款呢" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  expect(JSON.parse(request.mock.calls[1][1].body)).toMatchObject({
    context: { listing_id: "s/A", shop_id: "s", filter_shop_id: "" },
    history: [{ question: "看看全部店铺销售", answer: answer.answer }],
  });
  await screen.findByText("查不到这款的更多证据");
  expect(screen.getByLabelText(/测试商品甲 · 甲店/)).toBeTruthy();
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

it("只解释缺记录后仍能追问同一款，并保留上一问", async () => {
  const queries: { context: PageContext; history: { question: string }[] }[] = [];
  vi.stubGlobal("fetch", async (url: string, options: RequestInit) => {
    if (url.endsWith("/models")) return { ok: true, json: async () => ({ models: ["m"] }) };
    queries.push(JSON.parse(String(options.body)));
    return {
      ok: true,
      json: async () => ({ ...answer, subject: null, answer: "可作为线索", evidence: "" }),
    };
  });
  renderAssistant();
  const ask = (text: string) => {
    fireEvent.change(screen.getByRole("textbox"), { target: { value: text } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  };
  ask("缺记录代表什么？");
  await screen.findByText("可作为线索");
  ask("查查它上周的销量");
  expect(queries[1].context).toMatchObject({ listing_id: "s/A", shop_id: "s" });
  expect(queries[1].history[0].question).toBe("缺记录代表什么？");
  await screen.findAllByText("可作为线索");
});

it("风险商品引用随追问传回，清除后继续使用原页面范围", async () => {
  const ref = {
    listing_id: "risk-id",
    shop_id: "shop-r",
    data_version: "pv-risk",
    date_from: "2026-11-18",
    date_to: "2026-11-24",
  };
  const replies = [
    { answer: "优先看测试帽。", risk_reference: ref },
    { answer: "全部商品共有两款。", risk_reference: null },
    { answer: "请指出商品。", risk_reference: null },
  ];
  const request = vi.fn().mockImplementation(async () => {
    const item = replies.shift()!;
    return {
      ok: true,
      json: async () => ({
        ...answer,
        subject: null,
        answer: item.answer,
        query_context: { versions: {}, risk_reference: item.risk_reference },
      }),
    };
  });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["model"] }) })
      : request(url, options),
  );
  renderAssistant(false);
  async function ask(question: string, expected: string) {
    fireEvent.change(screen.getByRole("textbox"), { target: { value: question } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await screen.findByText(expected);
  }
  await ask("优先看哪款？", "优先看测试帽。");
  await ask("那全部商品呢？", "全部商品共有两款。");
  const next = JSON.parse(request.mock.calls[1][1].body).context;
  expect(next.risk_reference).toEqual(ref);
  expect(next).not.toHaveProperty("listing_id");
  await ask("刚才那款呢？", "请指出商品。");
  expect(JSON.parse(request.mock.calls[2][1].body).context.risk_reference).toBeUndefined();
});

it("关闭跟随发送全局问题，再打开自动取最新页面，保留草稿和聊天", async () => {
  const request = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => ({ ...answer, subject: null }) });
  vi.stubGlobal("fetch", (url: string, options: RequestInit) =>
    url.endsWith("/models")
      ? Promise.resolve({ ok: true, json: async () => ({ models: ["m"] }) })
      : request(url, options),
  );
  renderAssistant();
  const follow = screen.getByRole("switch", { name: "跟随页面" });
  expect(follow.getAttribute("aria-checked")).toBe("true");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "整体怎么样" } });
  fireEvent.click(follow);
  expect(screen.getByText("全局问答")).toBeTruthy();
  fireEvent.click(screen.getByRole("link", { name: "推广页" }));
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  const global = JSON.parse(request.mock.calls[0][1].body);
  expect(global.question).toBe("整体怎么样");
  expect(global.page).toBe("运营看板中心");
  expect(global.context.listing_id).toBeUndefined();
  expect(global.context.date_from).toBeUndefined();
  await screen.findByText(answer.answer);
  fireEvent.click(follow);
  fireEvent.click(screen.getByRole("button", { name: "当前期间推广投产和花费怎么样？" }));
  expect(JSON.parse(request.mock.calls[1][1].body)).toMatchObject({
    page: "推广分析",
    history: [],
  });
  await waitFor(() => expect(screen.getAllByText(answer.answer)).toHaveLength(2));
});

it("生成中换款，首问固定甲款；这款取乙款，点名刚才的甲款恢复其证据", async () => {
  let finish!: () => void;
  const calls: {
    context: PageContext & { listing_id?: string };
    question: string;
    history: { question: string }[];
  }[] = [];
  vi.stubGlobal("fetch", async (url: string, options: RequestInit) => {
    if (url.endsWith("/models")) return { ok: true, json: async () => ({ models: ["m"] }) };
    const body = JSON.parse(String(options.body));
    calls.push(body);
    const index = calls.length;
    if (index === 1)
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
    return {
      ok: true,
      json: async () => ({
        ...answer,
        subject: body.context.listing_id === product.listing_id ? product : other,
        answer: "答复" + index,
      }),
    };
  });
  const tree = (selected: typeof product) => (
    <MemoryRouter initialEntries={["/products"]}>
      <AssistantWorkspace>
        <AssistantPageContext
          page="商品数据"
          subject={selected}
          context={{ ...period, shop_id: selected.shop_id }}
        />
        <AssistantEntry />
      </AssistantWorkspace>
    </MemoryRouter>
  );
  const ui = render(tree(product));
  fireEvent.click(screen.getByRole("button", { name: "AI助手" }));
  const ask = (text: string) => {
    fireEvent.change(screen.getByRole("textbox"), { target: { value: text } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  };
  ask("这款近七天呢");
  ui.rerender(tree(other));
  expect(screen.getByLabelText(/测试商品乙 · 乙店/)).toBeTruthy();
  finish();
  await screen.findByText("答复1");
  ask("这款转化怎么样");
  await screen.findByText("答复2");
  expect(calls.map((c) => c.context.listing_id)).toEqual(["s/A", "t/B"]);
  expect(calls[1].history).toEqual([]);
  ask("刚才测试商品甲的推广呢");
  await screen.findByText("答复3");
  expect(calls[2].context.listing_id).toBe("s/A");
  expect(calls[2].history.map((t) => t.question)).toEqual(["这款近七天呢"]);
  ask("这款呢");
  await screen.findByText("答复4");
  expect(calls[3].context.listing_id).toBe("t/B");
});
