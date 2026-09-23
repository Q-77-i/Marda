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

/** 主动结束指令（与 backend rules/advance.END_COMMANDS 一致）。 */
export const END_COMMAND = "结束面试";

/**
 * 追问轮回答分段标记（与 backend graph/state.FOLLOWUP_ANSWER_MARKER 一致）。
 * 复盘卡按它把 candidate_answer 拆成「首答 / 追问补充 N」（FR-25）。
 */
export const FOLLOWUP_ANSWER_MARKER = "【追问补充】";

/** 题量可选项（SPEC §9 仪表盘表单）。 */
export const QUESTION_COUNT_OPTIONS = [5, 10, 15] as const;

/** 岗位方向（阶段 1 固定）。 */
export const POSITION = "Agent/AI 工程师";
