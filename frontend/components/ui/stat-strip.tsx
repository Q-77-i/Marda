import type { ReactNode } from "react";
import { cn } from "cn";

import { MonoLabel } from "@/components/ui/mono-label";

export type StatItem = {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "up" | "down";
};

/**
 * 概览统计条：用 1px 间隔线而不是四张独立卡片。
 * 2×2 在窄屏、4×1 在桌面；条目数量为 3 时自动切成 3 列。
 */
function StatStrip({
  items,
  className,
}: {
  items: StatItem[];
  className?: string;
}) {
  const columns =
    items.length === 3
      ? "grid-cols-1 sm:grid-cols-3"
      : "grid-cols-2 md:grid-cols-4";

  return (
    <section
      aria-label="概览统计"
      className={cn(
        "grid gap-px overflow-hidden rounded-xl border border-border bg-border",
        columns,
        className,
      )}
    >
      {items.map((item) => (
        <div key={item.label} className="min-w-0 bg-card px-4 py-[15px] md:px-[18px] md:py-4">
          <MonoLabel className="text-[9.5px]">{item.label}</MonoLabel>
          <div
            className={cn(
              "mt-1.5 text-[27px] leading-none font-semibold tracking-[-0.035em]",
              item.tone === "up" && "text-success",
              item.tone === "down" && "text-destructive",
            )}
          >
            {item.value}
          </div>
          {item.hint ? (
            <div className="mt-1.5 truncate text-[11px] text-muted-foreground">
              {item.hint}
            </div>
          ) : null}
        </div>
      ))}
    </section>
  );
}

export { StatStrip };
