import { describe, expect, it } from "vitest";

import {
  asrUrl,
  blocksSubmit,
  canStartRecording,
  downsampleTo16k,
  floatToPcm16,
  mergeTranscript,
  micDisabled,
  micErrorMessage,
  pcmBytes,
} from "@/lib/voice";

describe("音频数学", () => {
  it("48k → 16k：长度取整三分之一，常量信号均值不变", () => {
    const input = new Float32Array(4800).fill(0.5);
    const out = downsampleTo16k(input, 48000);
    expect(out.length).toBe(1600);
    expect(out[0]).toBeCloseTo(0.5, 6);
    expect(out[1599]).toBeCloseTo(0.5, 6);
  });

  it("44.1k → 16k：长度按比例向下取整", () => {
    const out = downsampleTo16k(new Float32Array(4410), 44100);
    expect(out.length).toBe(1600); // floor(4410 / 2.75625)
  });

  it("已是 16k 或更低：原样返回副本（不改调用方的缓冲）", () => {
    const input = new Float32Array([0.1, 0.2]);
    const out = downsampleTo16k(input, 16000);
    expect(out.length).toBe(2);
    expect(out[0]).toBeCloseTo(0.1, 6); // Float32 精度：0.1 存进去本就不是精确的 0.1
    expect(out[1]).toBeCloseTo(0.2, 6);
    expect(out).not.toBe(input);
  });

  it("Float32 → Int16：超范围夹紧，两端取满量程", () => {
    const out = floatToPcm16(new Float32Array([0, 1, -1, 2, -2]));
    expect(Array.from(out)).toEqual([0, 32767, -32768, 32767, -32768]);
  });

  it("Int16 → 字节：小端序（-2 = FE FF）", () => {
    expect(Array.from(pcmBytes(new Int16Array([-2, 1])))).toEqual([0xfe, 0xff, 0x01, 0x00]);
  });
});

describe("连接地址", () => {
  const dev = { protocol: "http:", hostname: "localhost", port: "3000", host: "localhost:3000" };

  it("next dev（3000）：直连后端 8000（rewrites 不代理 WS upgrade）", () => {
    expect(asrUrl(dev, "tok")).toBe("ws://localhost:8000/api/asr?token=tok");
  });

  it("生产/容器：同源（nginx 转发带 Upgrade 头）", () => {
    const prod = { protocol: "http:", hostname: "marda.local", port: "8080", host: "marda.local:8080" };
    expect(asrUrl(prod, "tok")).toBe("ws://marda.local:8080/api/asr?token=tok");
  });

  it("https 下用 wss；token 做 URL 编码", () => {
    const prod = { protocol: "https:", hostname: "marda.app", port: "", host: "marda.app" };
    expect(asrUrl(prod, "a b+c")).toBe("wss://marda.app/api/asr?token=a%20b%2Bc");
  });
});

describe("转写落框", () => {
  it("空草稿：直接用转写", () => {
    expect(mergeTranscript("", "你好")).toBe("你好");
  });

  it("有草稿：草稿是底稿，转写替换其后整段（不叠片）", () => {
    expect(mergeTranscript("先补充一点", "你好")).toBe("先补充一点\n你好");
  });

  it("草稿末尾空白先收干净，再与转写分段", () => {
    expect(mergeTranscript("先补充一点\n\n", "你好")).toBe("先补充一点\n你好");
  });

  it("转写为空（没听清）：草稿原样保留", () => {
    expect(mergeTranscript("先补充一点", "   ")).toBe("先补充一点");
  });
});

describe("按钮与文案", () => {
  it("录音键：面试官说话中（busy）不给开麦", () => {
    const base = { recording: false, connecting: false, supported: true, busy: false };
    expect(canStartRecording(base)).toBe(true);
    expect(canStartRecording({ ...base, busy: true })).toBe(false);
    expect(canStartRecording({ ...base, recording: true })).toBe(false);
    expect(canStartRecording({ ...base, connecting: true })).toBe(false);
    expect(canStartRecording({ ...base, supported: false })).toBe(false);
  });

  it("录音期间挡发送（半截话不该发出去）", () => {
    expect(blocksSubmit(true)).toBe(true);
    expect(blocksSubmit(false)).toBe(false);
  });

  it("录音键禁用判据：**录音中永远可点**（否则用户按不停、录音收不了尾）", () => {
    const idle = { recording: false, connecting: false, supported: true, busy: false };
    expect(micDisabled({ ...idle, finished: false })).toBe(false);
    expect(micDisabled({ ...idle, recording: true, finished: false })).toBe(false); // ← 这条是真 bug 的回归
    expect(micDisabled({ ...idle, recording: true, busy: true, finished: false })).toBe(false);
    expect(micDisabled({ ...idle, busy: true, finished: false })).toBe(true);
    expect(micDisabled({ ...idle, connecting: true, finished: false })).toBe(true);
    expect(micDisabled({ ...idle, supported: false, finished: false })).toBe(true);
    expect(micDisabled({ ...idle, finished: true })).toBe(true);
  });

  it("降级文案按错误类型分流，且都给文字出路", () => {
    expect(micErrorMessage(new DOMException("x", "NotAllowedError"))).toContain("麦克风权限");
    expect(micErrorMessage(new DOMException("x", "NotFoundError"))).toContain("没有找到");
    expect(micErrorMessage(new Error("语音服务连接失败：OSError"))).toContain("语音服务连接失败");
    expect(micErrorMessage(undefined)).toContain("文字");
  });
});
