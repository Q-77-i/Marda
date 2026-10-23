/**
 * 面试记录页的筛选与分页判据（纯函数，vitest 钉死）。
 *
 * 与题库页（lib/bank.ts）同构：改过滤一律回第一页、空值不发参数。
 * 时间区间在前端算成 `since=YYYY-MM-DD`（UTC 日期），后端按当日 00:00 UTC 比较。
 */

export type InterviewStatusFilter = "" | "running" | "finished";
export type InterviewTypeFilter = "" | "tech" | "behavioral";
/** 时间区间：空 = 全部；7d / 30d = 近 7 / 30 天。 */
export type InterviewSinceFilter = "" | "7d" | "30d";

export type InterviewFilters = {
  status: InterviewStatusFilter;
  type: InterviewTypeFilter;
  since: InterviewSinceFilter;
  page: number;
};

export const EMPTY_INTERVIEW_FILTERS: InterviewFilters = {
  status: "",
  type: "",
  since: "",
  page: 1,
};

/** 改任何过滤条件都回到第一页；只改 page 时保留过滤条件（翻页）。 */
export function withInterviewFilter(
  filters: InterviewFilters,
  patch: Partial<InterviewFilters>,
): InterviewFilters {
  const next = { ...filters, ...patch };
  return "page" in patch ? next : { ...next, page: 1 };
}

/** 时间区间 → since 日期（UTC，YYYY-MM-DD）；全部返回 undefined（不发参数）。 */
export function sinceIso(
  range: InterviewSinceFilter,
  now: Date = new Date(),
): string | undefined {
  if (!range) return undefined;
  const days = range === "7d" ? 7 : 30;
  return new Date(now.getTime() - days * 86_400_000).toISOString().slice(0, 10);
}

/** 筛选状态 → 查询参数：空值不发，page 与 page_size 恒发。 */
export function toInterviewQuery(
  filters: InterviewFilters,
  pageSize: number,
  now: Date = new Date(),
): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.type) params.set("type", filters.type);
  const since = sinceIso(filters.since, now);
  if (since) params.set("since", since);
  params.set("page", String(filters.page));
  params.set("page_size", String(pageSize));
  return params;
}

/** 删除后当前页剩余 0 条且不在第一页 → 回退一页重取；否则留在原页。 */
export function pageAfterDelete(page: number, remaining: number): number {
  return remaining === 0 && page > 1 ? page - 1 : page;
}
