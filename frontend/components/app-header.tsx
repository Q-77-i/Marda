import Link from "next/link";
import type { ReactNode } from "react";

/** 页头：品牌 + 可选右侧内容（进度/操作）。三页共用，保持导航一致。 */
export function AppHeader({ right }: { right?: ReactNode }) {
  return (
    <header className="border-b">
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-baseline gap-2">
          <span className="text-sm font-semibold tracking-tight">Marda 码达</span>
          <span className="hidden text-xs text-muted-foreground sm:inline">
            Agent 智能面试
          </span>
        </Link>
        {right}
      </div>
    </header>
  );
}
