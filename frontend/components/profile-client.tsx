"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { MiniTrend, OverallTrend } from "@/components/profile-charts";
import { DomainBars, ScoreRadar } from "@/components/report-charts";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getProfile, type ProfileResponse, type ProfileSession } from "@/lib/api";
import {
  DIMENSIONS,
  DOMAIN_LABELS,
  WEIGHTED_DOMAINS,
  difficultyLabel,
  domainLabel,
} from "@/lib/constants";
import { formatScore } from "@/lib/format";
import { deltaLabel, overallRows, profileStage, scoreRows, tickLabels, weaknessRows } from "@/lib/profile";
import { cn } from "@/lib/utils";

/**
 * 能力档案（FR-19）：多场得分曲线 + 短板变化。
 *
 * 三种形态（判定在 lib/profile.ts）：**空档案**给引导与入口（空态不只是告知，要给出路）、
 * **只有一场**画不出曲线故给该场快照、**多场**才是完整档案。
 * 数据一次取回、本地切图（场次规模小，不为切图多跑往返）。
 */
export function ProfileClient() {
  const router = useRouter();
  const [data, setData] = useState<ProfileResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    getProfile()
      .then((payload) => {
        if (active) setData(payload);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : "档案加载失败");
      });
    return () => {
      active = false;
    };
  }, [reloadKey]);

  if (error) {
    return (
      <Card>
        <CardContent className="flex flex-col items-start gap-3 py-6">
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
          <Button variant="outline" size="sm" onClick={() => setReloadKey((key) => key + 1)}>
            重试
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (data === null) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const stage = profileStage(data.sessions.length);
  if (stage === "empty") return <EmptyProfile />;

  const sessions = data.sessions;
  const latest = sessions[sessions.length - 1];
  const labels = tickLabels(sessions);
  const labelOf = (interviewId: string) =>
    labels[sessions.findIndex((session) => session.interview_id === interviewId)] ?? "";

  return (
    <div className="flex flex-col gap-6">
      <SummaryTiles data={data} labelOf={labelOf} />

      {stage === "single" ? (
        <SingleSession session={latest} />
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>总分曲线</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              <OverallTrend
                rows={overallRows(sessions)}
                onSelect={(interviewId) => router.push(`/report/${interviewId}`)}
              />
              <p className="text-xs text-muted-foreground">点击曲线上的点可打开该场报告。</p>
            </CardContent>
          </Card>

          <WeaknessChanges data={data} />
        </>
      )}

      <Card>
        <CardHeader>
          <CardTitle>知识域趋势</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-xs text-muted-foreground">
            每场只考部分知识域，没考到的场次曲线断开——不是 0 分。
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {WEIGHTED_DOMAINS.map((domain) => {
              const rows = scoreRows(sessions, [domain], "domain_scores");
              const values = rows
                .map((row) => row[domain])
                .filter((value): value is number => typeof value === "number");
              return (
                <MiniTrend
                  key={domain}
                  title={domainLabel(domain)}
                  rows={rows}
                  dataKey={domain}
                  footnote={
                    values.length ? `最近 ${formatScore(values[values.length - 1])}` : null
                  }
                />
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>五维趋势</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {DIMENSIONS.map((dim) => {
            const rows = scoreRows(sessions, [dim.key], "scores");
            const values = rows
              .map((row) => row[dim.key])
              .filter((value): value is number => typeof value === "number");
            return (
              <MiniTrend
                key={dim.key}
                title={dim.label}
                rows={rows}
                dataKey={dim.key}
                footnote={values.length ? `最近 ${formatScore(values[values.length - 1])}` : null}
              />
            );
          })}
        </CardContent>
      </Card>
    </div>
  );
}

/** 概览四格：场次 / 均分 / 最近变化 / 最高分。 */
function SummaryTiles({
  data,
  labelOf,
}: {
  data: ProfileResponse;
  labelOf: (interviewId: string) => string;
}) {
  const { summary } = data;
  const tiles: { title: string; value: string; hint: string }[] = [
    { title: "已面试", value: `${summary.session_count} 场`, hint: "含报告的场次" },
    { title: "平均总分", value: formatScore(summary.average_overall), hint: "五维等权均值" },
    summary.latest_delta
      ? {
          title: "最近变化",
          value: deltaLabel(summary.latest_delta.delta),
          // 跨场比较必须带刻度标签：只说「涨了 0.6」用户不知道跟哪场比
          hint: `${labelOf(data.sessions[data.sessions.length - 2].interview_id)} → ${labelOf(
            data.sessions[data.sessions.length - 1].interview_id,
          )}`,
        }
      : { title: "最近变化", value: "—", hint: "再完成一场才有对比" },
    summary.best
      ? {
          title: "最高分",
          value: formatScore(summary.best.overall),
          hint: labelOf(summary.best.interview_id),
        }
      : { title: "最高分", value: "—", hint: "" },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {tiles.map((tile) => (
        <Card key={tile.title}>
          <CardContent className="flex flex-col gap-1 py-4">
            <span className="text-xs text-muted-foreground">{tile.title}</span>
            <span className="text-2xl font-medium tabular-nums">{tile.value}</span>
            <span className="truncate text-xs text-muted-foreground">{tile.hint}</span>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

/** 只有一场时的视图：给该场快照 + 说明为什么没有曲线。 */
function SingleSession({ session }: { session: ProfileSession }) {
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>还差一场就能看曲线</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col items-start gap-3">
          <p className="text-sm text-muted-foreground">
            得分曲线要两场以上才画得出来。再面一场，这里会显示总分与各维度的走向对比。
          </p>
          <Link href="/" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
            再开始一场面试
          </Link>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            {session.position} · {difficultyLabel(session.difficulty)}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-6">
          <div className="grid items-center gap-6 md:grid-cols-2">
            <ScoreRadar scores={session.scores} />
            <div className="flex flex-col gap-2">
              {DIMENSIONS.map((dim) => (
                <div key={dim.key} className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">{dim.label}</span>
                  <span className="tabular-nums">{formatScore(session.scores[dim.key] ?? 0)}</span>
                </div>
              ))}
            </div>
          </div>
          <DomainBars
            domainScores={session.domain_scores}
            weaknesses={session.weaknesses}
            labels={DOMAIN_LABELS}
          />
          <Link
            href={`/report/${session.interview_id}`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }), "self-start")}
          >
            查看该场报告
          </Link>
        </CardContent>
      </Card>
    </>
  );
}

/** 短板变化：逐场对比上一场，三种走向分开说（不是笼统的「短板变了」）。 */
function WeaknessChanges({ data }: { data: ProfileResponse }) {
  const rows = weaknessRows(data.sessions, data.weakness_changes).reverse(); // 最近一场在最上面

  return (
    <Card>
      <CardHeader>
        <CardTitle>短板变化</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {rows.map((row) => {
          const empty = row.new.length === 0 && row.persistent.length === 0 && row.resolved.length === 0;
          return (
            <div key={row.interview_id} className="flex flex-col gap-2 border-b pb-4 last:border-b-0 last:pb-0">
              <span className="text-sm font-medium">{row.label}</span>
              {empty ? (
                <span className="text-xs text-muted-foreground">与上一场相比，短板域没有变化。</span>
              ) : (
                <div className="flex flex-col gap-1.5 text-sm">
                  <WeaknessLine label="持续存在" domains={row.persistent} tone="warning" />
                  <WeaknessLine label="新出现" domains={row.new} tone="danger" />
                  <WeaknessLine label="已改善" domains={row.resolved} tone="ok" />
                </div>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function WeaknessLine({
  label,
  domains,
  tone,
}: {
  label: string;
  domains: string[];
  tone: "warning" | "danger" | "ok";
}) {
  if (domains.length === 0) return null;
  // 语义色同「短板域」徽标（text-warning）；已改善用 emerald（同私有题库的「使用中」绿）
  const toneClass = {
    warning: "text-warning",
    danger: "text-destructive",
    ok: "text-emerald-600 dark:text-emerald-400",
  }[tone];

  return (
    <div className="flex flex-wrap items-baseline gap-2">
      <span className={cn("shrink-0 text-xs", toneClass)}>{label}</span>
      <span className="text-muted-foreground">{domains.map(domainLabel).join("、")}</span>
    </div>
  );
}

/** 空档案（D4）：文案说清怎么才有数据，按钮直接给出路。 */
function EmptyProfile() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>能力档案</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-3">
        <p className="text-sm text-muted-foreground">
          还没有可分析的面试记录。完成第一场模拟面试后，这里会记录你的总分、五维能力与各知识域的走向，
          并把每场暴露的短板变化连起来看。
        </p>
        <Link href="/" className={cn(buttonVariants({ size: "sm" }))}>
          开始第一场面试
        </Link>
      </CardContent>
    </Card>
  );
}
