import type { ReactNode } from "react";

/**
 * 页面标题区：一级标题 + 一行说明 + 右侧（统计/动作）。
 *
 * 全站唯一的一级标题刻度在这里定义（text-2xl）；此前只有报告页与回放页有 h1，
 * 题库/档案等页直接以卡片标题（text-base）当最高层级，页面之间没有层级节奏。
 */
export function PageHeader({
  title,
  description,
  right,
}: {
  title: string;
  description?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description ? (
          <p className="text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {right ? <div className="flex items-center gap-6">{right}</div> : null}
    </div>
  );
}
