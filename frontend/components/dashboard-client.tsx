"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { CreateForm } from "@/components/create-form";
import { InterviewList } from "@/components/interview-list";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MonoLabel } from "@/components/ui/mono-label";
import { Skeleton } from "@/components/ui/skeleton";
import { StatStrip, type StatItem } from "@/components/ui/stat-strip";
import {
  getProfile,
  type InterviewPage,
  type ProfileResponse,
} from "@/lib/api";
import { DIMENSIONS, difficultyLabel, domainLabel } from "@/lib/constants";
import { formatScore, formatTime } from "@/lib/format";
import { deltaLabel } from "@/lib/profile";

/**
 * 仪表盘主体：统计条 + 创建表单 + 面试记录 + 能力快照/下一步。
 *
 * 面试记录只取最近 5 场（服务端分页），统计条读接口返回的 counts（全量状态分布），
 * 不再从列表行里数；能力快照/下一步走 profile 接口。两者各自失败只坏自己那块。
 */
export function DashboardClient() {
  const [counts, setCounts] = useState<InterviewPage["counts"] | null>(null);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [profileError, setProfileError] = useState(false);

  useEffect(() => {
    let active = true;
    getProfile()
      .then((payload) => {
        if (active) setProfile(payload);
      })
      .catch(() => {
        // 档案拉不到只收起快照区，不挡创建与列表
        if (active) setProfileError(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const finished = counts?.finished;
  const running = counts?.running;
  const delta = profile?.summary.latest_delta?.delta;
  const hasSessions = (profile?.sessions.length ?? 0) > 0;
  const deltaTone: StatItem["tone"] =
    typeof delta === "number" ? (delta > 0 ? "up" : delta < 0 ? "down" : "default") : "default";

  const stats: StatItem[] = [
    {
      label: "已完成面试",
      value:
        counts === null ? (
          "…"
        ) : (
          <>
            {finished}
            <span className="ml-1 text-xs font-medium text-muted-foreground">场</span>
          </>
        ),
      hint: "含报告的场次",
    },
    {
      label: "进行中",
      value:
        counts === null ? (
          "…"
        ) : (
          <>
            {running}
            <span className="ml-1 text-xs font-medium text-muted-foreground">场</span>
          </>
        ),
      hint: running ? "可以随时续面" : "当前没有进行中的场次",
    },
    {
      label: "平均总分",
      value:
        profile === null ? (
          "…"
        ) : hasSessions ? (
          <>
            {formatScore(profile.summary.average_overall)}
            <span className="ml-1 text-xs font-medium text-muted-foreground">/ 5</span>
          </>
        ) : (
          "暂无"
        ),
      hint: hasSessions ? "五维等权均值" : "完成一场后生成",
    },
    {
      label: "最近变化",
      value:
        profile === null ? "…" : typeof delta === "number" ? deltaLabel(delta) : "暂无",
      hint: profile?.summary.latest_delta ? "与上一场对比" : "再完成一场才有对比",
      tone: deltaTone,
    },
  ];

  return (
    <div className="flex flex-col gap-5">
      <StatStrip items={stats} />

      <div className="grid gap-5 xl:grid-cols-[372px_minmax(0,1fr)] xl:items-start">
        <CreateForm />

        <div className="flex min-w-0 flex-col gap-5">
          <InterviewList
            pageSize={5}
            showControls={false}
            onCountsChange={setCounts}
          />

          <div className="grid gap-5 lg:grid-cols-2">
            <CapabilitySnapshot profile={profile} failed={profileError} />
            <NextStep profile={profile} failed={profileError} />
          </div>
        </div>
      </div>
    </div>
  );
}

function CapabilitySnapshot({
  profile,
  failed,
}: {
  profile: ProfileResponse | null;
  failed: boolean;
}) {
  const latest = profile
    ? (profile.sessions[profile.sessions.length - 1] ?? null)
    : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>能力快照</CardTitle>
        <MonoLabel>Latest</MonoLabel>
      </CardHeader>
      <CardContent>
        {failed ? (
          <p className="text-xs text-muted-foreground">能力档案暂不可用，不影响面试。</p>
        ) : profile === null ? (
          <div className="flex flex-col gap-3">
            {DIMENSIONS.map((dim) => (
              <Skeleton key={dim.key} className="h-4 w-full" />
            ))}
          </div>
        ) : !latest ? (
          <p className="text-xs leading-relaxed text-muted-foreground">
            完成一场面试并生成报告后，这里会显示最近一次的五维快照。
          </p>
        ) : Object.keys(latest.scores).length === 0 ? (
          <p className="text-xs leading-relaxed text-muted-foreground">
            最近一场没有评分数据（上游降级），快照暂无。
          </p>
        ) : (
          <div className="flex flex-col gap-2.5">
            {DIMENSIONS.map((dim) => {
              const score = latest.scores[dim.key] ?? 0;
              return (
                <div
                  key={dim.key}
                  className="grid grid-cols-[74px_minmax(0,1fr)_34px] items-center gap-2.5"
                >
                  <span className="truncate text-[11.5px] text-muted-foreground">
                    {dim.label}
                  </span>
                  <div className="h-1 overflow-hidden rounded-full bg-accent">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${Math.max(0, Math.min(100, (score / 5) * 100))}%` }}
                    />
                  </div>
                  <span className="text-right font-mono text-[10.5px]">
                    {formatScore(score)}
                  </span>
                </div>
              );
            })}
            <p className="mt-1 font-mono text-[9px] tracking-[0.04em] text-muted-foreground">
              {formatTime(latest.started_at)} / {latest.question_count} 题
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function NextStep({
  profile,
  failed,
}: {
  profile: ProfileResponse | null;
  failed: boolean;
}) {
  const latest = profile
    ? (profile.sessions[profile.sessions.length - 1] ?? null)
    : null;
  const weaknesses = latest?.weaknesses.slice(0, 2) ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>下一步</CardTitle>
        <MonoLabel>From report</MonoLabel>
      </CardHeader>
      <CardContent className="flex h-full flex-col gap-2.5">
        {failed ? (
          <p className="text-xs text-muted-foreground">能力档案暂不可用，不影响面试。</p>
        ) : profile === null ? (
          <>
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-12 w-full" />
          </>
        ) : !latest ? (
          <p className="text-xs leading-relaxed text-muted-foreground">
            完成一场面试后，这里会按报告里的短板给出下一步。
          </p>
        ) : (
          <>
            <span className="font-mono text-[9px] tracking-[0.12em] text-muted-foreground uppercase">
              最近一场暴露的短板
            </span>
            <h3 className="text-lg font-semibold tracking-[-0.02em]">
              {weaknesses.length > 0
                ? weaknesses.map(domainLabel).join(" / ")
                : "最近一场没有定位到短板"}
            </h3>
            <p className="text-[11.5px] leading-relaxed text-muted-foreground">
              {weaknesses.length > 0
                ? "先补该域的 L2 题，再做一次专项面试，观察短板是否消失。"
                : "可以继续下一场，或者回看报告里的逐题点评。"}
            </p>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="outline">{latest.question_count} 题</Badge>
              <Badge variant="secondary">{difficultyLabel(latest.difficulty)}</Badge>
              <span className="font-mono text-[9px] text-muted-foreground">
                {formatTime(latest.started_at)}
              </span>
            </div>
            <Link
              href={
                weaknesses.length > 0
                  ? `/learn?interview=${latest.interview_id}`
                  : `/report/${latest.interview_id}`
              }
              className="mt-auto inline-flex h-[30px] items-center gap-1 self-start rounded-md border border-border px-2.5 text-xs transition-colors hover:border-foreground/40 hover:bg-muted"
            >
              {weaknesses.length > 0 ? "查看学习推荐" : "查看报告"}
              <ArrowRight className="size-3.5" strokeWidth={1.8} />
            </Link>
          </>
        )}
      </CardContent>
    </Card>
  );
}
