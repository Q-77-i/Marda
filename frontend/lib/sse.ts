/**
 * SSE 流解析（SPEC §7）：POST 用不了 EventSource，走 fetch + 手动解析。
 *
 * 纯函数实现，与 fetch/React 解耦，便于单测。职责：
 * - 按空行切分事件块，兼容 \n\n 与 \r\n\r\n；
 * - 跳过注释行（sse-starlette 的 `: ping` 心跳）；
 * - TextDecoder stream 模式处理 UTF-8 跨 chunk 截断（中文必踩）。
 */

export type SSEEvent = { event: string; data: string };

/** 事件块边界：返回 [块结束位置, 下一块起始位置]，无边界返回 null。 */
function findBoundary(buffer: string): [number, number] | null {
  const lf = buffer.indexOf("\n\n");
  const crlf = buffer.indexOf("\r\n\r\n");
  if (lf === -1 && crlf === -1) return null;
  // \r\n\r\n 内含 \n\n 吗？不含（\r\n\r\n 的第 2、3 字节是 \n\r）。取更靠前的边界。
  if (crlf !== -1 && (lf === -1 || crlf < lf)) return [crlf, crlf + 4];
  return [lf, lf + 2];
}

/** 解析单个事件块；无 event/data 字段（纯注释/空块）返回 null。 */
export function parseBlock(block: string): SSEEvent | null {
  let event = "";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line === "" || line.startsWith(":")) continue; // 空行 / 心跳注释
    const colon = line.indexOf(":");
    if (colon === -1) continue;
    const field = line.slice(0, colon);
    // 规范：冒号后仅一个空格需去掉，其余保留
    const value = line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }
  if (dataLines.length === 0) return null;
  return { event: event || "message", data: dataLines.join("\n") };
}

/** ReadableStream<Uint8Array> → 事件异步生成器。 */
export async function* parseSSE(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SSEEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      for (;;) {
        const boundary = findBoundary(buffer);
        if (!boundary) break;
        const [end, next] = boundary;
        const block = buffer.slice(0, end);
        buffer = buffer.slice(next);
        const parsed = parseBlock(block);
        if (parsed) yield parsed;
      }
    }
    buffer += decoder.decode(); // flush 解码器内部残留字节
    const parsed = parseBlock(buffer); // 末尾无空行的收尾块
    if (parsed) yield parsed;
  } finally {
    reader.releaseLock();
  }
}

/** POST 并逐事件回调；HTTP 非 2xx 抛错（4xx 在流开始前是普通 JSON，SPEC §7）。 */
export async function postSSE(
  url: string,
  body: unknown,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    throw new Error(networkMessage(err)); // fetch 只在网络层失败时 reject
  }
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  if (!response.body) throw new Error("响应无流式内容");
  for await (const event of parseSSE(response.body)) {
    onEvent(event);
  }
}

/** 网络层失败（后端未启动/断开）转成中文提示；主动取消原样抛出。 */
export function networkMessage(err: unknown): string {
  if (err instanceof DOMException && err.name === "AbortError") {
    return "请求已取消";
  }
  return "无法连接服务器，请确认后端已启动后重试";
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string") return payload.detail;
  } catch {
    // 非 JSON 响应，走通用文案
  }
  return `请求失败（${response.status}）`;
}
