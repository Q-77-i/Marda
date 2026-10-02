/**
 * 后端 API 封装（SPEC §7 契约）：五端点 + SSE 事件分发。
 *
 * 请求走同源 /api/*（next.config.ts rewrites → 后端），避免 CORS；
 * 登录态与 401 处置见 lib/http.ts、lib/session.ts（本文件不重复处理）。
 */

import { authorizedFetch, responseError } from "@/lib/http";
import { postSSE, type SSEEvent } from "@/lib/sse";

export type Phase =
  | "intro"
  | "warmup"
  | "tech_base"
  | "project"
  | "behavioral"
  | "closing"
  | "finished";

export type MetaEvent = {
  interview_id?: string;
  phase: Phase;
  answered_count: number;
  question_count: number;
};
/**
 * 面试官消息的**终稿全文**（P2-M4）：语义未变（旧后端/非流式消息仍只有它），
 * 流式消息在它之前另有 delta_start + delta_chunk，前端用终稿替换累积文本做对账。
 */
export type DeltaEvent = { text: string };
/** 一条面试官消息开始（P2-M4）：data 为空对象，仅作消息边界。 */
export type DeltaStartEvent = Record<string, never>;
/** 流式增量分片（P2-M4）：追加到最近一条未结算的消息上。 */
export type DeltaChunkEvent = { text: string };
export type QuestionEvent = {
  index: number;
  question_id: string;
  domain: string;
  difficulty: string;
};
export type DoneEvent = { interview_id: string; report_ready: boolean };
export type ErrorEvent = { code: string; message: string; retryable?: boolean };

export type ChatMessage = { role: "user" | "assistant"; content: string };

export type Session = {
  interview_id: string;
  position: string;
  phase: Phase;
  status: "running" | "finished";
  answered_count: number;
  question_count: number;
  chat_history: ChatMessage[];
  report_ready: boolean;
  /** 引擎是否卡在失败节点上（重试判据，见 lib/recovery.ts；后端 service.engine_stalled）。 */
  stalled: boolean;
};

export type InterviewRow = {
  id: string;
  position: string;
  /** 会话类型（P1-M11）：tech / behavioral；老场次为 null（按技术面处理，不显示徽标） */
  interview_type?: string | null;
  question_count: number;
  phase: Phase;
  difficulty: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  report_ready: boolean;
};

/**
 * 五维得分（FR-25 复盘卡逐题展示）。
 *
 * P1-M11 起**不锁死键**：技术面与行为面各一套五维，维度表由报告 payload 的 `dims` 给
 * （单一来源在后端 aggregate）；前端只按 dims 取键，不硬编。
 */
export type ScoreDimensions = Record<string, number>;

/**
 * 逐题点评 / 复盘条目。
 *
 * 2026-09-19 起后端补上 `index`/`domain`/`text`（场景题 `domain="project"`、`question_id=null`）；
 * 2026-09-21（T7a）再补 `question_type`/`number`——题型语义由后端定义，前端只消费：
 * `number` 为计入配置题量的题型按作答顺序的编号（场景题为 null），`question_type`
 * 为题型种类（tech/scenario）。历史报告的 payload 没有这些字段，前端按 domain/位置兜底。
 *
 * 2026-09-23（FR-25）复盘扩展：`candidate_answer`（含追问轮，按 `【追问补充】` 分段）、
 * `score`（五维）、`covered_key_points`/`missed_key_points`、`reference_answer`
 * （题库题参考答案全文，场景题为 null 不渲染）。历史 payload 同样没有，全部可选。
 */
export type PerQuestionComment = {
  question_id: string | null;
  comment: string;
  index?: number;
  domain?: string;
  text?: string;
  number?: number | null;
  question_type?: string;
  candidate_answer?: string | null;
  score?: ScoreDimensions | null;
  covered_key_points?: string[];
  missed_key_points?: string[];
  reference_answer?: string | null;
};
export type StudyAdvice = { domain: string; advice: string };

/** 评分维度表条目（P1-M11）：技术面/行为面各一套，标签单一来源在后端（报告与回放随响应下发）。 */
export type Dim = { key: string; label: string };

export type ReportPayload = {
  interview_id: string;
  position: string;
  /** 会话类型（P1-M11）：tech / behavioral；老报告无该字段（按技术面处理） */
  interview_type?: string;
  /**
   * 评分维度表 `[{key, label}]`（P1-M11，顺序即展示顺序）：技术面五维 / 行为面五维由后端
   * 决定，前端按它渲染雷达与逐题得分（**不再硬编维度表**——行为面的中文标签否则要复制两份）。
   * 老报告无该字段 → 退回技术面常量 DIMENSIONS（见 report-client 的 dimsOf）。
   */
  dims?: Dim[];
  scores: Record<string, number>;
  /** 总分（五维等权均值，后端单一来源，P1-M10 D1）。FR-19 之前的 payload 没有此字段 → 前端兜底现算。 */
  overall?: number;
  domain_scores: Record<string, number>;
  weaknesses: string[];
  answered_count: number;
  question_count: number;
  total_comment: string;
  per_question_comments: PerQuestionComment[];
  study_advice: StudyAdvice[];
};

export type ReportResponse = {
  interview_id: string;
  report: ReportPayload;
  created_at: string;
};

/**
 * 决策回放事件（FR-21 / SPEC §4.7）。
 *
 * `detail` 的字段随 `type` 而变（见 SPEC §4.7 事件表），后端已保证是纯标量；
 * 前端按类型取用、缺字段即跳过，未知类型原样展示不猜（同 question_type 口径）。
 */
export type TraceEvent = {
  type: string;
  round: number | null;
  detail: Record<string, unknown>;
};

export type TraceResponse = {
  interview_id: string;
  position: string;
  /**
   * 评分维度表 `[{key, label}]`（P1-M11）：judge 事件的 detail 是评分模型裸 dump，
   * 键随会话类型变（技术面/行为面五维），标签由后端给。老场次无该字段 → 退回技术面常量。
   */
  dims?: Dim[];
  status: "running" | "finished";
  answered_count: number;
  question_count: number;
  events: TraceEvent[];
};

export type SSEHandlers = {
  meta?: (event: MetaEvent) => void;
  /** 消息边界（P2-M4）：先于该消息的 delta_chunk 到达 */
  delta_start?: (event: DeltaStartEvent) => void;
  /** 流式增量（P2-M4）：真 token 流的每一片 */
  delta_chunk?: (event: DeltaChunkEvent) => void;
  delta?: (event: DeltaEvent) => void;
  question?: (event: QuestionEvent) => void;
  done?: (event: DoneEvent) => void;
  error?: (event: ErrorEvent) => void;
};

/**
 * SSE 原始事件 → 按 event 名分发 + JSON 解析（数据格式与后端 §7 事件表一致）。
 *
 * **未注册的事件名静默丢弃**——这正是旧前端兼容新后端的方式：后端加 delta_start /
 * delta_chunk（P2-M4）或 M5 的语音事件（asr_partial / tts_chunk，协议里已登记）时，
 * 没有对应 handler 的客户端不受影响，只是看不到那一路数据。别在这里抛错。
 */
export function dispatcher(handlers: SSEHandlers): (event: SSEEvent) => void {
  return (event) => {
    const handler = handlers[event.event as keyof SSEHandlers];
    if (!handler) return;
    try {
      handler(JSON.parse(event.data));
    } catch {
      // 单条事件解析失败不应中断整个流
    }
  };
}

/** 创建面试并跑完开场流；解析首事件 meta 得到 interview_id（决策 1A）。 */
export async function createInterview(
  position: string,
  questionCount: number,
  difficulty: string,
  onEvent: (event: SSEEvent) => void,
  interviewType: string = "tech",
): Promise<string> {
  let interviewId = "";
  await postSSE(
    "/api/interviews",
    { position, question_count: questionCount, difficulty, interview_type: interviewType },
    (event) => {
      if (!interviewId && event.event === "meta") {
        interviewId = JSON.parse(event.data).interview_id ?? "";
      }
      onEvent(event);
    },
  );
  if (!interviewId) throw new Error("创建面试失败：未收到会话 ID");
  return interviewId;
}

/** 发送候选人消息，流式返回面试官后续（含追问/下一题/报告触发）。 */
export async function sendMessage(
  interviewId: string,
  content: string,
  onEvent: (event: SSEEvent) => void,
): Promise<void> {
  await postSSE(
    `/api/interviews/${interviewId}/messages`,
    { content },
    onEvent,
  );
}

async function getJSON<T>(url: string): Promise<T> {
  const response = await authorizedFetch(url);
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<T>;
}

/** 会话恢复数据；reconnect=true 时后端在响应里附一句重连问候（重发当前题干，P1-M4.7-D）。 */
export function getSession(
  interviewId: string,
  { reconnect = false }: { reconnect?: boolean } = {},
): Promise<Session> {
  const query = reconnect ? "?reconnect=true" : "";
  return getJSON<Session>(`/api/interviews/${interviewId}${query}`);
}

export function getReport(interviewId: string): Promise<ReportResponse> {
  return getJSON<ReportResponse>(`/api/interviews/${interviewId}/report`);
}

// ---- 学习推荐（FR-20，P1-M9）----

/**
 * 推荐资料卡（一张题库题卡）：短板域里检索到的新题，附答案全文与来源明细。
 *
 * 本场已问过的题不会出现在这里（复盘卡已给过它们的参考答案），故卡片一定"没见过"。
 */
export type RecommendCard = {
  question_id: string;
  question: string;
  answer: string;
  key_points: string[];
  domain: string;
  topic: string;
  difficulty: string;
  company: string | null;
  round: string | null;
  /** 来源明细，主源首位（同题库页） */
  sources: BankSource[];
};

/**
 * 一个短板域的分组。`status` 由后端判定（空分组不静默隐藏，前端按它给文案）：
 * `ok` 有卡片；`exhausted` 该域题目已全部练过；`empty` 该域题库暂无题。
 */
export type RecommendationGroup = {
  domain: string;
  /** 报告里的学习建议（同一批 LLM 文案，不重复调用）；缺省为 null */
  advice: string | null;
  status: "ok" | "exhausted" | "empty";
  cards: RecommendCard[];
};

export type RecommendationsResponse = {
  interview_id: string;
  position: string;
  groups: RecommendationGroup[];
};

/** 学习推荐（FR-20）：读该场报告的短板域现检索，未结束/无报告 404。 */
export function getRecommendations(interviewId: string): Promise<RecommendationsResponse> {
  return getJSON<RecommendationsResponse>(`/api/interviews/${interviewId}/recommendations`);
}

// ---- 能力档案（FR-19，P1-M10）----

/** 档案里的一场：报告分数 + 场次元信息（曲线上的一个点）。 */
export type ProfileSession = {
  interview_id: string;
  position: string;
  /** 创建时选的难度档："adaptive" 或 L1/L2/L3（不是自适应过程中的中间档） */
  difficulty: string;
  question_count: number;
  answered_count: number;
  started_at: string;
  overall: number;
  scores: Record<string, number>;
  /** 该场考过的域才有键——没考的域缺失（曲线断点，不补零，P1-M10 D2） */
  domain_scores: Record<string, number>;
  weaknesses: string[];
};

/** 相邻两场的短板走向（首场不产出条目）。 */
export type WeaknessChange = {
  interview_id: string;
  started_at: string;
  new: string[];
  persistent: string[];
  resolved: string[];
};

export type ProfileSummary = {
  session_count: number;
  average_overall: number;
  best: { interview_id: string; overall: number } | null;
  worst: { interview_id: string; overall: number } | null;
  latest_delta: { from: number; to: number; delta: number } | null;
};

export type ProfileResponse = {
  sessions: ProfileSession[];
  summary: ProfileSummary;
  weakness_changes: WeaknessChange[];
  /** 未计入档案的场次计数（P1-M11 {"behavioral": N} / P2-M3 {"no_report": N}），空态/混排时用来说明原因 */
  excluded?: Record<string, number>;
};

/** 能力档案（FR-19）：登录用户级，无场次参数；没有场次返回零态结构而非 404。 */
export function getProfile(): Promise<ProfileResponse> {
  return getJSON<ProfileResponse>("/api/profile");
}

/** 报告导出 PDF（FR-18）：二进制响应，不能走 getJSON（它按 JSON 解析）。 */
export async function exportReportPdf(interviewId: string): Promise<Blob> {
  const response = await authorizedFetch(`/api/interviews/${interviewId}/report.pdf`);
  if (!response.ok) throw new Error(await responseError(response));
  return response.blob();
}

/** 决策回放（FR-21）：未结束的场次同样可查；旧场次 events 为空表。 */
export function getTrace(interviewId: string): Promise<TraceResponse> {
  return getJSON<TraceResponse>(`/api/interviews/${interviewId}/trace`);
}

export function listInterviews(): Promise<InterviewRow[]> {
  return getJSON<InterviewRow[]>("/api/interviews");
}

/** 物理删除场次（T7a-R1）：业务库三表 + checkpointer 线程，不可恢复。 */
export async function deleteInterview(interviewId: string): Promise<void> {
  const response = await authorizedFetch(`/api/interviews/${interviewId}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error(await responseError(response));
}

// ---- 题库（FR-12 浏览搜索 / FR-14 容量校验，P1-M6）----

export type BankSource = {
  source: string;
  license: string | null;
  url: string | null;
  source_detail: string | null;
};

export type BankQuestion = {
  question_id: string;
  question: string;
  answer: string;
  key_points: string[];
  follow_ups: string[];
  domain: string;
  topic: string;
  difficulty: string;
  company: string | null;
  round: string | null;
  source: string | null;
  /** 来源明细，主源首位（合规四要素，M5 拆表） */
  sources: BankSource[];
};

export type BankListResponse = {
  /** browse = 筛选浏览（可翻页，total 有值）；search = 关键词混合检索（单页，total 为 null） */
  mode: "browse" | "search";
  total: number | null;
  page: number;
  page_size: number;
  items: BankQuestion[];
};

export type FacetValue = { value: string; count: number };
export type BankFacets = {
  domain: FacetValue[];
  difficulty: FacetValue[];
  company: FacetValue[];
  round: FacetValue[];
};

export type CapacityOption = {
  difficulty: string;
  /** 实际校验用的难度（adaptive → L1 起点） */
  base: string;
  question_count: number;
  ok: boolean;
  shortfalls: { domain: string; required: number; available: number }[];
};

export function getBankQuestions(params: URLSearchParams): Promise<BankListResponse> {
  return getJSON<BankListResponse>(`/api/bank/questions?${params.toString()}`);
}

export function getBankFacets(): Promise<BankFacets> {
  return getJSON<BankFacets>("/api/bank/facets");
}

/** 容量校验（FR-14）：只回被问到的题数配置。 */
export function getBankCapacity(counts: number[]): Promise<{ options: CapacityOption[] }> {
  return getJSON(`/api/bank/capacity?counts=${counts.join(",")}`);
}

// ---- 私有题库（FR-13，P1-M7）----

export type PrivateQuestion = {
  question_id: string;
  question: string;
  answer: string;
  key_points: string[];
  follow_ups: string[];
  domain: string;
  topic: string;
  difficulty: string;
  /** 后端复用公共题状态枚举：enabled = 使用中、draft = 已归档（文案见 private-bank.ts） */
  status: string;
  sources: BankSource[];
};

export type PrivateListResponse = {
  total: number;
  page: number;
  page_size: number;
  items: PrivateQuestion[];
};

/** 上传报告：部分成功语义（好题照常入库，坏题逐条给原因）。 */
export type UploadReport = {
  /** 解析出的题目总数（= 成功数 + 失败数）；0 = 文件里没有【题目】标记 */
  parsed: number;
  imported: number;
  /** 已存在（同用户 + 同题干）而跳过的题干，绝不覆盖已有编辑 */
  duplicated: string[];
  errors: { question: string; reason: string }[];
};

/** 可编辑字段（PATCH 只发改动项，未发的字段保持原值）。 */
export type PrivateQuestionPatch = Partial<
  Pick<PrivateQuestion, "question" | "answer" | "key_points" | "follow_ups" | "topic" | "domain" | "difficulty" | "status">
>;

export function getPrivateQuestions(params: URLSearchParams): Promise<PrivateListResponse> {
  return getJSON<PrivateListResponse>(`/api/bank/private/questions?${params.toString()}`);
}

/**
 * 上传 md/txt/pdf（multipart）。
 *
 * 不设 Content-Type：浏览器要自己补 multipart 的 boundary，手写反而会丢。
 * authorizedFetch 只在调用方给的 headers 上叠加 Authorization，故这里不传即可。
 */
export async function uploadPrivateFile(
  file: File,
  domain: string,
  difficulty: string,
): Promise<UploadReport> {
  const body = new FormData();
  body.append("file", file);
  body.append("domain", domain);
  body.append("difficulty", difficulty);
  const response = await authorizedFetch("/api/bank/private/upload", { method: "POST", body });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<UploadReport>;
}

/** 编辑或归档/恢复；返回更新后的完整题目（含来源明细）。 */
export async function patchPrivateQuestion(
  questionId: string,
  patch: PrivateQuestionPatch,
): Promise<PrivateQuestion> {
  const response = await authorizedFetch(`/api/bank/private/questions/${questionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<PrivateQuestion>;
}
