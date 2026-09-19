/** 展示格式化工具。 */

import type { PerQuestionComment } from "@/lib/api";

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
 * 分母永远是用户创建面试时选的题量（`question_count`），否则"我明明选的 15 题，
 * 报告上却是 16"本身就是矛盾。超出部分来自场景题：它不属于配置的题量，
 * 由阶段标签（场景题 / 反问环节）表达，不参与计数。
 */
export function completedCount(answered: number, total: number): number {
  return Math.min(answered, total);
}

/**
 * 进度文案「已答/题量」。
 *
 * `answered_count` 在 judge 节点对每道首次作答 +1（含场景题，追问重评不加），
 * 而 `question_count` 只统计技术题，因此 answered 会超出 total；封顶避免出现
 * "16/15" 或"进度满了还在提问"这类与配置矛盾的展示。
 */
export function progressLabel(answered: number, total: number): string {
  return `${completedCount(answered, total)}/${total}`;
}

/**
 * 场景题在逐题点评中的下标，没有则 -1。
 *
 * 场景题是 PROJECT 阶段额外加问的那道（`domain="project"`、无题库 id），不属于配置题量，
 * 总排在所有技术题之后。
 */
function scenarioIndex(items: PerQuestionComment[], answered: number, total: number): number {
  const byDomain = items.findIndex((item) => item.domain === "project");
  if (byDomain >= 0) return byDomain;
  // 2026-09-19 之前的报告 payload 没有 domain，只能按位置推断：超出配置题量的那条即场景题
  if (answered > total && items.length > 0) return items.length - 1;
  return -1;
}

/**
 * 逐题点评标题：场景题单列，其余按作答顺序编号。
 *
 * 若一律按数组下标编号，15 题配置的自然结束会显示成「第 16 题」——看起来像凭空多了一题，
 * 与用户选的题量矛盾。场景题由标签点明，编号只覆盖配置的题量。
 */
export function commentLabels(
  items: PerQuestionComment[],
  answered: number,
  total: number,
): string[] {
  const scenarioAt = scenarioIndex(items, answered, total);
  return items.map((_, index) => (index === scenarioAt ? "场景题" : `第 ${index + 1} 题`));
}
