import { cn } from "cn"

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  )
}

/**
 * 列表型加载骨架——列表页与卡片内的统一加载态，不再各写各的三行。
 * `rowClassName` 用来贴合真实行高（题库卡高、面试记录行矮），形状一致、高度按列表走。
 */
function ListSkeleton({
  rows = 3,
  className,
  rowClassName = "h-16 w-full",
}: {
  rows?: number;
  className?: string;
  rowClassName?: string;
}) {
  return (
    <div data-slot="list-skeleton" className={cn("flex flex-col gap-3", className)}>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className={rowClassName} />
      ))}
    </div>
  );
}

export { ListSkeleton, Skeleton }
