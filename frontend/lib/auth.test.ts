import { afterEach, describe, expect, it, vi } from "vitest";

import { login, validatePassword, validateUsername } from "@/lib/auth";
import { setUnauthorizedHandler } from "@/lib/session";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("validateUsername（与后端 Pydantic 同规则）", () => {
  it("合法用户名通过", () => {
    expect(validateUsername("zhouq_77")).toBeNull();
    expect(validateUsername("abc")).toBeNull();
  });

  it("长度不足或超限报错", () => {
    expect(validateUsername("ab")).toBe("用户名需 3–32 位");
    expect(validateUsername("a".repeat(33))).toBe("用户名需 3–32 位");
  });

  it("含非法字符报错（中文、连字符、空格）", () => {
    // 长度校验优先：这里用 3 个汉字，确保命中的是字符集规则
    expect(validateUsername("码达君")).toBe("用户名只能包含字母、数字和下划线");
    expect(validateUsername("zhou-q")).toBe("用户名只能包含字母、数字和下划线");
  });

  it("首尾空格按去掉后校验（与后端 strip 一致）", () => {
    expect(validateUsername("  abc  ")).toBeNull();
  });
});

describe("validatePassword", () => {
  it("6–72 位通过", () => {
    expect(validatePassword("123456")).toBeNull();
    expect(validatePassword("a".repeat(72))).toBeNull();
  });

  it("过短或过长报错", () => {
    expect(validatePassword("12345")).toBe("密码需 6–72 位");
    expect(validatePassword("a".repeat(73))).toBe("密码需 6–72 位");
  });
});

describe("登录接口的 401", () => {
  it("只抛表单错误，绝不触发全局跳转（P1-M2 会话 1 口径）", async () => {
    const handled: string[] = [];
    setUnauthorizedHandler(() => handled.push("handled"));
    vi.stubGlobal("fetch", () =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: "用户名或密码错误" }), { status: 401 }),
      ),
    );

    await expect(login("someone", "bad-pass")).rejects.toThrow("用户名或密码错误");
    expect(handled).toEqual([]);
    setUnauthorizedHandler(null);
  });
});
