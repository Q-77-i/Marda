import { cn } from "cn";

/**
 * 行内提示条：`info` 告知 / `warning` 降级（P2-M9：AI 上游不可用、已切兜底——不是失败，
 * 但要显眼）/ `error` 可恢复的失败。用于页面内的过程反馈（断线提示、登录页存储降级、
 * 上传结果），不替代 ErrorState——那个用于「这块内容没加载出来」，这个用于
 * 「发生了某件事，告诉你一声」。
 */
function StatusBanner({
  tone = "info",
  children,
  action,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  tone?: "info" | "warning" | "error";
  action?: React.ReactNode;
}) {
  return (
    <div
      data-slot="status-banner"
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm",
        tone === "error" && "border-destructive/40 bg-destructive/5 text-destructive",
        tone === "warning" && "border-warning/40 bg-warning/5 text-foreground/90",
        tone === "info" && "bg-muted/40 text-muted-foreground",
        className,
      )}
      {...props}
    >
      <span className="min-w-0">{children}</span>
      {action}
    </div>
  );
}

export { StatusBanner };
