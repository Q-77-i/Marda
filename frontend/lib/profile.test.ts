/**
 * 能力档案展示逻辑单测（FR-19 / P1-M10 / P1-M10.5）：
 * 曲线整形、刻度文案、热力图分档与缺场、短板变化与洞察文案。
 *
 * 页面渲染靠联调 smoke（同项目惯例：vitest 只覆盖 lib 纯逻辑）。
 */

import { describe, expect, it } from "vitest";

import type { ProfileSession, WeaknessChange } from "@/lib/api";
import {
  HEATMAP_WINDOW,
  axisTicks,
  deltaLabel,
  dimensionStats,
  domainStats,
  emptyProfileCopy,
  emptyProfileKind,
  excludedNotes,
  heatLevel,
  heatRows,
  heatmapWindow,
  insightLine,
  overallRows,
  profileStage,
  recentSlice,
  tickLabels,
  weaknessRows,
  weaknessStreak,
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

describe("axisTicks", () => {
  it("一天一个标签：同日多场只标该组首场", () => {
    const labels = ["09-26", "09-30", "09-30 #2", "10-01", "10-01 #2", "10-01 #3", "10-01 #4"];

    expect(axisTicks(labels)).toEqual(["09-26", "09-30", "10-01"]);
  });

  it("不产生孤儿编号（#2 出现则 #1 必在）", () => {
    const ticks = axisTicks(["09-30", "09-30 #2", "10-01"]);

    expect(ticks.some((tick) => tick.includes("#"))).toBe(false);
    expect(ticks).toEqual(["09-30", "10-01"]);
  });

  it("日期组超过上限时等距抽稀，首末必留", () => {
    const labels = Array.from({ length: 12 }, (_, index) => `09-${String(index + 1).padStart(2, "0")}`);

    const ticks = axisTicks(labels, 6);

    expect(ticks[0]).toBe("09-01");
    expect(ticks[ticks.length - 1]).toBe("09-12");
    expect(ticks.length).toBeLessThanOrEqual(6);
  });

  it("时间缺失的退化标签各自成组，不被吞掉", () => {
    expect(axisTicks(["第 1 场", "第 2 场"])).toEqual(["第 1 场", "第 2 场"]);
  });
});

describe("heatRows / heatLevel", () => {
  it("域没考过是 null 而不是 0（热力图上渲染成「未考」）", () => {
    const rows = heatRows(
      [session({ domain_scores: { rag: 4.2 } }), session({ domain_scores: { memory: 2.0 } })],
      ["rag", "memory"],
    );

    expect(rows[0]).toEqual({ domain: "rag", cells: [4.2, null] });
    expect(rows[1]).toEqual({ domain: "memory", cells: [null, 2.0] });
  });

  it("分数就近取整成 1–5 档，超界夹到端点", () => {
    expect(heatLevel(1.2)).toBe(1);
    expect(heatLevel(2.5)).toBe(3);
    expect(heatLevel(4.8)).toBe(5);
    expect(heatLevel(0)).toBe(1);
    expect(heatLevel(9)).toBe(5);
  });

  it("未考没有档（不给颜色，也不参与分档）", () => {
    expect(heatLevel(null)).toBeNull();
  });
});

describe("domainStats", () => {
  it("只数考过的场次（缺场不计 0），均分保留一位", () => {
    const stats = domainStats(
      [
        session({ domain_scores: { rag: 4.0 } }),
        session({ domain_scores: { memory: 2.0 } }),
        session({ domain_scores: { rag: 3.0 } }),
      ],
      ["rag", "memory"],
    );

    expect(stats).toEqual([
      { domain: "rag", tested: 2, average: 3.5 },
      { domain: "memory", tested: 1, average: 2 },
    ]);
  });
});

describe("recentSlice / heatmapWindow", () => {
  const many = Array.from({ length: 10 }, (_, index) => `s${index + 1}`);

  it("不足窗口或已展开时原样返回（场次没到窗口就不给展开按钮）", () => {
    expect(recentSlice(["a", "b"], 5, false)).toEqual(["a", "b"]);
    expect(recentSlice(many, 5, true)).toEqual(many);
  });

  it("取的是**尾部**最近 limit 项（不是头部）", () => {
    expect(recentSlice(many, 3, false)).toEqual(["s8", "s9", "s10"]);
  });

  it("热力图列与列头按同一后缀切片，一一对应", () => {
    const sessions = Array.from({ length: 10 }, (_, index) =>
      session({ interview_id: `iv-${index + 1}`, domain_scores: { rag: 4 } }),
    );
    const labels = tickLabels(sessions);

    const shown = heatmapWindow(sessions, labels, false);

    expect(shown.sessions).toHaveLength(HEATMAP_WINDOW);
    expect(shown.labels).toEqual(labels.slice(-HEATMAP_WINDOW));
    expect(shown.labels).toHaveLength(shown.sessions.length);
  });

  it("窗口从某天中间截断时列头保留 #n（两个视图同一场必须同名）", () => {
    const same = "2026-09-01T10:00:00+00:00";
    const sessions = Array.from({ length: 9 }, () => session({ started_at: same }));
    const labels = tickLabels(sessions); // 09-01、09-01 #2 … 09-01 #9

    const shown = heatmapWindow(sessions, labels, false);

    // 保留的是全量那套标签（第 3 场起），不是重算成「09-01 #1」
    expect(shown.labels[0]).toBe(labels[2]);
    expect(shown.labels[0]).toContain("#3");
  });
});

describe("dimensionStats", () => {
  it("场均覆盖全部场次、最近一场取末场（scores 五维齐，后端缺维按 0 落库）", () => {
    const stats = dimensionStats(
      [
        session({ scores: { ...session().scores, technical_depth: 2 } }),
        session({ scores: { ...session().scores, technical_depth: 4 } }),
      ],
      ["technical_depth"],
    );

    expect(stats[0]).toEqual({ key: "technical_depth", average: 3, latest: 4 });
  });
});

describe("weaknessStreak", () => {
  it("从最后一场往前数连续是短板的场次", () => {
    const sessions = [
      session({ weaknesses: ["memory"], domain_scores: { memory: 2.0 } }),
      session({ weaknesses: ["memory", "rag"], domain_scores: { memory: 2.0, rag: 3.0 } }),
      session({ weaknesses: ["memory"], domain_scores: { memory: 2.0 } }),
    ];

    expect(weaknessStreak(sessions, "memory")).toBe(3);
    // 中间场断过一次，trailing 连续段归零
    expect(weaknessStreak(sessions, "rag")).toBe(0);
  });
});

describe("insightLine", () => {
  it("连续短板与场均最低各成事实，按此顺序连写", () => {
    const sessions = [
      session({ weaknesses: ["memory"], domain_scores: { memory: 1.0, rag: 4.0 } }),
      session({ weaknesses: ["memory"], domain_scores: { memory: 2.0, rag: 4.0 } }),
      session({ weaknesses: ["memory"], domain_scores: { memory: 2.0, rag: 4.0 } }),
    ];

    expect(insightLine(sessions, ["memory", "rag"])).toBe(
      "Memory 已连续 3 场是短板；场均最低的知识域是 Memory（1.7 分，考过 3 场）。",
    );
  });

  it("只考过一场的域不参与「场均最低」", () => {
    const sessions = [
      session({ weaknesses: [], domain_scores: { rag: 4.0, memory: 1.0 } }),
      session({ weaknesses: [], domain_scores: { rag: 4.0 } }),
    ];

    // memory 只考过一场（1.0 分最低）→ 不出现；rag 考过两场但无高低差 → 也不出现
    expect(insightLine(sessions, ["memory", "rag"])).toBeNull();
  });

  it("各域打平不说「最低」（最低最高是同一个人）", () => {
    const sessions = [
      session({ weaknesses: [], domain_scores: { rag: 4.0, memory: 4.0 } }),
      session({ weaknesses: [], domain_scores: { rag: 4.0, memory: 4.0 } }),
    ];

    expect(insightLine(sessions, ["rag", "memory"])).toBeNull();
  });

  it("不足两场无从比较，不产出文案", () => {
    expect(insightLine([session()], ["rag"])).toBeNull();
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

describe("未计入场次的说明（P1-M11 D4 / P2-M3）", () => {
  it("有行为面场次时给一行说明，让用户知道那几场去哪了", () => {
    expect(excludedNotes({ behavioral: 3 })).toEqual(["另有 3 场行为面，不计入技术能力档案"]);
    expect(excludedNotes({})).toEqual([]);
    expect(excludedNotes(undefined)).toEqual([]);
  });

  it("报告缺失的场次也说明，并给出去处（不静默、不留死胡同）", () => {
    const notes = excludedNotes({ no_report: 2 });

    expect(notes).toHaveLength(1);
    expect(notes[0]).toContain("2 场面试已完成");
    expect(notes[0]).toContain("仪表盘");
    expect(excludedNotes({ no_report: 0 })).toEqual([]); // 0 不说（不误报）
  });

  it("两种原因都有时逐条给，顺序稳定", () => {
    expect(excludedNotes({ behavioral: 1, no_report: 2 })).toHaveLength(2);
  });

  it("空档案区分「一场没跑」/「只跑了行为面」/「有场次但缺报告」", () => {
    expect(emptyProfileKind({ behavioral: 2 })).toBe("behavioral-only");
    expect(emptyProfileKind({ no_report: 2 })).toBe("no-report-only");
    expect(emptyProfileKind({ behavioral: 1, no_report: 2 })).toBe("behavioral-only"); // 两种都有时行为面优先
    expect(emptyProfileKind({})).toBe("none");
    expect(emptyProfileKind(undefined)).toBe("none");
  });

  it("空档案的三态文案：标题/正文/CTA 都说得清为什么是空的", () => {
    const noReport = emptyProfileCopy({ no_report: 3 });
    expect(noReport.title).toContain("报告");
    expect(noReport.paragraphs.join("")).toContain("3 场");
    expect(noReport.cta).toBe("开始新的一场");

    const behavioral = emptyProfileCopy({ behavioral: 2 });
    expect(behavioral.title).toBe("只跑过行为面");
    expect(behavioral.paragraphs.join("")).toContain("2 场行为面");
    expect(behavioral.cta).toBe("开始第一场技术面");

    const none = emptyProfileCopy(undefined);
    expect(none.title).toBe("还没有可分析的面试记录");
    expect(none.cta).toBe("开始第一场面试");
  });
});
