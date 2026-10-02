import { describe, expect, it } from "vitest";

import { StreamBuffer, applyStreamAction, type StreamAction } from "@/lib/stream-render";

/** 顺序 id 工厂：断言里能直接说出「哪条消息」。 */
function ids() {
  let n = 0;
  return () => `m${n++}`;
}

function buffer() {
  return new StreamBuffer(ids());
}

type Msg = { id: string; role: string; content: string; tag?: string };
const create = (id: string, content: string): Msg => ({ id, role: "assistant", content });

describe("StreamBuffer 事件配对", () => {
  it("start 开一条、chunk 追加给最近一条", () => {
    const buf = buffer();

    expect(buf.start()).toEqual({ kind: "open", id: "m0" });
    expect(buf.chunk("你")).toEqual({ kind: "append", id: "m0", text: "你" });
    expect(buf.chunk("好")).toEqual({ kind: "append", id: "m0", text: "好" });
  });

  it("同一节点两条消息：分片各归各的（出题节点的答错缓冲 + 题目）", () => {
    const buf = buffer();

    buf.start(); // 缓冲语
    const first = buf.chunk("上一题答得有点偏。");
    buf.start(); // 题目（衔接语 + LLM 文案）
    const second = buf.chunk("好，换个方向。");

    expect(first).toEqual({ kind: "append", id: "m0", text: "上一题答得有点偏。" });
    expect(second).toEqual({ kind: "append", id: "m1", text: "好，换个方向。" });
  });

  it("delta 按序结算：谁先开始谁先结算（FIFO）", () => {
    const buf = buffer();
    buf.start();
    buf.chunk("甲");
    buf.start();
    buf.chunk("乙");

    expect(buf.delta("甲乙")).toEqual({ kind: "settle", id: "m0", text: "甲乙" });
    expect(buf.delta("乙")).toEqual({ kind: "settle", id: "m1", text: "乙" });
  });

  it("结算用终稿全文（不是拼接结果）——服务端会 strip 两侧空白", () => {
    const buf = buffer();
    buf.start();
    buf.chunk("\n\n你好");

    expect(buf.delta("你好")).toEqual({ kind: "settle", id: "m0", text: "你好" });
  });

  it("没有 start 的 delta → legacy（旧打字机路径，兼容无分片的消息）", () => {
    const buf = buffer();

    expect(buf.delta("整段文案")).toEqual({ kind: "legacy", id: "m0", text: "整段文案" });
  });

  it("没有 start 的 chunk 兜底开一条，不丢字", () => {
    const buf = buffer();

    expect(buf.chunk("孤块")).toEqual({ kind: "append", id: "m0", text: "孤块" });
    expect(buf.delta("孤块")).toEqual({ kind: "settle", id: "m0", text: "孤块" });
  });

  it("abandon 只收未结算的，已结算的不受影响", () => {
    const buf = buffer();
    buf.start();
    buf.chunk("说完的");
    buf.delta("说完的");
    buf.start();
    buf.chunk("半截");

    expect(buf.abandon()).toEqual(["m1"]);
    expect(buf.abandon()).toEqual([]); // 幂等：收过就没了
    expect(buf.hasOpen).toBe(false);
  });

  it("hasOpen 跟着未结算的气泡数走", () => {
    const buf = buffer();
    expect(buf.hasOpen).toBe(false);
    buf.start();
    expect(buf.hasOpen).toBe(true);
    buf.delta("终稿");
    expect(buf.hasOpen).toBe(false);
  });
});

describe("applyStreamAction 落列表", () => {
  const run = (messages: Msg[], action: StreamAction) =>
    applyStreamAction(messages, action, create);

  it("open 追加一个空气泡", () => {
    expect(run([], { kind: "open", id: "m0" })).toEqual([
      { id: "m0", role: "assistant", content: "" },
    ]);
  });

  it("append 累加，保留同条消息的其它字段（tag 不丢）", () => {
    const list: Msg[] = [{ id: "m0", role: "assistant", content: "你", tag: "第 1 题" }];

    expect(run(list, { kind: "append", id: "m0", text: "好" })).toEqual([
      { id: "m0", role: "assistant", content: "你好", tag: "第 1 题" },
    ]);
  });

  it("append 到列表里没有的 id：就地补一条（不静默丢字）", () => {
    expect(run([], { kind: "append", id: "m9", text: "先到的分片" })).toEqual([
      { id: "m9", role: "assistant", content: "先到的分片" },
    ]);
  });

  it("settle 用终稿替换累积文本", () => {
    const list: Msg[] = [{ id: "m0", role: "assistant", content: "半截" }];

    expect(run(list, { kind: "settle", id: "m0", text: "完整文案" })).toEqual([
      { id: "m0", role: "assistant", content: "完整文案" },
    ]);
  });

  it("legacy 建空气泡（正文由打字机逐字吐）", () => {
    expect(run([], { kind: "legacy", id: "m3", text: "整段" })).toEqual([
      { id: "m3", role: "assistant", content: "" },
    ]);
  });

  it("不就地改原列表（React 状态更新要纯）", () => {
    const list: Msg[] = [{ id: "m0", role: "assistant", content: "旧" }];
    run(list, { kind: "settle", id: "m0", text: "新" });

    expect(list[0].content).toBe("旧");
  });
});
