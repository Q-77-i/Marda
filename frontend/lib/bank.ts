/**
 * 题库页纯逻辑（FR-12 / FR-14）：筛选状态 ↔ 查询参数、容量禁用判据。
 *
 * 页面组件只消费这里的返回值，不在渲染里推算（可单测）；筛选项候选值
 * 一律来自后端分面（不硬编厂商/面次），知识域标签走 constants.DOMAIN_LABELS。
 */

import type { CapacityOption, FacetValue } from "@/lib/api";

export type BankFilters = {
  domain: string;
  difficulty: string;
  company: string;
  round: string;
  q: string;
  page: number;
};

export const EMPTY_FILTERS: BankFilters = {
  domain: "",
  difficulty: "",
  company: "",
  round: "",
  q: "",
  page: 1,
};

/** 改动任一筛选（含输入关键词）都要回第一页——否则会停在空页上。 */
export function withFilter(
  filters: BankFilters,
  patch: Partial<Omit<BankFilters, "page">>,
): BankFilters {
  return { ...filters, ...patch, page: 1 };
}

/** 筛选状态 → 查询参数：空值不发（后端把缺省视为不筛），q 走关键词检索模式。 */
export function toQuery(filters: BankFilters, pageSize: number): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of ["domain", "difficulty", "company", "round"] as const) {
    if (filters[key]) params.set(key, filters[key]);
  }
  const q = filters.q.trim();
  if (q) params.set("q", q);
  params.set("page", String(filters.page));
  params.set("page_size", String(pageSize));
  return params;
}

export function totalPages(total: number | null, pageSize: number): number {
  if (!total) return 1;
  return Math.max(1, Math.ceil(total / pageSize));
}

/** 某难度下某题数的容量结论（未加载完返回 null = 不禁用，不因网络慢挡住用户）。 */
export function capacityFor(
  options: CapacityOption[] | null,
  difficulty: string,
  questionCount: number,
): CapacityOption | null {
  if (!options) return null;
  return (
    options.find((o) => o.difficulty === difficulty && o.question_count === questionCount) ?? null
  );
}

/** 容量不足的中文提示（缺在哪个域、差几题）——比"题量不足"有信息量。 */
export function shortfallMessage(
  option: CapacityOption | null,
  domainLabel: (domain: string) => string,
): string | null {
  if (!option || option.ok || option.shortfalls.length === 0) return null;
  const parts = option.shortfalls.map(
    (s) => `${domainLabel(s.domain)}（需 ${s.required} 题，题库 ${s.available} 题）`,
  );
  return `题库直供不足：${parts.join("、")}`;
}

/** 分面下拉选项：空值 = "全部"；值原样展示（计数不进选项，结果区给总数就够）。 */
export function selectOptions(facet: FacetValue[] | undefined): { value: string; label: string }[] {
  return [{ value: "", label: "全部" }, ...(facet ?? []).map((f) => ({ value: f.value, label: f.value }))];
}
