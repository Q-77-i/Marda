import { describe, expect, it } from "vitest";

import {
  LONG_PAUSE_MS,
  SpeechAccumulator,
  fillerCount,
  formatSpeechDuration,
  formatSpeechRate,
  internalPauses,
  silenceThreshold,
  spokenCharCount,
} from "@/lib/expression";

/** 造一段采样：levels 每个元素是一帧（默认 50ms）。 */
function frames(levels: number[], step = 50) {
  return levels.map((rms, index) => ({ t: index * step, rms }));
}

function voiced(ms: number, step = 50, rms = 0.1) {
  return Array.from({ length: Math.round(ms / step) }, () => rms);
}

function silent(ms: number, step = 50, rms = 0.001) {
  return Array.from({ length: Math.round(ms / step) }, () => rms);
}

describe("字数与口头禅", () => {
  it("只数字母/数字，不数标点与空格", () => {
    expect(spokenCharCount("你好，world 123！")).toBe(10);
    expect(spokenCharCount("   。，！ ")).toBe(0);
  });

  it("口头禅按计划口径计数", () => {
    expect(fillerCount("嗯，那个就是然后")).toBe(4);
    expect(fillerCount("这是正常的一句话")).toBe(0);
  });
});

describe("静音阈值", () => {
  it("按本段 p10 自适应并夹紧", () => {
    expect(silenceThreshold([])).toBeCloseTo(0.012);
    expect(silenceThreshold(silent(2000))).toBeCloseTo(0.008);
    expect(silenceThreshold(voiced(2000, 50, 0.02))).toBeCloseTo(0.04);
    expect(silenceThreshold([0.002, 0.003, 0.004, 0.01, 0.1])).toBeCloseTo(0.008);
  });
});

describe("内部长停顿", () => {
  it("首尾静音不算，中间 ≥2s 才算", () => {
    const scores = frames([
      ...silent(1000), // 开头按键延迟
      ...voiced(500),
      ...silent(LONG_PAUSE_MS), // 一次长停顿
      ...voiced(500),
      ...silent(1000), // 结尾
    ]);
    expect(internalPauses(scores, 0.01)).toEqual({
      count: 1,
      ms: LONG_PAUSE_MS,
    });
  });

  it("1.9s 的停顿不算", () => {
    const scores = frames([...voiced(500), ...silent(1900), ...voiced(500)]);
    expect(internalPauses(scores, 0.01)).toEqual({ count: 0, ms: 0 });
  });

  it("多次长停顿累计", () => {
    const scores = frames([
      ...voiced(400),
      ...silent(2000),
      ...voiced(400),
      ...silent(2500),
      ...voiced(400),
    ]);
    expect(internalPauses(scores, 0.01)).toEqual({ count: 2, ms: 4500 });
  });

  it("样本太少不判", () => {
    expect(internalPauses(frames(silent(300)), 0.01)).toEqual({ count: 0, ms: 0 });
  });
});

describe("SpeechAccumulator", () => {
  it("stop 冻结时长，final 只计税；时长不含等 final 的 8 秒", () => {
    const acc = new SpeechAccumulator();
    acc.begin(1000);
    for (const sample of frames(voiced(1000))) acc.sample(sample.rms, 1000 + sample.t);
    acc.stop(2600);
    // final 在 8s 后才回来
    acc.finalize("你好世界");

    expect(acc.snapshot()).toEqual({
      duration_ms: 1600,
      chars: 4,
      pause_count: 0,
      pause_ms: 0,
      filler_count: 0,
      segments: 1,
    });
  });

  it("stop 之后的采样不再计入", () => {
    const acc = new SpeechAccumulator();
    acc.begin(0);
    for (const sample of frames([...voiced(500), ...silent(3000)])) acc.sample(sample.rms, sample.t);
    acc.stop(3500);
    for (const sample of frames(silent(4000))) acc.sample(sample.rms, 5000 + sample.t);
    acc.finalize("测试");

    expect(acc.snapshot()?.pause_count).toBe(0);
  });

  it("空转写丢弃该段", () => {
    const acc = new SpeechAccumulator();
    acc.begin(0);
    acc.stop(1000);
    acc.finalize("   ");
    expect(acc.snapshot()).toBeNull();
  });

  it("没 stop 就 finalize 不产生记录", () => {
    const acc = new SpeechAccumulator();
    acc.begin(0);
    acc.finalize("你好");
    expect(acc.snapshot()).toBeNull();
  });

  it("多段累加", () => {
    const acc = new SpeechAccumulator();
    acc.begin(0);
    acc.stop(2000);
    acc.finalize("第一段");
    acc.begin(5000);
    acc.stop(8000);
    acc.finalize("嗯第二段");

    expect(acc.snapshot()).toEqual({
      duration_ms: 5000,
      chars: 7, // 第一段(3) + 嗯第二段(4；口头禅也占字，语速按实际说出的字算)
      pause_count: 0,
      pause_ms: 0,
      filler_count: 1,
      segments: 2,
    });
  });

  it("快照所有计数都是整数（后端 SpeechStats 是 int，小数会 422）", () => {
    const acc = new SpeechAccumulator();
    acc.begin(0.4);
    for (const sample of frames([...voiced(500), ...silent(2500), ...voiced(500)], 50)) {
      acc.sample(sample.rms, 0.4 + sample.t + 0.37);
    }
    acc.stop(3600.77);
    acc.finalize("嗯，测试一下");

    const snapshot = acc.snapshot();
    expect(snapshot).not.toBeNull();
    for (const value of Object.values(snapshot!)) {
      expect(Number.isInteger(value)).toBe(true);
    }
  });

  it("校对替换最后一段：字数/口头禅重算，时长不变", () => {
    const acc = new SpeechAccumulator();
    acc.begin(0);
    acc.stop(3000);
    acc.finalize("嗯，那个");
    acc.updateLastTranscript("你好世界");

    expect(acc.snapshot()).toMatchObject({
      duration_ms: 3000,
      chars: 4,
      filler_count: 0,
    });
  });

  it("reset 清空全部；cancel 只丢当前段", () => {
    const acc = new SpeechAccumulator();
    acc.begin(0);
    acc.stop(1000);
    acc.finalize("你好");
    acc.begin(2000);
    acc.cancel();
    expect(acc.snapshot()?.segments).toBe(1);

    acc.reset();
    expect(acc.snapshot()).toBeNull();
  });
});

describe("展示文案", () => {
  it("时长", () => {
    expect(formatSpeechDuration(130000)).toBe("2 分 10 秒");
    expect(formatSpeechDuration(35000)).toBe("35 秒");
    expect(formatSpeechDuration(0)).toBe("0 秒");
  });

  it("语速", () => {
    expect(formatSpeechRate(186.4)).toBe("186 字/分");
    expect(formatSpeechRate(null)).toBe("—");
    expect(formatSpeechRate(Number.NaN)).toBe("—");
  });
});
