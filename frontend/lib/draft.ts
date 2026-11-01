/**
 * 面试输入框草稿（2026-11）：按场次存 localStorage，刷新 / 误关页面后恢复。
 *
 * 只存在用户自己的浏览器里，不落服务端；发送成功、场次结束或删除场次时清除。
 * 存储不可用（隐私模式 / 配额 / 企业策略）时静默降级——草稿丢失只是体验退化，
 * 不能影响面试本身。
 */

export const DRAFT_STORAGE_PREFIX = "marda.draft";

export function draftStorageKey(interviewId: string): string {
  return `${DRAFT_STORAGE_PREFIX}.${interviewId}`;
}

/** 读取到的原文本；空串 / 纯空白视为没有草稿。保留首尾空白，不替用户改写。 */
export function parseSavedDraft(raw: string | null): string | null {
  return raw !== null && raw.trim() ? raw : null;
}

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function readSavedDraft(interviewId: string): string | null {
  const store = storage();
  if (!store) return null;
  try {
    return parseSavedDraft(store.getItem(draftStorageKey(interviewId)));
  } catch {
    return null;
  }
}

export function writeSavedDraft(interviewId: string, text: string): void {
  const store = storage();
  if (!store) return;
  try {
    if (text.trim()) store.setItem(draftStorageKey(interviewId), text);
    else store.removeItem(draftStorageKey(interviewId));
  } catch {
    // 存储不可用：只是刷新后不恢复
  }
}

export function clearSavedDraft(interviewId: string): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(draftStorageKey(interviewId));
  } catch {
    // 同上：清除失败不阻塞面试
  }
}
