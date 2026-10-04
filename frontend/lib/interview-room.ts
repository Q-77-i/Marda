/**
 * 面试间舞台纯逻辑（P2-M7 面试间改版）：面试官状态徽标的派生。
 *
 * 舞台把「面试官」与「我」放成两个等大的 tile（对等感：面试官也有人像框），
 * 面试官 tile 上的状态是**现有状态推导**出来的——不引入任何新的会话状态、
 * 不新增后端字段；分支做成纯函数才进得了 vitest（P2-M3 的教训）。
 */

export type InterviewerRoomState = {
  /** 只读回放 / 报告就绪 */
  ended: boolean;
  /** TTS 正在播报 */
  speaking: boolean;
  /** 候选人正在语音作答（面试官在听） */
  recording: boolean;
  /** 本回合进行中、面试官尚无输出（首 token 前） */
  thinking: boolean;
  /** 本回合进行中（已在出字） */
  busy: boolean;
};

/**
 * 面试官状态徽标。优先级 = 终止态 > 声音态 > 聆听 > 思考 > 回应 > 空闲：
 * 「当下正在发生」的压过「回合级」的——用户在听播报时最该看到的就是"正在播报"。
 */
export function interviewerChip(state: InterviewerRoomState): string {
  if (state.ended) return "面试已结束";
  if (state.speaking) return "正在播报";
  if (state.recording) return "正在聆听";
  if (state.thinking) return "正在思考";
  if (state.busy) return "正在回应";
  return "等待你的回答";
}
