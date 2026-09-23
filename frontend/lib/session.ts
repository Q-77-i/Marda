/**
 * 登录态（FR-23，SPEC §7）：token 存哪、请求怎么带、401 怎么处置。
 *
 * 无业务依赖、无网络调用（纯浏览器存储逻辑，便于单测）。
 *
 * 存 localStorage 的理由：SSE 走 fetch 能带 header（EventSource 带不了），
 * cookie 方案要额外配 CSRF —— 见 P1-M2 拍板。localStorage 不可用时
 * （隐私模式 / 企业策略 / 存储被禁用）降级 sessionStorage：本标签页内仍可登录，
 * 关标签即失效；两者都不可用时 isStorageAvailable() 为 false，由登录页明确提示，
 * 不静默失败（P1-M2 会话 2 补充点 ③）。
 */

const TOKEN_KEY = "marda_token";

/**
 * 探测存储可用性：读属性与写入都可能抛（Safari 隐私模式、企业策略），
 * 一律按「不可用」处理 —— 抛异常等同于没有这个存储。
 */
function probe(read: () => Storage | undefined): Storage | null {
  try {
    const storage = read();
    if (!storage) return null;
    const key = "__marda_probe__";
    storage.setItem(key, "1");
    storage.removeItem(key);
    return storage;
  } catch {
    return null;
  }
}

/** localStorage 优先（刷新与多标签都保持），不可用则降级 sessionStorage。 */
function resolveStorage(): Storage | null {
  return probe(() => globalThis.localStorage) ?? probe(() => globalThis.sessionStorage);
}

export function isStorageAvailable(): boolean {
  return resolveStorage() !== null;
}

export function getToken(): string | null {
  return resolveStorage()?.getItem(TOKEN_KEY) ?? null;
}

/** 写入 token；返回 false 表示当前环境无法持久化（调用方应提示用户）。 */
export function setToken(token: string): boolean {
  const storage = resolveStorage();
  if (!storage) return false;
  storage.setItem(TOKEN_KEY, token);
  return true;
}

export function clearToken(): void {
  resolveStorage()?.removeItem(TOKEN_KEY);
}

/** 登录态请求头；未登录返回空对象（是否放行由后端判定）。 */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 401 专用错误：调用方据此区分「需重新登录」与其他失败（此类失败重试无意义）。 */
export class UnauthorizedError extends Error {
  constructor(message = "登录已过期，请重新登录") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

export function redirectToLogin(): void {
  if (typeof window !== "undefined") window.location.assign("/login");
}

/**
 * 401 处置策略：默认「清 token + 整页跳登录」。
 *
 * 面试页会临时接管为「弹确认再跳」—— 答题答到一半被直接踢走体感很差，
 * 且用户不知道发生了什么（P1-M2 会话 2 补充点 ①）。传 null 恢复默认。
 */
let handleUnauthorized: () => void = redirectToLogin;

export function setUnauthorizedHandler(next: (() => void) | null): void {
  handleUnauthorized = next ?? redirectToLogin;
}

/** 401 统一处置：token 先清（已失效），再交给当前策略决定怎么告知用户。 */
export function notifyUnauthorized(): void {
  clearToken();
  handleUnauthorized();
}
