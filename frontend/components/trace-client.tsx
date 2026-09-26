"use client";

import { cn } from "cn";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { AppHeader } from "@/components/app-header";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getTrace, type TraceEvent, type TraceResponse } from "@/lib/api";
import {
  DIMENSIONS,
  QUESTION_TYPE_LABELS,
  decisionLabel,
  domainLabel,
  phaseName,
  reasonLabel,
  traceEventLabel,
} from "@/lib/constants";
import { progressLabel } from "@/lib/format";
import {
  asFlag,
  asNumber,
  asRecord,
  asStringList,
  asText,
  coveragePercent,
  groupTraceEvents,
  judgeEvidence,
  type TraceRound,
} from "@/lib/trace";

/**
 * 决策回放页（FR-21 / SPEC §4.7）。
 *
 * 只读展示：数据是后端记录的决策事件本身（选了什么题、评分多少、为什么追问、
 * 为什么换题），前端不重算任何决策——重算就可能与当时不一致，那回放就没有意义。
 */
export function TraceClient({ interviewId }: { interviewId: string }) {
  const [data, setData] = useState<TraceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getTrace(interviewId)
      .then((payload) => {
        if (active) setData(payload);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : "决策回放加载失败");
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
        <main className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-8 sm:px-6">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </main>
      </div>
    );
  }

  const { rounds, closing } = groupTraceEvents(data.events);

  return (
    <div className="min-h-[100dvh]">
      <AppHeader
        right={
          <div className="flex items-center gap-2">
            <Link
              href={`/report/${interviewId}`}
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              查看报告
            </Link>
            <Link
              href={`/interview/${interviewId}`}
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              面试回放
            </Link>
          </div>
        }
      />
      <main className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8 sm:px-6 sm:py-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold tracking-tight">决策回放</h1>
            <p className="text-sm text-muted-foreground">{data.position}</p>
          </div>
          <div className="flex items-center gap-4">
            <span className="tabular text-sm text-muted-foreground">
              {progressLabel(data.answered_count, data.question_count)} 题
            </span>
            <Badge variant={data.status === "finished" ? "secondary" : "outline"}>
              {data.status === "finished" ? "已结束" : "进行中"}
            </Badge>
          </div>
        </div>

        <p className="text-sm leading-relaxed text-muted-foreground">
          这里记录面试过程中引擎的每一次判断：选了哪道题、评分如何、
          为什么追问、为什么换题。展示的是当时写下的决策记录，事后不重算。
        </p>

        {data.events.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col gap-1 py-4 text-sm">
              <p className="font-medium">该场次未记录决策</p>
              <p className="text-muted-foreground">
                决策回放从本版面试引擎起开始记录，更早的场次没有事件流可展示。
              </p>
            </CardContent>
          </Card>
        ) : (
          <>
            <ol className="flex flex-col gap-4">
              {rounds.map((item) => (
                <RoundCard key={item.round} round={item} />
              ))}
            </ol>
            {closing.length > 0 && <ClosingCard events={closing} />}
          </>
        )}
      </main>
    </div>
  );
}

/** 单轮卡片：出题信息进头部，其余事件按发生顺序排在时间线上。 */
function RoundCard({ round }: { round: TraceRound }) {
  const ask = round.events.find((event) => event.type === "ask");
  const question = ask ? asText(ask.detail.question) : null;
  const domain = ask ? asText(ask.detail.domain) : null;
  const difficulty = ask ? asText(ask.detail.difficulty) : null;
  const questionType = ask ? asText(ask.detail.question_type) : null;
  const source = ask ? askSource(ask) : null;
  const sections = round.events.filter((event) => event !== ask);

  return (
    <li className="flex flex-col gap-3 rounded-xl border p-4">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="tabular text-sm font-medium">第 {round.round} 题</span>
        {domain && (
          <span className="text-xs text-muted-foreground">{domainLabel(domain)}</span>
        )}
        {difficulty && (
          <Badge variant="outline" className="tabular">
            {difficulty}
          </Badge>
        )}
        {/* tech 走编号展示不必标注；其余题型（含将来新增的）显示标签，未知显示原值 */}
        {questionType && questionType !== "tech" && (
          <Badge variant="secondary">
            {QUESTION_TYPE_LABELS[questionType] ?? questionType}
          </Badge>
        )}
        {source && <span className="text-xs text-muted-foreground">{source}</span>}
      </div>

      {question && <p className="text-sm leading-relaxed font-medium">{question}</p>}

      {sections.length > 0 && (
        <ol className="flex flex-col gap-4 border-l pl-5">
          {sections.map((event, index) => (
            <TraceSection key={`${event.type}-${index}`} event={event} />
          ))}
        </ol>
      )}
    </li>
  );
}

/** 出题来源：题库命中几个候选，还是检索未命中由 LLM 生成（SPEC §4.4）。 */
function askSource(ask: TraceEvent): string | null {
  if (ask.detail.from_bank === true) {
    const hits = asNumber(ask.detail.hits);
    return hits === null ? "题库题" : `题库题 · 命中 ${hits} 个候选`;
  }
  if (ask.detail.from_bank === false) return "生成题";
  return null;
}

function TraceSection({ event }: { event: TraceEvent }) {
  switch (event.type) {
    case "judge":
      return <JudgeSection event={event} />;
    case "followup":
      return <FollowupSection event={event} />;
    case "advance":
      return <AdvanceSection event={event} />;
    case "end_refused":
      return <RefusedSection event={event} />;
    default:
      return <RawSection event={event} />;
  }
}

/** 小节外壳：时间线圆点 + 标签 + 状态 chips + 正文。 */
function Section({
  label,
  chips,
  children,
}: {
  label: string;
  chips?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <li className="relative flex flex-col gap-2">
      <span
        aria-hidden
        className="absolute top-1.5 -left-6 size-2 rounded-full border-2 border-background bg-muted-foreground/40"
      />
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-xs font-medium">{label}</span>
        {chips}
      </div>
      {children}
    </li>
  );
}

/** 评分：五维得分 + 覆盖率 + 难度调整；回答原文默认折叠（可能很长）。 */
function JudgeSection({ event }: { event: TraceEvent }) {
  const score = asRecord(event.detail.score);
  const { missedKeyPoints, comment } = judgeEvidence(event.detail.score);
  const coverage = coveragePercent(event.detail.coverage);
  const difficulty = asText(event.detail.difficulty);
  const changed = asFlag(event.detail.difficulty_changed);
  const answer = asText(event.detail.answer);

  return (
    <Section
      label="评分"
      chips={
        <>
          {coverage !== null && (
            <Badge variant="outline" className="tabular">
              覆盖率 {coverage}%
            </Badge>
          )}
          {asFlag(score?.error_flag) && (
            <Badge variant="destructive">回答有误</Badge>
          )}
          {difficulty && (
            <span className="tabular text-xs text-muted-foreground">
              {changed ? `难度变化 → ${difficulty}` : `难度 ${difficulty}`}
            </span>
          )}
        </>
      }
    >
      {score && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {DIMENSIONS.map((dim) => {
            const value = asNumber(score[dim.key]);
            if (value === null) return null;
            return (
              <li key={dim.key} className="flex items-baseline gap-1">
                <span className="text-xs text-muted-foreground">{dim.label}</span>
                <span className="tabular text-xs font-medium">{value}</span>
              </li>
            );
          })}
        </ul>
      )}
      {missedKeyPoints.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-xs text-muted-foreground">漏掉的关键点</span>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {missedKeyPoints.map((point) => (
              <li key={point} className="text-xs text-muted-foreground">
                <span aria-hidden>✗</span> {point}
              </li>
            ))}
          </ul>
        </div>
      )}
      {comment && (
        <p className="text-sm leading-relaxed text-foreground/90">{comment}</p>
      )}
      {answer && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            我的回答
          </summary>
          <p className="mt-2 leading-relaxed whitespace-pre-wrap text-foreground/90">
            {answer}
          </p>
        </details>
      )}
    </Section>
  );
}

/** 追问：决策 + 原因（与条件边同源）+ 追问文案。 */
function FollowupSection({ event }: { event: TraceEvent }) {
  const decision = asText(event.detail.decision);
  const reason = asText(event.detail.reason);
  const text = asText(event.detail.text);

  return (
    <Section
      label="追问"
      chips={
        <>
          {decision && <Badge variant="secondary">{decisionLabel(decision)}</Badge>}
          {reason && (
            <span className="text-xs text-muted-foreground">{reasonLabel(reason)}</span>
          )}
        </>
      }
    >
      {text && (
        <p className="border-l-2 pl-3 text-sm leading-relaxed text-foreground/90">{text}</p>
      )}
    </Section>
  );
}

/** 换题：换题原因 + 推进到的阶段。 */
function AdvanceSection({ event }: { event: TraceEvent }) {
  const reason = asText(event.detail.reason);
  const phase = asText(event.detail.phase);
  const nextPhase = phase ? phaseName(phase) : null;

  return (
    <Section
      label="换题"
      chips={
        <>
          {reason && (
            <span className="text-xs text-muted-foreground">{reasonLabel(reason)}</span>
          )}
          {nextPhase && <Badge variant="outline">进入{nextPhase}</Badge>}
        </>
      }
    />
  );
}

/** 主动结束未达门槛被挽留（PRD §4.5）：缺口按后端给的数字算，不重推门槛。 */
function RefusedSection({ event }: { event: TraceEvent }) {
  const answered = asNumber(event.detail.answered_count);
  const threshold = asNumber(event.detail.threshold);
  const gap = answered !== null && threshold !== null ? threshold - answered : null;

  return (
    <Section
      label="结束被挽留"
      chips={
        gap !== null && gap > 0 ? (
          <Badge variant="outline" className="tabular">
            还差 {gap} 题
          </Badge>
        ) : undefined
      }
    >
      {answered !== null && threshold !== null && (
        <p className="text-xs text-muted-foreground">
          已答 {answered} 题，未达结束门槛 {threshold} 题，面试继续
        </p>
      )}
    </Section>
  );
}

/** 未知事件类型：显示原值 + 原始 detail，不猜语义。 */
function RawSection({ event }: { event: TraceEvent }) {
  return (
    <Section label={traceEventLabel(event.type)}>
      <p className="font-mono text-xs break-all text-muted-foreground">
        {JSON.stringify(event.detail)}
      </p>
    </Section>
  );
}

/** 收尾区：非轮次事件（报告生成）。 */
function ClosingCard({ events }: { events: TraceEvent[] }) {
  const report = events.find((event) => event.type === "report");
  const answered = report ? asNumber(report.detail.answered_count) : null;
  const total = report ? asNumber(report.detail.question_count) : null;
  const weaknesses = report ? asStringList(report.detail.weaknesses) : [];
  const others = events.filter((event) => event !== report);

  return (
    <Card>
      <CardHeader>
        <CardTitle>收尾</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {answered !== null && total !== null && (
          <p className="text-sm">
            完成 <span className="tabular font-medium">{answered}</span> /{" "}
            <span className="tabular">{total}</span> 题后生成报告
          </p>
        )}
        {weaknesses.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">短板域</span>
            {weaknesses.map((domain) => (
              <Badge key={domain} variant="outline" className="text-warning">
                {domainLabel(domain)}
              </Badge>
            ))}
          </div>
        )}
        {others.length > 0 && (
          <ol className="flex flex-col gap-4 border-l pl-5">
            {others.map((event, index) => (
              <TraceSection key={`${event.type}-${index}`} event={event} />
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
