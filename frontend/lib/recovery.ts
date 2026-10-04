/**
 * 失败重发决策（P1-M4.7 后续）：一条发出去没收到正常收尾的回答，该重发还是只重建列表。
 *
 * 为什么不能一律重发：图停在**失败节点**上时，重发 = 把那个节点踢起来（resume 值被丢弃，
 * 判的仍是 state 里那份原始回答，服务端已由集成测试钉死）；可服务端若已经把这轮跑完、
 * 只是 SSE 在回程断了，重发就是**新开一轮**——同一份回答被判两次、计数虚高。
 * 客户端分辨不了这两态（报告节点失败也表现为「末条是 assistant」），所以判据由服务端给：
 * `GET /interviews/{id}` 的 `stalled`（见 backend/app/service.py::engine_stalled）。
 *
 * 纯函数、无网络：调用方拿到会话后调 `reconcile`，本模块不碰 IO。
 */

import type { ChatMessage, Session } from "@/lib/api";

/** 一次发出但没正常收尾的作答；baseline = 发送前本地已渲染的候选人气泡数。
 *  images（P2-M6）：该轮附带的 image_id 列表——重发复用它（图已上传，不重复落盘）。 */
export type PendingTurn = { text: string; baseline: number; images?: string[] };

export type Recovery =
  /** 再 POST 一次原文本：踢活失败节点，或补发从未送达的回答。 */
  | "resend"
  /** 服务端已经跑完了这一轮：只把漏掉的回复补进列表，绝不能再 POST。 */
  | "resync";

function userContents(history: ChatMessage[]): string[] {
  return history.filter((m) => m.role === "user").map((m) => m.content);
}

/**
 * 判定这条作答在服务端的去向。
 *
 * 「已入账」= 服务端用户消息条数不少于发送前的本地条数，**且**末条正是这条文本。
 * 两个条件缺一不可：
 * - 只看条数：正常发送时 baseline 就等于服务端的条数，`>=` 恒真 → 永远判已入账，
 *   真没送到的回答会被静默丢掉；
 * - 只看末条文本：用户连着两次发同样的话时，第一次入账的那条会让第二次误判成已入账。
 * 用 `>=` 而不是 `>` 是为了容忍本地气泡**多于**服务端的情况（「结束面试」被婉拒这类
 * 不产生用户消息的路径会在本地留一个多余气泡）——那时严格大于永远不成立。
 */
export function reconcile(pending: PendingTurn, session: Session): Recovery {
  const users = userContents(session.chat_history);
  const banked = users.length >= pending.baseline && users[users.length - 1] === pending.text;
  // 服务端没卡住 = 这一轮已经跑完（只是流断在回程），别再 POST
  return !session.stalled && banked ? "resync" : "resend";
}
