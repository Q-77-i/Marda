import Link from "next/link";
import type { ReactNode } from "react";

import { MainNav } from "@/components/main-nav";

/**
 * 页头：品牌 +（可选）主导航 + 可选右侧内容（页内动作/用户菜单）。各页共用。
 *
 * `nav` 默认为 false——面试页/报告页/回放页是沉浸式体验，藏导航；
 * 只有仪表盘与题库页传 nav（P1-M6 顶栏 tab 口径）。
 */
export function AppHeader({ right, nav = false }: { right?: ReactNode; nav?: boolean }) {
  return (
    <header className="border-b">
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-4 sm:gap-6">
          <Link href="/" className="flex shrink-0 items-baseline gap-2">
            <span className="text-sm font-semibold tracking-tight">Marda 码达</span>
            <span className="hidden text-xs text-muted-foreground sm:inline">
              Agent 智能面试
            </span>
          </Link>
          {nav ? <MainNav /> : null}
        </div>
        {right}
      </div>
    </header>
  );
}
