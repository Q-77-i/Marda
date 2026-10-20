"use client";

import {
  ArrowRight,
  ArrowUpRight,
  ChartNoAxesCombined,
  ChevronDown,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import { RecommendGroups } from "@/components/recommend-groups";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { MonoLabel } from "@/components/ui/mono-label";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusDot } from "@/components/ui/status-dot";
import {
  getReport,
  listInterviews,
  type InterviewRow,
  type RecommendationGroup,
  type ReportPayload,
} from "@/lib/api";
import {
  difficultyLabel,
  domainLabel,
  interviewTypeLabel,
  isBehavioral,
} from "@/lib/constants";
import { formatDuration, formatScore } from "@/lib/format";
import {
  finishedSessions,
  interviewOptionLabel,
  pickDefaultInterview,
} from "@/lib/learn";
import { cn } from "@/lib/utils";

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
  const [report, setReport] = useState<ReportPayload | null>(null);
  const [reportError, setReportError] = useState(false);
  const [groups, setGroups] = useState<RecommendationGroup[]>([]);
  const [groupsReady, setGroupsReady] = useState(false);

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
  const row = rows?.find((item) => item.id === current) ?? null;
  const behavioral = isBehavioral(row?.interview_type);

  useEffect(() => {
    if (!current) return;
    let active = true;
    setReport(null);
    setReportError(false);
    setGroups([]);
    setGroupsReady(false);
    getReport(current)
      .then((payload) => {
        if (active) setReport(payload.report);
      })
      .catch(() => {
        // 报告暂不可用只收起分数与短板区，推荐仍可独立加载
        if (active) setReportError(true);
      });
    return () => {
      active = false;
    };
  }, [current]);

  const overall =
    report && Object.keys(report.scores).length > 0
      ? (report.overall ??
        Object.values(report.scores).reduce((sum, value) => sum + value, 0) /
          Object.values(report.scores).length)
      : null;
  const weaknesses = report?.weaknesses ?? [];
  const firstGroup = groups[0] ?? null;
  const duration = row ? formatDuration(row.started_at, row.ended_at) : null;

  const page = (content: ReactNode) => (
    <>
      <PageHeader
        kicker="Learn / Recommended practice"
        title="学习推荐"
        description="按某一场面试暴露的短板域，给该域的针对性练习。"
        right={
          <Link
            href="/profile"
            className={cn(buttonVariants({ variant: "outline" }))}
          >
            <ChartNoAxesCombined className="size-4" strokeWidth={1.8} />
            能力档案
          </Link>
        }
      />
      {content}
    </>
  );

  if (error) {
    return page(
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
      </Card>,
    );
  }

  if (rows === null) {
    return page(
      <div className="flex flex-col gap-5">
        <Skeleton className="h-10 w-full max-w-md" />
        <Skeleton className="h-64 w-full" />
      </div>,
    );
  }

  if (rows.length === 0) {
    return page(
      <Card>
        <CardContent>
          <EmptyState
            title="还没有已结束的面试"
            description="先完成一场模拟面试，拿到报告里的短板定位，这里就会给出针对性的练习题。"
            action={
              <Link
                href="/"
                className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
              >
                去开始一场面试
              </Link>
            }
          />
        </CardContent>
      </Card>,
    );
  }

  return page(
    <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)] xl:items-start">
      <div className="flex flex-col gap-5">
        <Card>
          <CardHeader>
            <CardTitle>来源场次</CardTitle>
            <MonoLabel>Source session</MonoLabel>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {rows.length > 1 ? (
              <label className="flex flex-col gap-2">
                <MonoLabel>面试场次</MonoLabel>
                <div className="relative">
                  <select
                    value={current ?? ""}
                    onChange={(event) => setSelected(event.target.value)}
                    className="h-10 w-full appearance-none rounded-lg border border-input bg-card px-3 pr-8 text-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20"
                  >
                    {rows.map((item) => (
                      <option key={item.id} value={item.id}>
                        {interviewOptionLabel(item)}
                      </option>
                    ))}
                  </select>
                  <ChevronDown
                    className="pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
                    strokeWidth={1.8}
                  />
                </div>
              </label>
            ) : (
              <p className="text-xs text-muted-foreground">
                {rows[0] ? interviewOptionLabel(rows[0]) : ""}
              </p>
            )}

            <div className="rounded-lg border border-border bg-muted/40 p-3.5">
              <div className="flex items-center justify-between gap-2">
                <Badge variant={behavioral ? "secondary" : "outline"}>
                  {interviewTypeLabel(row?.interview_type)}
                </Badge>
                <span className="flex items-center gap-1.5 font-mono text-[9.5px] text-success">
                  <StatusDot />
                  已完成
                </span>
              </div>

              <div className="mt-3 flex items-end gap-1.5">
                {reportError ? (
                  <span className="text-xs text-muted-foreground">报告暂不可用</span>
                ) : report === null ? (
                  <Skeleton className="h-8 w-20" />
                ) : overall !== null ? (
                  <>
                    <span className="text-[34px] leading-none font-semibold tracking-[-0.04em]">
                      {formatScore(overall)}
                    </span>
                    <span className="pb-1 font-mono text-[9px] leading-relaxed text-muted-foreground">
                      / 5
                      <br />
                      总分
                    </span>
                  </>
                ) : (
                  <span className="text-xs text-muted-foreground">暂无评分</span>
                )}
              </div>

              <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 border-t border-border pt-3">
                <SummaryStat label="题量" value={`${row?.question_count ?? 0}`} />
                <SummaryStat label="用时" value={duration ?? "暂无"} />
                <SummaryStat
                  label="难度"
                  value={behavioral ? "不适用" : difficultyLabel(row?.difficulty ?? "")}
                />
                <SummaryStat
                  label="完成"
                  value={`${report?.answered_count ?? row?.question_count ?? 0} / ${
                    row?.question_count ?? 0
                  }`}
                />
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <MonoLabel>本场短板域</MonoLabel>
              <div className="flex flex-wrap gap-1.5">
                {reportError ? (
                  <span className="text-[11px] text-muted-foreground">
                    报告暂不可用，短板域未知。
                  </span>
                ) : report === null ? (
                  <Skeleton className="h-5 w-28" />
                ) : weaknesses.length > 0 ? (
                  weaknesses.map((domain) => (
                    <Badge key={domain} variant="warning">
                      {domainLabel(domain)}
                    </Badge>
                  ))
                ) : (
                  <span className="text-[11px] text-muted-foreground">
                    报告未定位到短板域。
                  </span>
                )}
              </div>
            </div>

            {current ? (
              <Link
                href={`/report/${current}`}
                className={cn(buttonVariants({ variant: "outline" }), "w-full")}
              >
                查看该场完整报告
                <ArrowUpRight className="size-3.5" strokeWidth={1.8} />
              </Link>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>训练建议</CardTitle>
            <MonoLabel>Next step</MonoLabel>
          </CardHeader>
          <CardContent className="flex h-full flex-col gap-2.5">
            {!groupsReady ? (
              <>
                <Skeleton className="h-3 w-20" />
                <Skeleton className="h-5 w-36" />
                <Skeleton className="h-12 w-full" />
              </>
            ) : firstGroup ? (
              <>
                <span className="font-mono text-[9px] tracking-[0.12em] text-muted-foreground uppercase">
                  优先补强
                </span>
                <h3 className="text-base font-semibold tracking-[-0.02em]">
                  {domainLabel(firstGroup.domain)}
                </h3>
                <p className="text-[11.5px] leading-relaxed text-muted-foreground">
                  {firstGroup.advice ||
                    "右侧题目按本场报告现检索，已问过的不再重复。先做该域的题，再回来看短板是否变化。"}
                </p>
                <Link
                  href="/bank"
                  className="mt-auto inline-flex h-[30px] items-center gap-1 self-start rounded-md border border-border px-2.5 text-xs transition-colors hover:border-foreground/40 hover:bg-muted"
                >
                  去题库继续练
                  <ArrowRight className="size-3.5" strokeWidth={1.8} />
                </Link>
              </>
            ) : (
              <p className="text-[11.5px] leading-relaxed text-muted-foreground">
                本场没有定位到可推荐的短板域。可以继续下一场，或回看报告里的逐题点评。
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="min-w-0">
        <CardHeader>
          <div className="min-w-0">
            <CardTitle>针对性练习</CardTitle>
            <p className="mt-1 text-[11px] text-muted-foreground">
              按本场报告现检索，已问过的不再重复。
            </p>
          </div>
          <MonoLabel>Recommendations</MonoLabel>
        </CardHeader>
        <CardContent>
          {current ? (
            <RecommendGroups
              interviewId={current}
              onGroups={(next) => {
                setGroups(next);
                setGroupsReady(true);
              }}
            />
          ) : (
            <p className="text-xs text-muted-foreground">暂无可用场次。</p>
          )}
        </CardContent>
      </Card>
    </div>,
  );
}

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[10.5px] text-muted-foreground">{label}</span>
      <span className="font-mono text-[10px]">{value}</span>
    </div>
  );
}
