"use client";

import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { InlinePanel } from "@/components/ui/inline-panel";
import { Input } from "@/components/ui/input";
import { ListSkeleton } from "@/components/ui/skeleton";
import {
  getBankFacets,
  getBankQuestions,
  type BankFacets,
  type BankListResponse,
} from "@/lib/api";
import {
  EMPTY_FILTERS,
  selectOptions,
  toQuery,
  totalPages,
  withFilter,
  type BankFilters,
} from "@/lib/bank";
import { domainLabel, difficultyLabel } from "@/lib/constants";

const PAGE_SIZE = 10;

/**
 * 题库浏览（FR-12）：知识域/难度/厂商/面次筛选 + 关键词检索。
 *
 * 关键词模式走后端混合检索（向量 + rerank），按相关性返回单页 top 20、不翻页；
 * 无关键词时是 SQL 浏览（可分页）。筛选项候选值来自后端分面，不硬编。
 */
export function BankClient() {
  const [filters, setFilters] = useState<BankFilters>(EMPTY_FILTERS);
  const [draft, setDraft] = useState(""); // 搜索框输入（提交后才进 filters）
  const [facets, setFacets] = useState<BankFacets | null>(null);
  const [data, setData] = useState<BankListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    getBankFacets()
      .then(setFacets)
      .catch(() => setFacets(null)); // 分面失败只影响筛选项，不挡浏览
  }, []);

  const load = useCallback(async (next: BankFilters) => {
    setLoading(true);
    setError(null);
    try {
      const body = await getBankQuestions(toQuery(next, PAGE_SIZE));
      setData(body);
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? err.message : "题库加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(filters);
  }, [filters, load]);

  const searchMode = data?.mode === "search";
  const pages = totalPages(data?.total ?? null, PAGE_SIZE);

  return (
    <>
      <PageHeader
        title="题库"
        description="按知识域、难度、厂商、面试轮次筛选，或直接搜题；每题都带来源与许可"
      />
      <Card>
        <CardContent className="flex flex-col gap-4">
          <form
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              setFilters((prev) => withFilter(prev, { q: draft }));
            }}
          >
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="搜索题目（如：RAG 切片粒度怎么定）"
              aria-label="关键词搜索"
            />
            <Button type="submit" disabled={loading}>
              搜索
            </Button>
            {filters.q ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setDraft("");
                  setFilters((prev) => withFilter(prev, { q: "" }));
                }}
              >
                清除
              </Button>
            ) : null}
          </form>

          {facets ? (
            <div className="flex flex-col gap-3">
              <FilterChips
                facet={facets.domain}
                selected={filters.domain}
                onSelect={(value) => setFilters((prev) => withFilter(prev, { domain: value }))}
              />
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {(
                  [
                    ["difficulty", "难度"],
                    ["company", "厂商"],
                    ["round", "面次"],
                  ] as const
                ).map(([kind, label]) => (
                  <label key={kind} className="flex items-center gap-2 text-sm">
                    <span className="shrink-0 text-muted-foreground">{label}</span>
                    <select
                      className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
                      value={filters[kind]}
                      onChange={(event) =>
                        setFilters((prev) => withFilter(prev, { [kind]: event.target.value }))
                      }
                    >
                      {selectOptions(facets[kind]).map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">筛选项加载失败，可用关键词搜索</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-4">
          <CardTitle className="text-base">
            {searchMode ? "检索结果" : "全部题目"}
          </CardTitle>
          <span className="text-xs text-muted-foreground">
            {searchMode
              ? "按相关性排序 · 最多 20 条"
              : data
                ? `共 ${data.total} 题 · 第 ${data.page}/${pages} 页`
                : ""}
          </span>
        </CardHeader>
        <CardContent>
          {loading ? (
            <ListSkeleton />
          ) : error ? (
            <ErrorState
              message={error}
              action={
                <Button variant="outline" size="sm" onClick={() => void load(filters)}>
                  重试
                </Button>
              }
            />
          ) : !data || data.items.length === 0 ? (
            <EmptyState
              className="py-12"
              title="没有匹配的题目"
              description={searchMode ? "换个说法再搜，或清空筛选条件" : "试试放宽筛选条件"}
              action={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setDraft("");
                    setFilters(EMPTY_FILTERS);
                  }}
                >
                  清空筛选
                </Button>
              }
            />
          ) : (
            <ul className="divide-y">
              {data.items.map((item) => {
                const open = expanded === item.question_id;
                return (
                  <li key={item.question_id} className="py-3">
                    <button
                      type="button"
                      className="flex w-full flex-col gap-2 text-left"
                      aria-expanded={open}
                      onClick={() => setExpanded(open ? null : item.question_id)}
                    >
                      <span className="text-sm font-medium leading-relaxed">{item.question}</span>
                      <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                        <Badge variant="secondary">{domainLabel(item.domain)}</Badge>
                        <Badge variant="outline">{difficultyLabel(item.difficulty)}</Badge>
                        {item.company ? <span>{item.company}</span> : null}
                        {item.round ? <span>· {item.round}</span> : null}
                        <span>· {item.topic}</span>
                      </span>
                    </button>

                    {open ? (
                      <InlinePanel>
                        <section className="flex flex-col gap-1">
                          <h3 className="text-xs font-medium text-muted-foreground">参考答案</h3>
                          <p className="whitespace-pre-wrap text-sm leading-relaxed">
                            {item.answer}
                          </p>
                        </section>
                        {item.key_points.length > 0 ? (
                          <section className="flex flex-col gap-1">
                            <h3 className="text-xs font-medium text-muted-foreground">关键点</h3>
                            <ul className="flex flex-wrap gap-1.5">
                              {item.key_points.map((point) => (
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
                        {item.sources.length > 0 ? (
                          <section className="flex flex-col gap-1">
                            <h3 className="text-xs font-medium text-muted-foreground">来源</h3>
                            <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
                              {item.sources.map((source, index) => (
                                <li key={source.source} className="flex flex-wrap items-center gap-1.5">
                                  <span className="text-foreground/80">{source.source}</span>
                                  {source.license ? (
                                    <Badge variant="outline">{source.license}</Badge>
                                  ) : null}
                                  {index === 0 && item.sources.length > 1 ? (
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

          {!loading && !error && !searchMode && data && data.total ? (
            <div className="flex items-center justify-between gap-2 pt-4">
              <Button
                variant="outline"
                size="sm"
                disabled={data.page <= 1}
                onClick={() => setFilters((prev) => ({ ...prev, page: prev.page - 1 }))}
              >
                上一页
              </Button>
              <span className="tabular-nums text-xs text-muted-foreground">
                {data.page} / {pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={data.page >= pages}
                onClick={() => setFilters((prev) => ({ ...prev, page: prev.page + 1 }))}
              >
                下一页
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </>
  );
}

/** 知识域筛选：横向 chips（域不多，比下拉直观），不带计数。 */
function FilterChips({
  facet,
  selected,
  onSelect,
}: {
  facet: { value: string }[];
  selected: string;
  onSelect: (value: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {[{ value: "" }, ...facet].map((item) => {
        const active = selected === item.value;
        return (
          <button
            key={item.value || "all"}
            type="button"
            aria-pressed={active}
            onClick={() => onSelect(item.value)}
            className={
              "rounded-full border px-2.5 py-1 text-xs transition-colors " +
              (active ? "bg-primary text-primary-foreground" : "hover:bg-muted")
            }
          >
            {item.value ? domainLabel(item.value) : "全部"}
          </button>
        );
      })}
    </div>
  );
}
