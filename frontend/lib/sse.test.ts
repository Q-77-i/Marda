import { describe, expect, it } from "vitest";

import { parseBlock, parseSSE, postSSE, type SSEEvent } from "@/lib/sse";

/** 字节分片 → ReadableStream，模拟网络分块到达。 */
function streamOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

function bytes(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<SSEEvent[]> {
  const events: SSEEvent[] = [];
  for await (const event of parseSSE(stream)) events.push(event);
  return events;
}

describe("parseBlock", () => {
  it("解析 event + data", () => {
    expect(parseBlock('event: delta\ndata: {"text":"你好"}')).toEqual({
      event: "delta",
      data: '{"text":"你好"}',
    });
  });

  it("无 event 字段时默认 message", () => {
    expect(parseBlock("data: hi")).toEqual({ event: "message", data: "hi" });
  });

  it("跳过心跳注释行", () => {
    expect(parseBlock(': ping\nevent: meta\ndata: {"a":1}')).toEqual({
      event: "meta",
      data: '{"a":1}',
    });
  });

  it("无 data 字段返回 null（纯注释块）", () => {
    expect(parseBlock(": ping")).toBeNull();
    expect(parseBlock("")).toBeNull();
  });

  it("多 data 行按换行拼接", () => {
    expect(parseBlock("event: x\ndata: line1\ndata: line2")).toEqual({
      event: "x",
      data: "line1\nline2",
    });
  });

  it("只去掉冒号后第一个空格，其余保留", () => {
    expect(parseBlock("data:  two spaces")).toEqual({
      event: "message",
      data: " two spaces",
    });
  });
});

describe("parseSSE", () => {
  it("单 chunk 多事件", async () => {
    const events = await collect(
      streamOf([bytes("event: meta\ndata: {}\n\nevent: delta\ndata: hi\n\n")]),
    );
    expect(events).toEqual([
      { event: "meta", data: "{}" },
      { event: "delta", data: "hi" },
    ]);
  });

  it("事件被 chunk 边界劈开仍能解析", async () => {
    const events = await collect(
      streamOf([bytes("event: del"), bytes('ta\ndata: {"text":"你'), bytes('好"}\n\n')]),
    );
    expect(events).toEqual([{ event: "delta", data: '{"text":"你好"}' }]);
  });

  it("中文被劈在多字节序列中间不出现乱码", async () => {
    const payload = bytes('event: delta\ndata: {"text":"面试官提问"}\n\n');
    // 逐字节切碎：每个多字节字符都被劈开
    const chunks = Array.from(payload, (byte) => Uint8Array.of(byte));
    const events = await collect(streamOf(chunks));
    expect(events).toEqual([
      { event: "delta", data: '{"text":"面试官提问"}' },
    ]);
  });

  it("心跳注释块被跳过，只产出业务事件", async () => {
    const events = await collect(
      streamOf([bytes(': ping\n\nevent: delta\ndata: x\n\n: ping\n\n')]),
    );
    expect(events).toEqual([{ event: "delta", data: "x" }]);
  });

  it("支持 CRLF 分隔", async () => {
    const events = await collect(
      streamOf([bytes("event: delta\r\ndata: x\r\n\r\n")]),
    );
    expect(events).toEqual([{ event: "delta", data: "x" }]);
  });

  it("末尾无空行的收尾块也产出", async () => {
    const events = await collect(streamOf([bytes("event: done\ndata: {}")]));
    expect(events).toEqual([{ event: "done", data: "{}" }]);
  });

  it("空流产出空数组", async () => {
    expect(await collect(streamOf([]))).toEqual([]);
  });
});

describe("postSSE", () => {
  it("网络层失败（后端未启动）给出中文提示", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = () => Promise.reject(new TypeError("Failed to fetch"));
    try {
      await expect(postSSE("/api/x", {}, () => {})).rejects.toThrow(
        "无法连接服务器",
      );
    } finally {
      globalThis.fetch = original;
    }
  });

  it("HTTP 错误优先用后端 detail 文案", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = () =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: "面试已结束" }), { status: 409 }),
      );
    try {
      await expect(postSSE("/api/x", {}, () => {})).rejects.toThrow("面试已结束");
    } finally {
      globalThis.fetch = original;
    }
  });
});
