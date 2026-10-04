"use client";

import type { Dim, PerQuestionComment } from "@/lib/api";
import { DIMENSIONS, domainLabel } from "@/lib/constants";
import { formatScore, splitAnswerSegments } from "@/lib/format";

/**
 * 单题复盘卡（FR-25 / SPEC §4.6 / §9）。
 *
 * `dims` 不传时退回技术面五维常量（老报告 payload 无 dims 字段，且档案页等旧调用点不变）。
 */
export function ReviewCard({
  item,
  label,
  dims = DIMENSIONS,
}: {
  item: PerQuestionComment;
  label: string;
  dims?: Dim[];
}) {
  const segments = splitAnswerSegments(item.candidate_answer);
  const covered = item.covered_key_points ?? [];
  const missed = item.missed_key_points ?? [];
  const score = item.score ?? null;
  const mean = score
    ? dims.reduce((sum, dim) => sum + (score[dim.key] ?? 0), 0) / dims.length
    : null;

  return (
    <li className="flex flex-col gap-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="tabular-nums text-xs font-medium">{label}</span>
          {item.domain && (
            <span className="text-xs text-muted-foreground">{domainLabel(item.domain)}</span>
          )}
        </div>
        {mean !== null && (
          <span className="tabular-nums text-xs text-muted-foreground">
            五维均分 <span className="font-medium text-foreground">{formatScore(mean)}</span>
          </span>
        )}      </div>

      {item.text && <p className="text-sm font-medium leading-relaxed">{item.text}</p>}

      {/* 我的回答（含追问轮）：按后端标记分段，区分首答与追问补充 */}
      {segments.length > 0 && (
        <div className="flex flex-col gap-2 border-l-2 pl-3">
          {segments.map((segment) => (
            <div key={segment.label} className="flex flex-col gap-0.5">
              <span className="text-xs text-muted-foreground">{segment.label}</span>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
                {segment.text}
              </p>
            </div>
          ))}
          {/* 图片计数（P2-M6，2026-10-04 用户补充的「闭合信号」）：报告不展示图，
              但要让用户看到「传过的图没有丢」；图本体在面试页回放里 */}
          {(item.image_count ?? 0) > 0 && (
            <p className="text-xs text-muted-foreground">
              本题回答含 {item.image_count} 张截图
            </p>
          )}
        </div>
      )}

      {score && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {dims.map((dim) => (
            <li key={dim.key} className="flex items-baseline gap-1">
              <span className="text-xs text-muted-foreground">{dim.label}</span>
              <span className="tabular-nums text-xs font-medium">
                {score[dim.key] ?? 0}
              </span>
            </li>
          ))}
        </ul>
      )}

      {(covered.length > 0 || missed.length > 0) && (
        <div className="flex flex-col gap-1">
          <span className="text-xs text-muted-foreground">关键点覆盖</span>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {covered.map((point) => (
              <li key={point} className="text-xs">
                <span aria-hidden>✓</span> {point}
              </li>
            ))}
            {missed.map((point) => (
              <li key={point} className="text-xs text-muted-foreground">
                <span aria-hidden>✗</span> {point}
              </li>
            ))}
          </ul>
        </div>
      )}

      {item.comment && (
        <p className="text-sm leading-relaxed text-foreground/90">{item.comment}</p>
      )}

      {/* 参考答案仅题库题有（场景题无权威答案，后端给 null） */}
      {item.reference_answer && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            参考答案
          </summary>
          <p className="mt-2 whitespace-pre-wrap leading-relaxed text-foreground/90">
            {item.reference_answer}
          </p>
        </details>
      )}
    </li>
  );
}
