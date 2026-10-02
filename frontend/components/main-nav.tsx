"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS } from "@/lib/constants";
import { cn } from "@/lib/utils";

/**
 * 顶层导航（P1-M6 拍板：顶栏 tab）。只出现在非沉浸式页面——
 * 面试页、报告页、回放页是全屏体验，由调用方决定不传 nav（AppHeader 默认不渲染）。
 */
export function MainNav() {
  const pathname = usePathname();
  return (
    // 窄屏（P2-M3）：order-3 + w-full 让导航在品牌那一行之下独占一行；item 一律
    // whitespace-nowrap 不被压成竖排单字，容器 overflow-x-auto 兜底（320px 放不下时横滑）
    <nav
      aria-label="主导航"
      className="order-3 flex w-full min-w-0 items-center gap-1 overflow-x-auto sm:order-2 sm:w-auto"
    >
      {NAV_ITEMS.map((item) => {
        const active = pathname === item.href;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "shrink-0 whitespace-nowrap rounded-md px-2 py-1 text-sm transition-colors hover:bg-muted hover:text-foreground",
              active ? "font-medium text-foreground" : "text-muted-foreground",
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
