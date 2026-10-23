"use client";

import {
  FolderOpen,
  History,
  LayoutDashboard,
  Library,
  Sparkles,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { UserMenu } from "@/components/user-menu";
import { NAV_ITEMS } from "@/lib/constants";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = {
  "/": LayoutDashboard,
  "/bank": Library,
  "/bank/private": FolderOpen,
  "/interviews": History,
  "/profile": UserRound,
  "/learn": Sparkles,
};

/**
 * 左栏导航（界面重塑）：宽栏 236px、窄栏 74px 图标栏、移动端顶部图标条。
 *
 * 活动项按「最长前缀」判定，/bank/private 不会同时点亮 /bank。
 * 窄栏隐藏文字与底部说明，只留图标和用户名首字母；移动端整体变成一行。
 */
export function AppRail() {
  const pathname = usePathname();
  const activeHref = NAV_ITEMS.map((item) => item.href)
    .filter((href) =>
      href === "/"
        ? pathname === "/"
        : pathname === href || pathname.startsWith(`${href}/`),
    )
    .sort((a, b) => b.length - a.length)[0];

  return (
    <aside className="rail-texture fixed top-0 left-0 z-30 flex h-14 w-full items-center gap-1 overflow-x-auto border-b border-rail-line bg-rail px-2 text-rail-foreground md:h-[100dvh] md:w-[74px] md:flex-col md:items-stretch md:overflow-visible md:border-r md:border-b-0 md:px-2.5 md:py-4 xl:w-[236px] xl:px-3.5">
      <Link
        href="/"
        aria-label="Marda 码达"
        className="flex h-10 shrink-0 items-center gap-2.5 rounded-md px-2 md:mb-4 md:justify-center md:px-0 xl:justify-start xl:px-2"
      >
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-sm font-bold tracking-[-0.05em] text-primary-foreground">
          M
        </span>
        <span className="hidden flex-col leading-none md:hidden xl:flex">
          <span className="text-[13.5px] font-semibold tracking-[-0.01em] text-white">
            Marda 码达
          </span>
          <span className="mt-1 font-mono text-[9px] tracking-[0.14em] text-rail-muted uppercase">
            Interview Engine
          </span>
        </span>
      </Link>

      <div className="hidden px-2.5 pt-2 pb-2 font-mono text-[9px] tracking-[0.16em] text-[#6d7179] uppercase xl:block">
        工作台
      </div>

      <nav
        aria-label="主导航"
        className="flex items-center gap-1 md:flex-col md:items-stretch md:gap-0.5"
      >
        {NAV_ITEMS.map((item) => {
          const Icon = ICONS[item.href] ?? LayoutDashboard;
          const active = item.href === activeHref;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              title={item.label}
              className={cn(
                "relative flex h-10 shrink-0 items-center justify-center gap-2.5 rounded-md px-2.5 text-[13px] text-[#a7abb2] transition-colors hover:bg-white/5 hover:text-white md:justify-start",
                active && "bg-white/[0.075] text-white",
              )}
            >
              {active ? (
                <span
                  aria-hidden
                  className="absolute top-2 bottom-2 -left-2.5 w-0.5 bg-[#8ba1ff] max-md:hidden xl:-left-3.5"
                />
              ) : null}
              <Icon
                className={cn(
                  "size-[18px] shrink-0 text-[#7e838b]",
                  active && "text-[#c9ccd3]",
                )}
                strokeWidth={1.8}
              />
              <span className="hidden whitespace-nowrap xl:inline">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="hidden flex-1 md:block" />

      <div className="mt-3 hidden rounded-lg border border-rail-line bg-white/[0.025] p-3 xl:block">
        <div className="font-mono text-[9px] tracking-[0.12em] text-rail-muted uppercase">
          本地演示
        </div>
        <div className="mt-2 text-[10.5px] leading-relaxed text-[#858990]">
          面试、题库与能力档案保存在本地 SQLite。
        </div>
      </div>

      <div className="ml-auto shrink-0 md:ml-0 md:mt-3">
        <UserMenu />
      </div>
    </aside>
  );
}
