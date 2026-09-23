import { afterEach, describe, expect, it, vi } from "vitest";

import { authorizedFetch, responseError } from "@/lib/http";
import {
  UnauthorizedError,
  getToken,
  setToken,
  setUnauthorizedHandler,
} from "@/lib/session";

/** 内存版 Storage（同 session.test.ts：node 环境没有浏览器存储）。 */
function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (key: string) => map.get(key) ?? null,
    key: (index: number) => [...map.keys()][index] ?? null,
    removeItem: (key: string) => void map.delete(key),
    setItem: (key: string, value: string) => void map.set(key, value),
  } as unknown as Storage;
}

function stubFetch(handler: (url: string, init: RequestInit) => Response): RequestInit[] {
  const seen: RequestInit[] = [];
  vi.stubGlobal("fetch", (url: string, init: RequestInit = {}) => {
    seen.push(init);
    return Promise.resolve(handler(url, init));
  });
  return seen;
}

afterEach(() => {
  setUnauthorizedHandler(null); // 恢复默认策略，避免用例间串味
  vi.unstubAllGlobals();
  delete (globalThis as { localStorage?: unknown }).localStorage;
});

describe("authorizedFetch", () => {
  it("带上 Bearer 头", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    setToken("t-http");
    const seen = stubFetch(() => new Response("{}", { status: 200 }));
    await authorizedFetch("/api/interviews");
    expect((seen[0].headers as Record<string, string>).Authorization).toBe("Bearer t-http");
  });

  it("未登录时不伪造 Authorization 头", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const seen = stubFetch(() => new Response("{}", { status: 200 }));
    await authorizedFetch("/api/interviews");
    expect(seen[0].headers).toEqual({});
  });

  it("401 清 token + 交给当前策略，并抛 UnauthorizedError", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    setToken("t-dead");
    const handled: string[] = [];
    setUnauthorizedHandler(() => handled.push("handled"));
    stubFetch(() => new Response(JSON.stringify({ detail: "未登录或登录已过期" }), { status: 401 }));

    await expect(authorizedFetch("/api/interviews")).rejects.toBeInstanceOf(UnauthorizedError);
    expect(handled).toEqual(["handled"]);
    expect(getToken()).toBeNull();
  });

  it("其他非 2xx 原样返回（由调用方按业务处理）", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const handled: string[] = [];
    setUnauthorizedHandler(() => handled.push("handled"));
    stubFetch(() => new Response(JSON.stringify({ detail: "面试不存在" }), { status: 404 }));

    const response = await authorizedFetch("/api/interviews/x");
    expect(response.status).toBe(404);
    expect(await responseError(response)).toBe("面试不存在");
    expect(handled).toEqual([]);
  });

  it("网络层失败转中文提示", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("Failed to fetch")));
    await expect(authorizedFetch("/api/x")).rejects.toThrow("无法连接服务器");
  });
});
