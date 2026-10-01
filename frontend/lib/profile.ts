/**
 * 能力档案展示逻辑（FR-19 / P1-M10）：曲线数据整形与文案。
 *
 * 纯函数（vitest 主战场）：刻度、断点、变化文案都由这里定，页面只消费——
 * 与 lib/learn.ts、lib/trace.ts 同一分工（页面不管判断，只管渲染）。
 */

import type { ProfileSession, WeaknessChange } from "@/lib/api";
import { formatTime } from "@/lib/format";

/** 三种页面形态：空档案 / 只有一场（画不出曲线）/ 多场。 */
export type ProfileStage = "empty" | "single" | "series";

export function profileStage(sessionCount: number): ProfileStage {
  if (sessionCount === 0) return "empty";
  if (sessionCount === 1) return "single";
  return "series";
}

/**
 * 曲线横轴刻度（`MM-DD`）。
 *
 * 同一天的场次加序号（`#2`、`#3`）：一天跑两场时只写日期，两个点会看起来是重复的。
 * 时间戳缺失（历史脏数据）退化为场次序，不留空标签。
 */
export function tickLabels(sessions: ProfileSession[]): string[] {
  const seen = new Map<string, number>();
  return sessions.map((session, index) => {
    const day = formatTime(session.started_at).slice(0, 5);
    if (!day) return `第 ${index + 1} 场`;
    const count = (seen.get(day) ?? 0) + 1;
    seen.set(day, count);
    return count === 1 ? day : `${day} #${count}`;
  });
}

export type ProfileChartRow = { label: string } & Record<string, number | string | null>;

/** 总分曲线：每场一个点。带 `interview_id` 供点位点击跳该场报告。 */
export function overallRows(sessions: ProfileSession[]): ProfileChartRow[] {
  const labels = tickLabels(sessions);
  return sessions.map((session, index) => ({
    label: labels[index],
    overall: session.overall,
    interview_id: session.interview_id,
  }));
}

/**
 * 五维 / 知识域曲线。
 *
 * **缺键 = `null`（曲线断点），不是 0**：一场只考部分域，没考的域不是「得 0 分」——
 * 补零会凭空造出一个低谷，那是假信号（P1-M10 D2，前端 `connectNulls={false}` 如实断开）。
 */
export function scoreRows(
  sessions: ProfileSession[],
  keys: string[],
  field: "scores" | "domain_scores",
): ProfileChartRow[] {
  const labels = tickLabels(sessions);
  return sessions.map((session, index) => {
    const row: ProfileChartRow = { label: labels[index] };
    for (const key of keys) {
      row[key] = session[field][key] ?? null;
    }
    return row;
  });
}

/** 最近一场的变化文案：升/降/持平（数值本身由页面另行展示）。 */
export function deltaLabel(delta: number): string {
  if (delta > 0) return `↑ ${delta.toFixed(1)}`;
  if (delta < 0) return `↓ ${Math.abs(delta).toFixed(1)}`;
  return "持平";
}

export type WeaknessChangeRow = WeaknessChange & { label: string };

/** 短板变化条目：把场次 id 换成曲线同一套刻度标签（两处对得上，用户才知道在说哪一场）。 */
export function weaknessRows(
  sessions: ProfileSession[],
  changes: WeaknessChange[],
): WeaknessChangeRow[] {
  const labels = tickLabels(sessions);
  const labelById = new Map(sessions.map((session, index) => [session.interview_id, labels[index]]));
  return changes.map((change) => ({
    ...change,
    label: labelById.get(change.interview_id) ?? formatTime(change.started_at),
  }));
}
