import { cn } from "cn";

/** 语义状态点：只和文字一起出现，不单独表意。 */
function StatusDot({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("inline-block size-1.5 shrink-0 rounded-full bg-current", className)}
    />
  );
}

export { StatusDot };
