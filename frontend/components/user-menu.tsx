"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { fetchMe, type Me } from "@/lib/auth";
import { clearToken } from "@/lib/session";

/**
 * 左栏用户区（FR-23）：显示当前用户 + 登出。
 *
 * token 失效时 fetchMe 内部走全局 401 处置（清 token + 跳登录），此处无需兜底。
 * 窄栏只留头像与登出图标，宽栏才展开用户名。
 */
export function UserMenu() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let active = true;
    fetchMe()
      .then((user) => {
        if (active) setMe(user);
      })
      .catch(() => {
        // 401 已由全局处置跳登录；其他失败（如后端未启动）只不显示用户名，不挡页面
      });
    return () => {
      active = false;
    };
  }, []);

  function handleLogout() {
    clearToken();
    router.replace("/login");
  }

  const initial = me?.username?.slice(0, 1).toUpperCase() ?? "M";

  return (
    <div className="flex items-center gap-2.5 md:border-t md:border-rail-line md:pt-3">
      <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-white/10 text-[10px] font-semibold text-rail-foreground">
        {initial}
      </span>
      <span className="hidden min-w-0 flex-1 xl:block">
        <span className="block truncate text-xs text-rail-foreground">
          {me?.username ?? "账号"}
        </span>
        <span className="mt-0.5 block font-mono text-[9px] tracking-[0.08em] text-rail-muted uppercase">
          Candidate
        </span>
      </span>
      <button
        type="button"
        onClick={handleLogout}
        aria-label="退出登录"
        title="退出登录"
        className="flex size-7 shrink-0 items-center justify-center rounded-md text-rail-muted transition-colors hover:bg-white/5 hover:text-white"
      >
        <LogOut className="size-4" strokeWidth={1.8} />
      </button>
    </div>
  );
}
