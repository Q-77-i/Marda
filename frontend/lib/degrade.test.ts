import { describe, expect, it } from "vitest";

import { degradedNotice, reportDegradedNotice } from "@/lib/degrade";

describe("degradedNotice（面试页横幅）", () => {
  it("无降级不发横幅", () => {
    expect(degradedNotice([])).toBeNull();
  });

  it("原因逐条列出并说明面试照常", () => {
    const text = degradedNotice(["评分服务不可用，未评分的题目不计入能力评估"]);
    expect(text).toContain("降级模式");
    expect(text).toContain("评分服务不可用");
    expect(text).toContain("面试照常进行");
  });
});

describe("reportDegradedNotice（报告页横幅）", () => {
  it("非降级报告不发横幅", () => {
    expect(reportDegradedNotice({})).toBeNull();
    expect(reportDegradedNotice({ degraded: false })).toBeNull();
  });

  it("整场未评分：说明分数区留空、复盘仍完整", () => {
    const text = reportDegradedNotice({
      degraded: true,
      degraded_reasons: ["评分服务不可用"],
      scores: {},
    });
    expect(text).toContain("未生成能力评分");
    expect(text).toContain("逐题复盘与参考答案仍完整");
  });

  it("部分未评分：分数照常但标注未评分条数", () => {
    const text = reportDegradedNotice({
      degraded: true,
      degraded_reasons: ["评分服务不可用"],
      scores: { technical_depth: 4 },
      answered_count: 3,
      unscored_count: 1,
    });
    expect(text).toContain("3 题中有 1 题未评分");
  });

  it("原因缺失时退回兜底文案（不显示半句）", () => {
    const text = reportDegradedNotice({ degraded: true, scores: {} });
    expect(text).toContain("AI 服务暂时不可用");
  });
});
