// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantReply } from "./AssistantReply";

let frames: Map<number, FrameRequestCallback>;
let clock: number;
beforeEach(() => {
  frames = new Map();
  clock = 0;
  let id = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function step(count = 1) {
  act(() => {
    for (let i = 0; i < count; i++) {
      clock += 16;
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((callback) => callback(clock));
    }
  });
}

it("突发的大段文字逐帧呈现，完成事件也不会跳出剩余整段，表情按完整字素显示", () => {
  const onReveal = vi.fn();
  const props = { text: "", streaming: true, paused: false, onReveal };
  const view = render(<AssistantReply {...props} />);
  const text = "👨‍👩‍👧‍👦本期访客减少，需要先查看同款推广曝光和点击变化。";
  view.rerender(<AssistantReply {...props} text={text} />);
  const reply = view.container.querySelector("p")!;
  expect(reply.textContent).toBe("👨‍👩‍👧‍👦");
  step(2);
  expect(text.startsWith(reply.textContent!)).toBe(true);
  expect(reply.textContent).not.toBe(text);
  view.rerender(<AssistantReply {...props} text={text} streaming={false} />);
  expect(reply.textContent).not.toBe(text);
  step(80);
  expect(reply.textContent).toBe(text);
  expect(reply.classList.contains("is-streaming")).toBe(false);
  expect(frames.size).toBe(0);
});

it("停止时冻结可见文字，重试清空旧文字，卸载回收动画帧", () => {
  const props = { text: "", streaming: true, paused: false, onReveal: vi.fn() };
  const view = render(<AssistantReply {...props} />);
  view.rerender(<AssistantReply {...props} text="这段网络已收到的文字还没有全部显示" />);
  step();
  const reply = view.container.querySelector("p")!;
  const shown = reply.textContent;
  view.rerender(
    <AssistantReply
      {...props}
      text="这段网络已收到的文字还没有全部显示"
      streaming={false}
      paused
    />,
  );
  step(20);
  expect(reply.textContent).toBe(shown);
  view.rerender(<AssistantReply {...props} />);
  expect(reply.textContent).toBe("");
  view.rerender(<AssistantReply {...props} text="重试后的回答" />);
  view.unmount();
  expect(frames.size).toBe(0);
});

it("减少动态效果时直接呈现，历史回答不重播动画", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  const props = { text: "完整回答", streaming: true, paused: false, onReveal: vi.fn() };
  const view = render(<AssistantReply {...props} />);
  expect(view.container.textContent).toBe("完整回答");
  expect(frames.size).toBe(0);
});
