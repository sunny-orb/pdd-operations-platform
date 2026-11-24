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
  expect(await reading).toEqual({ ...result, subject_cleared: false });
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

const dashboard = {
  kind: "dashboard",
  domain: "sales",
  capability: "ratings",
  title: "店铺评级",
  source: "已发布结果",
  period: null,
  scope: {},
  data_version: "sv1",
  versions: { sales: "sv1" },
  facts: [
    {
      key: "score",
      label: "评分",
      value: "95.5",
      unit: "number",
      status: "available",
      reason: null,
    },
  ],
  tables: [],
  notes: [],
  series: [
    {
      key: "score",
      label: "评分",
      unit: "number",
      points: [
        { date: "2026-11-20", value: "95.5" },
        { date: "2026-11-21", value: null },
      ],
    },
  ],
};
it("SSE最终校验接收通用报告、空usage与完整多选范围，旧JSON回答仍兼容", async () => {
  const query_context = {
    domain: "products",
    versions: { products: "pv1" },
    shop_ids: ["s", "t"],
    category_values: ["衣", "帽"],
    operator_values: ["甲", "乙"],
    level_values: ["A", "B"],
  };
  const payload = { ...result, reports: [dashboard], query_context, usage: {} };
  const response = new Response(
    "data: " + JSON.stringify({ type: "done", result: payload }) + "\n\n",
    { headers: { "content-type": "text/event-stream" } },
  );
  expect(await readAnswer(response, () => {})).toEqual({ ...payload, subject_cleared: false });
  expect(await readAnswer(new Response(JSON.stringify(result)), () => {})).toEqual({
    ...result,
    subject_cleared: false,
  });
});

it("SSE拒绝单位或序列结构错误的通用报告，不把未校验报告用于下一轮", async () => {
  for (const invalid of [
    { ...dashboard, facts: [{ ...dashboard.facts[0], unit: "pp" }] },
    {
      ...dashboard,
      series: [
        {
          key: "score",
          label: "评分",
          unit: "number",
          points: [{ date: "2026-11-20", value: {} }],
        },
      ],
    },
    { ...dashboard, series: [{ date: "2026-11-20", values: { score: "95.5" } }] },
  ]) {
    const response = new Response(
      "data: " +
        JSON.stringify({ type: "done", result: { ...result, reports: [invalid] } }) +
        "\n\n",
      { headers: { "content-type": "text/event-stream" } },
    );
    await expect(readAnswer(response, () => {})).rejects.toThrow();
  }
});

it.each(["shop_ids", "category_values", "operator_values", "level_values"])(
  "服务端回传的%s不能超过已约定的20项",
  async (key) => {
    const response = new Response(
      JSON.stringify({
        ...result,
        query_context: { versions: {}, [key]: Array.from({ length: 21 }, (_, i) => String(i)) },
      }),
    );
    await expect(readAnswer(response, () => {})).rejects.toThrow();
  },
);
