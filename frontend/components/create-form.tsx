"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { createInterview, dispatcher, getBankCapacity, type CapacityOption } from "@/lib/api";
import { capacityFor, shortfallMessage } from "@/lib/bank";
import {
  DIFFICULTY_OPTIONS,
  POSITION,
  QUESTION_COUNT_OPTIONS,
  domainLabel,
} from "@/lib/constants";

/**
 * 创建面试表单（决策 1A）：提交后跟完开场流再跳转面试页，
 * 等待期间把面试官开场白流式显示出来，避免"点了没反应"。
 *
 * 难度（P1-M6 FR-14）：自适应 / L1 / L2 / L3；题库直供不足的题量禁用并给出缺在哪。
 */
export function CreateForm() {
  const router = useRouter();
  const [count, setCount] = useState<number>(10);
  const [difficulty, setDifficulty] = useState<string>(DIFFICULTY_OPTIONS[0].value);
  const [capacity, setCapacity] = useState<CapacityOption[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBankCapacity([...QUESTION_COUNT_OPTIONS])
      .then((body) => setCapacity(body.options))
      .catch(() => setCapacity(null)); // 拉不到容量不禁用任何选项（服务端不拦，别自锁）
  }, []);

  const blocked = QUESTION_COUNT_OPTIONS.map((option) => ({
    count: option,
    reason: shortfallMessage(capacityFor(capacity, difficulty, option), domainLabel),
  })).filter((item) => item.reason !== null);
  const currentBlocked = blocked.some((item) => item.count === count);

  async function handleStart() {
    setBusy(true);
    setError(null);
    setOpening("");
    try {
      const interviewId = await createInterview(
        POSITION,
        count,
        difficulty,
        dispatcher({
          delta: ({ text }) => setOpening((prev) => prev + text),
          error: ({ message }) => setError(message),
        }),
      );
      router.push(`/interview/${interviewId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败，请重试");
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>新建模拟面试</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium text-muted-foreground">岗位方向</span>
          <div className="rounded-lg border bg-muted/40 px-3 py-2 text-sm">
            {POSITION}
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium text-muted-foreground">难度</span>
          <div className="grid grid-cols-2 gap-2" role="group" aria-label="难度">
            {DIFFICULTY_OPTIONS.map((option) => (
              <Button
                key={option.value}
                type="button"
                variant={option.value === difficulty ? "default" : "outline"}
                aria-pressed={option.value === difficulty}
                disabled={busy}
                title={option.hint}
                onClick={() => setDifficulty(option.value)}
              >
                {option.label}
              </Button>
            ))}
          </div>
          <span className="text-xs text-muted-foreground">
            {DIFFICULTY_OPTIONS.find((o) => o.value === difficulty)?.hint}
          </span>
        </div>

        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium text-muted-foreground">题目数量</span>
          <div className="grid grid-cols-3 gap-2" role="group" aria-label="题目数量">
            {QUESTION_COUNT_OPTIONS.map((option) => {
              const reason = blocked.find((item) => item.count === option)?.reason ?? null;
              return (
                <Button
                  key={option}
                  type="button"
                  variant={option === count ? "default" : "outline"}
                  aria-pressed={option === count}
                  disabled={busy || reason !== null}
                  title={reason ?? undefined}
                  onClick={() => setCount(option)}
                >
                  {option} 题
                </Button>
              );
            })}
          </div>
          {blocked.map((item) => (
            <span key={item.count} className="text-xs text-destructive" role="status">
              {item.count} 题不可选 —— {item.reason}
            </span>
          ))}
        </div>

        <Button size="lg" disabled={busy || currentBlocked} onClick={handleStart}>
          {busy ? "正在准备面试…" : "开始面试"}
        </Button>

        {busy && (
          <div className="rounded-lg border bg-muted/40 p-3 text-sm leading-relaxed text-muted-foreground">
            {opening || "面试官正在准备开场…"}
          </div>
        )}

        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
