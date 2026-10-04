import { describe, expect, it, vi } from "vitest";

import {
  CAMERA_CONSTRAINTS,
  CAMERA_MODE_KEY,
  cameraDisabled,
  cameraErrorMessage,
  stopStream,
} from "@/lib/camera";

describe("摄像头开关判据", () => {
  it("未开启且支持：可点（去开）", () => {
    expect(cameraDisabled({ on: false, starting: false, supported: true })).toBe(false);
  });

  it("开启中且支持：可点——那就是「关闭画面」，不能像 M5 录音键那样把它禁用掉", () => {
    expect(cameraDisabled({ on: true, starting: false, supported: true })).toBe(false);
  });

  it("启动中：不可点（防连点开出两条流）", () => {
    expect(cameraDisabled({ on: false, starting: true, supported: true })).toBe(true);
  });

  it("浏览器不支持（无 getUserMedia / 非安全上下文）：不可点", () => {
    expect(cameraDisabled({ on: false, starting: false, supported: false })).toBe(true);
  });
});

describe("摄像头错误分流（降级链：明说原因 + 给出路，不弹死胡同）", () => {
  it("权限被拒（NotAllowedError / SecurityError）→ 指路地址栏", () => {
    const msg = cameraErrorMessage(new DOMException("denied", "NotAllowedError"));
    expect(msg).toContain("权限");
    expect(msg).toContain("地址栏");
  });

  it("没有摄像头（NotFoundError / OverconstrainedError）→ 明说没有设备", () => {
    expect(cameraErrorMessage(new DOMException("x", "NotFoundError"))).toContain("没有找到");
    expect(cameraErrorMessage(new DOMException("x", "OverconstrainedError"))).toContain("没有找到");
  });

  it("被占用（NotReadableError / AbortError）→ 明说被其他程序占用", () => {
    expect(cameraErrorMessage(new DOMException("x", "NotReadableError"))).toContain("占用");
    expect(cameraErrorMessage(new DOMException("x", "AbortError"))).toContain("占用");
  });

  it("未知 DOMException 名 → 带原因兜底，不抛错", () => {
    const msg = cameraErrorMessage(new DOMException("weird", "WeirdError"));
    expect(msg).toContain("WeirdError");
  });

  it("普通 Error 用其 message；完全未知形状给通用文案", () => {
    expect(cameraErrorMessage(new Error("boom"))).toBe("boom");
    expect(cameraErrorMessage(null)).toContain("摄像头");
  });
});

describe("取流约束", () => {
  it("音频显式关闭（只开画面，不偷开麦克风）", () => {
    expect(CAMERA_CONSTRAINTS.audio).toBe(false);
  });

  it("分辨率用 ideal 不用 exact（exact 会给不支持的设备抛 OverconstrainedError）", () => {
    const video = CAMERA_CONSTRAINTS.video as MediaTrackConstraints;
    expect(video.width).toEqual({ ideal: 1280 });
    expect(video.height).toEqual({ ideal: 720 });
    expect(video.facingMode).toBe("user"); // 前置摄像头优先（ideal 语义）
  });
});

describe("轨道收摊（关闭开关 / 离开页面 / 面试结束三处共用一个出口）", () => {
  it("停掉流上的全部轨道", () => {
    const stopA = vi.fn();
    const stopB = vi.fn();
    stopStream({ getTracks: () => [{ stop: stopA }, { stop: stopB }] } as unknown as MediaStream);
    expect(stopA).toHaveBeenCalledTimes(1);
    expect(stopB).toHaveBeenCalledTimes(1);
  });

  it("null 安全（未开启时各收摊路径照跑）", () => {
    expect(() => stopStream(null)).not.toThrow();
  });
});

describe("持久化键", () => {
  it("与语音模式的键同名空间（marda.*）", () => {
    expect(CAMERA_MODE_KEY).toBe("marda.cameraMode");
  });
});
