import { describe, expect, it } from "vitest";

import type { PerQuestionComment } from "@/lib/api";
import {
  commentLabels,
  completedCount,
  formatDuration,
  formatScore,
  formatTime,
  progressLabel,
} from "@/lib/format";

describe("progressLabel", () => {
  it("常规进度原样展示", () => {
    expect(progressLabel(3, 5)).toBe("3/5");
    expect(progressLabel(0, 10)).toBe("0/10");
    expect(progressLabel(15, 15)).toBe("15/15");
  });

  it("分母恒为用户配置的题量，场景题造成的超出被封顶", () => {
    // 15 题配置答完 15 道技术题后再答 1 道场景题 → answered=16，但显示仍是 /15
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

  it("场景题导致的超出被封顶到配置题量", () => {
    expect(completedCount(16, 15)).toBe(15);
    expect(completedCount(6, 5)).toBe(5);
  });
});

/** 造逐题点评条目：新 payload 带 domain，历史 payload 只有 question_id。 */
const comment = (
  question_id: string | null,
  domain?: string,
): PerQuestionComment => ({ question_id, comment: "点评", domain });

describe("commentLabels", () => {
  it("新 payload：场景题单列，技术题按序编号", () => {
    const items = [
      comment("q_a", "rag"),
      comment("q_b", "memory"),
      comment(null, "project"), // 场景题
    ];

    expect(commentLabels(items, 3, 2)).toEqual(["第 1 题", "第 2 题", "场景题"]);
  });

  it("15 题自然结束：第 16 条标场景题而不是「第 16 题」", () => {
    const items = [
      ...Array.from({ length: 15 }, (_, i) => comment(`q_${i}`, "rag")),
      comment(null, "project"),
    ];

    const labels = commentLabels(items, 16, 15);

    expect(labels[14]).toBe("第 15 题");
    expect(labels[15]).toBe("场景题");
    expect(labels).not.toContain("第 16 题");
  });

  it("主动结束（跳过场景题）：全部按序编号", () => {
    const items = Array.from({ length: 9 }, (_, i) => comment(`q_${i}`, "rag"));

    expect(commentLabels(items, 9, 15)).toEqual(
      Array.from({ length: 9 }, (_, i) => `第 ${i + 1} 题`),
    );
  });

  it("历史 payload 无 domain：超出配置题量的最后一条按场景题兜底", () => {
    const items = Array.from({ length: 16 }, () => comment("1"));

    const labels = commentLabels(items, 16, 15);

    expect(labels[14]).toBe("第 15 题");
    expect(labels[15]).toBe("场景题");
  });

  it("历史 payload 且未超出配置题量：不误标场景题", () => {
    const items = Array.from({ length: 9 }, () => comment("1"));

    expect(commentLabels(items, 9, 15)).not.toContain("场景题");
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
