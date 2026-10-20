import { cn } from "cn";

/**
 * 等宽元数据标签：面板眉题、字段名、统计标签。
 * 全站只在一个地方定义字号与字距，避免每页各写一份 tracking。
 */
function MonoLabel({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="mono-label"
      className={cn(
        "font-mono text-[10px] leading-none font-medium tracking-[0.12em] text-muted-foreground uppercase",
        className,
      )}
      {...props}
    />
  );
}

export { MonoLabel };
