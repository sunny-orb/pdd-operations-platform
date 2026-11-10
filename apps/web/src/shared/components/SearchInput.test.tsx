// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SearchInput } from "./SearchInput";

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const tick = (ms = 500) => act(() => vi.advanceTimersByTimeAsync(ms));
const input = () => screen.getByRole("textbox") as HTMLInputElement;

it("连续输入同步显示草稿，停顿后只提交完整ID", async () => {
  const search = vi.fn();
  render(<SearchInput value="" onSearch={search} />);
  fireEvent.change(input(), { target: { value: "123" } });
  await tick(350);
  fireEvent.change(input(), { target: { value: "1234567890123" } });
  expect(input().value).toBe("1234567890123");
  await tick(499);
  expect(search).not.toHaveBeenCalled();
  await tick(1);
  expect(search).toHaveBeenCalledExactlyOnceWith("1234567890123");
});

it("中文组词取消旧计时且确认候选的回车不查询", async () => {
  const search = vi.fn();
  render(<SearchInput value="" onSearch={search} />);
  fireEvent.change(input(), { target: { value: "儿童" } });
  await tick(300);
  fireEvent.compositionStart(input());
  fireEvent.change(input(), { target: { value: "儿童yumao" } });
  fireEvent.keyDown(input(), { key: "Enter", keyCode: 229, isComposing: true });
  await tick(900);
  expect(search).not.toHaveBeenCalled();
  fireEvent.change(input(), { target: { value: "儿童羽毛" } });
  fireEvent.compositionEnd(input(), { data: "羽毛" });
  fireEvent.keyDown(input(), { key: "Enter", keyCode: 229 });
  await tick(499);
  expect(search).not.toHaveBeenCalled();
  await tick(1);
  expect(search).toHaveBeenCalledExactlyOnceWith("儿童羽毛");
});

it("普通回车立即提交且随后不会重复查询，清空即时提交", async () => {
  const search = vi.fn();
  render(<SearchInput value="旧词" onSearch={search} allowClear />);
  fireEvent.change(input(), { target: { value: "帽子" } });
  fireEvent.keyDown(input(), { key: "Enter", keyCode: 13 });
  expect(search).toHaveBeenCalledExactlyOnceWith("帽子");
  await tick();
  expect(search).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "close-circle" }));
  expect(input().value).toBe("");
  expect(search.mock.calls).toEqual([["帽子"], [""]]);
});

it("提交回显不覆盖继续输入的新草稿，也不取消它的查询", async () => {
  const search = vi.fn();
  const view = render(<SearchInput value="" onSearch={search} />);
  fireEvent.change(input(), { target: { value: "儿童" } });
  fireEvent.keyDown(input(), { key: "Enter", keyCode: 13 });
  fireEvent.change(input(), { target: { value: "儿童帽子" } });
  view.rerender(<SearchInput value="儿童" onSearch={search} />);
  expect(input().value).toBe("儿童帽子");
  await tick();
  expect(search.mock.calls).toEqual([["儿童"], ["儿童帽子"]]);
});

it("外部恢复条件覆盖草稿并取消旧计时；同值重置也有效", async () => {
  const search = vi.fn();
  const view = render(<SearchInput value="旧词" onSearch={search} resetKey="0" />);
  fireEvent.change(input(), { target: { value: "未提交" } });
  view.rerender(<SearchInput value="后退条件" onSearch={search} resetKey="0" />);
  expect(input().value).toBe("后退条件");
  await tick();
  expect(search).not.toHaveBeenCalled();
  fireEvent.compositionStart(input());
  fireEvent.change(input(), { target: { value: "pinyin" } });
  view.rerender(<SearchInput value="后退条件" onSearch={search} resetKey="1" />);
  expect(input().value).toBe("后退条件");
  await tick();
  expect(search).not.toHaveBeenCalled();
});

it("卸载后不再提交草稿", async () => {
  const search = vi.fn();
  const view = render(<SearchInput value="" onSearch={search} />);
  fireEvent.change(input(), { target: { value: "离开页面" } });
  view.unmount();
  await tick();
  expect(search).not.toHaveBeenCalled();
});
