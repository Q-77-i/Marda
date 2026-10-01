/**
 * 学习推荐展示逻辑（FR-20 / P1-M9）：场次选择与分组状态文案。
 *
 * 纯函数（vitest 主战场）：默认选中哪一场、空分组说什么话都由这里定，
 * 页面只消费——不把这类判断散在 JSX 里。
 */

import type { InterviewRow, RecommendationGroup } from "@/lib/api";
import { isBehavioral } from "@/lib/constants";
import { formatTime } from "@/lib/format";

/**
 * 有报告的已结束场次：推荐读的是报告短板，未结束/无报告的场次给不出推荐。
 * **行为面场次排除**（P1-M11 D5）：推荐检索的是六大技术域，行为面没有可推的域。
 */
export function finishedSessions(rows: InterviewRow[]): InterviewRow[] {
  return rows.filter((row) => row.report_ready && !isBehavioral(row.interview_type));
}

/**
 * 默认选中场次：URL 带过来的场次优先（从报告页「查看全部推荐」跳进来时**承接来源**，
 * 否则用户会落到最近一场的推荐上，路径断裂），无效或没带时取最近一场（列表倒序）。
 * 没有可选场次 → null。
 */
export function pickDefaultInterview(
  rows: InterviewRow[],
  requested: string | null,
): string | null {
  if (requested && rows.some((row) => row.id === requested)) return requested;
  return rows[0]?.id ?? null;
}

/**
 * 空分组文案（③「不静默隐藏」）：`exhausted` = 该域题都问过了，
 * `empty` = 该域题库里没题——两者都不是「系统漏了」，要说清楚是哪一种。
 */
export function groupNotice(status: RecommendationGroup["status"]): string | null {
  if (status === "exhausted") return "该域题目已全部练过，暂无新推荐";
  if (status === "empty") return "该域题库暂无题目";
  return null;
}

/** 场次下拉的展示文案。 */
export function interviewOptionLabel(row: InterviewRow): string {
  return `${row.position} · ${formatTime(row.started_at)} · ${row.question_count} 轮`;
}
