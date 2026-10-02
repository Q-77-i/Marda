"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { InlinePanel } from "@/components/ui/inline-panel";
import { ListSkeleton } from "@/components/ui/skeleton";
import { getRecommendations, type RecommendationGroup } from "@/lib/api";
import { domainLabel, difficultyLabel } from "@/lib/constants";
import { groupNotice } from "@/lib/learn";

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
}: {
  interviewId: string;
  /** 报告页已有「学习建议」卡（同一批文案），传 false 避免同页复述 */
  showAdvice?: boolean;
}) {
  const [groups, setGroups] = useState<RecommendationGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setGroups(null);
    try {
      const body = await getRecommendations(interviewId);
      setGroups(body.groups);
    } catch (err) {
      setError(err instanceof Error ? err.message : "推荐加载失败");
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
        className="py-6"
        title="本场没有定位到短板域"
        description="暂无针对性推荐"
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {groups.map((group) => {
        const notice = groupNotice(group.status);
        return (
          <section key={group.domain} className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="text-warning">
                  {domainLabel(group.domain)}
                </Badge>
                <span className="text-xs text-muted-foreground">薄弱知识域</span>
              </div>
              {showAdvice && group.advice ? (
                <p className="text-sm leading-relaxed text-foreground/90">{group.advice}</p>
              ) : null}
            </div>

            {notice ? (
              <p className="rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground">
                {notice}
              </p>
            ) : (
              <ul className="divide-y rounded-lg border">
                {group.cards.map((card) => {
                  const open = expanded === card.question_id;
                  return (
                    <li key={card.question_id} className="px-3 py-3">
                      <button
                        type="button"
                        className="flex w-full flex-col gap-2 text-left"
                        aria-expanded={open}
                        onClick={() => setExpanded(open ? null : card.question_id)}
                      >
                        <span className="text-sm font-medium leading-relaxed">
                          {card.question}
                        </span>
                        <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                          <Badge variant="outline">{difficultyLabel(card.difficulty)}</Badge>
                          {card.company ? <span>{card.company}</span> : null}
                          {card.round ? <span>· {card.round}</span> : null}
                          <span>· {card.topic}</span>
                        </span>
                      </button>

                      {open ? (
                        <InlinePanel>
                          <section className="flex flex-col gap-1">
                            <h3 className="text-xs font-medium text-muted-foreground">参考答案</h3>
                            <p className="whitespace-pre-wrap text-sm leading-relaxed">
                              {card.answer}
                            </p>
                          </section>
                          {card.key_points.length > 0 ? (
                            <section className="flex flex-col gap-1">
                              <h3 className="text-xs font-medium text-muted-foreground">关键点</h3>
                              <ul className="flex flex-wrap gap-1.5">
                                {card.key_points.map((point) => (
                                  <li
                                    key={point}
                                    className="rounded-md bg-background px-2 py-0.5 text-xs"
                                  >
                                    {point}
                                  </li>
                                ))}
                              </ul>
                            </section>
                          ) : null}
                          {card.sources.length > 0 ? (
                            <section className="flex flex-col gap-1">
                              <h3 className="text-xs font-medium text-muted-foreground">来源</h3>
                              <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
                                {card.sources.map((source, index) => (
                                  <li
                                    key={source.source}
                                    className="flex flex-wrap items-center gap-1.5"
                                  >
                                    <span className="text-foreground/80">{source.source}</span>
                                    {source.license ? (
                                      <Badge variant="outline">{source.license}</Badge>
                                    ) : null}
                                    {index === 0 && card.sources.length > 1 ? (
                                      <span>（答案主源）</span>
                                    ) : null}
                                    {source.source_detail ? <span>{source.source_detail}</span> : null}
                                    {source.url ? (
                                      <a
                                        href={source.url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="underline hover:text-foreground"
                                      >
                                        原文
                                      </a>
                                    ) : null}
                                  </li>
                                ))}
                              </ul>
                            </section>
                          ) : null}
                        </InlinePanel>
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
