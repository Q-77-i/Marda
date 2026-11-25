/**
 * 学习计划 API（P5-J3）：Java business 经网关暴露的 `/api/v1/learning/**`。
 *
 * 与 Python API 分开：错误信封是 `{code, message, request_id, details}`（不是 detail），
 * 故这里自己解析；网络层与 401 处置仍复用 lib/http 的 authorizedFetch。
 */

import { authorizedFetch } from "@/lib/http";
import type {
  LearningPlan,
  LearningTask,
  PaginatedPlans,
  PlanStatus,
  ReportSummaryInput,
  TaskStatus,
  TodayTasks,
} from "@/lib/learning";

const CJK = /[一-鿿]/;

export class LearningApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "LearningApiError";
  }
}

type ErrorBody = {
  code?: unknown;
  message?: unknown;
  request_id?: unknown;
};

async function parse<T>(response: Response): Promise<T> {
  if (response.ok) {
    return (await response.json()) as T;
  }
  let body: ErrorBody | null = null;
  try {
    body = (await response.json()) as ErrorBody;
  } catch {
    body = null;
  }
  const message =
    typeof body?.message === "string" && CJK.test(body.message)
      ? body.message
      : `学习计划请求失败（${response.status}）`;
  throw new LearningApiError(
    message,
    typeof body?.code === "string" ? body.code : "UNKNOWN",
    response.status,
    typeof body?.request_id === "string" ? body.request_id : undefined,
  );
}

export type CreatePlanInput = {
  sourceInterviewId: string;
  goal?: string;
  position?: string;
  interviewType?: string;
  reportSummary: ReportSummaryInput;
};

/** 建计划：201 新建，200 幂等重放；两者都返回同一计划。 */
export async function createLearningPlan(
  input: CreatePlanInput,
): Promise<{ plan: LearningPlan; created: boolean }> {
  const response = await authorizedFetch("/api/v1/learning/plans", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": input.sourceInterviewId,
    },
    body: JSON.stringify({
      source_interview_id: input.sourceInterviewId,
      goal: input.goal,
      position: input.position,
      interview_type: input.interviewType,
      report_summary: input.reportSummary,
    }),
  });
  const plan = await parse<LearningPlan>(response);
  return { plan, created: response.status === 201 };
}

export async function listLearningPlans(
  status?: PlanStatus,
): Promise<PaginatedPlans> {
  const params = new URLSearchParams({ page: "1", page_size: "50" });
  if (status) params.set("status", status);
  const response = await authorizedFetch(
    `/api/v1/learning/plans?${params.toString()}`,
  );
  return parse<PaginatedPlans>(response);
}

export async function getTodayTasks(): Promise<TodayTasks> {
  const response = await authorizedFetch("/api/v1/learning/today");
  return parse<TodayTasks>(response);
}

/**
 * 更新任务：必须带当前 version。
 *
 * `dueAtPresent` 区分“不改截止日”与“显式清空”；当前 UI 只改 status，保留这个能力给后续编辑。
 */
export async function updateLearningTask(
  taskId: string,
  input: {
    status?: TaskStatus;
    dueAtPresent?: boolean;
    dueAt?: string | null;
    version: number;
  },
): Promise<LearningTask> {
  const body: Record<string, unknown> = { version: input.version };
  if (input.status) body.status = input.status;
  if (input.dueAtPresent) body.due_at = input.dueAt;
  const response = await authorizedFetch(`/api/v1/learning/tasks/${taskId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parse<LearningTask>(response);
}

/** 测试提醒：J3 只写 in_app / pending 行；投递由 J5 的 worker 接手。 */
export async function sendTestReminder(): Promise<{
  notification_id: string;
  status: string;
}> {
  const response = await authorizedFetch("/api/v1/learning/reminders/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel: "in_app" }),
  });
  return parse<{ notification_id: string; status: string }>(response);
}
