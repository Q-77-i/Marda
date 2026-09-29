"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS } from "@/lib/constants";
import { cn } from "@/lib/utils";

/**
 * 顶层导航（P1-M6 拍板：顶栏 tab）。只出现在非沉浸式页面（仪表盘 / 题库）——
 * 面试页、报告页、回放页是全屏体验，由调用方决定不传 nav（AppHeader 默认不渲染）。
 *
 * 未上线项（能力档案 / 学习推荐）渲染成不可点的灰文本：占住信息架构，
 * 但不给出会 404 的链接。
 */
export function MainNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="主导航" className="flex items-center gap-1">
      {NAV_ITEMS.map((item) => {
        if (!item.ready) {
          return (
            <span
              key={item.href}
              aria-disabled="true"
              title="即将上线"
              className="cursor-not-allowed rounded-md px-2 py-1 text-sm text-muted-foreground/50"
            >
              {item.label}
            </span>
          );
        }
        const active = pathname === item.href;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "rounded-md px-2 py-1 text-sm transition-colors hover:bg-muted hover:text-foreground",
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
