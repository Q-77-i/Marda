"use client";

import { ChevronDown, ChevronRight, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { MonoLabel } from "@/components/ui/mono-label";
import { Segmented } from "@/components/ui/segmented";
import { ListSkeleton } from "@/components/ui/skeleton";
import {
  getBankFacets,
  getBankQuestions,
  type BankFacets,
  type BankListResponse,
  type BankQuestion,
} from "@/lib/api";
import {
  EMPTY_FILTERS,
  selectOptions,
  toQuery,
  totalPages,
  withFilter,
  type BankFilters,
} from "@/lib/bank";
import { difficultyLabel, domainLabel } from "@/lib/constants";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 10;

/**
 * 题库浏览（FR-12）：知识域/难度/厂商/面次筛选 + 关键词检索。
 *
 * 三栏 master-detail：筛选、结果、详情。窄屏收起详情栏，改为行内展开，
 * 答案不会因为屏窄而消失。关键词模式走后端混合检索（向量 + rerank），
 * 按相关性返回单页 top 20、不翻页；无关键词时是 SQL 浏览（可分页）。
 * 筛选项候选值来自后端分面，不硬编。
 */
export function BankClient() {
  const [filters, setFilters] = useState<BankFilters>(EMPTY_FILTERS);
  const [draft, setDraft] = useState(""); // 搜索框输入（提交后才进 filters）
  const [facets, setFacets] = useState<BankFacets | null>(null);
  const [data, setData] = useState<BankListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

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

  // 数据变化后保证详情栏指向当前页的一道题；当前选中项不在了就落到第一条
  useEffect(() => {
    if (!data || data.items.length === 0) {
      setSelected(null);
      return;
    }
    setSelected((prev) =>
      data.items.some((item) => item.question_id === prev)
        ? prev
        : data.items[0].question_id,
    );
  }, [data]);

  const searchMode = data?.mode === "search";
  const pages = totalPages(data?.total ?? null, PAGE_SIZE);
  const selectedItem =
    data?.items.find((item) => item.question_id === selected) ?? null;
  const offset = searchMode ? 0 : ((data?.page ?? 1) - 1) * PAGE_SIZE;
  const domainTotal = facets
    ? facets.domain.reduce((sum, facet) => sum + facet.count, 0)
    : null;

  const difficultyOptions = [
    { value: "", label: "全部" },
    ...(facets?.difficulty ?? []).map((facet) => ({
      value: facet.value,
      label: facet.value,
      title: difficultyLabel(facet.value),
    })),
  ];

  function resetFilters() {
    setDraft("");
    setFilters(EMPTY_FILTERS);
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[200px_minmax(0,1fr)] xl:grid-cols-[208px_minmax(0,1fr)_356px] xl:items-start">
      <Card className="xl:sticky xl:top-5">
        <CardHeader>
          <CardTitle>筛选</CardTitle>
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            清空
          </Button>
        </CardHeader>
        <div className="flex flex-col gap-5 p-[14px]">
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              setFilters((prev) => withFilter(prev, { q: draft }));
            }}
          >
            <div className="relative">
              <Search
                className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground"
                strokeWidth={1.8}
              />
              <Input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="搜题，例如 RAG 切片"
                aria-label="关键词搜索"
                className="h-9 pl-8 text-xs"
              />
            </div>
            {filters.q ? (
              <button
                type="button"
                onClick={() => {
                  setDraft("");
                  setFilters((prev) => withFilter(prev, { q: "" }));
                }}
                className="self-start font-mono text-[9.5px] text-muted-foreground underline underline-offset-2 hover:text-foreground"
              >
                清除搜索：{filters.q}
              </button>
            ) : null}
          </form>

          {facets === null ? (
            <p className="text-[10.5px] leading-relaxed text-muted-foreground">
              筛选项加载失败，可用关键词搜索。
            </p>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between px-1.5">
              <MonoLabel>知识域</MonoLabel>
              <span className="font-mono text-[9px] text-muted-foreground">
                {domainTotal ?? ""}
              </span>
            </div>
            <div className="flex flex-col gap-px">
              <DomainButton
                label="全部"
                count={domainTotal}
                active={filters.domain === ""}
                onClick={() => setFilters((prev) => withFilter(prev, { domain: "" }))}
              />
              {(facets?.domain ?? []).map((facet) => (
                <DomainButton
                  key={facet.value}
                  label={domainLabel(facet.value)}
                  count={facet.count}
                  active={filters.domain === facet.value}
                  onClick={() =>
                    setFilters((prev) => withFilter(prev, { domain: facet.value }))
                  }
                />
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <MonoLabel>难度</MonoLabel>
            <Segmented
              options={difficultyOptions}
              value={filters.difficulty}
              onChange={(value) =>
                setFilters((prev) => withFilter(prev, { difficulty: value }))
              }
              columns={4}
              ariaLabel="难度"
            />
          </div>

          <SelectField
            label="厂商"
            value={filters.company}
            options={selectOptions(facets?.company)}
            onChange={(value) =>
              setFilters((prev) => withFilter(prev, { company: value }))
            }
          />
          <SelectField
            label="面次"
            value={filters.round}
            options={selectOptions(facets?.round)}
            onChange={(value) =>
              setFilters((prev) => withFilter(prev, { round: value }))
            }
          />
        </div>
      </Card>

      <Card className="min-w-0">
        <CardHeader>
          <div className="min-w-0">
            <CardTitle>{searchMode ? "检索结果" : "全部题目"}</CardTitle>
            <p className="mt-1 font-mono text-[9.5px] text-muted-foreground">
              {searchMode
                ? "按相关性排序 / 最多 20 条"
                : data
                  ? `共 ${data.total} 题 / 第 ${data.page} / ${pages} 页`
                  : ""}
            </p>
          </div>
          <MonoLabel>{searchMode ? "Search" : "Browse"}</MonoLabel>
        </CardHeader>

        {loading ? (
          <div className="p-[18px]">
            <ListSkeleton />
          </div>
        ) : error ? (
          <div className="p-[18px]">
            <ErrorState
              message={error}
              action={
                <Button variant="outline" size="sm" onClick={() => void load(filters)}>
                  重试
                </Button>
              }
            />
          </div>
        ) : !data || data.items.length === 0 ? (
          <EmptyState
            className="py-12"
            title="没有匹配的题目"
            description={searchMode ? "换个说法再搜，或清空筛选条件" : "试试放宽筛选条件"}
            action={
              <Button variant="outline" size="sm" onClick={resetFilters}>
                清空筛选
              </Button>
            }
          />
        ) : (
          <div role="listbox" aria-label="题目列表" className="flex flex-col">
            {data.items.map((item, index) => {
              const active = selected === item.question_id;
              return (
                <div
                  key={item.question_id}
                  className="border-t border-border first:border-t-0"
                >
                  <button
                    type="button"
                    role="option"
                    aria-selected={active}
                    onClick={() => setSelected(item.question_id)}
                    className={cn(
                      "relative grid w-full grid-cols-[30px_minmax(0,1fr)_18px] gap-2.5 px-4 py-3 text-left transition-colors",
                      "hover:bg-muted/40",
                      active && "bg-muted/40",
                    )}
                  >
                    {active ? (
                      <span
                        aria-hidden
                        className="absolute top-2 bottom-2 left-0 w-0.5 bg-primary"
                      />
                    ) : null}
                    <span className="pt-0.5 font-mono text-[9.5px] text-muted-foreground">
                      {String(offset + index + 1).padStart(3, "0")}
                    </span>
                    <span className="min-w-0">
                      <span className="block text-[12.5px] leading-snug font-medium">
                        {item.question}
                      </span>
                      <span className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 font-mono text-[9px] text-muted-foreground">
                        <span>{domainLabel(item.domain)}</span>
                        <span className="h-2 w-px bg-border" />
                        <span>{difficultyLabel(item.difficulty)}</span>
                        {item.company ? (
                          <>
                            <span className="h-2 w-px bg-border" />
                            <span>{item.company}</span>
                          </>
                        ) : null}
                        {item.round ? (
                          <>
                            <span className="h-2 w-px bg-border" />
                            <span>{item.round}</span>
                          </>
                        ) : null}
                        <span className="h-2 w-px bg-border" />
                        <span>{item.topic}</span>
                      </span>
                    </span>
                    <ChevronRight
                      className="size-4 self-center text-muted-foreground/50"
                      strokeWidth={1.8}
                    />
                  </button>

                  {/* 窄屏：详情栏收起，改为行内展开；宽屏只认右侧详情栏 */}
                  {active ? (
                    <div className="xl:hidden">
                      <QuestionDetail item={item} compact />
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}

        {!loading && !error && !searchMode && data && data.total ? (
          <div className="flex items-center justify-between border-t border-border px-4 py-3">
            <Button
              variant="outline"
              size="sm"
              disabled={data.page <= 1}
              onClick={() => setFilters((prev) => ({ ...prev, page: prev.page - 1 }))}
            >
              上一页
            </Button>
            <span className="font-mono text-[9.5px] text-muted-foreground">
              {String(data.page).padStart(2, "0")} / {String(pages).padStart(2, "0")}
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
      </Card>

      <Card className="hidden overflow-visible xl:sticky xl:top-5 xl:block">
        <CardHeader>
          <CardTitle>题目详情</CardTitle>
          <MonoLabel>Question</MonoLabel>
        </CardHeader>
        {selectedItem ? (
          <QuestionDetail item={selectedItem} />
        ) : (
          <div className="p-[18px] text-xs text-muted-foreground">
            从左侧选一道题查看答案与来源。
          </div>
        )}
      </Card>
    </div>
  );
}

function DomainButton({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number | null;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "flex h-[30px] items-center justify-between gap-2 rounded-md border-l-2 border-transparent px-2 text-left text-[11.5px] transition-colors",
        active
          ? "border-primary bg-accent font-medium text-accent-foreground"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      <span className="truncate">{label}</span>
      {count !== null ? (
        <span className="font-mono text-[9px] text-muted-foreground">{count}</span>
      ) : null}
    </button>
  );
}

function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex flex-col gap-2">
      <MonoLabel>{label}</MonoLabel>
      <div className="relative">
        <select
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="h-9 w-full appearance-none rounded-lg border border-input bg-card px-3 pr-8 text-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20"
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <ChevronDown
          className="pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
          strokeWidth={1.8}
        />
      </div>
    </label>
  );
}

function QuestionDetail({
  item,
  compact = false,
}: {
  item: BankQuestion;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-4",
        compact
          ? "border-t border-border bg-muted/20 px-4 py-4"
          : "p-[18px]",
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge>{domainLabel(item.domain)}</Badge>
        <Badge variant="outline">{difficultyLabel(item.difficulty)}</Badge>
        {item.company ? (
          <span className="font-mono text-[9.5px] text-muted-foreground">
            {item.company}
          </span>
        ) : null}
        {item.round ? (
          <span className="font-mono text-[9.5px] text-muted-foreground">
            {item.round}
          </span>
        ) : null}
      </div>

      <h2 className="text-base leading-snug font-semibold tracking-[-0.015em]">
        {item.question}
      </h2>

      <section className="flex flex-col gap-1.5">
        <MonoLabel>参考答案</MonoLabel>
        <p className="text-xs leading-7 whitespace-pre-wrap text-foreground/90">
          {item.answer}
        </p>
      </section>

      {item.key_points.length > 0 ? (
        <section className="flex flex-col gap-1.5">
          <MonoLabel>关键点</MonoLabel>
          <ol className="flex flex-col gap-1.5">
            {item.key_points.map((point, index) => (
              <li
                key={point}
                className="grid grid-cols-[18px_minmax(0,1fr)] gap-1.5 text-[11.5px] leading-relaxed"
              >
                <span className="pt-0.5 font-mono text-[9px] text-muted-foreground">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span>{point}</span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      {item.sources.length > 0 ? (
        <section className="flex flex-col gap-1.5">
          <MonoLabel>来源与许可</MonoLabel>
          <div className="flex flex-col">
            {item.sources.map((source, index) => (
              <div
                key={source.source}
                className="flex flex-col gap-1 border-t border-border py-2 first:border-t-0 first:pt-0"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[11.5px]">
                    {source.source}
                    {index === 0 && item.sources.length > 1 ? (
                      <span className="text-muted-foreground">（答案主源）</span>
                    ) : null}
                  </span>
                  <span className="flex items-center gap-1.5">
                    {source.license ? (
                      <Badge variant="outline">{source.license}</Badge>
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
                {source.source_detail ? (
                  <span className="text-[10.5px] leading-relaxed text-muted-foreground">
                    {source.source_detail}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
