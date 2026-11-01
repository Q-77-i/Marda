"use client";

import {
  ArrowRight,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { MonoLabel } from "@/components/ui/mono-label";
import { Segmented } from "@/components/ui/segmented";
import { ListSkeleton } from "@/components/ui/skeleton";
import { StatusBanner } from "@/components/ui/status-banner";
import { StatusDot } from "@/components/ui/status-dot";
import { deleteInterview, listInterviews, type InterviewPage } from "@/lib/api";
import { difficultyLabel, interviewTypeLabel, isBehavioral } from "@/lib/constants";
import { clearSavedDraft } from "@/lib/draft";
import { formatDuration, formatTime } from "@/lib/format";
import {
  EMPTY_INTERVIEW_FILTERS,
  pageAfterDelete,
  toInterviewQuery,
  withInterviewFilter,
  type InterviewFilters,
  type InterviewSinceFilter,
  type InterviewStatusFilter,
  type InterviewTypeFilter,
} from "@/lib/interviews";
import { totalPages } from "@/lib/pagination";
import { cn } from "@/lib/utils";

const STATUS_OPTIONS = [
  { value: "", label: "全部" },
  { value: "running", label: "进行中" },
  { value: "finished", label: "已完成" },
] as const;

const TYPE_OPTIONS = [
  { value: "", label: "全部" },
  { value: "tech", label: "技术面" },
  { value: "behavioral", label: "行为面" },
] as const;

const SINCE_OPTIONS = [
  { value: "", label: "全部" },
  { value: "7d", label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
] as const;

/**
 * 历史面试列表：进行中 → 续面，已完成 → 报告；每条可物理删除（T7a-R1）。
 *
 * 查询走服务端（分页 + 过滤，P2 面试记录页）：total/counts 基于过滤后的全集。
 * 仪表盘用 pageSize=5 + showControls=false 只要前几场；统计条读 counts，不再数行。
 */
export function InterviewList({
  pageSize = 10,
  showControls = true,
  showHeader = true,
  onCountsChange,
}: {
  /** 仪表盘只展示最近几场，完整列表在面试记录页 */
  pageSize?: number;
  /** 工具条与分页栏（独立页开，仪表盘关） */
  showControls?: boolean;
  /** 独立页面自带页头时关掉卡片头，否则标题重复 */
  showHeader?: boolean;
  /** 全量状态分布（随过滤条件变化）通知外层统计条 */
  onCountsChange?: (counts: InterviewPage["counts"]) => void;
}) {
  const [filters, setFilters] = useState<InterviewFilters>(EMPTY_INTERVIEW_FILTERS);
  const [data, setData] = useState<InterviewPage | null>(null);
  // 加载失败（列表没内容可显示）与删除失败（列表还在）分开：后者不该吃掉整块面板
  const [loadError, setLoadError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const onCountsChangeRef = useRef(onCountsChange);

  useEffect(() => {
    onCountsChangeRef.current = onCountsChange;
  }, [onCountsChange]);

  useEffect(() => {
    let active = true;
    setLoadError(null);
    listInterviews(toInterviewQuery(filters, pageSize))
      .then((page) => {
        if (!active) return;
        setData(page);
        onCountsChangeRef.current?.(page.counts);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setData(null);
        setLoadError(err instanceof Error ? err.message : "加载失败");
      });
    return () => {
      active = false;
    };
  }, [filters, pageSize, reloadKey]);

  const pages = totalPages(data?.total ?? null, pageSize);
  const filtered =
    filters.status !== "" || filters.type !== "" || filters.since !== "";

  async function handleDelete(id: string) {
    setDeletingId(id);
    setDeleteError(null);
    try {
      await deleteInterview(id);
      clearSavedDraft(id);
      // 删到当页空且不在第一页 → 回退一页；否则原地重取（total/counts 都要刷新）
      const remaining = (data?.items.length ?? 1) - 1;
      const nextPage = pageAfterDelete(filters.page, remaining);
      if (nextPage !== filters.page) {
        setFilters((prev) => ({ ...prev, page: nextPage }));
      } else {
        setReloadKey((key) => key + 1);
      }
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "删除失败，请重试");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <Card>
      {showHeader ? (
        <CardHeader>
          <div className="min-w-0">
            <CardTitle>面试记录</CardTitle>
            <p className="mt-1 text-[11px] text-muted-foreground">
              进行中的场次可以随时续面
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <MonoLabel className="hidden sm:inline">Sessions</MonoLabel>
            <Link
              href="/interviews"
              className="inline-flex h-[30px] items-center gap-1 rounded-md px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              全部场次
              <ArrowUpRight className="size-3.5" strokeWidth={1.8} />
            </Link>
          </div>
        </CardHeader>
      ) : null}

      {showControls ? (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-border px-[18px] py-3">
          <div className="flex items-center gap-2">
            <MonoLabel className="hidden sm:inline">状态</MonoLabel>
            <Segmented
              options={STATUS_OPTIONS}
              value={filters.status}
              onChange={(value) =>
                setFilters((prev) =>
                  withInterviewFilter(prev, {
                    status: value as InterviewStatusFilter,
                  }),
                )
              }
              ariaLabel="按状态筛选"
              className="w-[168px]"
            />
          </div>
          <div className="flex items-center gap-2">
            <MonoLabel className="hidden sm:inline">类型</MonoLabel>
            <Segmented
              options={TYPE_OPTIONS}
              value={filters.type}
              onChange={(value) =>
                setFilters((prev) =>
                  withInterviewFilter(prev, {
                    type: value as InterviewTypeFilter,
                  }),
                )
              }
              ariaLabel="按面试类型筛选"
              className="w-[168px]"
            />
          </div>
          <div className="flex items-center gap-2">
            <MonoLabel className="hidden sm:inline">时间</MonoLabel>
            <Segmented
              options={SINCE_OPTIONS}
              value={filters.since}
              onChange={(value) =>
                setFilters((prev) =>
                  withInterviewFilter(prev, {
                    since: value as InterviewSinceFilter,
                  }),
                )
              }
              ariaLabel="按开始时间筛选"
              className="w-[200px]"
            />
          </div>
        </div>
      ) : null}

      {deleteError ? (
        <div className="px-[18px] pt-3">
          <StatusBanner tone="error">{deleteError}</StatusBanner>
        </div>
      ) : null}

      {loadError ? (
        <div className="p-[18px]">
          <ErrorState
            message={loadError}
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() => setReloadKey((key) => key + 1)}
              >
                重试
              </Button>
            }
          />
        </div>
      ) : data === null ? (
        <div className="p-[18px]">
          <ListSkeleton rowClassName="h-12 w-full" />
        </div>
      ) : data.items.length === 0 ? (
        <EmptyState
          className="py-12"
          title={filtered ? "没有符合条件的场次" : "还没有面试记录"}
          description={
            filtered
              ? "换个筛选条件试试"
              : "创建一场面试后，这里会列出全部场次"
          }
          action={
            filtered ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setFilters({ ...EMPTY_INTERVIEW_FILTERS })}
              >
                清空筛选
              </Button>
            ) : undefined
          }
        />
      ) : (
        <ul className="flex flex-col">
          {data.items.map((row) => {
            const finished = row.status === "finished";
            const duration = formatDuration(row.started_at, row.ended_at);
            const behavioral = isBehavioral(row.interview_type);
            return (
              <li
                key={row.id}
                className="group relative flex items-center gap-2 border-t border-border px-[18px] py-3 transition-colors first:border-t-0 hover:bg-muted/40"
              >
                {!finished ? (
                  <span
                    aria-hidden
                    className="absolute top-2.5 bottom-2.5 left-0 w-0.5 bg-primary"
                  />
                ) : null}
                <Link
                  href={
                    finished ? `/report/${row.id}` : `/interview/${row.id}`
                  }
                  className="flex min-w-0 flex-1 items-center gap-3 outline-none focus-visible:rounded-md focus-visible:ring-2 focus-visible:ring-ring/30"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-[12.5px] font-medium">
                        {row.position}
                      </span>
                      <Badge variant={behavioral ? "secondary" : "outline"}>
                        {interviewTypeLabel(row.interview_type)}
                      </Badge>
                    </div>
                    <div className="mt-1 truncate font-mono text-[9.5px] text-muted-foreground">
                      {row.question_count} 题
                      {/* 行为面不展示难度（P1-M11 D3）：难度不参与行为面出题，是死数据 */}
                      {!behavioral ? ` / ${difficultyLabel(row.difficulty)}` : ""}
                      {duration ? ` / ${duration}` : ""}
                    </div>
                  </div>
                  <span className="hidden shrink-0 font-mono text-[9.5px] text-muted-foreground sm:block">
                    {formatTime(row.started_at)}
                  </span>
                  <span
                    className={cn(
                      "flex shrink-0 items-center gap-1.5 font-mono text-[9.5px] tracking-[0.04em]",
                      finished ? "text-success" : "text-primary",
                    )}
                  >
                    <StatusDot />
                    {finished ? "已完成" : "进行中"}
                  </span>
                  <ArrowRight
                    className="size-4 shrink-0 text-muted-foreground/60 transition-transform group-hover:translate-x-0.5 group-hover:text-primary"
                    strokeWidth={1.8}
                  />
                </Link>

                <AlertDialog>
                  <AlertDialogTrigger
                    render={
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="shrink-0 text-muted-foreground opacity-100 hover:text-destructive sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100"
                        aria-label={`删除这场面试（${row.question_count} 题）`}
                      />
                    }
                  >
                    <Trash2 className="size-3.5" strokeWidth={1.8} />
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>删除这场面试？</AlertDialogTitle>
                      <AlertDialogDescription>
                        面试记录、逐题回答与报告将被永久删除，无法恢复
                        {finished ? "" : "（含进行中的会话）"}。
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>取消</AlertDialogCancel>
                      <AlertDialogAction
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        disabled={deletingId !== null}
                        onClick={() => handleDelete(row.id)}
                      >
                        {deletingId === row.id ? "删除中…" : "确认删除"}
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </li>
            );
          })}
        </ul>
      )}

      {showControls && data !== null && data.total > 0 ? (
        <div className="flex items-center justify-between border-t border-border px-[18px] py-2">
          <span className="font-mono text-[9.5px] tracking-[0.04em] text-muted-foreground">
            共 {data.total} 场 / 第 {data.page} / {pages} 页
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="上一页"
              disabled={data.page <= 1}
              onClick={() =>
                setFilters((prev) =>
                  withInterviewFilter(prev, { page: prev.page - 1 }),
                )
              }
            >
              <ChevronLeft strokeWidth={1.8} />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="下一页"
              disabled={data.page >= pages}
              onClick={() =>
                setFilters((prev) =>
                  withInterviewFilter(prev, { page: prev.page + 1 }),
                )
              }
            >
              <ChevronRight strokeWidth={1.8} />
            </Button>
          </div>
        </div>
      ) : null}
    </Card>
  );
}
