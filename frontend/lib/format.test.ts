import { describe, expect, it } from "vitest";

import type { PerQuestionComment } from "@/lib/api";
import { FOLLOWUP_ANSWER_MARKER } from "@/lib/constants";
import {
  commentLabels,
  completedCount,
  formatDuration,
  formatScore,
  formatTime,
  progressLabel,
  splitAnswerSegments,
} from "@/lib/format";

describe("progressLabel", () => {
  it("常规进度原样展示", () => {
    expect(progressLabel(3, 5)).toBe("3/5");
    expect(progressLabel(0, 10)).toBe("0/10");
    expect(progressLabel(15, 15)).toBe("15/15");
  });

  it("分母恒为用户配置的题量，项目深挖题造成的超出被封顶", () => {
    // 15 题配置答完 15 道技术题后再答 1 道项目深挖题 → answered=16，但显示仍是 /15
    expect(progressLabel(16, 15)).toBe("15/15");
    expect(progressLabel(6, 5)).toBe("5/5");
    expect(progressLabel(11, 10)).toBe("10/10");
  });
});

describe("completedCount", () => {
  it("不超过配置题量时原样返回", () => {
    expect(completedCount(9, 15)).toBe(9);
    expect(completedCount(15, 15)).toBe(15);
  });

  it("项目深挖题导致的超出被封顶到配置题量", () => {
    expect(completedCount(16, 15)).toBe(15);
    expect(completedCount(6, 5)).toBe(5);
  });
});

/** 造逐题点评条目：新 payload 带 number/question_type，历史 payload 只有 question_id。 */
const comment = (
  question_id: string | null,
  domain?: string,
  number?: number | null,
  question_type?: string,
): PerQuestionComment => ({ question_id, comment: "点评", domain, number, question_type });

describe("commentLabels", () => {
  it("旧 payload（有 domain 无题型字段）：项目深挖题按 domain 兜底", () => {
    const items = [
      comment("q_a", "rag"),
      comment("q_b", "memory"),
      comment(null, "project"), // 项目深挖题
    ];

    expect(commentLabels(items, 3, 2)).toEqual(["第 1 题", "第 2 题", "项目深挖"]);
  });

  it("T7a 新契约：number 与 question_type 由后端给，项目深挖题计入轮次", () => {
    const items = [
      comment("q_a", "rag", 1, "tech"),
      comment("q_b", "memory", 2, "tech"),
      comment(null, "project", 3, "scenario"),
    ];

    expect(commentLabels(items, 3, 3)).toEqual(["第 1 题", "第 2 题", "第 3 题 · 项目深挖"]);
  });

  it("T7a：number 优先于 question_type（轮次编号 + 题型标注）", () => {
    const items = [comment(null, "project", 3, "scenario")];

    expect(commentLabels(items, 1, 3)).toEqual(["第 3 题 · 项目深挖"]);
  });

  it("T7a：未知题型无编号时显示原值，不猜", () => {
    const items = [comment(null, "behavioral", null, "behavioral")];

    expect(commentLabels(items, 1, 2)).toEqual(["behavioral"]);
  });

  it("15 题自然结束：第 16 条标项目深挖题而不是「第 16 题」", () => {
    const items = [
      ...Array.from({ length: 15 }, (_, i) => comment(`q_${i}`, "rag")),
      comment(null, "project"),
    ];

    const labels = commentLabels(items, 16, 15);

    expect(labels[14]).toBe("第 15 题");
    expect(labels[15]).toBe("项目深挖");
    expect(labels).not.toContain("第 16 题");
  });

  it("主动结束（跳过项目深挖题）：全部按序编号", () => {
    const items = Array.from({ length: 9 }, (_, i) => comment(`q_${i}`, "rag"));

    expect(commentLabels(items, 9, 15)).toEqual(
      Array.from({ length: 9 }, (_, i) => `第 ${i + 1} 题`),
    );
  });

  it("历史 payload 无 domain：超出配置题量的最后一条按项目深挖题兜底", () => {
    const items = Array.from({ length: 16 }, () => comment("1"));

    const labels = commentLabels(items, 16, 15);

    expect(labels[14]).toBe("第 15 题");
    expect(labels[15]).toBe("项目深挖");
  });

  it("历史 payload 且未超出配置题量：不误标项目深挖题", () => {
    const items = Array.from({ length: 9 }, () => comment("1"));

    expect(commentLabels(items, 9, 15)).not.toContain("项目深挖");
  });
});

describe("formatDuration", () => {
  it("未结束返回 null", () => {
    expect(formatDuration("2026-09-19T13:00:00+00:00", null)).toBeNull();
  });

  it("不足一分钟按 1 分钟计", () => {
    expect(
      formatDuration("2026-09-19T13:00:00+00:00", "2026-09-19T13:00:42+00:00"),
    ).toBe("1 分钟");
  });

  it("起止倒挂返回 null", () => {
    expect(
      formatDuration("2026-09-19T13:05:00+00:00", "2026-09-19T13:00:00+00:00"),
    ).toBeNull();
  });
});

describe("formatScore", () => {
  it("保留一位小数", () => {
    expect(formatScore(3)).toBe("3.0");
    expect(formatScore(2.45)).toBe("2.5");
  });
});

describe("formatTime", () => {
  it("非法时间串返回空", () => {
    expect(formatTime("not-a-date")).toBe("");
  });
});

describe("splitAnswerSegments", () => {
  it("未追问：整段即首答", () => {
    expect(splitAnswerSegments("我的回答是这样的")).toEqual([
      { label: "首答", text: "我的回答是这样的" },
    ]);
  });

  it("追问轮：按后端标记拆段并标序号（数据层是拼接串）", () => {
    const answer = `首答内容\n\n${FOLLOWUP_ANSWER_MARKER}补充一\n\n${FOLLOWUP_ANSWER_MARKER}补充二`;

    expect(splitAnswerSegments(answer)).toEqual([
      { label: "首答", text: "首答内容" },
      { label: "追问补充 1", text: "补充一" },
      { label: "追问补充 2", text: "补充二" },
    ]);
  });

  it("标记文案是前后端契约（后端 graph/state.FOLLOWUP_ANSWER_MARKER）", () => {
    expect(FOLLOWUP_ANSWER_MARKER).toBe("【追问补充】");
  });

  it("空值与空白返回空数组（历史 payload 无 candidate_answer）", () => {
    expect(splitAnswerSegments(null)).toEqual([]);
    expect(splitAnswerSegments(undefined)).toEqual([]);
    expect(splitAnswerSegments("   ")).toEqual([]);
  });

  it("多行回答保留段内换行", () => {
    expect(splitAnswerSegments("第一行\n第二行")).toEqual([
      { label: "首答", text: "第一行\n第二行" },
    ]);
  });
});
