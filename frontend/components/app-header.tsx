import Link from "next/link";
import type { ReactNode } from "react";

import { MainNav } from "@/components/main-nav";

/** 内沿宽度与 PageShell 的两档对齐：页头品牌与页面内容左边缘落在同一条竖线上。 */
const WIDTHS = {
  default: "max-w-5xl",
  narrow: "max-w-3xl",
} as const;

/**
 * 页头：品牌 +（可选）主导航 + 可选右侧内容（页内动作/用户菜单）。各页共用。
 *
 * `nav` 默认为 false——面试页/报告页/回放页是沉浸式体验，藏导航；
 * 只有仪表盘与题库页传 nav（P1-M6 顶栏 tab 口径）。
 */
export function AppHeader({
  right,
  nav = false,
  width = "default",
}: {
  right?: ReactNode;
  nav?: boolean;
  width?: keyof typeof WIDTHS;
}) {
  return (
    <header className="border-b">
      {/* 窄屏（P2-M3）：品牌 + 右侧内容一行、导航独占一行——五个 tab 挤在一行会被压成
          竖排单字。≥sm 换回原来的单行 h-14（order 只影响排布顺序，桌面上与 DOM 序一致）。 */}
      <div
        className={`mx-auto flex min-h-14 flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 sm:h-14 sm:flex-nowrap sm:gap-6 sm:px-6 sm:py-0 ${WIDTHS[width]}`}
      >
        <Link href="/" className="order-1 flex shrink-0 items-baseline gap-2">
          <span className="text-sm font-semibold tracking-tight">Marda 码达</span>
          <span className="hidden text-xs text-muted-foreground sm:inline">
            Agent 智能面试
          </span>
        </Link>
        {nav ? <MainNav /> : null}
        {right ? <div className="order-2 ml-auto sm:order-3">{right}</div> : null}
      </div>
    </header>
  );
}
