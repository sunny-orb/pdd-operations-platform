// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { StoreNet } from "./StoreNet";
import type { Overview } from "./contracts";

afterEach(cleanup);
const metric = (value: string | null, status = "observed", type = "currency") => ({
  value,
  status,
  type,
  label: "",
  reason: "不应展示的技术原因",
});
function overview({
  count = "2",
  net = "80",
  spend = "10",
  roi = "8",
  status = "observed",
  product = "80",
}: {
  count?: string;
  net?: string | null;
  spend?: string | null;
  roi?: string | null;
  status?: string;
  product?: string | null;
} = {}): Overview {
  return {
    metrics: { spend: metric(spend) },
    net: { net_revenue: metric(net, status), net_roi: metric(roi, status, "ratio") },
    current_record_count: count,
    dataset_record_count: "20",
    series: [],
    funnel: [],
    top5: [],
    windows: [],
    coverage: { missing_count: 1 },
    channel_summaries: [
      {
        channel: "product_promotion",
        label: "商品推广",
        metrics: {},
        net: { net_revenue: metric(product) },
      },
    ],
  };
}

it("有数时只显示数值；某店缺日不增加净额提示，真实0仍显示", () => {
  const { container, rerender } = render(<StoreNet data={overview()} shop="799193231" />);
  expect(container.querySelector("p")).toBeNull();
  expect(container.textContent).toContain("¥80.00");
  rerender(<StoreNet data={overview({ net: "0", roi: "0" })} channel="product_promotion" />);
  expect(container.querySelector("p")).toBeNull();
  expect(container.textContent).toContain("¥0.00");
});

it.each([
  ["", "", "所选期间暂无推广数据。"],
  ["product_promotion", "799193231", "所选期间暂无推广数据。"],
  ["star_shop", "799193231", "该店暂无明星店铺推广数据。"],
  ["star_shop", "663599807", "所选期间暂无推广数据。"],
])("无记录优先于缺项状态：%s / %s", (channel, shop, message) => {
  render(
    <StoreNet
      data={overview({ count: "0", net: null, roi: null, status: "missing_channel_input" })}
      channel={channel}
      shop={shop}
    />,
  );
  expect(screen.getByText(message)).toBeTruthy();
  expect(screen.queryByText(/可切换/)).toBeNull();
});

it("明星单渠道不提示合计；合计只在商品净额有值时提供切换建议（含0）", () => {
  const data = overview({ net: null, roi: null, status: "missing_channel_input" });
  const { rerender } = render(<StoreNet data={data} channel="star_shop" />);
  expect(screen.getByText("明星店铺暂无净交易额，无法计算净投产。")).toBeTruthy();
  rerender(<StoreNet data={data} />);
  expect(
    screen.getByText("明星店铺暂无净交易额，无法计算合计。可切换“商品推广”查看。"),
  ).toBeTruthy();
  rerender(
    <StoreNet
      data={overview({ net: null, roi: null, status: "missing_channel_input", product: null })}
    />,
  );
  expect(screen.getByText("明星店铺暂无净交易额，无法计算合计。")).toBeTruthy();
  rerender(
    <StoreNet
      data={overview({ net: null, roi: null, status: "missing_channel_input", product: "0" })}
    />,
  );
  expect(screen.getByText(/可切换/)).toBeTruthy();
});

it("历史记录缺净额不能误报为花费为0", () => {
  const data = overview({ net: null, roi: null, spend: "0", status: "partial" });
  data.net.net_roi.status = "zero_denominator";
  render(<StoreNet data={data} channel="product_promotion" />);
  expect(screen.getByText("部分推广记录没有净交易额，暂无法计算合计。")).toBeTruthy();
  expect(screen.queryByText(/技术原因|花费为0/)).toBeNull();
});

it.each([
  ["0", "推广花费为0，不计算净投产。"],
  [null, "暂无完整的推广花费，无法计算净投产。"],
])("净额有值，花费%s时仅净投产无值", (spend, message) => {
  const { container } = render(<StoreNet data={overview({ spend, roi: null })} />);
  expect(screen.getByText(message)).toBeTruthy();
  expect(container.textContent).toContain("¥80.00");
});

it("切换筛选的加载期和请求失败不冒充无数据，重试成功后清除提示", () => {
  const { rerender } = render(<StoreNet data={overview()} />);
  rerender(<StoreNet data={undefined} />);
  expect(screen.getByText("正在读取推广数据…")).toBeTruthy();
  rerender(<StoreNet data={undefined} failed />);
  expect(screen.getByText("暂时无法加载，请重试。")).toBeTruthy();
  expect(screen.queryByText(/暂无推广数据/)).toBeNull();
  rerender(<StoreNet data={overview()} />);
  expect(screen.queryByText(/重试|正在读取/)).toBeNull();
});
