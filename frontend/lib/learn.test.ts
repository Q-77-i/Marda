/**
 * 学习推荐纯逻辑单测（FR-20 / P1-M9）：可选场次、默认场次承接、空分组文案。
 *
 * 页面渲染靠联调 smoke（同项目惯例：vitest 只覆盖 lib 纯逻辑）。
 */

import { describe, expect, it } from "vitest";

import type { InterviewRow } from "@/lib/api";
import {
  finishedSessions,
  groupNotice,
  interviewOptionLabel,
  pickDefaultInterview,
} from "@/lib/learn";

function row(overrides: Partial<InterviewRow> = {}): InterviewRow {
  return {
    id: "i1",
    position: "Agent/AI 工程师",
    question_count: 10,
    phase: "finished",
    difficulty: "adaptive",
    status: "finished",
    started_at: "2026-09-30T06:30:00Z",
    ended_at: "2026-09-30T07:00:00Z",
    report_ready: true,
    ...overrides,
  };
}

describe("finishedSessions", () => {
  it("只留有报告的已结束场次", () => {
    const rows = [
      row({ id: "a", report_ready: true }),
      row({ id: "b", report_ready: false, status: "running" }),
    ];

    expect(finishedSessions(rows).map((r) => r.id)).toEqual(["a"]);
  });

  it("排除行为面场次（P1-M11 D5：推荐检索的是六大技术域）", () => {
    const rows = [
      row({ id: "tech", report_ready: true }),
      row({ id: "beh", report_ready: true, interview_type: "behavioral" }),
      row({ id: "old", report_ready: true, interview_type: null }),
    ];

    expect(finishedSessions(rows).map((r) => r.id)).toEqual(["tech", "old"]);
  });
});

describe("pickDefaultInterview", () => {
  const rows = [row({ id: "recent" }), row({ id: "older" })];

  it("URL 带过来的场次优先（从报告页跳进来时承接来源，不落到另一场）", () => {
    expect(pickDefaultInterview(rows, "older")).toBe("older");
  });

  it("没带或带的场次不在列表里 → 最近一场", () => {
    expect(pickDefaultInterview(rows, null)).toBe("recent");
    expect(pickDefaultInterview(rows, "deleted")).toBe("recent");
  });

  it("没有可选场次 → null", () => {
    expect(pickDefaultInterview([], null)).toBeNull();
    expect(pickDefaultInterview([], "x")).toBeNull();
  });
});

describe("groupNotice", () => {
  it("有卡片不给文案", () => {
    expect(groupNotice("ok")).toBeNull();
  });

  it("已练过与该域没题分开说——都不静默隐藏", () => {
    expect(groupNotice("exhausted")).toBe("该域题目已全部练过，暂无新推荐");
    expect(groupNotice("empty")).toBe("该域题库暂无题目");
  });
});

describe("interviewOptionLabel", () => {
  it("岗位 · 时间 · 轮次", () => {
    const label = interviewOptionLabel(row({ position: "Agent/AI 工程师", question_count: 5 }));

    expect(label).toContain("Agent/AI 工程师");
    expect(label).toContain("5 轮");
    expect(label).toMatch(/\d{2}-\d{2} \d{2}:\d{2}/);
  });

  it("时间解析不了也不崩（历史脏数据）", () => {
    expect(interviewOptionLabel(row({ started_at: "not-a-date" }))).toBe(
      "Agent/AI 工程师 ·  · 10 轮",
    );
  });
});
