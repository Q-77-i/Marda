import { describe, expect, it } from "vitest";

import type { TraceEvent } from "@/lib/api";
import { decisionLabel, reasonLabel, traceEventLabel } from "@/lib/constants";
import {
  asFlag,
  asNumber,
  asRecord,
  asStringList,
  asText,
  coveragePercent,
  groupTraceEvents,
  judgeEvidence,
} from "@/lib/trace";

const event = (
  type: string,
  round: number | null,
  detail: Record<string, unknown> = {},
): TraceEvent => ({ type, round, detail });

describe("groupTraceEvents", () => {
  it("旧场次无 trace_log：空事件流给出空分组", () => {
    expect(groupTraceEvents([])).toEqual({ rounds: [], closing: [] });
  });

  it("同轮事件聚成一组，并保持事件原始顺序", () => {
    const events = [
      event("ask", 1, { question: "什么是 ReAct" }),
      event("judge", 1, { coverage: 0.5 }),
      event("followup", 1, { decision: "missing" }),
      event("advance", 1, { reason: "coverage_low" }),
    ];

    expect(groupTraceEvents(events).rounds).toEqual([
      { round: 1, events },
    ]);
  });

  it("同轮多次评分与追问（真实链路：追问后重评）按发生顺序保留，不去重不折叠", () => {
    const events = [
      event("ask", 1),
      event("judge", 1),
      event("followup", 1, { decision: "missing" }),
      event("judge", 1),
      event("followup", 1, { decision: "clarify" }),
      event("judge", 1),
      event("advance", 1, { reason: "total_limit" }),
    ];

    expect(groupTraceEvents(events).rounds[0].events.map((e) => e.type)).toEqual([
      "ask",
      "judge",
      "followup",
      "judge",
      "followup",
      "judge",
      "advance",
    ]);
  });

  it("轮号升序排列（输入顺序不作假设）", () => {
    const events = [event("ask", 2), event("ask", 1), event("ask", 3)];

    expect(groupTraceEvents(events).rounds.map((r) => r.round)).toEqual([1, 2, 3]);
  });

  it("round 为 null 的事件归入收尾区，不进轮次", () => {
    const report = event("report", null, { answered_count: 3 });
    const events = [event("ask", 1), report];

    const grouped = groupTraceEvents(events);

    expect(grouped.rounds.map((r) => r.round)).toEqual([1]);
    expect(grouped.closing).toEqual([report]);
  });

  it("收尾区多条事件保持原始顺序", () => {
    const first = event("report", null, { answered_count: 1 });
    const second = event("unknown_future_type", null);

    expect(groupTraceEvents([first, second]).closing).toEqual([first, second]);
  });

  it("被挽留事件（round = 当前题轮号）落进所属轮次", () => {
    const refused = event("end_refused", 3, { answered_count: 2, threshold: 6 });

    expect(groupTraceEvents([refused]).rounds[0]).toEqual({
      round: 3,
      events: [refused],
    });
  });
});

describe("traceEventLabel", () => {
  it("六种已知事件类型有中文标签", () => {
    expect(traceEventLabel("ask")).toBe("出题");
    expect(traceEventLabel("judge")).toBe("评分");
    expect(traceEventLabel("followup")).toBe("追问");
    expect(traceEventLabel("advance")).toBe("换题");
    expect(traceEventLabel("end_refused")).toBe("结束被挽留");
    expect(traceEventLabel("report")).toBe("报告生成");
  });

  it("未知类型显示原值，不猜", () => {
    expect(traceEventLabel("something_new")).toBe("something_new");
  });
});

describe("decisionLabel", () => {
  it("四种追问决策有中文标签", () => {
    expect(decisionLabel("clarify")).toBe("澄清追问");
    expect(decisionLabel("missing")).toBe("追问遗漏");
    expect(decisionLabel("deepen")).toBe("深挖追问");
    expect(decisionLabel("next")).toBe("换题");
  });

  it("未知决策显示原值", () => {
    expect(decisionLabel("escalate")).toBe("escalate");
  });
});

describe("reasonLabel", () => {
  it("九种决策原因有中文标签（与 backend rules/follow_up.Reason 同键）", () => {
    expect(reasonLabel("error_flag")).toBe("回答有明确错误");
    expect(reasonLabel("coverage_low")).toBe("关键点覆盖不足");
    expect(reasonLabel("deepen_ok")).toBe("覆盖达标，深挖边界");
    expect(reasonLabel("total_limit")).toBe("单题追问已达上限");
    expect(reasonLabel("remedy_limit")).toBe("全场补救额度用尽");
    expect(reasonLabel("clarify_limit")).toBe("澄清追问机会已用完");
    expect(reasonLabel("missing_limit")).toBe("遗漏追问已达上限");
    expect(reasonLabel("missing_asked")).toBe("遗漏点均已追问");
    expect(reasonLabel("coverage_ok")).toBe("覆盖率达标");
  });

  it("未知原因显示原值", () => {
    expect(reasonLabel("new_reason")).toBe("new_reason");
  });
});

describe("coveragePercent", () => {
  it("0-1 小数转百分比整数", () => {
    expect(coveragePercent(0.6667)).toBe(67);
    expect(coveragePercent(0.7)).toBe(70);
    expect(coveragePercent(1)).toBe(100);
    expect(coveragePercent(0)).toBe(0);
  });

  it("非数值（历史事件缺字段）返回 null", () => {
    expect(coveragePercent(undefined)).toBeNull();
    expect(coveragePercent(null)).toBeNull();
    expect(coveragePercent("0.5")).toBeNull();
  });
});

describe("detail 取值助手", () => {
  it("asText：空串与空白视为缺失，非字符串原样拒绝", () => {
    expect(asText("题干")).toBe("题干");
    expect(asText("")).toBeNull();
    expect(asText("   ")).toBeNull();
    expect(asText(7)).toBeNull();
    expect(asText(undefined)).toBeNull();
  });

  it("asNumber：只认有限数", () => {
    expect(asNumber(3)).toBe(3);
    expect(asNumber(0.25)).toBe(0.25);
    expect(asNumber(Number.NaN)).toBeNull();
    expect(asNumber("3")).toBeNull();
    expect(asNumber(null)).toBeNull();
  });

  it("asFlag：只有真 true 才算真（缺字段不当成 true）", () => {
    expect(asFlag(true)).toBe(true);
    expect(asFlag(false)).toBe(false);
    expect(asFlag(undefined)).toBe(false);
    expect(asFlag("true")).toBe(false);
  });

  it("asRecord：只认普通对象", () => {
    expect(asRecord({ technical_depth: 4 })).toEqual({ technical_depth: 4 });
    expect(asRecord(null)).toBeNull();
    expect(asRecord([1, 2])).toBeNull();
    expect(asRecord("x")).toBeNull();
  });

  it("asStringList：剔除非字符串项，非数组给空表", () => {
    expect(asStringList(["rag", "memory"])).toEqual(["rag", "memory"]);
    expect(asStringList(["rag", 3, null])).toEqual(["rag"]);
    expect(asStringList(undefined)).toEqual([]);
  });
});

describe("judgeEvidence", () => {
  it("从评分明细取出漏掉的关键点与评分官点评", () => {
    expect(
      judgeEvidence({
        missed_key_points: ["缓存失效策略", "评估方式"],
        comment: "整体到位，注意索引失效场景。",
      }),
    ).toEqual({
      missedKeyPoints: ["缓存失效策略", "评估方式"],
      comment: "整体到位，注意索引失效场景。",
    });
  });

  it("历史事件缺字段容缺（空表 / null），不显示 undefined", () => {
    expect(judgeEvidence({})).toEqual({ missedKeyPoints: [], comment: null });
    expect(judgeEvidence(null)).toEqual({ missedKeyPoints: [], comment: null });
    expect(judgeEvidence({ missed_key_points: ["x", 3], comment: "  " })).toEqual({
      missedKeyPoints: ["x"],
      comment: null,
    });
  });
});
