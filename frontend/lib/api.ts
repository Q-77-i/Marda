/**
 * 后端 API 封装（SPEC §7 契约）：五端点 + SSE 事件分发。
 *
 * 请求走同源 /api/*（next.config.ts rewrites → 后端），避免 CORS。
 */

import { networkMessage, postSSE, type SSEEvent } from "@/lib/sse";

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

/**
 * 逐题点评。
 *
 * 2026-09-19 起后端补上 `index`/`domain`/`text`（场景题 `domain="project"`、`question_id=null`）；
 * 历史报告的 payload 只有 `{question_id, comment}`，故新字段可选，前端按位置推断兜底。
 */
export type PerQuestionComment = {
  question_id: string | null;
  comment: string;
  index?: number;
  domain?: string;
  text?: string;
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
  let response: Response;
  try {
    response = await fetch(url);
  } catch (err) {
    throw new Error(networkMessage(err));
  }
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const payload = await response.json();
      if (typeof payload?.detail === "string") detail = payload.detail;
    } catch {
      // 非 JSON 响应，用通用文案
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export function getSession(interviewId: string): Promise<Session> {
  return getJSON<Session>(`/api/interviews/${interviewId}`);
}

export function getReport(interviewId: string): Promise<ReportResponse> {
  return getJSON<ReportResponse>(`/api/interviews/${interviewId}/report`);
}

export function listInterviews(): Promise<InterviewRow[]> {
  return getJSON<InterviewRow[]>("/api/interviews");
}
