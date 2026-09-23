/**
 * 账号接口（FR-23，SPEC §7）：注册 / 登录 / 当前用户 + 表单校验。
 *
 * 注册/登录自身的 401（账号或密码错误）、409（重名）都是**表单错误**，只在表单内展示，
 * 绝不触发全局跳转 —— 否则用户输错一次密码就被整页踢走（P1-M2 会话 1 备注）。
 * 因此这里用裸 fetch，不走 authorizedFetch。
 */

import { authorizedFetch, networkMessage, responseError } from "@/lib/http";

export type Me = { id: string; username: string };
export type AuthResult = { token: string; username: string };

/** 与后端 Pydantic 同规则（spec：username 3–32 位 [A-Za-z0-9_]，password 6–72）。 */
const USERNAME_PATTERN = /^[A-Za-z0-9_]+$/;

/** 返回错误文案；null 表示通过。 */
export function validateUsername(value: string): string | null {
  const name = value.trim();
  if (name.length < 3 || name.length > 32) return "用户名需 3–32 位";
  if (!USERNAME_PATTERN.test(name)) return "用户名只能包含字母、数字和下划线";
  return null;
}

export function validatePassword(value: string): string | null {
  if (value.length < 6 || value.length > 72) return "密码需 6–72 位";
  return null;
}

async function authRequest(path: string, body: unknown): Promise<AuthResult> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new Error(networkMessage(err));
  }
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<AuthResult>;
}

export function login(username: string, password: string): Promise<AuthResult> {
  return authRequest("/api/auth/login", { username, password });
}

export function register(username: string, password: string): Promise<AuthResult> {
  return authRequest("/api/auth/register", { username, password });
}

/** 当前用户（UserMenu 挂载时校验 token）：token 失效走全局 401 处置。 */
export async function fetchMe(): Promise<Me> {
  const response = await authorizedFetch("/api/auth/me");
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<Me>;
}
