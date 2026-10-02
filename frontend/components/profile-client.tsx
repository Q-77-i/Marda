"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, useEffect, useState, type ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import { DomainHeatmap, OverallTrend } from "@/components/profile-charts";
import { DomainBars, ScoreRadar } from "@/components/report-charts";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
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
import {
  HEATMAP_WINDOW,
  WEAKNESS_WINDOW,
  deltaLabel,
  dimensionStats,
  emptyProfileCopy,
  excludedNotes,
  heatRows,
  heatmapWindow,
  insightLine,
  overallRows,
  profileStage,
  recentSlice,
  tickLabels,
  weaknessRows,
} from "@/lib/profile";
import { cn } from "@/lib/utils";

/**
 * 能力档案（FR-19）：多场得分曲线 + 短板变化。
 *
 * 三种形态（判定在 lib/profile.ts）：**空档案**给引导与入口（空态不只是告知，要给出路）、
 * **只有一场**画不出曲线故给该场快照、**多场**才是完整档案。
 * 数据一次取回、本地切图（场次规模小，不为切图多跑往返）。
 *
 * 多场态的**卡片顺序 = 认知路径**（P1-M10.5 用户定）：五维对照（我是谁，静态）→
 * 总分曲线（在变好还是变差，整体）→ 知识域趋势（哪个细分方向，交叉对比）→ 短板变化（逐场明细）。
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

  // 页头常驻：加载/错误/空档案三种形态下页面标题不变，切态时不跳
  const page = (content: ReactNode) => (
    <>
      <PageHeader
        title="能力档案"
        description="多次面试的得分曲线与短板变化，用来看在变好还是变差"
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
              <Button variant="outline" size="sm" onClick={() => setReloadKey((key) => key + 1)}>
                重试
              </Button>
            }
          />
        </CardContent>
      </Card>,
    );
  }

  if (data === null) {
    return page(
      <div className="flex flex-col gap-6">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>,
    );
  }

  const stage = profileStage(data.sessions.length);
  if (stage === "empty") return page(<EmptyProfile excluded={data.excluded} />);

  const sessions = data.sessions;
  const latest = sessions[sessions.length - 1];
  const labels = tickLabels(sessions);
  const insight = insightLine(sessions, WEIGHTED_DOMAINS);
  const notes = excludedNotes(data.excluded);
  const labelOf = (interviewId: string) =>
    labels[sessions.findIndex((session) => session.interview_id === interviewId)] ?? "";

  return page(
    <>
      <SummaryTiles data={data} labelOf={labelOf} />
      {/* 未计入档案的场次（P1-M11 行为面 / P2-M3 报告缺失）：逐条说明，别让用户以为那几场丢了 */}
      {notes.map((line) => (
        <p key={line} className="text-xs text-muted-foreground">
          {line}
        </p>
      ))}

      {stage === "single" ? (
        // 单场态没有曲线与短板变化，顺序仍是「先快照、后明细」
        <>
          <SingleSession session={latest} />
          <HeatmapCard sessions={sessions} labels={labels} insight={insight} />
          <DimensionCard sessions={sessions} />
        </>
      ) : (
        <>
          <DimensionCard sessions={sessions} />

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

          <HeatmapCard sessions={sessions} labels={labels} insight={insight} />

          <WeaknessChanges data={data} />
        </>
      )}
    </>,
  );
}

/** 知识域热力图卡：默认只出最近 7 场（列再多数字就难读），场次超了才给「查看全部」。 */
function HeatmapCard({
  sessions,
  labels,
  insight,
}: {
  sessions: ProfileSession[];
  labels: string[];
  insight: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const shown = heatmapWindow(sessions, labels, expanded);

  return (
    <Card>
      <CardHeader>
        <CardTitle>知识域趋势</CardTitle>
        {sessions.length > HEATMAP_WINDOW ? (
          <CardAction>
            <Button
              variant="ghost"
              size="xs"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? "收起" : `查看全部 ${sessions.length} 场`}
            </Button>
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {/* 不写「颜色越深分越高」：暗色主题下高分是更亮，这句只在明色成立 */}
        <p className="text-xs text-muted-foreground">
          行 = 知识域、列 = 场次，同一行的左右就是该域的走向；颜色分五档表示分数高低（见下方色阶）。
        </p>
        <DomainHeatmap rows={heatRows(shown.sessions, WEIGHTED_DOMAINS)} labels={shown.labels} />
        {insight ? <p className="text-sm text-muted-foreground">洞察：{insight}</p> : null}
      </CardContent>
    </Card>
  );
}

/**
 * 五维对照（M10.5）：场均 / 最近一场两列数字。
 *
 * 五维的走向与总分曲线同步（真数据上五条线近乎平行），单独画 5 张趋势图是重复信息；
 * 真正有差异的是**水平**——哪一维常年偏弱，数字比线读得准，也不必引入分类色板。
 */
function DimensionCard({ sessions }: { sessions: ProfileSession[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>五维对照</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <p className="text-xs text-muted-foreground">
          五维各场同涨同跌（走向见下方总分曲线），这里只对照各自的位置——哪一维常年偏弱。
        </p>
        <DimensionTable sessions={sessions} />
      </CardContent>
    </Card>
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
            <span className="text-2xl font-semibold tabular-nums">{tile.value}</span>
            <span className="truncate text-xs text-muted-foreground">{tile.hint}</span>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function DimensionTable({ sessions }: { sessions: ProfileSession[] }) {
  const stats = dimensionStats(
    sessions,
    DIMENSIONS.map((dim) => dim.key),
  );

  return (
    <div className="grid grid-cols-[1fr_4.5rem_4.5rem] items-center gap-y-1 text-sm">
      <span className="text-xs text-muted-foreground">维度</span>
      <span className="text-right text-xs text-muted-foreground">场均</span>
      <span className="text-right text-xs text-muted-foreground">最近一场</span>
      {DIMENSIONS.map((dim, index) => (
        <Fragment key={dim.key}>
          <span className="text-muted-foreground">{dim.label}</span>
          <span className="text-right tabular-nums">{formatScore(stats[index].average)}</span>
          <span className="text-right tabular-nums">{formatScore(stats[index].latest)}</span>
        </Fragment>
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

/**
 * 短板变化：逐场对比上一场，三种走向分开说（不是笼统的「短板变了」）。
 *
 * 默认只出最近 5 场：每场 3–5 行文字，再多场次就淹没在列表里；想看旧场次的明细，
 * 点总分曲线上对应的点进那一场的报告（那里有完整复盘）。
 */
function WeaknessChanges({ data }: { data: ProfileResponse }) {
  const [expanded, setExpanded] = useState(false);
  // 先按时间升序取尾部窗口、再倒序展示（顺序反了会切到最旧的 5 场）
  const rows = recentSlice(weaknessRows(data.sessions, data.weakness_changes), WEAKNESS_WINDOW, expanded)
    .reverse(); // 最近一场在最上面

  return (
    <Card>
      <CardHeader>
        <CardTitle>短板变化</CardTitle>
        {data.weakness_changes.length > WEAKNESS_WINDOW ? (
          <CardAction>
            <Button
              variant="ghost"
              size="xs"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? "收起" : `查看全部 ${data.weakness_changes.length} 场`}
            </Button>
          </CardAction>
        ) : null}
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
  // 语义色与全局一致：短板 text-warning / 新出现 text-destructive / 已改善 text-success
  const toneClass = {
    warning: "text-warning",
    danger: "text-destructive",
    ok: "text-success",
  }[tone];

  return (
    <div className="flex flex-wrap items-baseline gap-2">
      <span className={cn("shrink-0 text-xs", toneClass)}>{label}</span>
      <span className="text-muted-foreground">{domains.map(domainLabel).join("、")}</span>
    </div>
  );
}

/**
 * 空档案（M10 D4：空态要给出路）。
 *
 * P1-M11 D4 / P2-M3：**只跑过行为面**或**有场次但报告缺失**时不能只说「还没有面试记录」——
 * 用户明明跑过，要说清那些场次去哪了，否则看起来像系统把场次弄丢了。
 */
function EmptyProfile({ excluded }: { excluded?: Record<string, number> }) {
  // 文案（标题/正文/CTA）在 lib/profile.ts 里按成因分态，页面只渲染不判断
  const copy = emptyProfileCopy(excluded);
  return (
    <Card>
      <CardContent>
        <EmptyState
          className="py-12"
          title={copy.title}
          description={copy.paragraphs.join("")}
          action={
            <Link href="/" className={cn(buttonVariants({ size: "sm" }))}>
              {copy.cta}
            </Link>
          }
        />
      </CardContent>
    </Card>
  );
}
