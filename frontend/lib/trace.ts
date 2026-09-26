/**
 * 决策回放纯逻辑（P1-M4 / FR-21 / SPEC §4.7）。
 *
 * 事件按 `round`（所属问答轮次，null = 收尾）聚合成逐轮卡片，事件顺序即
 * 引擎当时的执行顺序，不再排序；`detail` 是后端给的纯标量字典，字段随
 * 事件类型而变，故取值一律经下面的守卫函数——缺字段给 null 让 UI 跳过，
 * 而不是显示 "undefined"。
 */

import type { TraceEvent } from "@/lib/api";

export type TraceRound = { round: number; events: TraceEvent[] };
export type GroupedTrace = { rounds: TraceRound[]; closing: TraceEvent[] };

/** 按轮次聚合：轮号升序，轮内保持事件原始顺序；round=null 归收尾区。 */
export function groupTraceEvents(events: TraceEvent[]): GroupedTrace {
  const buckets = new Map<number, TraceEvent[]>();
  const closing: TraceEvent[] = [];

  for (const event of events) {
    if (typeof event.round !== "number") {
      closing.push(event);
      continue;
    }
    const bucket = buckets.get(event.round);
    if (bucket) bucket.push(event);
    else buckets.set(event.round, [event]);
  }

  return {
    rounds: [...buckets.entries()]
      .sort(([a], [b]) => a - b)
      .map(([round, list]) => ({ round, events: list })),
    closing,
  };
}

/** 覆盖率（0-1）转百分比整数；缺字段返回 null。 */
export function coveragePercent(coverage: unknown): number | null {
  const value = asNumber(coverage);
  return value === null ? null : Math.round(value * 100);
}

/** 非空字符串；空串/空白/非字符串给 null（detail 里字段可能缺失）。 */
export function asText(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** 有限数值；其余（含 "3"、NaN、缺失）给 null。 */
export function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** 只有明确的 true 才算真：字段缺失不等于「是」。 */
export function asFlag(value: unknown): boolean {
  return value === true;
}

/** 普通对象（评分明细等嵌套结构）；数组与 null 给 null。 */
export function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** 字符串数组；剔除非字符串项，非数组给空表。 */
export function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** 评分证据（judge 事件的评分明细里）：漏掉的关键点与评分官点评。
 * 历史事件可能缺这两个字段，均容缺让 UI 跳过。 */
export function judgeEvidence(score: unknown): {
  missedKeyPoints: string[];
  comment: string | null;
} {
  const record = asRecord(score);
  return {
    missedKeyPoints: asStringList(record?.missed_key_points),
    comment: asText(record?.comment),
  };
}
