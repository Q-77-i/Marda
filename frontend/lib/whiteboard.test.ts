import { describe, expect, it } from "vitest";

import {
  BOARD_DEFAULT_TEXT,
  boardSubmitText,
  canSubmitBoard,
  parseWhiteboardScene,
} from "@/lib/whiteboard";

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

describe("白板场景刷新快照", () => {
  it("有 elements 即可恢复，appState/files 缺省为空对象", () => {
    const parsed = parseWhiteboardScene(JSON.stringify({
      elements: [{ id: "a", type: "rectangle" }],
      appState: { viewBackgroundColor: "#fff" },
    }));

    expect(parsed).not.toBeNull();
    expect(parsed?.elements).toHaveLength(1);
    expect(parsed?.appState).toEqual({ viewBackgroundColor: "#fff" });
    expect(parsed?.files).toEqual({});
  });

  it("坏 JSON / 没有 elements / 空值一律丢弃", () => {
    expect(parseWhiteboardScene(null)).toBeNull();
    expect(parseWhiteboardScene("not-json")).toBeNull();
    expect(parseWhiteboardScene(JSON.stringify({ appState: {} }))).toBeNull();
    expect(parseWhiteboardScene(JSON.stringify({ elements: "bad" }))).toBeNull();
  });
});
