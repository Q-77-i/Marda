import { describe, expect, it } from "vitest";

import { Typewriter, TypewriterQueue } from "@/lib/typewriter";

describe("Typewriter", () => {
  it("按 tick 数量逐字吐出", () => {
    const tw = new Typewriter();
    tw.append("面试官提问");
    tw.tick(2);
    expect(tw.text).toBe("面试");
    tw.tick(3);
    expect(tw.text).toBe("面试官提问");
  });

  it("多个 delta 连发时字符顺序不乱、不丢", () => {
    const tw = new Typewriter();
    tw.append("第一段。");
    tw.tick(2);
    tw.append("第二段。");
    tw.tick(100);
    expect(tw.text).toBe("第一段。第二段。");
  });

  it("drained 反映是否有未吐字符", () => {
    const tw = new Typewriter();
    expect(tw.drained).toBe(true); // 初始无内容 = 已吐完
    tw.append("abc");
    expect(tw.drained).toBe(false);
    tw.tick(3);
    expect(tw.drained).toBe(true);
  });

  it("flush 立即补全", () => {
    const tw = new Typewriter();
    tw.append("完整文案");
    tw.tick(1);
    tw.flush();
    expect(tw.text).toBe("完整文案");
    expect(tw.drained).toBe(true);
  });

  it("tick 不会劈开 emoji（按码点切）", () => {
    const tw = new Typewriter();
    tw.append("✅通过");
    tw.tick(1);
    expect(tw.text).toBe("✅");
    tw.tick(2);
    expect(tw.text).toBe("✅通过");
  });

  it("tick 超过剩余长度时全部吐出且不重复", () => {
    const tw = new Typewriter();
    tw.append("ab");
    tw.tick(99);
    tw.tick(99);
    expect(tw.text).toBe("ab");
  });

  it("reset 清空状态", () => {
    const tw = new Typewriter();
    tw.append("abc");
    tw.tick(1);
    tw.reset();
    expect(tw.text).toBe("");
    expect(tw.drained).toBe(true);
  });
});

describe("TypewriterQueue", () => {
  it("空队列 tick 返回 null 且标记 idle", () => {
    const queue = new TypewriterQueue();
    expect(queue.idle).toBe(true);
    expect(queue.tick(3)).toBeNull();
  });

  it("队首吐完才轮到下一条（delta 连发顺序性）", () => {
    const queue = new TypewriterQueue();
    queue.push("m1", "第一题");
    queue.push("m2", "第二题");
    expect(queue.pendingLength).toBe(3);

    // 前两次 tick 都作用在 m1 上
    expect(queue.tick(2)).toEqual({ id: "m1", text: "第一" });
    expect(queue.tick(1)).toEqual({ id: "m1", text: "第一题" });
    // m1 已吐完出队，此后轮到 m2
    expect(queue.tick(1)).toEqual({ id: "m2", text: "第" });
    expect(queue.idle).toBe(false);
    expect(queue.tick(10)).toEqual({ id: "m2", text: "第二题" });
    expect(queue.idle).toBe(true);
  });

  it("单次 tick 超过队首剩余时只吐完队首，不越界到下一条", () => {
    const queue = new TypewriterQueue();
    queue.push("m1", "ab");
    queue.push("m2", "cd");
    expect(queue.tick(99)).toEqual({ id: "m1", text: "ab" });
    expect(queue.tick(99)).toEqual({ id: "m2", text: "cd" });
  });

  it("clear 清空所有待吐内容", () => {
    const queue = new TypewriterQueue();
    queue.push("m1", "abc");
    queue.clear();
    expect(queue.idle).toBe(true);
  });
});
