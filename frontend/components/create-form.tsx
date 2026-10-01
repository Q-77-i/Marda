"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { createInterview, dispatcher, getBankCapacity, type CapacityOption } from "@/lib/api";
import { capacityFor, shortfallMessage } from "@/lib/bank";
import {
  BEHAVIORAL_MAX_QUESTIONS,
  DIFFICULTY_OPTIONS,
  INTERVIEW_TYPE_OPTIONS,
  POSITION,
  QUESTION_COUNT_OPTIONS,
  domainLabel,
  isBehavioral,
  questionCountOptions,
} from "@/lib/constants";

/**
 * 创建面试表单（决策 1A）：提交后跟完开场流再跳转面试页，
 * 等待期间把面试官开场白流式显示出来，避免"点了没反应"。
 *
 * 难度（P1-M6 FR-14）：自适应 / L1 / L2 / L3；题库直供不足的题量禁用并给出缺在哪。
 * 会话类型（P1-M11 FR-22）：技术面 / 行为面——行为面**隐藏难度与容量**（行为题的 L1-L3 是
 * 技术深度语义，题库也不按域配额供题），题量上限 10（池子只有十来道，再多当场耗尽走 LLM 兜底）。
 */
export function CreateForm() {
  const router = useRouter();
  const [interviewType, setInterviewType] = useState<string>(INTERVIEW_TYPE_OPTIONS[0].value);
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

  const behavioral = isBehavioral(interviewType);
  const countOptions = questionCountOptions(interviewType);
  // 行为面直接跳过容量判定：容量按「难度 × 域配额」算，与行为面题源无关（服务端也不拦）
  const blocked = behavioral
    ? []
    : countOptions.map((option) => ({
        count: option,
        reason: shortfallMessage(capacityFor(capacity, difficulty, option), domainLabel),
      })).filter((item) => item.reason !== null);
  const currentBlocked = blocked.some((item) => item.count === count);

  function pickType(value: string) {
    setInterviewType(value);
    setError(null);
    // 行为面题量上限 10：从技术面切过来时把 15 收回来，否则会撞 422
    if (isBehavioral(value) && count > BEHAVIORAL_MAX_QUESTIONS) setCount(BEHAVIORAL_MAX_QUESTIONS);
  }

  async function handleStart() {
    setBusy(true);
    setError(null);
    setOpening("");
    try {
      const interviewId = await createInterview(
        POSITION,
        count,
        // 行为面不消费难度（D3）：仍发 adaptive，state 里照常自适应但只作死数据
        behavioral ? DIFFICULTY_OPTIONS[0].value : difficulty,
        dispatcher({
          delta: ({ text }) => setOpening((prev) => prev + text),
          error: ({ message }) => setError(message),
        }),
        interviewType,
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
          <span className="text-xs font-medium text-muted-foreground">面试类型</span>
          <div className="grid grid-cols-2 gap-2" role="group" aria-label="面试类型">
            {INTERVIEW_TYPE_OPTIONS.map((option) => (
              <Button
                key={option.value}
                type="button"
                variant={option.value === interviewType ? "default" : "outline"}
                aria-pressed={option.value === interviewType}
                disabled={busy}
                title={option.hint}
                onClick={() => pickType(option.value)}
              >
                {option.label}
              </Button>
            ))}
          </div>
          <span className="text-xs text-muted-foreground">
            {INTERVIEW_TYPE_OPTIONS.find((o) => o.value === interviewType)?.hint}
          </span>
        </div>

        {!behavioral && (
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
        )}

        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium text-muted-foreground">题目数量</span>
          <div className="grid grid-cols-3 gap-2" role="group" aria-label="题目数量">
            {countOptions.map((option) => {
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
