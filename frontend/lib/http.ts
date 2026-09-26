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

/** 中文字符判定：后端业务文案（「面试不存在」）透传，框架兜底的英文原文
 * （Not Found / Internal Server Error，路由缺失或 500 时出现）不端给用户。 */
const CJK = /[一-鿿]/;

/** 非 2xx 时的提示文案：优先用后端 detail（中文业务文案），否则退回通用文案。 */
export async function responseError(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string" && CJK.test(payload.detail)) {
      return payload.detail;
    }
  } catch {
    // 非 JSON 响应，走通用文案
  }
  return `请求失败（${response.status}）`;
}
