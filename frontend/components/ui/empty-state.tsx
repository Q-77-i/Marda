import { cn } from "cn";

/**
 * 空态：标题 +（可选）说明 +（可选）下一步动作。
 *
 * 「有没有 CTA」的判据是**这一步是否真有下一步动作**——有就给出口，没有就不硬加
 * （M7/M9 的「不静默」口径：空态不只是告知，还要给路）。
 */
function EmptyState({
  title,
  description,
  action,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div
      data-slot="empty-state"
      className={cn("flex flex-col items-center gap-1 py-10 text-center", className)}
      {...props}
    >
      <p className="text-sm font-medium">{title}</p>
      {description ? (
        <p className="text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export { EmptyState };
