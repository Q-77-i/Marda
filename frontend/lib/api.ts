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
  | "closing"
  | "finished";

export type MetaEvent = {
  interview_id?: string;
  phase: Phase;
  answered_count: number;
  question_count: number;
};
export type DeltaEvent = { text: string };
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
};

export type InterviewRow = {
  id: string;
  position: string;
  question_count: number;
  phase: Phase;
  difficulty: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  report_ready: boolean;
};

/** 五维得分（FR-25 复盘卡逐题展示，与 backend aggregate.FIVE_DIMS 同源）。 */
export type ScoreDimensions = {
  technical_depth: number;
  fundamentals: number;
  project_experience: number;
  communication: number;
  problem_solving: number;
};

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

export type ReportPayload = {
  interview_id: string;
  position: string;
  scores: Record<string, number>;
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
  status: "running" | "finished";
  answered_count: number;
  question_count: number;
  events: TraceEvent[];
};

export type SSEHandlers = {
  meta?: (event: MetaEvent) => void;
  delta?: (event: DeltaEvent) => void;
  question?: (event: QuestionEvent) => void;
  done?: (event: DoneEvent) => void;
  error?: (event: ErrorEvent) => void;
};

/** SSE 原始事件 → 按 event 名分发 + JSON 解析（数据格式与后端 §7 事件表一致）。 */
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
  onEvent: (event: SSEEvent) => void,
): Promise<string> {
  let interviewId = "";
  await postSSE(
    "/api/interviews",
    { position, question_count: questionCount },
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
