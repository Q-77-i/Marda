import { describe, expect, it } from "vitest";

import { phaseSteps } from "@/lib/interview-phases";

describe("phaseSteps", () => {
  it("开场在第一步", () => {
    const result = phaseSteps("intro");
    expect(result.currentIndex).toBe(0);
    expect(result.steps.map((step) => step.label)).toEqual([
      "开场",
      "自我介绍",
      "问答",
      "反问",
      "报告",
    ]);
    expect(result.finished).toBe(false);
  });

  it("技术面的技术问答与项目深挖都落在问答段", () => {
    expect(phaseSteps("tech_base").currentIndex).toBe(2);
    expect(phaseSteps("project").currentIndex).toBe(2);
  });

  it("行为面与技术面共用同一条时间线", () => {
    expect(phaseSteps("behavioral").currentIndex).toBe(2);
  });

  it("反问与结束各就各位", () => {
    expect(phaseSteps("closing").currentIndex).toBe(3);
    const done = phaseSteps("finished");
    expect(done.currentIndex).toBe(4);
    expect(done.finished).toBe(true);
  });
});
