"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { MonoLabel } from "@/components/ui/mono-label";
import { Skeleton } from "@/components/ui/skeleton";
import { domainLabel } from "@/lib/constants";
import { formatTime } from "@/lib/format";
import {
  dueLabel,
  nextTaskStatus,
  planProgress,
  planStatusLabel,
  recommendationHref,
  taskStatusLabel,
  taskStatusTone,
  type LearningPlan,
  type LearningTask,
  type TaskStatus,
  type TodayTasks,
} from "@/lib/learning";
import {
  getTodayTasks,
  LearningApiError,
  listLearningPlans,
  updateLearningTask,
} from "@/lib/learning-api";
import { cn } from "@/lib/utils";

/**
 * 学习计划面板（P5-J3 / J3-D）：计划进度 + 任务状态 + 今日任务。
 *
 * 与 Python 推荐各自加载：学习服务不可用时只收起本面板，推荐与面试不受影响。
 * 任务操作带 version；409 时提示并自动刷新，避免用户拿着旧版本继续点。
 */
export function PlanPanel({ sourceInterviewId }: { sourceInterviewId: string | null }) {
  const [plans, setPlans] = useState<LearningPlan[] | null>(null);
  const [today, setToday] = useState<TodayTasks | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    Promise.all([listLearningPlans(), getTodayTasks()])
      .then(([page, todayTasks]) => {
        if (!active) return;
        setPlans(page.items);
        setToday(todayTasks);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setPlans(null);
        setToday(null);
        setError(err instanceof Error ? err.message : "学习计划暂不可用");
      });
    return () => {
      active = false;
    };
  }, [reloadKey]);

  async function onTaskAction(task: LearningTask, status: TaskStatus) {
    setBusyTaskId(task.id);
    setActionError(null);
    try {
      await updateLearningTask(task.id, { status, version: task.version });
      setReloadKey((key) => key + 1);
    } catch (err: unknown) {
      if (err instanceof LearningApiError && err.code === "VERSION_CONFLICT") {
        setActionError("任务已被更新，已为你刷新");
        setReloadKey((key) => key + 1);
      } else {
        setActionError(err instanceof Error ? err.message : "更新任务失败");
      }
    } finally {
      setBusyTaskId(null);
    }
  }

  const loading = plans === null && error === null;
  const plansById = new Map((plans ?? []).map((plan) => [plan.id, plan]));

  return (
    <section className="flex flex-col gap-3" aria-label="学习计划">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <MonoLabel>Learning plan</MonoLabel>
          <h2 className="mt-1 text-lg font-semibold tracking-[-0.01em]">
            我的学习计划
          </h2>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setReloadKey((key) => key + 1)}
        >
          刷新
        </Button>
      </div>

      {actionError ? (
        <p className="text-sm text-destructive" role="alert">
          {actionError}
        </p>
      ) : null}

      {loading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-16 w-full" />
          </CardContent>
        </Card>
      ) : error ? (
        <Card>
          <CardContent>
            {/* 学习服务不可用是空态，不是整页错误：面试与推荐仍正常，别让用户以为系统坏了。 */}
            <EmptyState
              title="学习计划暂不可用"
              description={`${error}。面试与推荐不受影响。`}
              action={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setReloadKey((key) => key + 1)}
                >
                  重试
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : plans && plans.length > 0 ? (
        <div className="flex flex-col gap-3">
          {plans.map((plan) => (
            <PlanCard
              key={plan.id}
              plan={plan}
              busyTaskId={busyTaskId}
              onAction={onTaskAction}
            />
          ))}
        </div>
      ) : (
        <Card>
          <CardContent>
            <EmptyState
              title="还没有学习计划"
              description="打开一场已结束的技术面报告，点击「生成学习计划」，把短板变成可执行的每日任务。"
              action={
                sourceInterviewId ? (
                  <Link
                    href={`/report/${sourceInterviewId}`}
                    className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
                  >
                    查看当前场次报告
                  </Link>
                ) : undefined
              }
            />
          </CardContent>
        </Card>
      )}

      {today ? (
        <Card>
          <CardHeader>
            <CardTitle>今日任务</CardTitle>
            <MonoLabel>{today.date}</MonoLabel>
          </CardHeader>
          <CardContent>
            {today.tasks.length === 0 ? (
              <p className="text-sm text-muted-foreground">今天没有到期任务。</p>
            ) : (
              <ul className="flex flex-col">
                {today.tasks.map((task) => {
                  const plan = plansById.get(task.plan_id);
                  return (
                    <TaskRow
                      key={task.id}
                      task={task}
                      busy={busyTaskId === task.id}
                      recommendationHref={
                        plan
                          ? recommendationHref(
                              plan.source_interview_id,
                              task.domain,
                            )
                          : null
                      }
                      onAction={onTaskAction}
                    />
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      ) : null}
    </section>
  );
}

function PlanCard({
  plan,
  busyTaskId,
  onAction,
}: {
  plan: LearningPlan;
  busyTaskId: string | null;
  onAction: (task: LearningTask, status: TaskStatus) => void;
}) {
  const progress = planProgress(plan);
  return (
    <Card>
      <CardHeader>
        <div className="min-w-0">
          <CardTitle>{plan.goal}</CardTitle>
          <p className="mt-1 font-mono text-[10px] text-muted-foreground">
            创建于 {formatTime(plan.created_at)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge variant={plan.status === "completed" ? "success" : "default"}>
            {planStatusLabel(plan.status)}
          </Badge>
          <MonoLabel>
            已处理 {progress.handled}/{progress.total}
          </MonoLabel>
        </div>
      </CardHeader>
      <CardContent>
        <div
          className="mb-2 h-1.5 w-full overflow-hidden rounded-full bg-muted"
          aria-hidden
        >
          <div
            className="h-full rounded-full bg-primary transition-[width]"
            style={{ width: `${progress.percent}%` }}
          />
        </div>
        <ul className="flex flex-col">
          {plan.tasks.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              busy={busyTaskId === task.id}
              recommendationHref={recommendationHref(
                plan.source_interview_id,
                task.domain,
              )}
              onAction={onAction}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function TaskRow({
  task,
  busy,
  recommendationHref,
  onAction,
}: {
  task: LearningTask;
  busy: boolean;
  recommendationHref: string | null;
  onAction: (task: LearningTask, status: TaskStatus) => void;
}) {
  const next = nextTaskStatus(task.status);
  const terminal = task.status === "done" || task.status === "skipped";
  return (
    <li className="flex flex-wrap items-center gap-x-2.5 gap-y-2 border-t border-border/60 py-2.5 first:border-t-0">
      <Badge variant="outline">{domainLabel(task.domain)}</Badge>
      <span
        className={cn(
          "min-w-0 flex-1 text-sm",
          terminal && "text-muted-foreground",
          task.status === "done" && "line-through",
        )}
      >
        {task.title}
      </span>
      <span className="font-mono text-[10px] text-muted-foreground">
        {dueLabel(task.due_at)}
      </span>
      <Badge variant={taskStatusTone(task.status)}>
        {taskStatusLabel(task.status)}
      </Badge>
      {recommendationHref ? (
        <Link
          href={recommendationHref}
          className={cn(buttonVariants({ variant: "ghost", size: "xs" }))}
        >
          查看资料
        </Link>
      ) : null}
      {next ? (
        <Button
          size="xs"
          variant="outline"
          disabled={busy}
          onClick={() => onAction(task, next)}
        >
          {busy ? "更新中…" : task.status === "todo" ? "开始" : "完成"}
        </Button>
      ) : null}
      {!terminal ? (
        <Button
          size="xs"
          variant="ghost"
          disabled={busy}
          onClick={() => onAction(task, "skipped")}
        >
          跳过
        </Button>
      ) : null}
    </li>
  );
}
