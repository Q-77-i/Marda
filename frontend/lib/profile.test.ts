/**
 * 能力档案展示逻辑单测（FR-19 / P1-M10）：曲线整形、刻度文案、短板变化。
 *
 * 页面渲染靠联调 smoke（同项目惯例：vitest 只覆盖 lib 纯逻辑）。
 */

import { describe, expect, it } from "vitest";

import type { ProfileSession, WeaknessChange } from "@/lib/api";
import {
  deltaLabel,
  overallRows,
  profileStage,
  scoreRows,
  tickLabels,
  weaknessRows,
} from "@/lib/profile";

function session(overrides: Partial<ProfileSession> = {}): ProfileSession {
  return {
    interview_id: "iv-1",
    position: "Agent/AI 工程师",
    difficulty: "adaptive",
    question_count: 10,
    answered_count: 10,
    started_at: "2026-09-01T10:00:00+00:00",
    overall: 4.0,
    scores: { technical_depth: 4, fundamentals: 4, project_experience: 4, communication: 4, problem_solving: 4 },
    domain_scores: { rag: 4.0 },
    weaknesses: ["rag"],
    ...overrides,
  };
}

describe("profileStage", () => {
  it("按场次数给三种形态", () => {
    expect(profileStage(0)).toBe("empty");
    expect(profileStage(1)).toBe("single");
    expect(profileStage(2)).toBe("series");
    expect(profileStage(9)).toBe("series");
  });
});

describe("tickLabels", () => {
  it("不同天用 MM-DD", () => {
    const labels = tickLabels([
      session({ started_at: "2026-09-01T10:00:00+00:00" }),
      session({ started_at: "2026-09-05T10:00:00+00:00" }),
    ]);

    expect(labels).toHaveLength(2);
    expect(labels[0]).not.toBe(labels[1]);
    expect(labels[0]).toMatch(/^09-0\d$/);
  });

  it("同一天多场加序号区分", () => {
    const same = "2026-09-01T10:00:00+00:00";
    const labels = tickLabels([
      session({ started_at: same }),
      session({ started_at: same }),
      session({ started_at: same }),
    ]);

    expect(labels[1]).toBe(`${labels[0]} #2`);
    expect(labels[2]).toBe(`${labels[0]} #3`);
  });

  it("时间缺失时退化为场次序", () => {
    expect(tickLabels([session({ started_at: "" })])).toEqual(["第 1 场"]);
  });
});

describe("overallRows", () => {
  it("总分与刻度一一对应", () => {
    const sessions = [
      session({ started_at: "2026-09-01T10:00:00+00:00", overall: 3.2 }),
      session({ started_at: "2026-09-05T10:00:00+00:00", overall: 4.1 }),
    ];

    const rows = overallRows(sessions);

    expect(rows.map((r) => r.overall)).toEqual([3.2, 4.1]);
    expect(rows.map((r) => r.label)).toEqual(tickLabels(sessions));
    // 点位点击跳报告靠它（图表不再自己去猜场次顺序）
    expect(rows.map((r) => r.interview_id)).toEqual(["iv-1", "iv-1"]);
  });
});

describe("scoreRows", () => {
  it("五维每场都有值", () => {
    const rows = scoreRows([session()], ["technical_depth", "fundamentals"], "scores");

    expect(rows[0]).toMatchObject({ technical_depth: 4, fundamentals: 4 });
  });

  it("域没考过是 null 而不是 0（曲线断开，不造低谷）", () => {
    const rows = scoreRows(
      [
        session({ domain_scores: { rag: 4.0 } }),
        session({ domain_scores: { memory: 2.0 } }),
      ],
      ["rag", "memory"],
      "domain_scores",
    );

    expect(rows[0]).toMatchObject({ rag: 4.0, memory: null });
    expect(rows[1]).toMatchObject({ rag: null, memory: 2.0 });
  });
});

describe("deltaLabel", () => {
  it("升/降/持平", () => {
    expect(deltaLabel(0.6)).toBe("↑ 0.6");
    expect(deltaLabel(-0.45)).toBe("↓ 0.5");
    expect(deltaLabel(0)).toBe("持平");
  });
});

describe("weaknessRows", () => {
  it("变化条目挂上对应场次的刻度标签", () => {
    const sessions = [
      session({ interview_id: "iv-1", started_at: "2026-09-01T10:00:00+00:00" }),
      session({ interview_id: "iv-2", started_at: "2026-09-05T10:00:00+00:00" }),
    ];
    const changes: WeaknessChange[] = [
      { interview_id: "iv-2", started_at: "2026-09-05T10:00:00+00:00", new: ["memory"], persistent: ["rag"], resolved: [] },
    ];

    const rows = weaknessRows(sessions, changes);

    expect(rows).toHaveLength(1);
    expect(rows[0].label).toBe(tickLabels(sessions)[1]);
    expect(rows[0].new).toEqual(["memory"]);
  });

  it("场次不在列表里时退化为时间文案", () => {
    const changes: WeaknessChange[] = [
      { interview_id: "gone", started_at: "2026-09-05T10:00:00+00:00", new: [], persistent: [], resolved: [] },
    ];

    expect(weaknessRows([], changes)[0].label).toMatch(/09-05/);
  });
});
