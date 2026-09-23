"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { fetchMe, type Me } from "@/lib/auth";
import { clearToken } from "@/lib/session";

/**
 * 头部用户区（FR-23）：显示当前用户 + 登出。
 *
 * 只挂仪表盘头部 —— 面试页/报告页的 right 位已被进度与操作占满，不挤占布局。
 * token 失效时 fetchMe 内部走全局 401 处置（清 token + 跳登录），此处无需兜底。
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

  return (
    <div className="flex items-center gap-2">
      {me && (
        <span className="max-w-[8rem] truncate text-xs text-muted-foreground">
          {me.username}
        </span>
      )}
      <Button variant="ghost" size="sm" onClick={handleLogout}>
        退出登录
      </Button>
    </div>
  );
}
