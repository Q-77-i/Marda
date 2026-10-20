import type { ReactNode } from "react";

import { MonoLabel } from "@/components/ui/mono-label";

/**
 * 页面标题区：等宽眉题 + 一级标题 + 一行说明 + 右侧动作。
 *
 * 用负边距把底边拉满内容区，和左栏形成一条完整的横向结构线；
 * 全站唯一的一级标题刻度（25px）仍在这里定义。
 */
export function PageHeader({
  kicker,
  title,
  description,
  right,
}: {
  kicker?: string;
  title: string;
  description?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <header className="-mx-5 flex flex-wrap items-end justify-between gap-4 border-b border-border px-5 pb-5 md:-mx-8 md:px-8">
      <div className="flex min-w-0 flex-col gap-1.5">
        {kicker ? <MonoLabel>{kicker}</MonoLabel> : null}
        <h1 className="text-[25px] leading-[1.15] font-semibold tracking-[-0.03em]">
          {title}
        </h1>
        {description ? (
          <p className="text-[12.5px] text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {right ? (
        <div className="flex flex-wrap items-center justify-end gap-2">{right}</div>
      ) : null}
    </header>
  );
}
