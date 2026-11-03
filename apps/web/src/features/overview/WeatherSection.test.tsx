// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { WeatherSection } from "./WeatherSection";
import { useWeather, type WeatherState } from "./weather";

vi.mock("./weather", () => ({ useWeather: vi.fn() }));
beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: () => ({
      matches: false,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
    }),
  });
});
afterEach(cleanup);

function state(): WeatherState {
  return {
    meta: {
      data_version: "weather-v2",
      status: "partial",
      default_publication_id: "weather-2",
      publications: [],
      last_success_at: "2026-11-02T08:15:00+08:00",
      last_attempt_at: "2026-11-02T08:15:00+08:00",
      last_attempt_status: "succeeded",
      error_code: null,
    },
    publication: {
      id: "weather-2",
      issue_date: "2026-11-02",
      revision_no: 2,
      document: {
        title: "全国关键天气",
        issue_time: "2026-11-02T08:00:00+08:00",
        focus_regions: ["浙江", "江苏"],
        today_rain: {
          regions: ["浙江"],
          detail: "降水源文片段，保留原地区和量级。".repeat(20),
          status: "parsed",
        },
        today_temperature: {
          regions: [],
          detail: "未识别到明确今日气温预报文本，待核原文。",
          status: "unidentified",
        },
        next_7_days: Array.from({ length: 6 }, (_, i) => ({
          period: `时段${i + 1}`,
          regions: i === 2 ? ["浙江"] : [],
          detail: `预报过程${i + 1}的完整来源正文`,
          status: i === 5 ? "boundary" : "parsed",
        })),
        sources: [
          {
            name: "天气公报",
            url: "https://www.nmc.cn/publish/weather-bulletin/index.htm",
            issue_time: "2026-11-02T08:00:00+08:00",
            text: "公报归档原文",
          },
          {
            name: "中期预报",
            url: "https://www.nmc.cn/publish/bulletin/mid-range.htm",
            issue_time: "2026-11-01T10:00:00+08:00",
            text: "中期归档原文",
          },
        ],
        forecast_url: "https://www.nmc.cn/publish/precipitation/1-day.html",
        reference_url: "https://www.nmc.cn/",
        group_card_link: null,
        note: "解析边界与归档说明",
      },
    },
    selectedId: "weather-2",
    selection: "latest",
    options: [
      {
        id: "weather-2",
        issue_date: "2026-11-02",
        revision_no: 2,
        issue_time: "2026-11-02T08:00:00+08:00",
      },
    ],
    select: vi.fn(),
    refresh: vi.fn(),
    loading: false,
    refreshing: false,
    error: null,
  };
}

it("天气首屏展示中文时间和缺项状态，完整预报与来源默认折叠", () => {
  const value = state();
  vi.mocked(useWeather).mockReturnValue(value);
  render(<WeatherSection />);
  expect(screen.getByRole("heading", { name: "2026年11月02日08时 发布" })).toBeTruthy();
  expect(screen.getByText("天气部分字段待核")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText("涉及地区：未识别")).toBeTruthy();
  expect(screen.queryByText("涉及地区：无")).toBeNull();
  const outlook = screen.getByRole("group", { name: "未来7天天气" }) as HTMLDetailsElement;
  expect(outlook.open).toBe(false);
  expect(within(outlook).getByText(/共 6 条预报过程/)).toBeTruthy();
  expect(screen.getByText("含待核时段")).toBeTruthy();
  fireEvent.click(within(outlook).getByText("查看全部预报"));
  expect(outlook.open).toBe(true);
  expect(within(outlook).getByText("预报过程6的完整来源正文")).toBeTruthy();
  fireEvent.click(within(outlook).getByText("收起预报"));
  expect(outlook.open).toBe(false);
  const records = screen.getByRole("group", { name: "天气来源与更新记录" }) as HTMLDetailsElement;
  expect(records.open).toBe(false);
  fireEvent.click(within(records).getByText("来源与更新记录"));
  expect(records.open).toBe(true);
  expect(within(records).getByText("公报归档原文")).toBeTruthy();
  expect(records.textContent).toMatch(/修订\s*2/);
});

it("今日长文可原样展开，切换发布后恢复摘要并更新上海时间", () => {
  const value = state();
  vi.mocked(useWeather).mockReturnValue(value);
  const { rerender } = render(<WeatherSection />);
  const expand = screen.getByRole("button", { name: "展开今日降水全文" });
  expect(expand.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(expand);
  expect(
    screen.getByRole("button", { name: "收起今日降水全文" }).getAttribute("aria-expanded"),
  ).toBe("true");
  expect(screen.getAllByText(value.publication!.document.today_rain.detail)).toHaveLength(1);
  const next = state();
  next.publication!.id = "weather-history";
  next.publication!.issue_date = "2026-11-01";
  next.publication!.document.issue_time = "2026-10-31T16:30:00Z";
  next.selection = "weather-history";
  next.selectedId = "weather-history";
  next.options.push({
    id: "weather-history",
    issue_date: "2026-11-01",
    revision_no: 2,
    issue_time: next.publication!.document.issue_time,
  });
  vi.mocked(useWeather).mockReturnValue(next);
  rerender(<WeatherSection />);
  expect(screen.getByRole("heading", { name: "2026年11月01日00时30分 发布" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "展开今日降水全文" }).getAttribute("aria-expanded"),
  ).toBe("false");
  expect(screen.getByRole("link", { name: "当前官方预报 ↗" }).getAttribute("href")).toBe(
    next.publication!.document.forecast_url,
  );
});

it("失败保旧只显示一条紧凑告警，保留影响日期、原因和本地重试", () => {
  const value = state();
  value.error = "天气查询失败";
  value.meta!.status = "source_unavailable";
  value.meta!.last_attempt_status = "failed";
  value.meta!.error_code = "SOURCE_TIMEOUT";
  vi.mocked(useWeather).mockReturnValue(value);
  render(<WeatherSection />);
  const alerts = screen.getAllByRole("alert");
  expect(alerts).toHaveLength(1);
  expect(alerts[0].textContent).toContain("2026-11-02");
  expect(alerts[0].textContent).toContain("SOURCE_TIMEOUT");
  fireEvent.click(screen.getByRole("button", { name: "重试天气本地查询" }));
  expect(value.refresh).toHaveBeenCalledOnce();
});

it.each(["来源尚未提供明确时刻", "2026-02-30T08:00:00+08:00", "2026-11-02 08:00"])(
  "不能解析或缺时区的发布时间保留原文：%s",
  (issueTime) => {
    const value = state();
    value.publication!.document.issue_time = issueTime;
    value.publication!.document.next_7_days = [];
    vi.mocked(useWeather).mockReturnValue(value);
    render(<WeatherSection />);
    expect(screen.getByRole("heading", { name: `${issueTime} 发布` })).toBeTruthy();
    expect(screen.getByText("未取得可靠裁剪的过程文本，不能据此判定无风险。")).toBeTruthy();
  },
);
