import { expect, it } from "vitest";
import { readAnswer } from "./stream";

const result = {
  answer: "访客减少",
  status: "no_evidence",
  model: "m",
  subject: null,
  candidates: [],
  reports: [],
  evidence: "",
};

it("中文跨字节分块时仍逐段更新，完成后校验最终回答", async () => {
  let send!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      send = controller;
    },
  });
  const updates: string[] = [];
  const response = new Response(body, { headers: { "content-type": "text/event-stream" } });
  const reading = readAnswer(response, (event) => updates.push(event.text));
  const text = 'data: {"type":"delta","text":"访客"}\n\n';
  const bytes = new TextEncoder().encode(text);
  // 在一个汉字的 UTF-8 字节内部断开。
  const split = new TextEncoder().encode(text.slice(0, text.indexOf("访"))).length + 1;
  send.enqueue(bytes.slice(0, split));
  send.enqueue(bytes.slice(split));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(updates).toEqual(["访客"]);
  send.enqueue(
    new TextEncoder().encode("data: " + JSON.stringify({ type: "done", result }) + "\n\n"),
  );
  send.close();
  expect(await reading).toEqual(result);
});

it("流中错误及没有完成事件的断连不会被当作完整回答", async () => {
  for (const frame of [
    'data: {"type":"error","error":{"message":"版本变动"}}\n\n',
    'data: {"type":"delta","text":"尚未完成"}\n\n',
  ]) {
    const response = new Response(frame, { headers: { "content-type": "text/event-stream" } });
    await expect(readAnswer(response, () => {})).rejects.toThrow(/版本变动|尚未完成/);
  }
});
