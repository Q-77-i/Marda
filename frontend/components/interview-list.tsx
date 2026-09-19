"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { listInterviews, type InterviewRow } from "@/lib/api";
import { formatDuration, formatTime } from "@/lib/format";

/** 历史面试列表：进行中 → 续面，已完成 → 报告。 */
export function InterviewList() {
  const [rows, setRows] = useState<InterviewRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

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
                <li key={row.id}>
                  <Link
                    href={finished ? `/report/${row.id}` : `/interview/${row.id}`}
                    className="flex items-center justify-between gap-4 py-3 transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
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
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
