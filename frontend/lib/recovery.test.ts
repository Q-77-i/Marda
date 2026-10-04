import { describe, expect, it } from "vitest";

import type { ChatMessage, Session } from "@/lib/api";
import { reconcile, type PendingTurn } from "@/lib/recovery";

/** 只造 reconcile 用得到的字段；其余按会话接口的默认值填。 */
function session(chat_history: ChatMessage[], stalled: boolean): Session {
  return {
    interview_id: "i1",
    position: "Agent/AI 工程师",
    phase: "project",
    status: "running",
    answered_count: 1,
    question_count: 5,
    chat_history,
    report_ready: false,
    stalled,
  };
}

const 题目 = (text = "请设计一个带工具调用的 Agent 系统"): ChatMessage => ({
  role: "assistant",
  content: text,
});
const 我说 = (content: string): ChatMessage => ({ role: "user", content });

/** 发送前本地已渲染 1 条候选人气泡（自我介绍那条），这条是第 2 条。 */
const pending: PendingTurn = { text: "我的回答……", baseline: 1 };
const 自我介绍后 = [题目("先做个自我介绍吧"), 我说("自我介绍"), 题目()];

describe("reconcile", () => {
  it("节点失败卡住 → 重发（重发把失败节点踢起来，原始回答已在 state 里）", () => {
    const s = session([...自我介绍后, 我说(pending.text)], true);
    expect(reconcile(pending, s)).toBe("resend");
  });

  it("评分节点就失败了、回答没进账 → 重发（这条真没送到服务端）", () => {
    // 图卡在 judge 上：chat_history 里只有上一轮的用户消息，这条还没入账
    const s = session([...自我介绍后], true);
    expect(reconcile(pending, s)).toBe("resend");
  });

  it("服务端已跑完这一轮、只是流断在回程 → 只重建列表（再 POST 会被当成新一轮）", () => {
    const s = session([...自我介绍后, 我说(pending.text), 题目("追问：那容错呢？")], false);
    expect(reconcile(pending, s)).toBe("resync");
  });

  it("网络断在发送途中的健康场次 → 重发（服务端末条用户消息还是上一条）", () => {
    expect(reconcile(pending, session([...自我介绍后], false))).toBe("resend");
  });

  it("本地气泡多于服务端（「结束面试」被婉拒这类不留用户消息的路径）也判得准", () => {
    // 婉拒不写用户消息，只在本地留个多余气泡 → 发送时 baseline 比服务端入账数大 1，
    // 这条入账后服务端条数刚好追平 baseline：等号也必须算「已入账」
    const banked = session([...自我介绍后, 我说(pending.text), 题目("追问：那容错呢？")], false);
    expect(reconcile({ ...pending, baseline: 2 }, banked)).toBe("resync");
    // 同一次漂移，但服务端末条不是这条 → 真没送到
    expect(reconcile({ ...pending, baseline: 2 }, session([...自我介绍后], false))).toBe("resend");
  });
});

describe("reconcile · 图片通道（P2-M6）", () => {
  it("带图的作答同样按文本对账（image_ids 不影响判据）", () => {
    const 带图 = { role: "user" as const, content: "见截图", image_ids: ["a".repeat(32)] };
    const turn: PendingTurn = { text: "见截图", baseline: 2, images: ["a".repeat(32)] };
    const s = session([...自我介绍后, 带图], false);
    expect(reconcile(turn, s)).toBe("resync"); // 已入账且没卡住 → 只重建列表，绝不重发
  });
});
