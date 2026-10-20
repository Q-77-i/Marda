"use client";

import type { ReactNode } from "react";
import { cn } from "cn";

export type SegmentedOption = {
  value: string;
  label: ReactNode;
  title?: string;
  disabled?: boolean;
};

/**
 * 分段选择：创建面试的类型/难度/题量与题库难度筛选共用。
 * 选中态用前景色实底（亮场下是墨黑，暗场下是纸白），主操作才用钴蓝。
 */
function Segmented({
  options,
  value,
  onChange,
  columns,
  ariaLabel,
  className,
  disabled = false,
}: {
  options: readonly SegmentedOption[];
  value: string;
  onChange: (value: string) => void;
  columns?: 2 | 3 | 4;
  ariaLabel: string;
  className?: string;
  disabled?: boolean;
}) {
  const cols =
    columns === 2
      ? "grid-cols-2"
      : columns === 3
        ? "grid-cols-3"
        : columns === 4
          ? "grid-cols-4"
          : options.length === 2
            ? "grid-cols-2"
            : options.length === 3
              ? "grid-cols-3"
              : "grid-cols-4";

  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn(
        "grid gap-px rounded-lg border border-border bg-muted p-[3px]",
        cols,
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            title={option.title}
            disabled={disabled || option.disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "h-8 rounded-md px-2 text-xs transition-colors",
              active
                ? "bg-foreground font-medium text-background"
                : "text-muted-foreground hover:text-foreground",
              "disabled:pointer-events-none disabled:opacity-40",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export { Segmented };
