"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { getToken } from "@/lib/session";

/**
 * 客户端登录守卫：无 token 跳登录页（FR-23）。
 *
 * 读 localStorage 只能发生在客户端，所以判断在 useEffect 里 —— 期间先渲染载入态：
 * 直接渲染 children 会先闪出受保护内容再跳走（P1-M2 会话 2 补充点 ②）。
 * 服务端 middleware 方案要把 JWT 密钥透到 edge，demo 阶段无收益，不用。
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    if (getToken()) {
      setAuthed(true);
      return;
    }
    router.replace("/login");
  }, [router]);

  if (!authed) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center">
        <p className="text-sm text-muted-foreground">正在载入…</p>
      </div>
    );
  }
  return <>{children}</>;
}
