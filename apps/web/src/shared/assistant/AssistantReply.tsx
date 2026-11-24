import { memo, useLayoutEffect, useRef } from "react";

const segmenter = new Intl.Segmenter("zh", { granularity: "grapheme" });

/** 网络到字节时只更新目标文本；逐帧呈现由此组件独立负责，避免重绘整段会话。 */
export const AssistantReply = memo(function AssistantReply({
  text,
  streaming,
  paused,
  onReveal,
}: {
  text: string;
  streaming: boolean;
  paused: boolean;
  onReveal: () => void;
}) {
  const element = useRef<HTMLParagraphElement>(null);
  const display = useRef({ text: "", started: false, lastFrame: 0, credit: 0 });

  useLayoutEffect(() => {
    const node = element.current;
    if (!node) return;
    const state = display.current;
    let frame = 0;
    const render = (value: string, animating: boolean) => {
      state.text = value;
      node.textContent = value;
      node.hidden = !value;
      node.classList.toggle("is-streaming", animating);
      onReveal();
    };
    // 历史已完成消息直接显示；停止时冻结已经可见的部分。
    if (paused) {
      node.classList.remove("is-streaming");
      return;
    }
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || (!state.started && !streaming) || !text.startsWith(state.text)) {
      render(text, streaming);
      state.started = streaming;
      state.lastFrame = 0;
      state.credit = 0;
      return;
    }
    if (streaming) state.started = true;
    const letters = Array.from(segmenter.segment(text), (part) => part.segment);
    let visible = Array.from(segmenter.segment(state.text)).length;
    if (!visible && letters.length) render(letters[visible++], true);
    const tick = (now: number) => {
      const elapsed = state.lastFrame ? Math.min(now - state.lastFrame, 50) : 16;
      state.lastFrame = now;
      const remaining = letters.length - visible;
      // 小块匀速输出，积压较大时适度加速，让完成事件到达后也能自然收尾。
      state.credit += (elapsed * Math.max(80, remaining * 6)) / 1000;
      const count = Math.min(remaining, Math.floor(state.credit), 8);
      if (count > 0) {
        visible += count;
        state.credit -= count;
        render(letters.slice(0, visible).join(""), streaming || visible < letters.length);
      }
      if (visible < letters.length) frame = requestAnimationFrame(tick);
      else {
        state.lastFrame = 0;
        state.credit = 0;
        node.classList.toggle("is-streaming", streaming);
      }
    };
    if (visible < letters.length) frame = requestAnimationFrame(tick);
    else node.classList.toggle("is-streaming", streaming && !!text);
    return () => cancelAnimationFrame(frame);
  }, [text, streaming, paused, onReveal]);

  return <p className="assistant-reply" ref={element} hidden />;
});
