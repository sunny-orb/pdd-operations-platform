// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StableContent } from "./StableContent";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("快速更新保留旧内容且不闪提示，慢请求只在等待后提示并禁止操作旧结果", () => {
  vi.useFakeTimers();
  const view = render(
    <StableContent pending={false} ready label="更新中">
      <button>旧范围 100 元</button>
    </StableContent>,
  );
  view.rerender(
    <StableContent pending ready={false} label="更新中">
      <button>新范围 —</button>
    </StableContent>,
  );
  expect(screen.queryByRole("status")).toBeNull();
  expect(view.container.textContent).toContain("旧范围 100 元");
  expect(view.container.textContent).not.toContain("新范围 —");
  expect(view.container.querySelector("[inert]")).not.toBeNull();
  act(() => vi.advanceTimersByTime(160));
  expect(screen.getByRole("status").textContent).toContain("上次结果");
  view.rerender(
    <StableContent pending={false} ready label="更新中">
      <button>新范围 200 元</button>
    </StableContent>,
  );
  expect(screen.getByRole("button").textContent).toBe("新范围 200 元");
  expect(screen.queryByRole("status")).toBeNull();
  expect(view.container.querySelector("[inert]")).toBeNull();
});

it("快速响应立即显示，失败后展示当前错误并清理等待计时", () => {
  vi.useFakeTimers();
  const view = render(
    <StableContent pending ready={false} label="更新中">
      <p>等待</p>
    </StableContent>,
  );
  view.rerender(
    <StableContent pending={false} ready label="更新中">
      <p>真实零 0 元</p>
    </StableContent>,
  );
  expect(screen.getByText("真实零 0 元")).toBeTruthy();
  view.rerender(
    <StableContent pending ready={false} label="更新中">
      <p>等待</p>
    </StableContent>,
  );
  view.rerender(
    <StableContent pending={false} ready={false} label="更新中">
      <p role="alert">读取失败，请重试</p>
    </StableContent>,
  );
  act(() => vi.advanceTimersByTime(500));
  expect(screen.getByRole("alert").textContent).toContain("读取失败");
  expect(screen.queryByRole("status")).toBeNull();
});
