"use client";

import { cn } from "cn";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { login, register, validatePassword, validateUsername } from "@/lib/auth";
import { getToken, isStorageAvailable, setToken } from "@/lib/session";

type Mode = "login" | "register";

const MODE_LABELS: Record<Mode, string> = { login: "登录", register: "注册" };

/**
 * 登录 / 注册表单（FR-23）：单页双 tab。
 *
 * - 已登录直接回首页（token 存在即视为已登录，失效由后续请求的 401 兜底）；
 * - 切 tab 清错误提示但保留输入（切 tab 多是「重名了换个名字再试」，重打更烦）；
 * - 后端错误文案（401 用户名或密码错误 / 409 用户名已存在）原样展示，不触发全局跳转；
 * - 存储不可用（隐私模式等）时禁用提交并明确提示，不静默失败。
 */
export function LoginForm() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [storageOk, setStorageOk] = useState(true);

  useEffect(() => {
    setStorageOk(isStorageAvailable());
    if (getToken()) router.replace("/");
  }, [router]);

  function switchMode(next: Mode) {
    if (next === mode) return;
    setMode(next);
    setError(null); // 上一个 tab 的报错留在新 tab 上会让人困惑
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    const invalid = validateUsername(username) ?? validatePassword(password);
    if (invalid) {
      setError(invalid);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result =
        mode === "login"
          ? await login(username, password)
          : await register(username, password);
      if (!setToken(result.token)) {
        setError("当前浏览器环境无法保存登录状态，请关闭隐私模式后重试");
        setBusy(false);
        return;
      }
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "请求失败，请重试");
      setBusy(false);
    }
  }

  const disabled = busy || !storageOk;

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>{mode === "login" ? "登录" : "注册"}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1" role="tablist">
          {(Object.keys(MODE_LABELS) as Mode[]).map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={item === mode}
              onClick={() => switchMode(item)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                item === mode
                  ? "bg-background shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {MODE_LABELS[item]}
            </button>
          ))}
        </div>

        <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
          <label className="flex flex-col gap-2">
            <span className="text-xs font-medium text-muted-foreground">用户名</span>
            <Input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              disabled={disabled}
              placeholder="3–32 位字母、数字或下划线"
            />
          </label>

          <label className="flex flex-col gap-2">
            <span className="text-xs font-medium text-muted-foreground">密码</span>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              disabled={disabled}
              placeholder="至少 6 位"
            />
          </label>

          {error && (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          )}

          {!storageOk && (
            <p
              className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
              role="alert"
            >
              当前浏览器环境禁用了本地存储（常见于隐私/无痕模式），无法保存登录状态。
            </p>
          )}

          <Button type="submit" size="lg" disabled={disabled}>
            {busy ? "处理中…" : MODE_LABELS[mode]}
          </Button>
        </form>

        <p className="text-xs text-muted-foreground">
          {mode === "login"
            ? "还没有账号？切换到「注册」，注册后历史记录会归入你的账号。"
            : "用户名注册后不可修改，密码请自行保管（demo 暂不支持找回）。"}
        </p>
      </CardContent>
    </Card>
  );
}
