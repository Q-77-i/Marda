import { describe, expect, it } from "vitest";

import {
  ROOM_TABS,
  latestQuestionText,
  normalizeRoomTab,
  panelFromSearch,
  roomTabLabel,
} from "@/lib/room-tabs";

describe("右栏 Tab 数据", () => {
  it("顺序固定为 状态 / 白板 / 代码", () => {
    expect(ROOM_TABS.map((tab) => tab.label)).toEqual(["状态", "白板", "代码"]);
  });

  it("三项 Tab 都已就绪（M15 起代码工作台可用）", () => {
    expect(ROOM_TABS.find((tab) => tab.value === "status")?.ready).toBe(true);
    expect(ROOM_TABS.find((tab) => tab.value === "board")?.ready).toBe(true);
    expect(ROOM_TABS.find((tab) => tab.value === "code")?.ready).toBe(true);
  });

  it("未知值回落到状态", () => {
    expect(normalizeRoomTab("board")).toBe("board");
    expect(normalizeRoomTab("nope")).toBe("status");
    expect(normalizeRoomTab(null)).toBe("status");
  });

  it("标签可查", () => {
    expect(roomTabLabel("status")).toBe("状态");
    expect(roomTabLabel("board")).toBe("白板");
    expect(roomTabLabel("code")).toBe("代码");
  });
});

describe("当前题目", () => {
  it("取最后一条带 question 标签的面试官消息", () => {
    const messages = [
      { role: "assistant" as const, content: "开场白" },
      { role: "assistant" as const, content: "第一题", tag: { index: 1 } },
      { role: "user" as const, content: "回答" },
      { role: "assistant" as const, content: "点评" },
      { role: "assistant" as const, content: "第二题", tag: { index: 2 } },
    ];
    expect(latestQuestionText(messages)).toBe("第二题");
  });

  it("没有题目时返回 null", () => {
    expect(latestQuestionText([])).toBeNull();
    expect(latestQuestionText([{ role: "assistant", content: "开场白" }])).toBeNull();
    expect(
      latestQuestionText([{ role: "user", content: "回答", tag: { index: 1 } }]),
    ).toBeNull();
  });

  it("刷新/回放没有 question 标签：可回退到最后一条面试官消息", () => {
    const messages = [
      { role: "assistant" as const, content: "开场白" },
      { role: "user" as const, content: "回答" },
      { role: "assistant" as const, content: "当前题目" },
    ];
    expect(latestQuestionText(messages, true)).toBe("当前题目");
    expect(latestQuestionText(messages)).toBeNull();
  });
});

describe("刷新恢复面板", () => {
  it("只认 board / code，未知值回落面试间", () => {
    expect(panelFromSearch("?panel=code")).toBe("code");
    expect(panelFromSearch("?panel=board&x=1")).toBe("board");
    expect(panelFromSearch("?panel=status")).toBeNull();
    expect(panelFromSearch("?panel=")).toBeNull();
    expect(panelFromSearch("")).toBeNull();
  });
});
