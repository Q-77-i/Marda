/**
 * 能力档案展示逻辑（FR-19 / P1-M10 / P1-M10.5）：曲线与热力图的数据整形、文案。
 *
 * 纯函数（vitest 主战场）：刻度、色阶档、缺场、变化与洞察文案都由这里定，
 * 页面只消费——与 lib/learn.ts、lib/trace.ts 同一分工（页面不管判断，只管渲染）。
 *
 * 贯穿口径（M10 D2 起，M10.5 延续到热力图）：**缺场就是缺场**——没考到的域
 * 保持 `null` 并渲染成「未考」，不补 0、不画成低谷；颜色分档也只为有值的格子算。
 */

import type { ProfileSession, WeaknessChange } from "@/lib/api";
import { domainLabel } from "@/lib/constants";
import { formatScore, formatTime } from "@/lib/format";

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
 * 曲线横轴实际显示的刻度：**一天一个标签**（P1-M10.5）。
 *
 * 同日多场只标该组首场（`#n` 仍留在 tooltip 与热力图列头），因此不会出现
 * 「#1 没显示却出现 #2」的孤儿编号；日期组超过 `max` 时按等距抽稀、首末必留。
 * 抽稀只发生在日期组这一层——长历史（十几场）下标签才不会重新挤成一排。
 */
export function axisTicks(labels: string[], max = 7): string[] {
  const days: string[] = [];
  const seen = new Set<string>();
  for (const label of labels) {
    const day = label.split(" #")[0];
    if (seen.has(day)) continue;
    seen.add(day);
    days.push(day);
  }
  if (days.length <= max) return days;
  const step = Math.ceil((days.length - 1) / (max - 1));
  return days.filter((_, index) => index === 0 || index === days.length - 1 || index % step === 0);
}

/** 热力图一行：一个知识域在各场次的格子；`null` = 该场没考到这个域。 */
export type HeatRow = { domain: string; cells: (number | null)[] };

/**
 * 知识域热力矩阵（行 = 域、列 = 场次）。
 *
 * **缺键保持 `null`、不补 0**：与 M10「域曲线缺场断开」同一口径——没考到的域
 * 不是「得 0 分」，热力图里如实渲染成灰色「未考」。
 */
export function heatRows(sessions: ProfileSession[], domains: string[]): HeatRow[] {
  return domains.map((domain) => ({
    domain,
    cells: sessions.map((session) => session.domain_scores[domain] ?? null),
  }));
}

/** 分数 → 色阶档（1–5，就近取整并夹到端点）；未考（`null`）没有档。 */
export function heatLevel(value: number | null): number | null {
  if (value === null) return null;
  return Math.min(5, Math.max(1, Math.round(value)));
}

/**
 * 场次变多后的窗口（P1-M10.5）：**三种视图三种策略**，因为三者的「多少才看得懂」不一样——
 * 曲线**全量**（趋势的价值就在整体走向，截断就看不出长期变化）；
 * 热力图默认**最近 7 场**（列挤到 7 列以上数字就难读，一屏可读的极限）；
 * 短板变化默认**最近 5 场**（每场 3–5 行文字，再多就淹没在列表里，想看旧的点进报告）。
 */
export const HEATMAP_WINDOW = 7;
export const WEAKNESS_WINDOW = 5;

/** 取尾部窗口：`items` 须按时间升序（尾部 = 最近）；不足 `limit` 或已展开则原样返回。 */
export function recentSlice<T>(items: T[], limit: number, expanded: boolean): T[] {
  return expanded ? items : items.slice(-limit);
}

/**
 * 热力图列窗口：列与列头**按同一后缀切片**，保证一一对应。
 *
 * 列头取的是**全量**那套标签（不是重算）：窗口若从某天中间截断，会如实留下 `#2` 而没有 `#1`——
 * 这是对的，两个视图的同一场必须叫同一个名字；重算会把窗口内的第一场改名成「当天第 1 场」，
 * 于是热力图的某一列与曲线上的同名点指向不同的两场。
 */
export function heatmapWindow(
  sessions: ProfileSession[],
  labels: string[],
  expanded: boolean,
): { sessions: ProfileSession[]; labels: string[] } {
  const shown = recentSlice(sessions, HEATMAP_WINDOW, expanded);
  return { sessions: shown, labels: labels.slice(-shown.length) };
}

/** 逐域统计：`tested` = 考过几场（缺场不计 0），`average` = 这些场次的均分。 */
export type DomainStat = { domain: string; tested: number; average: number };

export function domainStats(sessions: ProfileSession[], domains: string[]): DomainStat[] {
  return domains.map((domain) => {
    const values = sessions
      .map((session) => session.domain_scores[domain])
      .filter((value): value is number => typeof value === "number");
    const sum = values.reduce((total, value) => total + value, 0);
    return {
      domain,
      tested: values.length,
      average: values.length ? Math.round((sum / values.length) * 10) / 10 : 0,
    };
  });
}

/**
 * 五维对照：每维的场均与最近一场。
 *
 * 与热力图不同，`scores` 每场都有全五维（后端把缺维按 0 落库，M10 已定的口径），
 * 故这里不筛场次——口径与报告页/PDF 的总分算法保持一致（同一批数字，不另立算法）。
 */
export type DimensionStat = { key: string; average: number; latest: number };

export function dimensionStats(sessions: ProfileSession[], keys: string[]): DimensionStat[] {
  return keys.map((key) => {
    const values = sessions.map((session) => session.scores[key] ?? 0);
    const sum = values.reduce((total, value) => total + value, 0);
    return {
      key,
      average: values.length ? sum / values.length : 0,
      latest: values.length ? values[values.length - 1] : 0,
    };
  });
}

/** 某域「连续多少场是短板」：从最后一场往前数，连续出现在 weaknesses 里的场次数。 */
export function weaknessStreak(sessions: ProfileSession[], domain: string): number {
  let streak = 0;
  for (let index = sessions.length - 1; index >= 0; index -= 1) {
    if (!sessions[index].weaknesses.includes(domain)) break;
    streak += 1;
  }
  return streak;
}

/**
 * 洞察一行（P1-M10.5）：只陈述事实、并带上依据（考过几场 / 连续几场），
 * **不替用户下结论**——口径同 M10 的「已改善」文案（不推断是真提升还是这场没考）。
 *
 * 两条事实各自成立才出现，用「；」连起来；都不成立返回 `null`（页面不渲染空话）：
 *   ① 连续 ≥2 场是短板的域（取连续最长者，并列取域序在前）
 *   ② 场均最低的域——只算考过 ≥2 场的域（一场的「场均」不成其为依据），
 *      且要有 ≥2 个可比域、确实存在高低差
 */
export function insightLine(sessions: ProfileSession[], domains: string[]): string | null {
  if (sessions.length < 2) return null;

  const facts: string[] = [];

  const longest = domains
    .map((domain) => ({ domain, streak: weaknessStreak(sessions, domain) }))
    .filter((item) => item.streak >= 2)
    .sort((a, b) => b.streak - a.streak)[0];
  if (longest) {
    facts.push(`${domainLabel(longest.domain)} 已连续 ${longest.streak} 场是短板`);
  }

  // 「最低」得有可比对象：至少两个域考过两场以上，且确实存在高低差——
  // 单一域或各域打平时说「最低」是废话（都 4.0 分时最低最高是同一个人）
  const tested = domainStats(sessions, domains).filter((stat) => stat.tested >= 2);
  if (tested.length >= 2) {
    const lowest = tested.reduce((min, stat) => (stat.average < min.average ? stat : min));
    const highest = tested.reduce((max, stat) => (stat.average > max.average ? stat : max));
    if (lowest.average < highest.average) {
      facts.push(
        `场均最低的知识域是 ${domainLabel(lowest.domain)}（${formatScore(lowest.average)} 分，考过 ${lowest.tested} 场）`,
      );
    }
  }

  return facts.length ? `${facts.join("；")}。` : null;
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
