"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { RecommendGroups } from "@/components/recommend-groups";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { listInterviews, type InterviewRow } from "@/lib/api";
import { cn } from "@/lib/utils";
import { finishedSessions, interviewOptionLabel, pickDefaultInterview } from "@/lib/learn";

/**
 * 学习页（FR-20）：选一场已结束的面试 → 看它暴露的短板域与该域的新材料。
 *
 * 默认场次优先取 URL 带过来的（从报告页「查看全部推荐」跳进来，承接来源），
 * 否则最近一场；两处判定都在 lib/learn.ts（纯函数，vitest 覆盖）。
 */
export function LearnClient({ requestedInterviewId }: { requestedInterviewId: string | null }) {
  const [rows, setRows] = useState<InterviewRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    listInterviews()
      .then((data) => {
        if (active) setRows(finishedSessions(data));
      })
      .catch((err: unknown) => {
        if (active) {
          setRows(null);
          setError(err instanceof Error ? err.message : "场次加载失败");
        }
      });
    return () => {
      active = false;
    };
  }, [reloadKey]);

  const current = rows ? (selected ?? pickDefaultInterview(rows, requestedInterviewId)) : null;

  return (
    <>
      <PageHeader
        title="学习推荐"
        description="按某一场面试暴露的短板域，给该域的针对性练习"
      />

      {error ? (
        <Card>
          <CardContent>
            <ErrorState
              message={error}
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
      ) : rows === null ? (
        <div className="flex flex-col gap-6">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <Card>
          <CardContent>
            <EmptyState
              title="还没有已结束的面试"
              description="先完成一场模拟面试，拿到报告里的短板定位，这里就会给出针对性的练习题。"
              action={
                <Link href="/" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
                  去开始一场面试
                </Link>
              }
            />
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardContent className="flex flex-col gap-4">
              {rows.length > 1 ? (
                <label className="flex items-center gap-2 text-sm">
                  <span className="shrink-0 text-muted-foreground">面试场次</span>
                  <select
                    className="h-9 w-full max-w-md rounded-md border bg-transparent px-2 text-sm"
                    value={current ?? ""}
                    onChange={(event) => setSelected(event.target.value)}
                  >
                    {rows.map((row) => (
                      <option key={row.id} value={row.id}>
                        {interviewOptionLabel(row)}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {rows[0] ? interviewOptionLabel(rows[0]) : ""}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                推荐按该场报告的短板域现检索题库——每次打开都是最新的题，本场已问过的不再重复推荐。
              </p>
            </CardContent>
          </Card>

          {current ? (
            <>
              <RecommendGroups interviewId={current} />
              <div>
                <Link
                  href={`/report/${current}`}
                  className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
                >
                  查看该场完整报告
                </Link>
              </div>
            </>
          ) : (
            <Button variant="outline" size="sm" disabled>
              暂无可用场次
            </Button>
          )}
        </>
      )}
    </>
  );
}
