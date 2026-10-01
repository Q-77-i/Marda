import { describe, expect, it } from "vitest";

import {
  BEHAVIORAL_MAX_QUESTIONS,
  PHASE_LABELS,
  QUESTION_TYPE_LABELS,
  REASON_LABELS,
  interviewTypeLabel,
  isBehavioral,
  questionCountOptions,
} from "@/lib/constants";

describe("会话类型（P1-M11）", () => {
  it("只有 behavioral 算行为面，缺失/NULL 按技术面处理", () => {
    expect(isBehavioral("behavioral")).toBe(true);
    expect(isBehavioral("tech")).toBe(false);
    expect(isBehavioral(null)).toBe(false);
    expect(isBehavioral(undefined)).toBe(false);
  });

  it("行为面题量上限 10（池子只有十来道，15 题场会当场耗尽）", () => {
    expect(questionCountOptions("behavioral")).toEqual([5, 10]);
    expect(questionCountOptions("tech")).toEqual([5, 10, 15]);
    expect(BEHAVIORAL_MAX_QUESTIONS).toBe(10);
  });

  it("类型徽标两种都标，老场次（NULL）按技术面显示", () => {
    expect(interviewTypeLabel("tech")).toBe("技术面");
    expect(interviewTypeLabel("behavioral")).toBe("行为面");
    expect(interviewTypeLabel(null)).toBe("技术面");
    expect(interviewTypeLabel(undefined)).toBe("技术面");
    expect(interviewTypeLabel("mixed")).toBe("mixed"); // 未知值原样显示，不猜
  });

  it("行为面阶段与题型都有展示标签（后端定义、前端只映射）", () => {
    expect(PHASE_LABELS.behavioral).toBe("行为面问答");
    expect(QUESTION_TYPE_LABELS.behavioral).toBe("行为面");
    expect(REASON_LABELS.deepen_limit).toBeTruthy();
  });
});
