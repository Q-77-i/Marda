import { describe, expect, it } from "vitest";

import {
  WAVE_BAR_COUNT,
  WAVE_MIN_SCALE,
  levelToBars,
  micLabel,
  rms,
  smoothLevel,
  voiceLevel,
  voiceStatusText,
  voiceWaveState,
} from "@/lib/voice-wave";

describe("音频振幅", () => {
  it("恒定信号的 RMS 等于其绝对值", () => {
    expect(rms(new Float32Array([0.5, 0.5, 0.5, 0.5]))).toBeCloseTo(0.5, 6);
  });

  it("静音为 0，正负满量程为 1", () => {
    expect(rms(new Float32Array([0, 0, 0]))).toBe(0);
    expect(rms(new Float32Array([1, -1]))).toBe(1);
  });

  it("空缓冲为 0（不产生 NaN）", () => {
    expect(rms(new Float32Array([]))).toBe(0);
  });
});

describe("柱条映射", () => {
  it("数量固定，静音时所有柱条落在下限", () => {
    const bars = levelToBars(0);
    expect(bars).toHaveLength(WAVE_BAR_COUNT);
    expect(bars.every((value) => value === WAVE_MIN_SCALE)).toBe(true);
  });

  it("满幅时中间最高、两侧递减，且不超过 1", () => {
    const bars = levelToBars(1);
    expect(bars[2]).toBe(1);
    expect(bars[0]).toBeLessThan(bars[2]);
    expect(bars[1]).toBeLessThan(bars[2]);
    expect(bars.every((value) => value >= WAVE_MIN_SCALE && value <= 1)).toBe(true);
  });

  it("振幅越大柱条越高（单调）", () => {
    const low = levelToBars(0.3);
    const high = levelToBars(0.8);
    expect(high.every((value, index) => value >= low[index])).toBe(true);
  });

  it("越界振幅被夹紧", () => {
    expect(levelToBars(2)).toEqual(levelToBars(1));
    expect(levelToBars(-1)).toEqual(levelToBars(0));
  });
});

describe("平滑", () => {
  it("按系数向新值靠近", () => {
    expect(smoothLevel(0, 1, 0.5)).toBeCloseTo(0.5, 6);
    expect(smoothLevel(0.5, 1, 0.5)).toBeCloseTo(0.75, 6);
  });

  it("非有限值不污染状态", () => {
    expect(smoothLevel(Number.NaN, 0.4)).toBeCloseTo(0.4, 6);
    expect(smoothLevel(0.4, Number.NaN)).toBeCloseTo(0.4, 6);
  });
});

describe("可视电平", () => {
  it("噪声地板以下归零", () => {
    expect(voiceLevel(0)).toBe(0);
    expect(voiceLevel(0.005)).toBe(0);
  });

  it("正常说话电平明显高于静音（平方根压缩）", () => {
    expect(voiceLevel(0.04)).toBeGreaterThan(0.25);
    expect(voiceLevel(0.2)).toBeGreaterThan(0.6);
  });

  it("单调且不超过 1", () => {
    expect(voiceLevel(0.8)).toBe(1);
    expect(voiceLevel(0.2)).toBeGreaterThan(voiceLevel(0.04));
  });
});

describe("状态与文案", () => {
  it("录音优先于整理与连接", () => {
    expect(voiceWaveState({ recording: true, transcribing: true, connecting: true })).toBe(
      "recording",
    );
  });

  it("整理优先于连接", () => {
    expect(
      voiceWaveState({ recording: false, transcribing: true, connecting: true }),
    ).toBe("transcribing");
    expect(
      voiceWaveState({ recording: false, transcribing: false, connecting: true }),
    ).toBe("connecting");
    expect(
      voiceWaveState({ recording: false, transcribing: false, connecting: false }),
    ).toBe("idle");
  });

  it("按钮文案四态", () => {
    expect(micLabel("idle")).toBe("语音作答");
    expect(micLabel("connecting")).toBe("连接中…");
    expect(micLabel("recording")).toBe("结束录音");
    expect(micLabel("transcribing")).toBe("整理中…");
  });

  it("状态文本给读屏用户", () => {
    expect(voiceStatusText("recording")).toContain("录音");
    expect(voiceStatusText("transcribing")).toContain("转写");
    expect(voiceStatusText("connecting")).toContain("连接");
    expect(voiceStatusText("idle")).toBe("");
  });
});
