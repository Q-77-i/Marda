"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { createInterview, dispatcher } from "@/lib/api";
import { POSITION, QUESTION_COUNT_OPTIONS } from "@/lib/constants";

/**
 * 创建面试表单（决策 1A）：提交后跟完开场流再跳转面试页，
 * 等待期间把面试官开场白流式显示出来，避免"点了没反应"。
 */
export function CreateForm() {
  const router = useRouter();
  const [count, setCount] = useState<number>(10);
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleStart() {
    setBusy(true);
    setError(null);
    setOpening("");
    try {
      const interviewId = await createInterview(
        POSITION,
        count,
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
          <span className="text-xs font-medium text-muted-foreground">题目数量</span>
          <div className="grid grid-cols-3 gap-2" role="group" aria-label="题目数量">
            {QUESTION_COUNT_OPTIONS.map((option) => (
              <Button
                key={option}
                type="button"
                variant={option === count ? "default" : "outline"}
                aria-pressed={option === count}
                disabled={busy}
                onClick={() => setCount(option)}
              >
                {option} 题
              </Button>
            ))}
          </div>
        </div>

        <Button size="lg" disabled={busy} onClick={handleStart}>
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
