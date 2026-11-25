import { memo, useLayoutEffect, useRef, useState } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const segmenter = new Intl.Segmenter("zh", { granularity: "grapheme" });
const plugins = [remarkGfm];
const components: Components = {
  a: ({ href, children, title }) =>
    href ? (
      <a href={href} title={title} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  // 回答只展示文字依据，不请求模型给出的远程图片。
  img: ({ alt }) => <span>{alt}</span>,
  table: ({ children }) => (
    <div className="assistant-markdown-table" role="region" aria-label="回答表格" tabIndex={0}>
      <table>{children}</table>
    </div>
  ),
};

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
  const element = useRef<HTMLDivElement>(null);
  const [visibleText, setVisibleText] = useState("");
  const display = useRef({ text: "", started: false, lastFrame: 0, credit: 0 });

  useLayoutEffect(() => {
    const node = element.current;
    if (!node) return;
    const state = display.current;
    let frame = 0;
    const render = (value: string, animating: boolean) => {
      state.text = value;
      setVisibleText(value);
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
    // 正文已收齐时用约 200ms 显示积压的字，避免长回答在网络结束后还要等很久。
    const finishRate = (letters.length - visible) / 200;
    const tick = (now: number) => {
      const elapsed = state.lastFrame ? Math.min(now - state.lastFrame, 50) : 16;
      state.lastFrame = now;
      const remaining = letters.length - visible;
      const rate = streaming ? Math.max(0.08, remaining * 0.006) : Math.max(0.08, finishRate);
      state.credit += elapsed * rate;
      const count = Math.min(remaining, Math.floor(state.credit), streaming ? 8 : remaining);
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

  return (
    <div className="assistant-reply" ref={element} hidden={!visibleText}>
      <Markdown remarkPlugins={plugins} components={components} skipHtml>
        {visibleText}
      </Markdown>
    </div>
  );
});
