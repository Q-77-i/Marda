/**
 * 请求基座：带登录态的 fetch 与统一错误文案。
 *
 * 注入 Authorization → 网络层异常转中文 → 401 交 session 统一处置后抛
 * UnauthorizedError；其余非 2xx 原样返回 Response，由调用方按业务处理
 * （404/409/422 的文案各不相同）。
 */

import { UnauthorizedError, authHeaders, notifyUnauthorized } from "@/lib/session";

/** 网络层失败（后端未启动/断开）转成中文提示；主动取消原样抛出。 */
export function networkMessage(err: unknown): string {
  if (err instanceof DOMException && err.name === "AbortError") {
    return "请求已取消";
  }
  return "无法连接服务器，请确认后端已启动后重试";
}

export async function authorizedFetch(
  url: string,
  init: Omit<RequestInit, "headers"> & { headers?: Record<string, string> } = {},
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { ...init.headers, ...authHeaders() },
    });
  } catch (err) {
    throw new Error(networkMessage(err));
  }
  if (response.status === 401) {
    notifyUnauthorized();
    throw new UnauthorizedError();
  }
  return response;
}

/** 非 2xx 时的提示文案：优先用后端 detail，非 JSON 响应退回通用文案。 */
export async function responseError(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string") return payload.detail;
  } catch {
    // 非 JSON 响应，走通用文案
  }
  return `请求失败（${response.status}）`;
}
