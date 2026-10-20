"use client";

import { ArrowRight, Eye, EyeOff } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MonoLabel } from "@/components/ui/mono-label";
import { StatusBanner } from "@/components/ui/status-banner";
import { login, register, validatePassword, validateUsername } from "@/lib/auth";
import { getToken, isStorageAvailable, setToken } from "@/lib/session";
import { cn } from "@/lib/utils";

type Mode = "login" | "register";

const MODE_LABELS: Record<Mode, string> = { login: "登录", register: "注册" };

const MODE_COPY: Record<
  Mode,
  { heading: string; description: string; submit: string }
> = {
  login: {
    heading: "登录你的训练台",
    description: "继续上次的面试，或开始一场新的训练。",
    submit: "登录",
  },
  register: {
    heading: "创建你的账号",
    description: "面试记录、能力档案与学习推荐都会保存在这个账号下。",
    submit: "注册并进入",
  },
};

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
  const [showPassword, setShowPassword] = useState(false);
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
  const copy = MODE_COPY[mode];

  return (
    <div className="flex flex-col">
      <h2 className="text-[26px] leading-tight font-semibold tracking-[-0.035em]">
        {copy.heading}
      </h2>
      <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
        {copy.description}
      </p>

      <div
        role="tablist"
        aria-label="登录与注册"
        className="mt-7 flex gap-6 border-b border-border"
      >
        {(Object.keys(MODE_LABELS) as Mode[]).map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={item === mode}
            onClick={() => switchMode(item)}
            className={cn(
              "relative pb-2.5 text-xs transition-colors",
              item === mode
                ? "font-medium text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {MODE_LABELS[item]}
            {item === mode ? (
              <span
                aria-hidden
                className="absolute right-0 -bottom-px left-0 h-0.5 bg-foreground"
              />
            ) : null}
          </button>
        ))}
      </div>

      <form className="mt-5 flex flex-col gap-4" onSubmit={handleSubmit}>
        <label className="flex flex-col gap-2">
          <MonoLabel>用户名</MonoLabel>
          <Input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoFocus
            disabled={disabled}
            placeholder="3 到 32 位字母、数字或下划线"
          />
        </label>

        <label className="flex flex-col gap-2">
          <MonoLabel>密码</MonoLabel>
          <div className="relative">
            <Input
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              disabled={disabled}
              placeholder="至少 6 位"
              className="pr-10"
            />
            <button
              type="button"
              onClick={() => setShowPassword((value) => !value)}
              aria-label={showPassword ? "隐藏密码" : "显示密码"}
              className="absolute top-1/2 right-1.5 flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {showPassword ? (
                <EyeOff className="size-4" strokeWidth={1.8} />
              ) : (
                <Eye className="size-4" strokeWidth={1.8} />
              )}
            </button>
          </div>
        </label>

        {error ? (
          <p
            role="alert"
            className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs leading-relaxed text-destructive"
          >
            {error}
          </p>
        ) : null}

        {!storageOk ? (
          <StatusBanner tone="error">
            当前浏览器环境禁用了本地存储（常见于隐私/无痕模式），无法保存登录状态。
          </StatusBanner>
        ) : null}

        <Button
          type="submit"
          size="lg"
          disabled={disabled}
          className="mt-1 w-full justify-between px-4"
        >
          <span className="flex items-center gap-2">
            <ArrowRight className="size-4" strokeWidth={1.8} />
            {busy ? "处理中…" : copy.submit}
          </span>
          <span className="font-mono text-[9px] tracking-[0.08em] opacity-70">
            ENTER
          </span>
        </Button>
      </form>

      <p className="mt-4 text-[11.5px] leading-relaxed text-muted-foreground">
        {mode === "login" ? "还没有账号？" : "已有账号？"}
        <button
          type="button"
          onClick={() => switchMode(mode === "login" ? "register" : "login")}
          className="ml-1 text-primary underline underline-offset-2 hover:text-primary-deep"
        >
          {mode === "login" ? "注册" : "登录"}
        </button>
        {mode === "login"
          ? "，历史面试与能力档案会归入你的账号。"
          : "，用户名注册后不可修改。"}
      </p>
    </div>
  );
}
