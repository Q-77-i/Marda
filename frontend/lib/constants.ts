/**
 * 展示用文案映射。domain 标签与 backend/app/domain.py DOMAIN_LABELS 保持一致
 * （单一来源在后端，前端此处为展示副本）。
 */

import type { Phase } from "@/lib/api";

export const DOMAIN_LABELS: Record<string, string> = {
  "agent-architecture": "Agent 认知与架构",
  "planning-reasoning": "规划与推理范式",
  "tool-use": "Tool 与 Function Calling",
  memory: "Memory",
  rag: "RAG",
  "engineering-observability": "工程化与可观测",
  algorithms: "手撕算法",
  behavioral: "行为与项目面",
  "cs-fundamentals": "计算机基础",
  project: "项目场景题",
};

/** 五维评分维度（与 backend aggregate.FIVE_DIMS 同序）。 */
export const DIMENSIONS: { key: string; label: string }[] = [
  { key: "technical_depth", label: "技术深度" },
  { key: "fundamentals", label: "基础掌握" },
  { key: "project_experience", label: "项目经验" },
  { key: "communication", label: "沟通表达" },
  { key: "problem_solving", label: "问题解决" },
];

export const PHASE_LABELS: Record<Phase, string> = {
  intro: "开场",
  warmup: "自我介绍",
  tech_base: "技术问答",
  project: "场景题",
  closing: "反问环节",
  finished: "已结束",
};

/**
 * 题型标签（T7a：question_type 语义由后端定义，此处仅作展示映射；
 * 未知题型显示原值不猜）。tech 走编号展示，不入此表。
 */
export const QUESTION_TYPE_LABELS: Record<string, string> = {
  scenario: "场景题",
};

export function domainLabel(domain: string): string {
  return DOMAIN_LABELS[domain] ?? domain;
}

/**
 * 阶段名；未知阶段给 null。
 *
 * 与 domainLabel 的差别：阶段名会被拼进「进入 X」这类文案里，未知阶段显示原值
 * 会得到半截句子，故由调用方决定不渲染。
 */
export function phaseName(phase: string): string | null {
  return (PHASE_LABELS as Record<string, string>)[phase] ?? null;
}

/** 主动结束指令（与 backend rules/advance.END_COMMANDS 一致）。 */
export const END_COMMAND = "结束面试";

/**
 * 追问轮回答分段标记（与 backend graph/state.FOLLOWUP_ANSWER_MARKER 一致）。
 * 复盘卡按它把 candidate_answer 拆成「首答 / 追问补充 N」（FR-25）。
 */
export const FOLLOWUP_ANSWER_MARKER = "【追问补充】";

/**
 * 决策回放文案（P1-M4 / SPEC §4.7）：事件类型、追问决策、决策原因三类
 * 均由后端定义（backend graph/state.TraceEvent、rules/follow_up），此处仅作展示映射。
 * 原因标签不含阈值数字（上限/覆盖率阈值只在后端 rules 里），避免两处各写一份而漂移。
 */
export const TRACE_EVENT_LABELS: Record<string, string> = {
  ask: "出题",
  judge: "评分",
  followup: "追问",
  advance: "换题",
  end_refused: "结束被挽留",
  report: "报告生成",
};

export const DECISION_LABELS: Record<string, string> = {
  clarify: "澄清追问",
  missing: "追问遗漏",
  deepen: "深挖追问",
  next: "换题",
};

export const REASON_LABELS: Record<string, string> = {
  error_flag: "回答有明确错误",
  coverage_low: "关键点覆盖不足",
  deepen_ok: "覆盖达标，深挖边界",
  total_limit: "单题追问已达上限",
  remedy_limit: "全场补救额度用尽",
  clarify_limit: "澄清追问机会已用完",
  missing_limit: "遗漏追问已达上限",
  missing_asked: "遗漏点均已追问",
  coverage_ok: "覆盖率达标",
};

export function traceEventLabel(type: string): string {
  return TRACE_EVENT_LABELS[type] ?? type;
}

export function decisionLabel(decision: string): string {
  return DECISION_LABELS[decision] ?? decision;
}

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason;
}

/** 题量可选项（SPEC §9 仪表盘表单）。 */
export const QUESTION_COUNT_OPTIONS = [5, 10, 15] as const;

/** 岗位方向（阶段 1 固定）。 */
export const POSITION = "Agent/AI 工程师";
