import { afterEach, describe, expect, it, vi } from "vitest";
import { parseEnvelope, type Days, type Overview, type Scores } from "./contracts";
import { buildDefaultFilters, productKey } from "../state/filters";
import { requestProducts } from "./productsApi";
import { getPreviewResponse } from "../preview/provider";
const filters = { ...buildDefaultFilters(), dateFrom: "2026-10-01", dateTo: "2026-10-14" };
const options = { mode: "preview" as const, scenario: "normal" as const };
afterEach(() => vi.unstubAllGlobals());

describe("商品契约与开发适配", () => {
  it("正式参数使用后端字段、复合商品键和评分独立版本", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetch);
    const selected = {
      ...filters,
      products: [productKey("663599807", "1007941996178")],
      judgment: "hold",
    };
    await requestProducts("trend", "days", selected, {
      mode: "api",
      scenario: "normal",
      metric: "aov",
      dataVersion: "facts",
    }).catch(() => {});
    const url = new URL(fetch.mock.calls[0][0], "http://localhost");
    expect(url.searchParams.get("scope")).toBe("detail");
    expect(url.searchParams.get("metric")).toBe("revenue_per_buyer");
    expect(url.searchParams.getAll("product_key")).toEqual(["pdd:663599807:1007941996178"]);
    expect(url.searchParams.has("decision")).toBe(false);
    await requestProducts("scores", "scores", selected, {
      mode: "api",
      scenario: "normal",
      scoreVersion: "score",
      dataVersion: "facts",
    }).catch(() => {});
    const scoreUrl = new URL(fetch.mock.calls[1][0], "http://localhost");
    expect(scoreUrl.searchParams.get("score_version")).toBe("score");
    expect(scoreUrl.searchParams.get("decision")).toBe("hold");
    expect(scoreUrl.searchParams.has("date_from")).toBe(false);
    expect(scoreUrl.searchParams.has("level")).toBe(false);
  });
  it("未接入不访问网络，也不生成经营零值", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const result = await requestProducts<Overview>("overview", "overview", filters, {
      mode: "offline",
      scenario: "normal",
    });
    expect(fetch).not.toHaveBeenCalled();
    expect(result.data).toBeNull();
    expect(result.availability).toBe("not_connected");
    expect(result.meta.dataThrough).toBeNull();
  });
  it("样例底表支持跨店ID隔离与分页且零和缺数不混淆", async () => {
    const full = parseEnvelope<Days>(
      "days",
      await getPreviewResponse("days", "days", { ...filters, pageSize: 100 }, options),
    );
    expect(full.meta.isPreview).toBe(true);
    expect(full.data!.items.some((row) => row.revenue === "0.00")).toBe(true);
    expect(full.data!.items.some((row) => row.revenue === null)).toBe(false);
    const partial = parseEnvelope<Days>(
      "days",
      await getPreviewResponse(
        "days",
        "days",
        { ...filters, pageSize: 100 },
        { ...options, scenario: "partial" },
      ),
    );
    expect(partial.data!.items.some((row) => row.revenue === null)).toBe(true);
    const selected = {
      ...filters,
      products: [productKey("demo-official", "sample-001")],
      pageSize: 5,
    };
    const result = parseEnvelope<Days>(
      "days",
      await getPreviewResponse("days", "days", selected, options),
    );
    expect(result.data!.items).toHaveLength(5);
    expect(result.data!.total).toBe(14);
    expect(result.data!.items.every((row) => row.product.shopId === "demo-official")).toBe(true);
  });
  it("样例评分判断筛选与总数来自相同范围", async () => {
    const result = parseEnvelope<Scores>(
      "scores",
      await getPreviewResponse("scores", "scores", { ...filters, judgment: "partial" }, options),
    );
    expect(result.data!.total).toBeGreaterThan(0);
    expect(result.data!.items.every((row) => row.status === "partial")).toBe(true);
  });
  it("契约拒绝非法金额而非改成0", async () => {
    const raw = (await getPreviewResponse("days", "days", filters, options)) as { data: Days };
    raw.data.items[0].revenue = "错误金额";
    expect(() => parseEnvelope("days", raw)).toThrow("格式不符合约定");
  });
  it("取消的请求不能发布结果", async () => {
    const abort = new AbortController();
    abort.abort();
    await expect(requestProducts("days", "days", filters, options, abort.signal)).rejects.toThrow();
  });
  it("样例错误不回退为正常数字，空结果与未接入独立", async () => {
    await expect(
      getPreviewResponse("overview", "overview", filters, { ...options, scenario: "error" }),
    ).rejects.toThrow("样例");
    const empty = await getPreviewResponse("days", "days", filters, {
      ...options,
      scenario: "empty",
    });
    expect(empty.availability).toBe("empty");
    expect(empty.data).toBeNull();
  });
});

describe("开发样例范围和版本一致性", () => {
  it("近7日独立窗口不被总览开始日误截断", async () => {
    const narrow = parseEnvelope<Overview>(
      "overview",
      await getPreviewResponse(
        "overview",
        "overview",
        { ...filters, dateFrom: "2026-10-14" },
        options,
      ),
    );
    const full = parseEnvelope<Overview>(
      "overview",
      await getPreviewResponse("overview", "overview", filters, options),
    );
    expect(narrow.data!.periods[0].value).toBe(full.data!.periods[0].value);
  });
  it("样例响应不能被调用者修改后污染后续请求", async () => {
    const first = parseEnvelope<Days>(
      "days",
      await getPreviewResponse("days", "days", filters, options),
    );
    first.data!.items[0].revenue = "99999999";
    const next = parseEnvelope<Days>(
      "days",
      await getPreviewResponse("days", "days", filters, options),
    );
    expect(next.data!.items[0].revenue).not.toBe("99999999");
  });
  it("旧版本明确拒绝，不混合返回新版样例", async () => {
    await expect(
      getPreviewResponse("overview", "overview", filters, { ...options, dataVersion: "obsolete" }),
    ).rejects.toMatchObject({ status: 409 });
  });
});

describe("状态与日期契约边界", () => {
  it("拒绝已就绪却无data的矛盾响应", async () => {
    const raw = await getPreviewResponse("overview", "overview", filters, options);
    expect(() => parseEnvelope("overview", { ...raw, data: null })).toThrow("格式不符合约定");
  });
  it.each(["2026-02-30", "2026-13-01", "2026-2-01", "bad-date"])(
    "非法自然日 %s 不能开始请求",
    async (dateFrom) => {
      await expect(
        requestProducts("days", "days", { ...filters, dateFrom }, options),
      ).rejects.toThrow("日期");
    },
  );
  it("反向日期明确拒绝而非颠倒", async () => {
    await expect(
      requestProducts(
        "days",
        "days",
        { ...filters, dateFrom: "2026-10-14", dateTo: "2026-10-01" },
        options,
      ),
    ).rejects.toThrow("日期");
  });
  it("有缺失金额的范围不冒充完整总额", async () => {
    const result = parseEnvelope<Overview>(
      "overview",
      await getPreviewResponse("overview", "overview", filters, {
        ...options,
        scenario: "partial",
      }),
    );
    expect(result.meta.completeness).toBe("partial");
    expect(result.data!.metrics.find((m) => m.key === "revenue")!.value).toBeNull();
    expect(result.data!.metrics.find((m) => m.key === "aov")!.value).toBeNull();
    const complete = parseEnvelope<Overview>(
      "overview",
      await getPreviewResponse(
        "overview",
        "overview",
        { ...filters, shop: "demo-official" },
        options,
      ),
    );
    expect(complete.meta.completeness).toBe("complete");
    expect(complete.data!.metrics.find((m) => m.key === "revenue")!.value).not.toBeNull();
  });
});

describe("趋势契约不能把未知结构当作曲线", () => {
  it.each([
    [{ unexpected: "not-a-date" }],
    [{ date: "2026-02-30", current: 1, lastYear: 2 }],
    [{ date: "2026-10-01", current: "1", lastYear: 2 }],
    [{ date: "2026-10-01", current: 1, lastYear: 2, product1: "非法值" }],
  ])("拒绝非法日期、缺值及字符串动态序列 %j", async (point) => {
    const raw = await getPreviewResponse("trend", "overview", filters, options);
    const value = { ...raw, data: { ...(raw.data as object), points: [point] } };
    expect(() => parseEnvelope("trend", value)).toThrow("格式不符合约定");
  });
  it("series指定的动态数值列必须存在", async () => {
    const raw = await getPreviewResponse("trend", "overview", filters, options);
    const value = {
      ...raw,
      data: {
        ...(raw.data as object),
        series: [{ key: "absent", label: "未知序列", color: "#000" }],
      },
    };
    expect(() => parseEnvelope("trend", value)).toThrow("格式不符合约定");
  });
});
