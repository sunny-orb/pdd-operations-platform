import { chatAnswer, type ChatAnswer } from "./contracts";

/** 解析真实网络分块，保留跨块的中文和事件；完整结果以 done 校验为准。 */
export async function readAnswer(
  response: Response,
  update: (event: { type: "delta" | "status"; text: string }) => void,
): Promise<ChatAnswer> {
  if (!response.headers?.get("content-type")?.includes("text/event-stream")) {
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error?.message || "未能取得回答，请稍后重试");
    return chatAnswer.parse(data);
  }
  if (!response.body) throw new Error("未能连接回答流，请重试");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: ChatAnswer | undefined;
  try {
    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value, { stream: !chunk.done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const payload = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (!payload) continue;
        const event = JSON.parse(payload);
        if (event.type === "error") throw new Error(event.error?.message || "回答中断，请重试");
        if (event.type === "done") result = chatAnswer.parse(event.result);
        if ((event.type === "delta" || event.type === "status") && typeof event.text === "string")
          update(event);
      }
      if (chunk.done) break;
    }
    if (!result) throw new Error("连接已中断，回答尚未完成，请重试");
    return result;
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
