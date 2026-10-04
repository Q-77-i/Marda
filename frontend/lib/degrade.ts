/**
 * 降级文案（P2-M9）：AI 上游不可用时引擎切确定性兜底，前端如实交代。
 *
 * 文案放这里而不是页面里：页面的分支进不了 vitest，做成数据才测得到
 * （同 `lib/profile.ts` 的空态文案口径）。原因文本由服务端给（中文一句话），
 * 前端只做拼接与去重，不自己判断「哪种降级该怎么解释」。
 */

const PREFIX = "本场已切换为降级模式（AI 服务暂时不可用）";

/** 面试进行中的横幅：原因逐条列出——用户必须知道题目/文案可能不是 AI 现场产出的。 */
export function degradedNotice(reasons: string[]): string | null {
  if (reasons.length === 0) return null;
  return `${PREFIX}：${reasons.join("；")}。面试照常进行，已答内容不受影响。`;
}

type DegradedReport = {
  degraded?: boolean;
  degraded_reasons?: string[];
  unscored_count?: number;
  answered_count?: number;
  scores?: Record<string, number>;
};

/** 报告页横幅：分两种态——整场未评分（分数区收起）/ 部分未评分（分数只统计已评题）。 */
export function reportDegradedNotice(report: DegradedReport): string | null {
  if (report.degraded !== true) return null;
  const reasons = (report.degraded_reasons ?? []).join("；") || "AI 服务暂时不可用";
  const hasScores = Object.keys(report.scores ?? {}).length > 0;
  if (!hasScores) {
    return `${PREFIX}：${reasons}。本场未生成能力评分——分数与雷达图留空，逐题复盘与参考答案仍完整。`;
  }
  const unscored = report.unscored_count ?? 0;
  if (unscored > 0) {
    return `${PREFIX}：${reasons}。本场 ${report.answered_count ?? 0} 题中有 ${unscored} 题未评分，分数只统计已评分的题目。`;
  }
  return `${PREFIX}：${reasons}。`;
}
