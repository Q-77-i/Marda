import { describe, expect, it } from "vitest";

import {
  ACCEPTED_TYPES,
  MAX_IMAGES_PER_MESSAGE,
  MAX_SOURCE_BYTES,
  TARGET_LONG_EDGE,
  pickError,
  targetSize,
} from "@/lib/vision";

const png = (size = 1024) => ({ type: "image/png", size });

describe("pickError", () => {
  it("正常图片通过", () => {
    expect(pickError(png(), 0)).toBeNull();
  });

  it("数量到上限就拒绝（含边界）", () => {
    expect(pickError(png(), MAX_IMAGES_PER_MESSAGE - 1)).toBeNull();
    expect(pickError(png(), MAX_IMAGES_PER_MESSAGE)).toContain("最多附");
  });

  it("类型白名单与后端一致：png/jpeg/webp 通过，gif/svg 拒绝", () => {
    expect(ACCEPTED_TYPES).toEqual(["image/png", "image/jpeg", "image/webp"]);
    expect(pickError({ type: "image/gif", size: 1024 }, 0)).toContain("仅支持");
    expect(pickError({ type: "image/svg+xml", size: 1024 }, 0)).toContain("仅支持");
  });

  it("超过选择上限就拒绝（压缩前；后端还有 8MB 兜底）", () => {
    expect(pickError({ type: "image/png", size: MAX_SOURCE_BYTES + 1 }, 0)).toContain("超过");
    expect(pickError({ type: "image/png", size: MAX_SOURCE_BYTES }, 0)).toBeNull();
  });
});

describe("targetSize", () => {
  it("长边超限按等比缩到 1568（竖图按高算）", () => {
    expect(targetSize(2800, 1040)).toEqual({ width: 1568, height: 582 });
    expect(targetSize(1040, 2800)).toEqual({ width: 582, height: 1568 });
  });

  it("本来就小则原样（不放大）", () => {
    expect(targetSize(900, 340)).toEqual({ width: 900, height: 340 });
  });

  it("正方形与取整边界", () => {
    expect(targetSize(3136, 3136)).toEqual({ width: TARGET_LONG_EDGE, height: TARGET_LONG_EDGE });
    expect(targetSize(2000, 999).width).toBe(1568);
  });
});
