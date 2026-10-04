import { describe, expect, it, vi } from "vitest";

import { dispatcher, type SSEHandlers } from "@/lib/api";

/** 造一条 SSEEvent（parseSSE 的产物形状：event 名 + JSON 字符串 data）。 */
const ev = (event: string, data: unknown) => ({ event, data: JSON.stringify(data) });

describe("dispatcher（事件名 → handler 的分发表）", () => {
  it("按事件名分发并解析 JSON", () => {
    const delta = vi.fn();

    dispatcher({ delta })(ev("delta", { text: "你好" }));

    expect(delta).toHaveBeenCalledWith({ text: "你好" });
  });

  it("未注册的事件名静默丢弃——旧前端兼容新后端就靠它", () => {
    const handlers: SSEHandlers = { delta: vi.fn() };
    const send = dispatcher(handlers);

    expect(() => send(ev("delta_start", {}))).not.toThrow();
    expect(() => send(ev("delta_chunk", { text: "片" }))).not.toThrow();
  });

  it("degraded 事件（P2-M9）带上原因且不误触发别的 handler", () => {
    // 注册漏了 / 名字写错都会被 dispatcher 静默吞掉（未注册即丢弃）——
    // 浏览器验收看得见横幅，但看不见「handler 其实没接上」这条，所以在这里钉死
    const degraded = vi.fn();
    const delta = vi.fn();
    const send = dispatcher({ degraded, delta });

    send(ev("degraded", { reason: "评分服务不可用，未评分的题目不计入能力评估" }));

    expect(degraded).toHaveBeenCalledWith({ reason: "评分服务不可用，未评分的题目不计入能力评估" });
    expect(delta).not.toHaveBeenCalled();
  });

  it("预留的语音事件（M5）既不炸也不误触发别的 handler", () => {
    const meta = vi.fn();
    const delta = vi.fn();
    const send = dispatcher({ meta, delta });

    send(ev("asr_partial", { text: "转写中" }));
    send(ev("tts_chunk", { audio: "…" }));

    expect(meta).not.toHaveBeenCalled();
    expect(delta).not.toHaveBeenCalled();
  });

  it("单条事件坏掉不中断整个流（后续事件照常）", () => {
    const delta = vi.fn();
    const send = dispatcher({ delta });

    expect(() => send({ event: "delta", data: "{坏掉的 JSON" })).not.toThrow();

    send(ev("delta", { text: "后续照常" }));
    expect(delta).toHaveBeenCalledTimes(1);
  });

  it("delta_start 的 data 是空对象，无参 handler 也接得住", () => {
    const start = vi.fn();

    dispatcher({ delta_start: start })(ev("delta_start", {}));

    expect(start).toHaveBeenCalledWith({});
  });
});
