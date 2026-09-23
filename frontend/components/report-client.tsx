"use client";

import { cn } from "cn";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { DomainBars, ScoreRadar } from "@/components/report-charts";
import { ReviewCard } from "@/components/review-card";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getReport, type ReportResponse } from "@/lib/api";
import { DIMENSIONS, domainLabel } from "@/lib/constants";
import { commentLabels, completedCount, formatScore, formatTime } from "@/lib/format";

export function ReportClient({ interviewId }: { interviewId: string }) {
  const [data, setData] = useState<ReportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getReport(interviewId)
      .then((payload) => {
        if (active) setData(payload);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : "报告加载失败");
      });
    return () => {
      active = false;
    };
  }, [interviewId]);

  if (error) {
    return (
      <div className="min-h-[100dvh]">
        <AppHeader />
        <main className="mx-auto max-w-3xl px-4 py-16 text-center sm:px-6">
          <p className="text-sm font-medium">{error}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            报告在面试结束后生成，未结束的场次暂无报告
          </p>
          <Link href="/" className={cn(buttonVariants({ variant: "outline" }), "mt-6")}>
            返回首页
          </Link>
        </main>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="min-h-[100dvh]">
        <AppHeader />
        <main className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-8 sm:px-6">
          <Skeleton className="h-8 w-48" />
          <div className="grid gap-6 lg:grid-cols-2">
            <Skeleton className="h-80 w-full" />
            <Skeleton className="h-80 w-full" />
          </div>
          <Skeleton className="h-32 w-full" />
        </main>
      </div>
    );
  }

  const report = data.report;
  const overall =
    DIMENSIONS.reduce((sum, dim) => sum + (report.scores[dim.key] ?? 0), 0) /
    DIMENSIONS.length;
  const labels = commentLabels(
    report.per_question_comments,
    report.answered_count,
    report.question_count,
  );

  return (
    <div className="min-h-[100dvh]">
      <AppHeader
        right={
          <div className="flex items-center gap-2">
            <Link
              href={`/interview/${interviewId}`}
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              查看面试回放
            </Link>
            <Link
              href="/"
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              返回首页
            </Link>
          </div>
        }
      />
      <main className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-8 sm:px-6 sm:py-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold tracking-tight">能力评估报告</h1>
            <p className="text-sm text-muted-foreground">
              {report.position}
            </p>
          </div>
          <div className="flex items-center gap-6">
            <Stat label="五维均分" value={formatScore(overall)} suffix="/ 5" />
            <Stat
              label="完成题量"
              value={`${completedCount(report.answered_count, report.question_count)}`}
              suffix={`/ ${report.question_count}`}
            />
            <Stat label="生成时间" value={formatTime(data.created_at)} />
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>五维能力</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <ScoreRadar scores={report.scores} />
              <ul className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
                {DIMENSIONS.map((dim) => (
                  <li key={dim.key} className="flex items-baseline justify-between gap-2">
                    <span className="text-xs text-muted-foreground">{dim.label}</span>
                    <span className="tabular text-sm font-medium">
                      {formatScore(report.scores[dim.key] ?? 0)}
                    </span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>知识域均分</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <DomainBars
                domainScores={report.domain_scores}
                weaknesses={report.weaknesses}
                labels={Object.fromEntries(
                  Object.keys(report.domain_scores).map((key) => [key, domainLabel(key)]),
                )}
              />
              {report.weaknesses.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-muted-foreground">短板域</span>
                  {report.weaknesses.map((domain) => (
                    <Badge key={domain} variant="outline" className="text-warning">
                      {domainLabel(domain)}
                    </Badge>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>总评</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground/90">
              {report.total_comment}
            </p>
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] lg:items-start">
          <Card>
            <CardHeader>
              <CardTitle>逐题复盘</CardTitle>
            </CardHeader>
            <CardContent>
              <ol className="flex flex-col gap-4">
                {report.per_question_comments.map((item, index) => (
                  <ReviewCard
                    key={`${item.question_id}-${index}`}
                    item={item}
                    label={labels[index]}
                  />
                ))}
              </ol>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>学习建议</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="flex flex-col gap-4">
                {report.study_advice.map((item, index) => (
                  <li key={`${item.domain}-${index}`} className="flex flex-col gap-2">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary">{domainLabel(item.domain)}</Badge>
                    </div>
                    <p className="text-sm leading-relaxed text-foreground/90">
                      {item.advice}
                    </p>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  );
}

function Stat({
  label,
  value,
  suffix,
}: {
  label: string;
  value: string;
  suffix?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="tabular text-lg font-semibold leading-none">
        {value}
        {suffix && (
          <span className="ml-1 text-xs font-normal text-muted-foreground">{suffix}</span>
        )}
      </span>
    </div>
  );
}
