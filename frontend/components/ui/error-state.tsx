import { cn } from "cn";

/**
 * 错误态两档：
 * - `inline`：局部加载失败（列表/卡片），destructive 文案 + 可选重试按钮；
 * - `page`：整页到不了（报告/回放加载失败），中性色——这不是「出错了」，是「没有」，
 *   红色整页会读成系统故障，附一句说明与返回出口。
 *
 * 表单校验与提交失败**不走这里**：那是字段级反馈，仍用一行 `text-sm text-destructive`
 * 贴着表单显示（块级状态才用本组件）。
 */
function ErrorState({
  variant = "inline",
  message,
  description,
  action,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  variant?: "inline" | "page";
  message: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  if (variant === "page") {
    return (
      <div
        data-slot="error-state"
        className={cn("flex flex-col items-center gap-1 py-16 text-center", className)}
        {...props}
      >
        <p className="text-sm font-medium">{message}</p>
        {description ? (
          <p className="text-sm text-muted-foreground">{description}</p>
        ) : null}
        {action ? <div className="mt-6">{action}</div> : null}
      </div>
    );
  }

  return (
    <div
      data-slot="error-state"
      className={cn("flex flex-col items-center gap-3 py-6 text-center", className)}
      {...props}
    >
      <p className="text-sm text-destructive" role="alert">
        {message}
      </p>
      {action}
    </div>
  );
}

export { ErrorState };
