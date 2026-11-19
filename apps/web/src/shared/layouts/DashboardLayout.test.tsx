// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Grid } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { DashboardLayout } from "./DashboardLayout";
import { dashboardPages } from "./dashboardNavigation";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
});
afterEach(cleanup);

it.each(dashboardPages)("$title可独立使用共享壳且不依赖商品页", ({ id, title }) => {
  render(
    <MemoryRouter>
      <DashboardLayout currentPage={id} sections={[{ id: "summary", title: "页面摘要" }]}>
        <main>
          <section id="summary">
            <h1>{title}</h1>
          </section>
        </main>
      </DashboardLayout>
    </MemoryRouter>,
  );
  const header = screen.getByRole("banner", { name: "看板导航" });
  expect(header.parentElement?.classList.contains("dashboard-sections-content")).toBe(true);
  expect(within(header).getByRole("link", { name: "返回运营看板中心" }).getAttribute("href")).toBe(
    "/",
  );
  const inventoryUrl =
    "https://stock-monitor.skills.kktree.cn/v2/#view=inventory&sort=sales7_market&order=desc";
  const destinations = within(header)
    .getAllByRole("link")
    .map((link) => link.getAttribute("href"));
  expect(destinations).toEqual(["/", ...dashboardPages.map((page) => page.path), inventoryUrl]);
  const switcher = within(header).getByRole("navigation", { name: "看板切换" });
  expect(switcher.previousElementSibling?.classList.contains("dashboard-topbar-context")).toBe(
    true,
  );
  expect(switcher.firstElementChild?.textContent).toContain("运营看板中心");
  const inventory = within(switcher).getByRole("link", { name: "库存监控" });
  expect(switcher.lastElementChild).toBe(inventory);
  expect(inventory.previousElementSibling?.textContent).toBe("售后分析");
  expect(inventory.getAttribute("target")).toBe("_blank");
  expect(within(switcher).getAllByRole("link")).toHaveLength(dashboardPages.length + 1);
  const current = within(switcher).getByRole("link", { name: title });
  expect(current.getAttribute("aria-current")).toBe("page");
  expect(
    within(switcher)
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page"),
  ).toHaveLength(1);

  expect(screen.getByRole("navigation", { name: "本页目录" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: title })).toBeTruthy();
});

it("没有页内区块时不生成空目录", () => {
  render(
    <MemoryRouter>
      <DashboardLayout currentPage="overview">
        <main>经营中心</main>
      </DashboardLayout>
    </MemoryRouter>,
  );
  expect(screen.queryByRole("navigation", { name: "本页目录" })).toBeNull();
  expect(screen.getByRole("main").textContent).toBe("经营中心");
});

it("目录支持键盘调宽、双击恢复且重新挂载使用默认宽度", () => {
  const breakpoint = vi.spyOn(Grid, "useBreakpoint").mockReturnValue({ lg: true });
  const page = () => (
    <MemoryRouter>
      <DashboardLayout currentPage="sales" sections={[{ id: "summary", title: "摘要" }]}>
        <main>正文</main>
      </DashboardLayout>
    </MemoryRouter>
  );
  const view = render(page());
  const handle = screen.getByRole("separator", { name: "调整目录宽度" });
  expect(handle.getAttribute("aria-valuenow")).toBe("192");
  fireEvent.keyDown(handle, { key: "ArrowRight" });
  expect(handle.getAttribute("aria-valuenow")).toBe("200");
  fireEvent.keyDown(handle, { key: "End" });
  expect(handle.getAttribute("aria-valuenow")).toBe("280");
  fireEvent.keyDown(handle, { key: "ArrowRight" });
  expect(handle.getAttribute("aria-valuenow")).toBe("280");
  fireEvent.keyDown(handle, { key: "Home" });
  expect(handle.getAttribute("aria-valuenow")).toBe("176");
  fireEvent.doubleClick(handle);
  expect(handle.getAttribute("aria-valuenow")).toBe("192");
  fireEvent.keyDown(handle, { key: "ArrowRight" });
  fireEvent.click(screen.getByRole("button", { name: "收起本页目录" }));
  expect(screen.queryByRole("separator")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "展开本页目录" }));
  expect(screen.getByRole("separator").getAttribute("aria-valuenow")).toBe("200");
  view.unmount();
  render(page());
  expect(screen.getByRole("separator").getAttribute("aria-valuenow")).toBe("192");
  breakpoint.mockRestore();
});
