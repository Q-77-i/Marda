import { describe, expect, it } from "vitest";

import { BOARD_DEFAULT_TEXT, boardSubmitText, canSubmitBoard } from "@/lib/whiteboard";

describe("白板提交判据", () => {
  it("没有图元不能提交", () => {
    expect(canSubmitBoard(0, false)).toBe(false);
    expect(canSubmitBoard(1, false)).toBe(true);
  });

  it("上传中不能重复提交", () => {
    expect(canSubmitBoard(3, true)).toBe(false);
  });

  it("草稿为空时用默认说明，有草稿则保留原话", () => {
    expect(boardSubmitText("   ")).toBe(BOARD_DEFAULT_TEXT);
    expect(boardSubmitText("这是我的架构图")).toBe("这是我的架构图");
  });
});
