"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { deleteInterview, listInterviews, type InterviewRow } from "@/lib/api";
import { formatDuration, formatTime } from "@/lib/format";

/** 历史面试列表：进行中 → 续面，已完成 → 报告；每条可物理删除（T7a-R1）。 */
export function InterviewList() {
  const [rows, setRows] = useState<InterviewRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listInterviews()
      .then((data) => {
        if (active) setRows(data);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : "加载失败");
      });
    return () => {
      active = false;
    };
  }, []);

  async function handleDelete(id: string) {
    setDeletingId(id);
    setError(null);
    try {
      await deleteInterview(id);
      setRows((prev) => (prev ? prev.filter((row) => row.id !== id) : prev));
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败，请重试");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>面试记录</CardTitle>
      </CardHeader>
      <CardContent>
        {error ? (
          <p className="py-6 text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : rows === null ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center gap-1 py-12 text-center">
            <p className="text-sm font-medium">还没有面试记录</p>
            <p className="text-sm text-muted-foreground">
              从左侧创建一场面试，开始你的第一次模拟
            </p>
          </div>
        ) : (
          <ul className="divide-y">
            {rows.map((row) => {
              const finished = row.status === "finished";
              const duration = formatDuration(row.started_at, row.ended_at);
              return (
                <li key={row.id} className="flex items-center gap-2 py-3">
                  <Link
                    href={finished ? `/report/${row.id}` : `/interview/${row.id}`}
                    className="flex min-w-0 flex-1 items-center justify-between gap-4 transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
                  >
                    <div className="flex min-w-0 flex-col gap-1">
                      <span className="truncate text-sm font-medium">{row.position}</span>
                      <span className="tabular text-xs text-muted-foreground">
                        {row.question_count} 题
                        {duration ? ` · ${duration}` : ""}
                      </span>
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <span className="tabular text-xs text-muted-foreground">
                        {formatTime(row.started_at)}
                      </span>
                      <Badge variant={finished ? "secondary" : "default"}>
                        {finished ? "已完成" : "进行中"}
                      </Badge>
                    </div>
                  </Link>
                  <AlertDialog>
                    <AlertDialogTrigger
                      render={
                        <Button
                          variant="ghost"
                          size="sm"
                          className="shrink-0 text-destructive"
                          aria-label={`删除这场面试（${row.question_count} 题）`}
                        />
                      }
                    >
                      删除
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
                          className="bg-destructive text-white hover:bg-destructive/90"
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
      </CardContent>
    </Card>
  );
}
