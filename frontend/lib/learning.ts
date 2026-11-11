/**
 * 学习计划展示逻辑（P5-J3 / J3-D）：类型与纯函数。
 *
 * 纯函数（vitest 主战场）：报告 → report_summary 的映射、进度、状态文案、截止日文案
 * 都在这里定，页面只消费。网络请求在 `lib/learning-api.ts`，避免纯函数测试拖进 fetch。
 */

import type { ReportPayload } from "@/lib/api";
import { isBehavioral } from "@/lib/constants";

export type TaskStatus = "todo" | "in_progress" | "done" | "skipped";
export type PlanStatus = "active" | "completed" | "archived";

export type LearningTask = {
  id: string;
  plan_id: string;
  domain: string;
  title: string;
  resource_url: string | null;
  status: TaskStatus;
  due_at: string | null;
  priority: number;
  version: number;
};

export type LearningPlan = {
  id: string;
  user_id: string;
  source_interview_id: string;
  goal: string;
  status: PlanStatus;
  version: number;
  created_at: string;
  updated_at: string;
  tasks: LearningTask[];
};

export type PaginatedPlans = {
  items: LearningPlan[];
  total: number;
  page: number;
  page_size: number;
};

export type TodayTasks = {
  date: string;
  tasks: LearningTask[];
};

/** 与事件 schema 的 `report_summary` 同构；不含题干 / 答案 / 关键点原文。 */
export type ReportSummaryInput = {
  overall: number;
  weak_domains: { domain: string; score: number; advice?: string | null }[];
};

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  todo: "待开始",
  in_progress: "进行中",
  done: "已完成",
  skipped: "已跳过",
};

export const PLAN_STATUS_LABELS: Record<PlanStatus, string> = {
  active: "进行中",
  completed: "已完成",
  archived: "已归档",
};

export type TaskStatusTone = "outline" | "default" | "success" | "secondary";

export function taskStatusLabel(status: TaskStatus): string {
  return TASK_STATUS_LABELS[status] ?? status;
}

export function taskStatusTone(status: TaskStatus): TaskStatusTone {
  switch (status) {
    case "in_progress":
      return "default";
    case "done":
      return "success";
    case "skipped":
      return "secondary";
    default:
      return "outline";
  }
}

export function planStatusLabel(status: PlanStatus): string {
  return PLAN_STATUS_LABELS[status] ?? status;
}

/**
 * 任务「查看资料」的落点：计划来自哪场报告，就回到哪场的推荐组。
 *
 * 已在当前场次时只改 hash（不重载）；不同场次带上 `interview` 让学习页切换来源。
 * RAG 检索仍由 Python 的 `/recommendations` 负责，Java 只存计划指针。
 */
export function recommendationHref(
  sourceInterviewId: string,
  selectedInterviewId: string | null,
): string {
  if (selectedInterviewId === sourceInterviewId) {
    return "#recommendations";
  }
  return `/learn?interview=${encodeURIComponent(sourceInterviewId)}#recommendations`;
}

/**
 * 任务主操作：todo → in_progress → done；终态没有下一步。
 * 跳过是单独动作，不走这里。
 */
export function nextTaskStatus(status: TaskStatus): TaskStatus | null {
  if (status === "todo") return "in_progress";
  if (status === "in_progress") return "done";
  return null;
}

export type PlanProgress = { handled: number; total: number; percent: number };

/** 进度按「已处理」算：done 与 skipped 都算推进，和计划自动完成的口径一致。 */
export function planProgress(plan: LearningPlan): PlanProgress {
  const total = plan.tasks.length;
  const handled = plan.tasks.filter(
    (task) => task.status === "done" || task.status === "skipped",
  ).length;
  return {
    handled,
    total,
    percent: total === 0 ? 0 : Math.round((handled / total) * 100),
  };
}

/**
 * 报告 payload → 建计划请求的 report_summary。
 *
 * - 行为面没有可学习的技术域 → null（页面不展示入口）；
 * - 短板域缺 `domain_scores` 分数 → 跳过该域，**不按 0 分猜**；
 * - 最多 5 个域（与事件 schema 的 maxItems 一致）；
 * - `overall` 缺省时按各评分维等权均值兜底（与报告页同一口径）。
 */
export function buildReportSummary(report: ReportPayload): ReportSummaryInput | null {
  if (isBehavioral(report.interview_type)) {
    return null;
  }
  const scores = report.domain_scores ?? {};
  const advice = new Map(
    (report.study_advice ?? []).map((item) => [item.domain, item.advice]),
  );
  const weakDomains: ReportSummaryInput["weak_domains"] = [];
  for (const domain of report.weaknesses ?? []) {
    const score = scores[domain];
    if (typeof score !== "number" || !Number.isFinite(score)) {
      continue;
    }
    weakDomains.push({ domain, score, advice: advice.get(domain) ?? null });
    if (weakDomains.length === 5) {
      break;
    }
  }
  if (weakDomains.length === 0) {
    return null;
  }

  const scoreValues = Object.values(report.scores ?? {}).filter((value) =>
    Number.isFinite(value),
  );
  const overall =
    typeof report.overall === "number" && Number.isFinite(report.overall)
      ? report.overall
      : scoreValues.length === 0
        ? 0
        : scoreValues.reduce((sum, value) => sum + value, 0) / scoreValues.length;

  return {
    overall: Math.round(overall * 100) / 100,
    weak_domains: weakDomains,
  };
}

/**
 * 截止日文案：今天 / 明天 / 已逾期 / MM-DD；无截止日单独说。
 *
 * `now` 可注入（vitest 固定时钟）；比较按本地自然日，避免跨时区把「今天」算成「明天」。
 */
export function dueLabel(dueAt: string | null, now: Date = new Date()): string {
  if (!dueAt) return "无截止日";
  const due = new Date(dueAt);
  if (Number.isNaN(due.getTime())) return "";
  const startOfDay = (date: Date) =>
    new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const dueDay = startOfDay(due);
  const today = startOfDay(now);
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  if (dueDay < today) return "已逾期";
  if (dueDay === today) return "今天";
  if (dueDay === startOfDay(tomorrow)) return "明天";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(due.getMonth() + 1)}-${pad(due.getDate())}`;
}
