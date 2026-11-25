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
  const reply = view.container.querySelector(".assistant-reply")!;
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
  const reply = view.container.querySelector(".assistant-reply")!;
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

it("完成回答呈现加粗、标题、列表和表格，不将Markdown原样展示", () => {
  const text =
    "### 推广表现\n\n**核心结论**：投产基本持平。\n\n- 花费增加\n- 点击成本下降\n\n1. 先看渠道\n2. 再看商品\n\n| 渠道 | 投产 |\n| --- | ---: |\n| 商品推广 | 7.60 |";
  const view = render(
    <AssistantReply text={text} streaming={false} paused={false} onReveal={vi.fn()} />,
  );
  expect(view.container.querySelector("h3")?.textContent).toBe("推广表现");
  expect(view.container.querySelector("strong")?.textContent).toBe("核心结论");
  expect(view.container.querySelectorAll("ul > li")).toHaveLength(2);
  expect(view.container.querySelectorAll("ol > li")).toHaveLength(2);
  expect(view.container.querySelector("table")?.textContent).toContain("7.60");
  expect(view.container.textContent).not.toContain("**");
  expect(view.container.textContent).not.toContain("###");
});

it("模型文字不能注入HTML、脚本链接或远程图片请求，正常来源链接可访问", () => {
  const text =
    "<script>alert(1)</script>\n\n[危险](javascript:alert%281%29)\n\n[依据](https://example.com/sop)\n\n![截图](https://example.com/tracking.png)";
  const view = render(
    <AssistantReply text={text} streaming={false} paused={false} onReveal={vi.fn()} />,
  );
  expect(view.container.querySelector("script")).toBeNull();
  expect(view.container.querySelector("img")).toBeNull();
  expect(view.container.querySelector('a[href^="javascript:"]')).toBeNull();
  const source = view.container.querySelector('a[href="https://example.com/sop"]');
  expect(source?.getAttribute("rel")).toContain("noopener");
});

it("流式语法跨块补全后正确排版，完成事件不打断收尾", () => {
  const props = { streaming: true, paused: false, onReveal: vi.fn() };
  const view = render(<AssistantReply {...props} text="**核心" />);
  step(15);
  view.rerender(
    <AssistantReply {...props} text={"**核心结论**\n\n- 先核查渠道"} streaming={false} />,
  );
  step(45);
  expect(view.container.querySelector("strong")?.textContent).toBe("核心结论");
  expect(view.container.querySelector("li")?.textContent).toBe("先核查渠道");
  expect(frames.size).toBe(0);
});

it("完成帧到达后，长回答在短时间内平滑补齐", () => {
  const props = { text: "", streaming: true, paused: false, onReveal: vi.fn() };
  const view = render(<AssistantReply {...props} />);
  const text = "访客有所增长。".repeat(70);
  view.rerender(<AssistantReply {...props} text={text} />);
  step(2);
  view.rerender(<AssistantReply {...props} streaming={false} text={text} />);
  step(1);
  expect(view.container.textContent).not.toBe(text);
  step(15);
  expect(view.container.textContent).toBe(text);
});
