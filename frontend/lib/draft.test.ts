import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DRAFT_STORAGE_PREFIX,
  clearSavedDraft,
  draftStorageKey,
  parseSavedDraft,
  readSavedDraft,
  writeSavedDraft,
} from "@/lib/draft";

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

afterEach(() => {
  vi.unstubAllGlobals();
  delete (globalThis as { localStorage?: unknown }).localStorage;
});

describe("草稿存储键", () => {
  it("按场次隔离，落在 marda.* 命名空间", () => {
    expect(DRAFT_STORAGE_PREFIX).toBe("marda.draft");
    expect(draftStorageKey("iv-1")).toBe("marda.draft.iv-1");
    expect(draftStorageKey("iv-2")).not.toBe(draftStorageKey("iv-1"));
  });
});

describe("parseSavedDraft", () => {
  it("没有值 / 空串 / 纯空白都视为没有草稿", () => {
    expect(parseSavedDraft(null)).toBeNull();
    expect(parseSavedDraft("")).toBeNull();
    expect(parseSavedDraft(" \n\t ")).toBeNull();
  });

  it("保留原文本（含首尾空白），只做「有没有内容」判断", () => {
    expect(parseSavedDraft("  我的回答\n第二行  ")).toBe("  我的回答\n第二行  ");
  });
});

describe("localStorage 读写", () => {
  it("写入后可读；清空内容即删除键", () => {
    vi.stubGlobal("localStorage", memoryStorage());
    writeSavedDraft("iv-1", "写到一半的回答");
    expect(readSavedDraft("iv-1")).toBe("写到一半的回答");

    writeSavedDraft("iv-1", "   ");
    expect(readSavedDraft("iv-1")).toBeNull();
    expect(globalThis.localStorage.getItem(draftStorageKey("iv-1"))).toBeNull();
  });

  it("清除按场次删除，不碰其他场次", () => {
    vi.stubGlobal("localStorage", memoryStorage());
    writeSavedDraft("iv-1", "A");
    writeSavedDraft("iv-2", "B");
    clearSavedDraft("iv-1");
    expect(readSavedDraft("iv-1")).toBeNull();
    expect(readSavedDraft("iv-2")).toBe("B");
  });

  it("存储不可用 / 抛异常时静默降级", () => {
    const blocked = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    } as unknown as Storage;
    vi.stubGlobal("localStorage", blocked);

    expect(readSavedDraft("iv-1")).toBeNull();
    expect(() => writeSavedDraft("iv-1", "x")).not.toThrow();
    expect(() => clearSavedDraft("iv-1")).not.toThrow();
  });
});
