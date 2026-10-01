"use client";

import { cn } from "cn";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { RecommendGroups } from "@/components/recommend-groups";
import { DomainBars, ScoreRadar } from "@/components/report-charts";
import { ReviewCard } from "@/components/review-card";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { exportReportPdf, getReport, type ReportResponse } from "@/lib/api";
import { DIMENSIONS, domainLabel, isBehavioral } from "@/lib/constants";
import { downloadBlob, reportFileName } from "@/lib/download";
import { commentLabels, completedCount, formatScore, formatTime } from "@/lib/format";

export function ReportClient({ interviewId }: { interviewId: string }) {
  const [data, setData] = useState<ReportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

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

  /** 导出 PDF（FR-18）：后端现渲染，前端只负责落盘；失败就地给一行提示，不挡页面。 */
  async function onExport() {
    setExporting(true);
    setExportError(null);
    try {
      const blob = await exportReportPdf(interviewId);
      downloadBlob(blob, reportFileName(report.position, interviewId));
    } catch (err: unknown) {
      setExportError(err instanceof Error ? err.message : "导出失败，请重试");
    } finally {
      setExporting(false);
    }
  }

  // 维度表由后端随 payload 给（P1-M11：行为面/技术面各一套，标签单一来源不在前端）；
  // 老报告没有 dims → 退回技术面常量
  const dims = report.dims?.length ? report.dims : DIMENSIONS;
  const behavioral = isBehavioral(report.interview_type);
  // 总分以后端为准（P1-M10 D1：与能力档案曲线同一个数）；FR-19 之前的历史 payload
  // 没有 overall 字段 → 按同一公式（各维等权均值）现算兜底
  const overall =
    report.overall ??
    dims.reduce((sum, dim) => sum + (report.scores[dim.key] ?? 0), 0) / dims.length;
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
            <button
              type="button"
              onClick={onExport}
              disabled={exporting}
              className={cn(buttonVariants({ size: "sm" }))}
            >
              {exporting ? "导出中…" : "导出 PDF"}
            </button>
            <Link
              href={`/trace/${interviewId}`}
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              决策回放
            </Link>
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

        {exportError && (
          <p className="text-sm text-destructive" role="alert">
            {exportError}
          </p>
        )}

        <div className={cn("grid gap-6", !behavioral && "lg:grid-cols-2")}>
          <Card>
            <CardHeader>
              <CardTitle>五维能力</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <ScoreRadar scores={report.scores} dims={dims} />
              <ul className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
                {dims.map((dim) => (
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

          {/* 知识域卡仅技术面（P1-M11 D5）：行为面整场一个域、没有域统计 */}
          {!behavioral && (
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
          )}

          {/* 行为面没有知识域可看：短板改为评分维度，跟着能力卡走 */}
          {behavioral && report.weaknesses.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>短板维度</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center gap-2">
                {report.weaknesses.map((key) => (
                  <Badge key={key} variant="outline" className="text-warning">
                    {dims.find((dim) => dim.key === key)?.label ?? key}
                  </Badge>
                ))}
                <span className="text-xs text-muted-foreground">
                  按本场各维度均分定位，具体建议见下方学习建议与逐题复盘
                </span>
              </CardContent>
            </Card>
          )}
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

        {/* 逐题复盘与学习建议各占一行：两者长度差一个量级，并排会让短的一侧空一大片 */}
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
                  dims={dims}
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
            <ul className="grid gap-4 lg:grid-cols-2">
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

        {/* 学习推荐（FR-20）：短板域 → 该域新材料。跟着「学习建议」走（建议给方向、推荐给题），
            独立请求 + 懒加载；跳学习页时带上本场次，页面上不会串到别的场次去。
            行为面不渲染（P1-M11 D5）：推荐检索的是六大技术域，行为面没有可推的域 */}
        {!behavioral && (
          <Card>
            <CardHeader className="flex-row items-center justify-between gap-4">
              <CardTitle>针对性练习推荐</CardTitle>
              <Link
                href={`/learn?interview=${interviewId}`}
                className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
              >
                查看全部推荐
              </Link>
            </CardHeader>
            <CardContent>
              <RecommendGroups interviewId={interviewId} showAdvice={false} />
            </CardContent>
          </Card>
        )}
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
