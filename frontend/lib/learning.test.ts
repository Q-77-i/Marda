import { describe, expect, it } from "vitest";

import type { ReportPayload } from "@/lib/api";
import {
  buildReportSummary,
  dueLabel,
  nextTaskStatus,
  planProgress,
  recommendationHref,
  taskStatusLabel,
  taskStatusTone,
  type LearningPlan,
  type LearningTask,
} from "@/lib/learning";

function report(overrides: Partial<ReportPayload> = {}): ReportPayload {
  return {
    interview_id: "iv-1",
    position: "Agent",
    interview_type: "tech",
    scores: { depth: 3, breadth: 4 },
    overall: 3.4,
    domain_scores: { rag: 2.5, memory: 3 },
    weaknesses: ["rag", "memory"],
    answered_count: 3,
    question_count: 3,
    total_comment: "",
    per_question_comments: [],
    study_advice: [{ domain: "rag", advice: "补混合检索" }],
    ...overrides,
  };
}

function task(overrides: Partial<LearningTask> = {}): LearningTask {
  return {
    id: "t1",
    plan_id: "p1",
    domain: "rag",
    title: "补检索",
    resource_url: null,
    status: "todo",
    due_at: null,
    priority: 1,
    version: 1,
    ...overrides,
  };
}

function plan(tasks: LearningTask[]): LearningPlan {
  return {
    id: "p1",
    user_id: "u1",
    source_interview_id: "iv-1",
    goal: "目标",
    status: "active",
    version: 1,
    created_at: "2026-11-11T00:00:00Z",
    updated_at: "2026-11-11T00:00:00Z",
    tasks,
  };
}

describe("buildReportSummary", () => {
  it("maps weak domains with scores and advice", () => {
    expect(buildReportSummary(report())).toEqual({
      overall: 3.4,
      weak_domains: [
        { domain: "rag", score: 2.5, advice: "补混合检索" },
        { domain: "memory", score: 3, advice: null },
      ],
    });
  });

  it("skips weaknesses without a numeric domain score", () => {
    expect(buildReportSummary(report({ domain_scores: { rag: 2.5 } }))).toEqual({
      overall: 3.4,
      weak_domains: [{ domain: "rag", score: 2.5, advice: "补混合检索" }],
    });
  });

  it("returns null when no weakness has a score", () => {
    expect(buildReportSummary(report({ domain_scores: {} }))).toBeNull();
  });

  it("returns null for behavioral reports", () => {
    expect(buildReportSummary(report({ interview_type: "behavioral" }))).toBeNull();
  });

  it("falls back to the mean of scores when overall is missing", () => {
    const summary = buildReportSummary(
      report({ overall: undefined, scores: { a: 3, b: 4 } }),
    );
    expect(summary?.overall).toBe(3.5);
  });

  it("caps weak domains at five", () => {
    const summary = buildReportSummary(
      report({
        weaknesses: ["a", "b", "c", "d", "e", "f"],
        domain_scores: { a: 1, b: 2, c: 3, d: 4, e: 5, f: 1 },
        study_advice: [],
      }),
    );
    expect(summary?.weak_domains).toHaveLength(5);
  });
});

describe("planProgress", () => {
  it("counts done and skipped as handled", () => {
    expect(
      planProgress(
        plan([
          task({ status: "done" }),
          task({ id: "t2", status: "skipped" }),
          task({ id: "t3" }),
        ]),
      ),
    ).toEqual({ handled: 2, total: 3, percent: 67 });
  });

  it("returns zero percent for an empty plan", () => {
    expect(planProgress(plan([]))).toEqual({ handled: 0, total: 0, percent: 0 });
  });
});

describe("task status helpers", () => {
  it("maps labels and tones", () => {
    expect(taskStatusLabel("in_progress")).toBe("进行中");
    expect(taskStatusTone("done")).toBe("success");
    expect(taskStatusTone("skipped")).toBe("secondary");
  });

  it("advances only non-terminal statuses", () => {
    expect(nextTaskStatus("todo")).toBe("in_progress");
    expect(nextTaskStatus("in_progress")).toBe("done");
    expect(nextTaskStatus("done")).toBeNull();
    expect(nextTaskStatus("skipped")).toBeNull();
  });
});

describe("recommendationHref", () => {
  it("uses only the hash when the plan source is already selected", () => {
    expect(recommendationHref("iv-1", "iv-1")).toBe("#recommendations");
  });

  it("navigates with the plan source when another interview is selected", () => {
    expect(recommendationHref("iv-1", "iv-2")).toBe(
      "/learn?interview=iv-1#recommendations",
    );
    expect(recommendationHref("iv-1", null)).toBe(
      "/learn?interview=iv-1#recommendations",
    );
  });
});

describe("dueLabel", () => {
  const now = new Date(2026, 10, 11, 12, 0, 0); // 本地时间 2026-11-11 12:00

  it("labels today, tomorrow, overdue, later and missing", () => {
    expect(dueLabel(new Date(2026, 10, 11, 23, 59, 59).toISOString(), now)).toBe(
      "今天",
    );
    expect(dueLabel(new Date(2026, 10, 12, 23, 59, 59).toISOString(), now)).toBe(
      "明天",
    );
    expect(dueLabel(new Date(2026, 10, 9, 23, 59, 59).toISOString(), now)).toBe(
      "已逾期",
    );
    expect(dueLabel(new Date(2026, 10, 20, 23, 59, 59).toISOString(), now)).toBe(
      "11-20",
    );
    expect(dueLabel(null, now)).toBe("无截止日");
  });
});
