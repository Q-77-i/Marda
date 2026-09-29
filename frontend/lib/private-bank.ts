/**
 * 私有题库纯逻辑（FR-13）：筛选状态 ↔ 查询参数、上传前置校验、结果条文案。
 *
 * 与 lib/bank.ts 分开的理由：两者筛选项不同（私有题无厂商/面次，多一个状态），
 * 且私有题的状态枚举被复用成中文语义（enabled = 使用中、draft = 已归档）——
 * 这层重命名只属于本功能，放 constants 会污染全局词表。
 */

import type { UploadReport } from "@/lib/api";

export type PrivateFilters = {
  status: string;
  domain: string;
  difficulty: string;
  page: number;
};

export const EMPTY_PRIVATE_FILTERS: PrivateFilters = {
  status: "",
  domain: "",
  difficulty: "",
  page: 1,
};

/** 改动任一筛选都要回第一页——否则会停在空页上（同 bank.ts 口径）。 */
export function withPrivateFilter(
  filters: PrivateFilters,
  patch: Partial<Omit<PrivateFilters, "page">>,
): PrivateFilters {
  return { ...filters, ...patch, page: 1 };
}

/** 筛选状态 → 查询参数：空值不发（后端把缺省视为不筛、且默认含已归档）。 */
export function toPrivateQuery(filters: PrivateFilters, pageSize: number): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of ["status", "domain", "difficulty"] as const) {
    if (filters[key]) params.set(key, filters[key]);
  }
  params.set("page", String(filters.page));
  params.set("page_size", String(pageSize));
  return params;
}

/**
 * 状态文案：后端复用 `enabled`/`draft` 枚举（与公共题同一套），私有题语义是
 * 「使用中/已归档」——照搬「启用/草稿」会让人以为是两回事。
 */
export function privateStatusLabel(status: string): string {
  if (status === "enabled") return "使用中";
  if (status === "draft") return "已归档";
  return status; // 未知值原样显示（不猜）
}

export const STATUS_OPTIONS = [
  { value: "", label: "全部" },
  { value: "enabled", label: "使用中" },
  { value: "draft", label: "已归档" },
] as const;

/** 与 backend `private_parse.UPLOAD_SUFFIXES` / `api.bank_private.MAX_UPLOAD_BYTES` 同口径。 */
export const UPLOAD_SUFFIXES = [".md", ".markdown", ".txt", ".pdf"];
export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;

/**
 * 上传前置校验，返回拒收原因（null = 可传）。文案与后端 400/413 逐字一致，
 * 用户不管在哪一侧被拦都看到同一句话。
 *
 * 本地先拦是为了不白传一次：2MB 的文件传到一半才 413，等的是自己的网速。
 */
export function fileRejectReason(file: { name: string; size: number }): string | null {
  const name = file.name.toLowerCase();
  if (!UPLOAD_SUFFIXES.some((suffix) => name.endsWith(suffix))) {
    return "仅支持 .md / .txt / .pdf 文件";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return "文件过大（上限 2MB）";
  }
  return null;
}

export type UploadTone = "ok" | "warn" | "none";

/**
 * 上传结果条：三份明细（导入/重复/失败）合成一句话。
 *
 * `parsed === 0` 单独成一档——它**不是失败**（一条错题都没有），而是文件里
 * 压根没有 `【题目】` 标记，此时错误列表是空的，只报「0 题」会让人以为坏题被吞了。
 */
export function uploadSummary(report: UploadReport): { tone: UploadTone; text: string } {
  if (report.parsed === 0) {
    return {
      tone: "none",
      text: "没有解析到题目——每题需以【题目】开头，【答案】紧随其后",
    };
  }
  const parts: string[] = [];
  if (report.imported > 0) parts.push(`导入 ${report.imported} 题`);
  if (report.duplicated.length > 0) parts.push(`重复 ${report.duplicated.length} 题（已存在，未覆盖）`);
  if (report.errors.length > 0) parts.push(`失败 ${report.errors.length} 题`);
  return { tone: report.imported > 0 ? "ok" : "warn", text: parts.join(" · ") };
}

/**
 * 关键点/追问的编辑态互转（textarea 一行一条 ↔ 字符串数组）。
 *
 * 分隔符与后端解析同口径（`；;` 与换行都算）：用户在模板里用分号写的内容，
 * 编辑框里展开成多行，存回去仍是一条一条，不因来回编辑把一条拆成三条。
 */
export function itemsToLines(items: string[]): string {
  return items.join("\n");
}

export function linesToItems(text: string): string[] {
  return text
    .split(/[\n；;]/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/**
 * 上传用的模板示例（页面上给用户抄的格式）。
 *
 * 用 `【题目】` 标记而非 markdown 的 `##`：PDF 抽文会把 `#` 丢掉，
 * 两种格式共用一套标记，用户不必按文件类型换写法。
 */
export const TEMPLATE_HINT = `【题目】Redis 持久化有哪几种？
【答案】RDB 与 AOF……
【关键点】RDB 快照；AOF 日志
【追问】AOF 重写怎么触发？

【题目】下一道题的题干……
【答案】……`;
