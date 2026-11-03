// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Diagnosis, DiagnosisView } from "./Diagnosis";
import type { PromotionState } from "./data";
import { diagnosis, type DiagnosisObject } from "./contracts";

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const object = (id: string): DiagnosisObject => ({
  object_key: id,
  object_type: "product",
  label: id,
  listing_id: id,
  product_id: id,
  product_name: id,
  shop_id: "shop",
  shop_name: "合成店铺",
  category: "合成品类",
  level: "A",
  operator: null,
  focus: false,
  current: {},
  previous: {},
  findings: [],
  clues: [],
  notes: [],
  priority: "未评级",
  evaluation_state: "unrated",
  rule_results: [],
  exclusions: {
    current: { missing_spend_rows: 0, missing_revenue_rows: 0, revenue_without_spend: "0" },
    previous: { missing_spend_rows: 0, missing_revenue_rows: 0, revenue_without_spend: "0" },
  },
  current_days: 7,
  previous_days: 7,
  current_record_count: "7",
  previous_record_count: "7",
});

it("诊断按需读取，分页后搜索全组、重开复用、晚响应隔离且失败可重试", async () => {
  const calls: URL[] = [];
  let failOnce = true;
  let releaseSlow: (() => void) | undefined;
  let slowSignal: AbortSignal | undefined;
  const summary = {
    current_window: {},
    previous_window: {},
    overall: null,
    products: [],
    flagged_products: [],
    focused_products: [],
    shops: [],
    categories: [],
    counts: { products: 27, flagged: 0, focused: 0, shops: 1, categories: 1 },
    coverage: {},
    rule_version: "test",
    rule_states: {},
    status: "insufficient_evidence",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const request = new URL(url, "http://localhost");
      calls.push(request);
      const search = request.searchParams.get("search");
      const page = Number(request.searchParams.get("page") ?? 1);
      if (search === "慢") {
        slowSignal = init?.signal ?? undefined;
        await new Promise<void>((resolve) => {
          releaseSlow = resolve;
        });
      }
      if (search === "失败" && failOnce) {
        failOnce = false;
        return {
          ok: false,
          status: 503,
          json: async () => ({ error: { code: "FAIL", message: "查询失败" } }),
        };
      }
      return {
        ok: true,
        json: async () => ({
          meta: { data_version: "v1", request_id: "test" },
          data: request.pathname.endsWith("/objects")
            ? {
                items: search
                  ? [object(`${search}命中商品`)]
                  : page === 1
                    ? [
                        object("第一页商品"),
                        ...Array.from({ length: 24 }, (_, i) => object(`其余商品${i}`)),
                      ]
                    : [object("第二页商品"), object("末页商品")],
                total: search ? 1 : 27,
                group_total: 27,
                page,
                size: 25,
                total_pages: search ? 1 : 2,
              }
            : summary,
        }),
      };
    }),
  );
  const scope = {
    from: "2026-10-03",
    to: "2026-11-01",
    shop: "",
    channel: "",
    products: [],
    keyword: "",
    category: "",
    level: "A级",
    operator: "",
    page: 1,
    size: 25,
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <Diagnosis
        state={
          {
            scope,
            queryScope: scope,
            version: "v1",
            invalid: null,
            businessReady: true,
            isScopePending: false,
            stale: vi.fn(),
          } as unknown as PromotionState
        }
      />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByText(/全部商品扫描（27）/)).toBeTruthy());
  expect(calls[0].searchParams.get("detail")).toBe("summary");
  expect(calls.some((url) => url.pathname.endsWith("/objects"))).toBe(false);
  const details = screen.getByText(/^全部商品扫描（\d+）$/).closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await waitFor(() => expect(within(details).getByText("第一页商品")).toBeTruthy());
  const request = calls.find((url) => url.pathname.endsWith("/objects"))!;
  expect(request.searchParams.get("group")).toBe("products");
  expect(request.searchParams.get("level")).toBe("A级");
  expect(request.searchParams.get("page_size")).toBe("25");
  details.open = false;
  fireEvent(details, new Event("toggle"));
  const countBeforeOpen = calls.length;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await waitFor(() => expect(within(details).getByText("第一页商品")).toBeTruthy());
  expect(calls.length).toBe(countBeforeOpen);
  fireEvent.click(within(details).getByTitle("2"));
  await waitFor(() => expect(within(details).getByText("第二页商品")).toBeTruthy());
  const searchInput = within(details).getByRole("searchbox");
  fireEvent.change(searchInput, { target: { value: "末页" } });
  await waitFor(() => expect(within(details).getByText("末页命中商品")).toBeTruthy());
  expect(calls.at(-1)?.searchParams.get("page")).toBe("1");
  expect(within(details).getByText("匹配 1 / 27 个对象")).toBeTruthy();
  fireEvent.change(searchInput, { target: { value: "慢" } });
  await waitFor(() => expect(releaseSlow).toBeTruthy());
  fireEvent.change(searchInput, { target: { value: "快" } });
  await waitFor(() => expect(within(details).getByText("快命中商品")).toBeTruthy());
  expect(slowSignal?.aborted).toBe(true);
  await act(async () => releaseSlow?.());
  expect(within(details).queryByText("慢命中商品")).toBeNull();
  fireEvent.change(searchInput, { target: { value: "失败" } });
  await waitFor(() => expect(within(details).getByText("清单暂时无法加载")).toBeTruthy());
  fireEvent.click(within(details).getByRole("button", { name: /重\s*试/ }));
  await waitFor(() => expect(within(details).getByText("失败命中商品")).toBeTruthy());
  client.clear();
});

it("关闭的诊断清单不挂载表格行，展开分页保留全量搜索，重新打开读取新结果", () => {
  const products = Array.from({ length: 250 }, (_, i) => object(`合成商品${i}`));
  products[249].notes = ["末页核查证据"];
  products[0].level = "A级/B级混合";
  products[1].level = null;
  const data = diagnosis.parse({
    current_window: {},
    previous_window: {},
    overall: null,
    products,
    flagged_products: [object("需关注商品")],
    focused_products: products.slice(0, 4),
    shops: [object("合成店1"), object("合成店2")],
    categories: products.slice(0, 3),
    counts: { products: 250, flagged: 1, focused: 4, shops: 2, categories: 3 },
    coverage: {},
    rule_version: "test",
    rule_states: {},
    status: "insufficient_evidence",
  });
  const { container, rerender } = render(<DiagnosisView data={data} />);
  expect(container.querySelectorAll("tbody tr")).toHaveLength(1);
  const details = screen.getByText(/^全部商品扫描（\d+）$/).closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(within(details).getAllByRole("row")).toHaveLength(26);
  expect(within(details).getByText("共 250 个对象")).toBeTruthy();
  expect(within(details).getByText("等级：多等级（含A级、B级）")).toBeTruthy();
  expect(within(details).getByText("等级：未匹配")).toBeTruthy();
  fireEvent.click(within(details).getByTitle("2"));
  expect(within(details).getByText("合成商品25")).toBeTruthy();
  for (const [keyword, name] of [
    ["多等级", "合成商品0"],
    ["未匹配", "合成商品1"],
  ]) {
    fireEvent.change(within(details).getByRole("searchbox"), { target: { value: keyword } });
    expect(within(details).getAllByRole("row")).toHaveLength(2);
    expect(within(details).getByText(name)).toBeTruthy();
    expect(within(details).getByText("匹配 1 / 250 个对象")).toBeTruthy();
  }
  fireEvent.change(within(details).getByRole("searchbox"), { target: { value: "末页核查证据" } });
  expect(within(details).getAllByRole("row")).toHaveLength(2);
  expect(within(details).getByText("合成商品249")).toBeTruthy();
  expect(within(details).getByText("匹配 1 / 250 个对象")).toBeTruthy();
  details.open = false;
  fireEvent(details, new Event("toggle"));
  expect(within(details).queryByText("合成商品249")).toBeNull();
  rerender(
    <DiagnosisView
      data={{ ...data, products: [object("新版本商品")], counts: { ...data.counts, products: 1 } }}
    />,
  );
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(within(details).getAllByRole("row")).toHaveLength(2);
  expect(within(details).getByText("新版本商品")).toBeTruthy();
});

const summaryData = (counts: Record<string, number>) =>
  diagnosis.parse({
    current_window: { start: "2026-10-26", end: "2026-11-01" },
    previous_window: { start: "2026-10-19", end: "2026-10-25" },
    overall: object("汇总"),
    products: [],
    flagged_products: [],
    focused_products: [],
    shops: [],
    categories: [],
    counts,
    coverage: {},
    rule_version: "test",
    rule_states: {},
    status: counts.products ? "insufficient_evidence" : "no_records",
  });
it("分层清单保留各自身份、记录天数和资料人员，展开范围跟随数据更新", () => {
  const product = { ...object("重点商品甲"), focus: true, operator: "运营甲", current_days: 4 };
  const shop = { ...object("店铺汇总甲"), object_type: "shop", shop_name: "店铺汇总甲" };
  const category = {
    ...object("店铺汇总甲 · 防晒帽"),
    object_type: "category",
    shop_name: "店铺汇总甲",
    category: "防晒帽",
  };
  const data = {
    ...summaryData({ products: 1, focused: 1, shops: 1, categories: 1, current_records: 4 }),
    focused_products: [product],
    products: [product],
    shops: [shop],
    categories: [category],
    coverage: { observed_through: "2026-11-01" },
  };
  const { rerender } = render(<DiagnosisView data={data} />);
  const expand = (name: RegExp) => {
    const details = screen.getByText(name, { selector: "summary" }).closest("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    return within(details);
  };
  const focused = expand(/^重点款完整诊断/);
  expect(focused.getByText("负责人：运营甲")).toBeTruthy();
  const days = focused.getByText(/本期 \/ 基期有记录日期：4 \/ 7 天/);
  expect(days.closest("details")!.className).not.toContain("sop-rule-details");
  const groups = expand(/^按店铺 \/ 品类下钻/);
  expect(groups.getByText("防晒帽", { selector: "b" })).toBeTruthy();
  expect(groups.queryByText("负责人：未分配")).toBeNull();
  expect(groups.getAllByText(/负责人：按商品明细分配/)).toHaveLength(2);
  expect(groups.getByText(/金额不要重复相加/)).toBeTruthy();
  const scan = expand(/^全部商品扫描/);
  expect(scan.getByText(/数据截至：2026-11-01/)).toBeTruthy();
  rerender(
    <DiagnosisView
      data={{
        ...data,
        coverage: { observed_through: "2026-11-02" },
        counts: { ...data.counts, current_records: 6 },
        products: [{ ...product, current_days: 6, operator: "运营乙" }],
      }}
    />,
  );
  expect(scan.getByText(/数据截至：2026-11-02/)).toBeTruthy();
  expect(scan.getByText(/本期统计6条推广记录/)).toBeTruthy();
  expect(scan.getByText("负责人：运营乙")).toBeTruthy();
  expect(scan.queryByText("负责人：运营甲")).toBeNull();
  expect(scan.getByText(/本期 \/ 基期有记录日期：6 \/ 7 天/)).toBeTruthy();
});
it("优先复核随范围更新商品、等级及花费，空范围和未触发时不残留旧推荐", () => {
  const data = summaryData({ products: 2, flagged: 1, fully_assessed: 2, p1: 1, p2: 0 });
  const first = object("合成预警商品");
  first.priority = "P1";
  first.current.spend = {
    value: "410.12",
    type: "currency",
    status: "observed",
    label: "花费",
    reason: null,
  };
  data.flagged_products = [first];
  const { rerender } = render(<DiagnosisView data={data} />);
  const priority = () => within(screen.getByText("优先动作与机会").closest("article")!);
  expect(priority().getByText(/优先复核 合成预警商品（花费 ¥410.12）/)).toBeTruthy();
  expect(priority().getByText(/同级按花费排序；花费不是损失金额/)).toBeTruthy();

  const next = { ...first, label: "另一个范围的关注商品", priority: "P2" };
  next.current = { spend: { ...first.current.spend, value: "690.83" } };
  rerender(
    <DiagnosisView
      data={{ ...data, flagged_products: [next], counts: { ...data.counts, p1: 0, p2: 1 } }}
    />,
  );
  expect(priority().queryByText(/合成预警商品|410.12/)).toBeNull();
  expect(priority().getByText(/优先复核 另一个范围的关注商品（花费 ¥690.83）/)).toBeTruthy();

  rerender(
    <DiagnosisView
      data={{
        ...data,
        flagged_products: [],
        counts: { products: 2, fully_assessed: 2, flagged: 0 },
      }}
    />,
  );
  expect(priority().queryByText(/优先复核|花费高的/)).toBeNull();
  expect(priority().getByText(/暂无已确认阈值触发的商品预警/)).toBeTruthy();
  rerender(<DiagnosisView data={summaryData({ products: 2, unrated: 2, flagged: 0 })} />);
  expect(priority().queryByText(/未触发提醒|优先复核/)).toBeNull();
  expect(priority().getByText(/本次没有可评级的商品/)).toBeTruthy();
  rerender(<DiagnosisView data={summaryData({ products: 0 })} />);
  expect(screen.queryByText("优先动作与机会")).toBeNull();
  expect(screen.getByText("当前筛选范围没有推广数据。")).toBeTruthy();
});
it("等级代码与名称同步显示，P3沿原观察结果且可搜索，查阅位置明确", () => {
  const data = summaryData({
    products: 3,
    flagged: 3,
    p1: 1,
    p2: 1,
    observation: 1,
    fully_assessed: 3,
    current_records: 17,
    unmatched_records: 2,
    missing_product_ids: 0,
  });
  const entries = ["P1", "P2", "观察"].map((priority, index) => ({
    ...object(`合成等级商品${index}`),
    priority,
    findings: [
      {
        level: priority,
        rule: `合成规则${index}`,
        evidence: `合成证据${index}`,
        action: `合成建议${index}`,
        status: "threshold_observed",
      },
    ],
  }));
  data.flagged_products = entries;
  data.products = entries;
  render(<DiagnosisView data={data} />);
  const problem = screen.getByText("问题定位").closest("article")!;
  expect(problem.textContent).toMatch(/P1 预警 1 个，P2 关注 1 个，P3 观察 1 个/);
  const list = screen.getByText("商品异常与行动清单（3）").closest("details")!;
  fireEvent.change(within(list).getByRole("searchbox"), { target: { value: "P3" } });
  expect(within(list).getAllByRole("row")).toHaveLength(2);
  expect(within(list).getByText("合成等级商品2")).toBeTruthy();
  expect(within(list).getByText("合成建议2")).toBeTruthy();
  expect(within(list).queryByText("合成等级商品0")).toBeNull();
  const scan = screen.getByText("全部商品扫描（3）").closest("details")!;
  scan.open = true;
  fireEvent(scan, new Event("toggle"));
  expect(within(scan).getByText(/本期统计17条推广记录/)).toBeTruthy();
  expect(within(scan).getByText(/商品资料未匹配完整2条/)).toBeTruthy();
  const rules = screen.getByText("查看判断规则").closest("details")!;
  rules.open = true;
  fireEvent(rules, new Event("toggle"));
  expect(within(rules).getByRole("link", { name: "上方“核心指标趋势”" }).getAttribute("href")).toBe(
    "#promotion-trend",
  );
  expect(within(rules).getByText(/不是AI生成/)).toBeTruthy();
});
it("整体提醒展示具体变化，切换范围后同步更新证据", () => {
  const data = summaryData({ products: 1, fully_assessed: 1 });
  const finding = {
    level: "P2",
    rule: "花费增长≥20%，交易额增幅不足花费增幅一半",
    evidence: "花费变化 30.00%；交易额变化 10.00%",
    action: "查看新增花费集中在哪些商品。",
    status: "threshold_observed",
  };
  data.overall!.findings = [finding];
  const { rerender } = render(<DiagnosisView data={data} />);
  const problem = () => within(screen.getByText("问题定位").closest("article")!);
  expect(problem().getByText(/花费变化 30.00%；交易额变化 10.00%/)).toBeTruthy();

  rerender(
    <DiagnosisView
      data={{
        ...data,
        overall: {
          ...data.overall!,
          findings: [{ ...finding, evidence: "花费变化 40.00%；交易额变化 15.00%" }],
        },
      }}
    />,
  );
  expect(problem().queryByText(/花费变化 30.00%/)).toBeNull();
  expect(problem().getByText(/花费变化 40.00%；交易额变化 15.00%/)).toBeTruthy();
});
it("未评级不显示零异常；加载、失败、正常无数据不生成诊断结论", () => {
  const { rerender } = render(<DiagnosisView data={undefined} pending />);
  expect(screen.queryByText(/来源完整性/)).toBeNull();
  expect(screen.queryByText(/问题定位/)).toBeNull();
  rerender(
    <DiagnosisView
      data={summaryData({
        products: 2,
        unrated: 2,
        partial: 0,
        fully_assessed: 0,
        flagged: 0,
        p1: 0,
        p2: 0,
      })}
    />,
  );
  expect(screen.getByText("暂不能判断")).toBeTruthy();
  expect(screen.queryByText(/预警 0/)).toBeNull();
  rerender(<DiagnosisView data={summaryData({ products: 0 })} />);
  expect(screen.getByText("当前筛选范围没有推广数据。")).toBeTruthy();
  expect(screen.queryByText(/问题定位/)).toBeNull();
  rerender(<DiagnosisView data={undefined} error={new Error("查询失败")} />);
  expect(screen.getByText("SOP诊断查询未完成")).toBeTruthy();
  expect(screen.queryByText(/问题定位/)).toBeNull();
});
it("已判断项目和暂不能判断的商品分开显示", () => {
  render(
    <DiagnosisView
      data={summaryData({
        products: 4,
        unrated: 1,
        partial: 2,
        fully_assessed: 1,
        flagged: 0,
        p1: 0,
        p2: 0,
        observation: 0,
      })}
    />,
  );
  expect(screen.getByText("已判断项目未触发提醒")).toBeTruthy();
  expect(screen.getByText(/已完整判断 1 款；部分可判断 2 款；未评级 1 款/)).toBeTruthy();
});

it("未知成交不写成0，清单保留数值并支持按可见优先级搜索", () => {
  const item = object("测试商品");
  item.priority = "P1";
  item.current.ctr = {
    value: "0.05",
    status: "partial",
    reason: null,
    label: "点击率",
    type: "percent",
  };
  item.clues = [
    {
      label: "CTR",
      current: "0.05",
      previous: "0.1",
      change: "-0.5",
      evidence: "",
      action: "查看主图",
      status: "reference",
    },
  ];
  item.exclusions.current = {
    missing_spend_rows: 1,
    missing_revenue_rows: 1,
    revenue_without_spend: "0",
  };
  const data = summaryData({ products: 1, flagged: 1, partial: 1 });
  data.overall = item;
  data.flagged_products = [item];
  render(<DiagnosisView data={data} />);
  const list = screen.getByText("商品异常与行动清单（1）").closest("details")!;
  expect(within(list).getByText("点击率线索：")).toBeTruthy();
  fireEvent.change(within(list).getByRole("searchbox"), { target: { value: "预警" } });
  expect(within(list).getByText("测试商品")).toBeTruthy();
  const rules = within(list).getByText("查看判断依据").closest("details")!;
  rules.open = true;
  fireEvent(rules, new Event("toggle"));
  expect(within(rules).queryByText(/¥0.00.*成交/)).toBeNull();
  expect(
    within(rules).getByText("本期部分商品花费未单独提供；成交照常计入，花费按已提供金额统计。"),
  ).toBeTruthy();
});

it("SOP合并相同的限制原因，显示可用ROI，不把未评级当无异常", () => {
  const data = summaryData({ products: 1, partial: 1, fully_assessed: 0, unrated: 0 });
  const item = data.overall!;
  item.current.roi = {
    value: "7.65",
    status: "partial",
    type: "ratio",
    label: "实际投产比",
    reason: null,
  };
  item.rule_results = [
    { key: "roi_drop", label: "投产比下降", state: "unavailable", reason: "本期花费未单独提供" },
    {
      key: "net_roi_drop",
      label: "净投产下降",
      state: "unavailable",
      reason: "本期花费未单独提供",
    },
    { key: "zero_deals", label: "有花费无成交", state: "clear", reason: null },
  ];
  render(<DiagnosisView data={data} />);
  const result = screen.getByText("经营结果").closest("article")!;
  expect(result.textContent).toContain("7.65");
  expect(result.textContent).not.toMatch(/参考投产|未知|暂不能判断/);
  const details = within(result).getByText("查看判断依据").closest("details")!;
  expect(within(details).getAllByText(/本期花费未单独提供/)).toHaveLength(1);
  expect(details.textContent).toContain("投产比下降、净投产下降");
  expect(details.textContent).toContain("有花费无成交：未触发");
});
