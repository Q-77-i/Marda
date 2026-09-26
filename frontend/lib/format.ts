/** 展示格式化工具。 */

import type { PerQuestionComment } from "@/lib/api";
import { FOLLOWUP_ANSWER_MARKER, QUESTION_TYPE_LABELS } from "@/lib/constants";

/** 后端时间为 UTC ISO 串，转为本地 "MM-DD HH:mm"。 */
export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 面试时长（分钟）：起止时间都在时给出。 */
export function formatDuration(startedAt: string, endedAt: string | null): string | null {
  if (!endedAt) return null;
  const start = new Date(startedAt).getTime();
  const end = new Date(endedAt).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return `${Math.max(1, Math.round((end - start) / 60000))} 分钟`;
}

/** 评分（1-5）保留一位小数展示。 */
export function formatScore(value: number): string {
  return value.toFixed(1);
}

/**
 * 完成题量（分子，封顶到配置题量）。
 *
 * 分母永远是用户创建面试时选的题量（`question_count`）。项目深挖题计入配置题量
 * （P1-M4.6-C），answered 不会超出；封顶保留兼容历史 payload（旧场次场景题为额外加问）。
 */
export function completedCount(answered: number, total: number): number {
  return Math.min(answered, total);
}

/**
 * 进度文案「已答/题量」。
 *
 * `answered_count` 在 judge 节点对每道首次作答 +1（追问重评不加），
 * 项目深挖题计入配置题量（P1-M4.6-C），answered 不会超出 total；封顶避免
 * 历史 payload 出现 "16/15" 这类与配置矛盾的展示。
 */
export function progressLabel(answered: number, total: number): string {
  return `${completedCount(answered, total)}/${total}`;
}

/**
 * 逐题点评标题（T7a/T7a-R1：题型语义由后端定义，前端零推断）。
 *
 * 新 payload 每条带 `number`（计入问答轮次的题型按作答顺序编号，含项目深挖题）与
 * `question_type`：有 number 的按「第 N 题」，非技术题型追加题型标签
 * （如「第 3 题 · 项目深挖」）；无 number 只有 type 的按标签（未知题型显示原值，不猜）。
 * 2026-09-19 之前的报告 payload 无新字段，保留 domain/位置推断兜底。
 */
export function commentLabels(
  items: PerQuestionComment[],
  answered: number,
  total: number,
): string[] {
  return items.map((item, index) => {
    if (typeof item.number === "number") {
      const label = item.question_type && item.question_type !== "tech"
        ? ` · ${QUESTION_TYPE_LABELS[item.question_type] ?? item.question_type}`
        : "";
      return `第 ${item.number} 题${label}`;
    }
    if (item.question_type) {
      return QUESTION_TYPE_LABELS[item.question_type] ?? item.question_type;
    }
    // 旧 payload 兜底：项目深挖题（domain="project"）单列，不参与编号
    if (item.domain === "project") return "项目深挖";
    if (answered > total && index === items.length - 1) return "项目深挖";
    return `第 ${index + 1} 题`;
  });
}

/** 复盘卡的一段回答（FR-25）：首答 / 追问补充 N。 */
export type AnswerSegment = { label: string; text: string };

/**
 * 拆分「我的回答」为多段（FR-25 复盘卡）。
 *
 * 追问轮回答在数据层是一个拼接串（后端 graph/state.merge_answer：
 * 首答 + 每轮追问以 FOLLOWUP_ANSWER_MARKER 追加）。整段渲染会让用户分不清
 * 哪段是首答、哪段是补充，故按标记切段并标序号；无标记（未追问、历史数据）即整段「首答」。
 */
export function splitAnswerSegments(answer: string | null | undefined): AnswerSegment[] {
  const text = (answer ?? "").trim();
  if (!text) return [];
  return text
    .split(FOLLOWUP_ANSWER_MARKER)
    .map((part, index) => ({
      label: index === 0 ? "首答" : `追问补充 ${index}`,
      text: part.trim(),
    }))
    .filter((segment) => segment.text !== "");
}
