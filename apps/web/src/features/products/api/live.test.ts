import { afterEach, expect, it, vi } from "vitest";
import { requestProducts } from "./productsApi";
import { buildDefaultFilters } from "../state/filters";
import type { Options, Overview, Scores, Score, Diagnosis } from "./contracts";

afterEach(() => vi.unstubAllGlobals());
/** 显式启用的本机真实PG/API联调；不写库，不把本次数据固化成前端样例。 */
it.skipIf(import.meta.env.VITE_M1_LIVE_TEST !== "1")(
  "九个正式GET响应可被前端契约解析，评分版本一致",
  async () => {
    const native = globalThis.fetch;
    vi.stubGlobal("fetch", (path: string, init?: RequestInit) =>
      native(`http://127.0.0.1:8000${path}`, init),
    );
    const f = buildDefaultFilters();
    const o = { mode: "api" as const, scenario: "normal" as const };
    const catalog = await requestProducts<Options>("filters", "overview", f, o);
    expect(catalog.meta.isPreview).toBe(false);
    expect(catalog.data?.productTotal).toBeGreaterThan(0);
    const scoped = {
      ...f,
      dateFrom: catalog.data!.dateRange.from,
      dateTo: catalog.data!.dateRange.to,
    };
    const pinned = { ...o, dataVersion: catalog.meta.dataVersion };
    const overview = await requestProducts<Overview>("overview", "overview", scoped, pinned);
    expect(typeof overview.data!.metrics[0].value).toBe("string");
    expect(overview.data!.metrics.find((m) => m.key === "favorites")?.value).toBeNull();
    for (const endpoint of ["brief", "trend", "diagnosis", "alerts", "days"]) {
      const result = await requestProducts(
        endpoint,
        endpoint === "days" ? "days" : "overview",
        scoped,
        pinned,
      );
      expect(result.meta.dataVersion).toBe(catalog.meta.dataVersion);
      expect(result.data).not.toBeNull();
    }
    const diagnosis = await requestProducts<Diagnosis>("diagnosis", "overview", scoped, {
      ...pinned,
      metric: "aov",
    });
    expect(diagnosis.data!.headline).toContain("客单价");
    const sc = await requestProducts<Options>("filters", "scores", f, pinned);
    const scoreOptions = { ...pinned, scoreVersion: sc.meta.scoreVersion };
    const scores = await requestProducts<Scores>("scores", "scores", f, scoreOptions);
    expect(scores.data!.total).toBeGreaterThan(0);
    const detail = await requestProducts<Score>(
      `scores/${scores.data!.items[0].id}`,
      "scores",
      f,
      scoreOptions,
    );
    expect(detail.data!.metrics).toHaveLength(10);
    expect(detail.meta.scoreVersion).toBe(scores.meta.scoreVersion);
    for (const comparison of ["products", "relative"] as const) {
      const trend = await requestProducts(
        "trend",
        "overview",
        { ...scoped, products: [catalog.data!.products[0].key] },
        { ...pinned, comparison },
      );
      expect(trend.data).not.toBeNull();
    }
  },
  30000,
);
