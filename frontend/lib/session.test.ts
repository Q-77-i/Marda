import { afterEach, describe, expect, it } from "vitest";

import {
  authHeaders,
  clearToken,
  getToken,
  isStorageAvailable,
  setToken,
} from "@/lib/session";

/** 内存版 Storage（node 环境没有浏览器存储）。 */
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

/** 读属性就抛的存储（Safari 隐私模式 / 企业策略）。 */
function stubBlocked(name: "localStorage" | "sessionStorage"): void {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    get() {
      throw new DOMException("The operation is insecure.", "SecurityError");
    },
  });
}

/** 写入被拒的存储（配额用尽等）。 */
function stubUnwritable(name: "localStorage" | "sessionStorage"): void {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    value: {
      getItem: () => null,
      setItem: () => {
        throw new DOMException("QuotaExceededError", "QuotaExceededError");
      },
      removeItem: () => undefined,
    },
  });
}

afterEach(() => {
  // node 环境本就没有这两个全局，删掉即还原
  delete (globalThis as { localStorage?: unknown }).localStorage;
  delete (globalThis as { sessionStorage?: unknown }).sessionStorage;
});

describe("token 存取", () => {
  it("localStorage 可用时往返一致，清除后为空", () => {
    globalThis.localStorage = memoryStorage();
    expect(setToken("t-1")).toBe(true);
    expect(getToken()).toBe("t-1");
    clearToken();
    expect(getToken()).toBeNull();
  });

  it("localStorage 读属性即抛时降级 sessionStorage", () => {
    stubBlocked("localStorage");
    globalThis.sessionStorage = memoryStorage();
    expect(isStorageAvailable()).toBe(true);
    expect(setToken("t-2")).toBe(true);
    expect(getToken()).toBe("t-2");
    expect(globalThis.sessionStorage.getItem("marda_token")).toBe("t-2");
  });

  it("localStorage 写入被拒时同样降级 sessionStorage", () => {
    stubUnwritable("localStorage");
    globalThis.sessionStorage = memoryStorage();
    expect(setToken("t-3")).toBe(true);
    expect(getToken()).toBe("t-3");
  });

  it("两者都不可用时不抛异常，setToken 返回 false", () => {
    stubBlocked("localStorage");
    stubBlocked("sessionStorage");
    expect(isStorageAvailable()).toBe(false);
    expect(setToken("t-4")).toBe(false);
    expect(getToken()).toBeNull();
    expect(() => clearToken()).not.toThrow();
  });
});

describe("authHeaders", () => {
  it("有 token 时带 Bearer", () => {
    globalThis.localStorage = memoryStorage();
    setToken("t-5");
    expect(authHeaders()).toEqual({ Authorization: "Bearer t-5" });
  });

  it("无 token 时为空对象（不伪造 header）", () => {
    globalThis.localStorage = memoryStorage();
    expect(authHeaders()).toEqual({});
  });
});
