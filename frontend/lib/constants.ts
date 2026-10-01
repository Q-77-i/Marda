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
  project: "项目深挖",
};

/**
 * 可选知识域（上传/编辑私有题的候选值，顺序同 DOMAIN_LABELS）。
 *
 * 与 backend/app/domain.py `ENABLED_DOMAINS` 一致（= DOMAIN_WEIGHTS ∪ {algorithms}）：
 * DOMAIN_LABELS 里多出的行为面/计算机基础/项目深挖**不在**题库可选范围内，
 * 故不能直接拿它的 key 当选项（后端会按「未知知识域」400）。
 */
export const ENABLED_DOMAINS = [
  "agent-architecture",
  "planning-reasoning",
  "tool-use",
  "memory",
  "rag",
  "engineering-observability",
  "algorithms",
] as const;

/**
 * 参与出题与域统计的六大知识域（= backend DOMAIN_WEIGHTS 的键，顺序同权重表）。
 *
 * 能力档案的域曲线按它列图：algorithms 只存不考、behavioral/cs-fundamentals 是 draft，
 * 报告聚合都不会写进 domain_scores，列出来只会得到一排「尚未考过」。
 */
export const WEIGHTED_DOMAINS = ENABLED_DOMAINS.filter(
  (domain) => domain !== "algorithms",
);

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
  project: "项目深挖",
  behavioral: "行为面问答",
  closing: "反问环节",
  finished: "已结束",
};

/**
 * 题型标签（T7a：question_type 语义由后端定义，此处仅作展示映射；
 * 未知题型显示原值不猜）。tech 走编号展示，不入此表。
 */
export const QUESTION_TYPE_LABELS: Record<string, string> = {
  scenario: "项目深挖",
  behavioral: "行为面",
};

/**
 * 会话类型（P1-M11 FR-22）：与岗位 position 正交——两种类型面向同一岗位。
 * 老场次（接口无该字段/NULL）视为技术面，不显示类型徽标（见 isBehavioral）。
 */
export const INTERVIEW_TYPE_LABELS: Record<string, string> = {
  tech: "技术面",
  behavioral: "行为面",
};

export function isBehavioral(interviewType: string | null | undefined): boolean {
  return interviewType === "behavioral";
}

/** 会话类型展示名；缺失（老场次 NULL）按技术面——语义等同，不做「未知」处理。 */
export function interviewTypeLabel(interviewType: string | null | undefined): string {
  const key = interviewType ?? "tech";
  return INTERVIEW_TYPE_LABELS[key] ?? key;
}

/** 行为面域 id（与 backend domain.BEHAVIORAL_DOMAIN 一致）：行为题的 domain 取值。 */
export const BEHAVIORAL_DOMAIN = "behavioral";

/** 创建表单的题量可选值（行为面上限 10，P1-M11 D1；技术面维持 5/10/15）。 */
export function questionCountOptions(interviewType: string): number[] {
  const all = [...QUESTION_COUNT_OPTIONS];
  return isBehavioral(interviewType)
    ? all.filter((count) => count <= BEHAVIORAL_MAX_QUESTIONS)
    : all;
}

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
  deepen_limit: "本题深挖已用过",
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

/** 行为面题量上限（与 backend domain.BEHAVIORAL_MAX_QUESTIONS 一致，P1-M11 D1）：
 * 行为题库只有十来道，15 题场会当场耗尽走 LLM 兜底——表单只给 ≤10 的选项。 */
export const BEHAVIORAL_MAX_QUESTIONS = 10;

/**
 * 面试类型选项（P1-M11 FR-22）：与技术岗位正交——两种类型面向同一个岗位。
 * 行为面用同一套状态机，只换能力模型与题源（评分维度、追问口径、报告维度全不同）。
 */
export const INTERVIEW_TYPE_OPTIONS = [
  { value: "tech", label: "技术面", hint: "项目深挖 + 六大知识域技术题" },
  { value: "behavioral", label: "行为面 / HR 面", hint: "经历叙事、动机与协作，不考技术细节" },
] as const;

/** 岗位方向（阶段 1 固定）。 */
export const POSITION = "Agent/AI 工程师";

/**
 * 难度选项（P1-M6 FR-14）：adaptive = 自适应（引擎从 L1 起按连击升降）；
 * L1/L2/L3 = 全场锁定该档（后端 state.difficulty_locked）。
 */
export const DIFFICULTY_OPTIONS = [
  { value: "adaptive", label: "自适应", hint: "从 L1 起，按表现升降" },
  { value: "L1", label: "L1 基础", hint: "概念与名词解释" },
  { value: "L2", label: "L2 进阶", hint: "原理、对比与选型" },
  { value: "L3", label: "L3 深入", hint: "底层实现与设计权衡" },
] as const;

/** 难度标签（面试列表/报告用）；未知值原样显示（不猜）。 */
export function difficultyLabel(value: string): string {
  return DIFFICULTY_OPTIONS.find((o) => o.value === value)?.label ?? value;
}

/**
 * 顶层导航（P1-M6 拍板：顶栏 tab 而非侧边栏）。
 *
 * `ready: false` 的项**渲染成不可点的灰文本**（不是 `<a>`，也没有 href）——
 * 这是给后续页面的占位机制（M6 起沿用至今，P1-M10 后五项全部就绪）；
 * 灰度即路线图，但绝不给出会 404 的链接。
 */
export const NAV_ITEMS = [
  { href: "/", label: "仪表盘", ready: true },
  { href: "/bank", label: "题库", ready: true },
  { href: "/bank/private", label: "我的题库", ready: true },
  { href: "/profile", label: "能力档案", ready: true },
  { href: "/learn", label: "学习推荐", ready: true },
] as const;

