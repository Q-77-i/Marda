/** 分页页数：按每页条数向上取整，无数据/未知也至少 1 页（避免「第 0 页」）。 */
export function totalPages(total: number | null, pageSize: number): number {
  if (!total) return 1;
  return Math.max(1, Math.ceil(total / pageSize));
}
