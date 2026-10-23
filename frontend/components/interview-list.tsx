"use client";

import { ArrowRight, ArrowUpRight, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { MonoLabel } from "@/components/ui/mono-label";
import { ListSkeleton } from "@/components/ui/skeleton";
import { StatusBanner } from "@/components/ui/status-banner";
import { StatusDot } from "@/components/ui/status-dot";
import { deleteInterview, listInterviews, type InterviewRow } from "@/lib/api";
import { difficultyLabel, interviewTypeLabel, isBehavioral } from "@/lib/constants";
import { formatDuration, formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/** 历史面试列表：进行中 → 续面，已完成 → 报告；每条可物理删除（T7a-R1）。 */
export function InterviewList({
  onRowsChange,
  limit,
  showHeader = true,
}: {
  /** 行数据变化（首次加载 / 删除）时通知外层，统计条与列表共用同一份数据 */
  onRowsChange?: (rows: InterviewRow[]) => void;
  /** 仪表盘只展示最近几场，完整列表在面试记录页；统计仍用全量行 */
  limit?: number;
  /** 独立页面（面试记录页）自带页头时关掉卡片头，否则标题重复 */
  showHeader?: boolean;
}) {
  const [rows, setRows] = useState<InterviewRow[] | null>(null);
  // 加载失败（列表没内容可显示）与删除失败（列表还在）分开：后者不该吃掉整块面板
  const [loadError, setLoadError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const onRowsChangeRef = useRef(onRowsChange);

  useEffect(() => {
    onRowsChangeRef.current = onRowsChange;
  }, [onRowsChange]);

  useEffect(() => {
    let active = true;
    setLoadError(null);
    listInterviews()
      .then((data) => {
        if (active) {
          setRows(data);
          onRowsChangeRef.current?.(data);
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setRows(null);
          setLoadError(err instanceof Error ? err.message : "加载失败");
        }
      });
    return () => {
      active = false;
    };
  }, [reloadKey]);

  const visibleRows = rows && limit ? rows.slice(0, limit) : rows;

  async function handleDelete(id: string) {
    setDeletingId(id);
    setDeleteError(null);
    try {
      await deleteInterview(id);
      const next = rows ? rows.filter((row) => row.id !== id) : rows;
      setRows(next);
      if (next) onRowsChangeRef.current?.(next);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "删除失败，请重试");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <Card>
      {showHeader ? (
        <CardHeader>
          <div className="min-w-0">
            <CardTitle>面试记录</CardTitle>
            <p className="mt-1 text-[11px] text-muted-foreground">
              进行中的场次可以随时续面
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <MonoLabel className="hidden sm:inline">Sessions</MonoLabel>
            <Link
              href="/interviews"
              className="inline-flex h-[30px] items-center gap-1 rounded-md px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              全部场次
              <ArrowUpRight className="size-3.5" strokeWidth={1.8} />
            </Link>
          </div>
        </CardHeader>
      ) : null}

      {deleteError ? (
        <div className="px-[18px] pt-3">
          <StatusBanner tone="error">{deleteError}</StatusBanner>
        </div>
      ) : null}

      {loadError ? (
        <div className="p-[18px]">
          <ErrorState
            message={loadError}
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
        </div>
      ) : rows === null ? (
        <div className="p-[18px]">
          <ListSkeleton rowClassName="h-12 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          className="py-12"
          title="还没有面试记录"
          description="创建一场面试后，这里会列出全部场次"
        />
      ) : (
        <ul className="flex flex-col">
          {(visibleRows ?? []).map((row) => {
            const finished = row.status === "finished";
            const duration = formatDuration(row.started_at, row.ended_at);
            const behavioral = isBehavioral(row.interview_type);
            return (
              <li
                key={row.id}
                className="group relative flex items-center gap-2 border-t border-border px-[18px] py-3 transition-colors first:border-t-0 hover:bg-muted/40"
              >
                {!finished ? (
                  <span
                    aria-hidden
                    className="absolute top-2.5 bottom-2.5 left-0 w-0.5 bg-primary"
                  />
                ) : null}
                <Link
                  href={
                    finished ? `/report/${row.id}` : `/interview/${row.id}`
                  }
                  className="flex min-w-0 flex-1 items-center gap-3 outline-none focus-visible:rounded-md focus-visible:ring-2 focus-visible:ring-ring/30"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-[12.5px] font-medium">
                        {row.position}
                      </span>
                      <Badge variant={behavioral ? "secondary" : "outline"}>
                        {interviewTypeLabel(row.interview_type)}
                      </Badge>
                    </div>
                    <div className="mt-1 truncate font-mono text-[9.5px] text-muted-foreground">
                      {row.question_count} 题
                      {/* 行为面不展示难度（P1-M11 D3）：难度不参与行为面出题，是死数据 */}
                      {!behavioral ? ` / ${difficultyLabel(row.difficulty)}` : ""}
                      {duration ? ` / ${duration}` : ""}
                    </div>
                  </div>
                  <span className="hidden shrink-0 font-mono text-[9.5px] text-muted-foreground sm:block">
                    {formatTime(row.started_at)}
                  </span>
                  <span
                    className={cn(
                      "flex shrink-0 items-center gap-1.5 font-mono text-[9.5px] tracking-[0.04em]",
                      finished ? "text-success" : "text-primary",
                    )}
                  >
                    <StatusDot />
                    {finished ? "已完成" : "进行中"}
                  </span>
                  <ArrowRight
                    className="size-4 shrink-0 text-muted-foreground/60 transition-transform group-hover:translate-x-0.5 group-hover:text-primary"
                    strokeWidth={1.8}
                  />
                </Link>

                <AlertDialog>
                  <AlertDialogTrigger
                    render={
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="shrink-0 text-muted-foreground opacity-100 hover:text-destructive sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100"
                        aria-label={`删除这场面试（${row.question_count} 题）`}
                      />
                    }
                  >
                    <Trash2 className="size-3.5" strokeWidth={1.8} />
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>删除这场面试？</AlertDialogTitle>
                      <AlertDialogDescription>
                        面试记录、逐题回答与报告将被永久删除，无法恢复
                        {finished ? "" : "（含进行中的会话）"}。
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>取消</AlertDialogCancel>
                      <AlertDialogAction
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        disabled={deletingId !== null}
                        onClick={() => handleDelete(row.id)}
                      >
                        {deletingId === row.id ? "删除中…" : "确认删除"}
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
