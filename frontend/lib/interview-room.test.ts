import { describe, expect, it } from "vitest";

import { interviewerChip } from "@/lib/interview-room";

const base = { ended: false, speaking: false, recording: false, thinking: false, busy: false };

describe("面试官状态徽标（面试间舞台）", () => {
  it("空闲 → 等待你的回答", () => {
    expect(interviewerChip(base)).toBe("等待你的回答");
  });

  it("回合进行中、尚无输出（首 token 前）→ 正在思考", () => {
    expect(interviewerChip({ ...base, busy: true, thinking: true })).toBe("正在思考");
  });

  it("回合进行中、已在出字 → 正在回应", () => {
    expect(interviewerChip({ ...base, busy: true })).toBe("正在回应");
  });

  it("候选人在语音作答 → 正在聆听", () => {
    expect(interviewerChip({ ...base, recording: true })).toBe("正在聆听");
  });

  it("TTS 播报中 → 正在播报", () => {
    expect(interviewerChip({ ...base, speaking: true })).toBe("正在播报");
  });

  it("已结束 → 面试已结束（最高优先级，压过其它任何瞬时态）", () => {
    expect(interviewerChip({ ended: true, speaking: true, recording: true, thinking: true, busy: true })).toBe("面试已结束");
  });

  it("优先级：播报 > 聆听 > 思考 > 回应（互斥场景下取更「当下」的那个）", () => {
    expect(interviewerChip({ ...base, speaking: true, thinking: true, busy: true })).toBe("正在播报");
    expect(interviewerChip({ ...base, recording: true, busy: true })).toBe("正在聆听");
    expect(interviewerChip({ ...base, thinking: true, busy: true })).toBe("正在思考");
  });
});
