"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { ListSkeleton } from "@/components/ui/skeleton";
import { MonoLabel } from "@/components/ui/mono-label";
import { getRecommendations, type RecommendationGroup } from "@/lib/api";
import { domainLabel, difficultyLabel } from "@/lib/constants";
import { groupNotice } from "@/lib/learn";
import { cn } from "@/lib/utils";

/**
 * 学习推荐分组（FR-20）：按短板域列出资料卡，卡片行内展开看答案与来源。
 *
 * 自己拉数据（报告页与学习页共用，两处都是「给个场次就渲染」）——
 * 推荐是报告之外的独立请求，失败只坏这一块，不挡报告主内容（同题库页的分面策略）。
 * 交互与题库页一致：点题干展开，不引新 dialog 原语。
 */
export function RecommendGroups({
  interviewId,
  showAdvice = true,
  onGroups,
}: {
  interviewId: string;
  /** 报告页已有「学习建议」卡（同一批文案），传 false 避免同页复述 */
  showAdvice?: boolean;
  /** 学习页用第一组生成左侧建议卡；报告页不传 */
  onGroups?: (groups: RecommendationGroup[]) => void;
}) {
  const [groups, setGroups] = useState<RecommendationGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const onGroupsRef = useRef(onGroups);

  useEffect(() => {
    onGroupsRef.current = onGroups;
  }, [onGroups]);

  const load = useCallback(async () => {
    setError(null);
    setGroups(null);
    try {
      const body = await getRecommendations(interviewId);
      setGroups(body.groups);
      onGroupsRef.current?.(body.groups);
    } catch (err) {
      setError(err instanceof Error ? err.message : "推荐加载失败");
      onGroupsRef.current?.([]);
    }
  }, [interviewId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <ErrorState
        message={error}
        action={
          <Button variant="outline" size="sm" onClick={() => void load()}>
            重试
          </Button>
        }
      />
    );
  }

  if (groups === null) {
    return <ListSkeleton />;
  }

  if (groups.length === 0) {
    return (
      <EmptyState
        className="py-8"
        title="本场没有定位到短板域"
        description="完成一场技术面并生成报告后，这里会按短板域给出练习题。"
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {groups.map((group) => {
        const notice = groupNotice(group.status);
        return (
          <section key={group.domain} className="flex flex-col gap-3">
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="warning">{domainLabel(group.domain)}</Badge>
                <MonoLabel>薄弱知识域</MonoLabel>
              </div>
              {showAdvice && group.advice ? (
                <p className="max-w-[62ch] text-[11.5px] leading-relaxed text-foreground/90">
                  {group.advice}
                </p>
              ) : null}
            </div>

            {notice ? (
              <p className="rounded-lg border border-dashed border-border px-3 py-2 text-[11px] text-muted-foreground">
                {notice}
              </p>
            ) : (
              <ul className="overflow-hidden rounded-lg border border-border">
                {group.cards.map((card) => {
                  const open = expanded === card.question_id;
                  return (
                    <li
                      key={card.question_id}
                      className="border-t border-border first:border-t-0"
                    >
                      <button
                        type="button"
                        className={cn(
                          "relative grid w-full grid-cols-[minmax(0,1fr)_18px] gap-x-2.5 px-3 py-3 text-left transition-colors",
                          "hover:bg-muted/40",
                          open && "bg-muted/40",
                        )}
                        aria-expanded={open}
                        onClick={() =>
                          setExpanded(open ? null : card.question_id)
                        }
                      >
                        {open ? (
                          <span
                            aria-hidden
                            className="absolute top-2 bottom-2 left-0 w-0.5 bg-primary"
                          />
                        ) : null}
                        <span className="min-w-0">
                          <span className="block text-[12.5px] leading-snug font-medium">
                            {card.question}
                          </span>
                          <span className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 font-mono text-[9px] text-muted-foreground">
                            <span>{difficultyLabel(card.difficulty)}</span>
                            {card.company ? (
                              <>
                                <span className="h-2 w-px bg-border" />
                                <span>{card.company}</span>
                              </>
                            ) : null}
                            {card.round ? (
                              <>
                                <span className="h-2 w-px bg-border" />
                                <span>{card.round}</span>
                              </>
                            ) : null}
                            <span className="h-2 w-px bg-border" />
                            <span>{card.topic}</span>
                          </span>
                        </span>
                        {open ? (
                          <ChevronUp
                            className="col-start-2 row-span-2 size-4 self-center text-muted-foreground"
                            strokeWidth={1.8}
                          />
                        ) : (
                          <ChevronDown
                            className="col-start-2 row-span-2 size-4 self-center text-muted-foreground"
                            strokeWidth={1.8}
                          />
                        )}
                      </button>

                      {open ? (
                        <div className="flex flex-col gap-3 border-t border-border bg-muted/20 px-3 py-3">
                          <section className="flex flex-col gap-1">
                            <MonoLabel>参考答案</MonoLabel>
                            <p className="text-xs leading-7 whitespace-pre-wrap text-foreground/90">
                              {card.answer}
                            </p>
                          </section>
                          {card.key_points.length > 0 ? (
                            <section className="flex flex-col gap-1.5">
                              <MonoLabel>关键点</MonoLabel>
                              <div className="flex flex-wrap gap-1.5">
                                {card.key_points.map((point) => (
                                  <Badge key={point} variant="secondary">
                                    {point}
                                  </Badge>
                                ))}
                              </div>
                            </section>
                          ) : null}
                          {card.sources.length > 0 ? (
                            <section className="flex flex-col gap-1">
                              <MonoLabel>来源</MonoLabel>
                              <div className="flex flex-col">
                                {card.sources.map((source, index) => (
                                  <div
                                    key={source.source}
                                    className="flex flex-wrap items-center justify-between gap-2 border-t border-border py-2 first:border-t-0 first:pt-0"
                                  >
                                    <span className="text-[11.5px]">
                                      {source.source}
                                      {index === 0 && card.sources.length > 1 ? (
                                        <span className="text-muted-foreground">
                                          （答案主源）
                                        </span>
                                      ) : null}
                                    </span>
                                    <span className="flex items-center gap-1.5">
                                      {source.license ? (
                                        <Badge variant="outline">
                                          {source.license}
                                        </Badge>
                                      ) : null}
                                      {source.url ? (
                                        <a
                                          href={source.url}
                                          target="_blank"
                                          rel="noreferrer"
                                          className="text-[11px] text-primary underline underline-offset-2 hover:text-primary-deep"
                                        >
                                          原文
                                        </a>
                                      ) : null}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </section>
                          ) : null}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
