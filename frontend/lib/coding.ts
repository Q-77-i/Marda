/**
 * 代码工作台纯逻辑（M15）：语言表、提交判据、请求体、点评展示文案。
 *
 * 页面里的分支进不了 vitest（同 P2-M3 的教训），凡是能做成纯函数的都放这里；
 * Monaco / fetch / React 状态留在组件里。
 */

export type CodingLanguage = "python" | "java" | "cpp" | "javascript";

export type CodingLanguageMeta = {
  value: CodingLanguage;
  label: string;
  /** Monaco 的语言 id */
  monaco: string;
  /** 新题/换题时的初始模板 */
  starter: string;
};

export const CODING_LANGUAGES: readonly CodingLanguageMeta[] = [
  {
    value: "python",
    label: "Python",
    monaco: "python",
    starter: "class Solution:\n    def solve(self):\n        pass\n",
  },
  {
    value: "java",
    label: "Java",
    monaco: "java",
    starter: "class Solution {\n    public void solve() {\n        \n    }\n}\n",
  },
  {
    value: "cpp",
    label: "C++",
    monaco: "cpp",
    starter:
      "#include <bits/stdc++.h>\nusing namespace std;\n\nclass Solution {\npublic:\n    void solve() {\n        \n    }\n};\n",
  },
  {
    value: "javascript",
    label: "JavaScript",
    monaco: "javascript",
    starter: "function solve() {\n  \n}\n",
  },
] as const;

export const MAX_CODE_CHARS = 8000;
export const MAX_HISTORY = 8;

export function codingLanguage(value: string | null | undefined): CodingLanguageMeta {
  return CODING_LANGUAGES.find((item) => item.value === value) ?? CODING_LANGUAGES[0];
}

export function starterCode(language: CodingLanguage): string {
  return codingLanguage(language).starter;
}

/** 提交点评的判据：非空、不超长、没有正在进行的请求。 */
export function canSubmitReview(
  code: string,
  options: { reviewing: boolean; loadingQuestion: boolean },
): boolean {
  return (
    !options.reviewing &&
    !options.loadingQuestion &&
    code.trim().length > 0 &&
    code.length <= MAX_CODE_CHARS
  );
}

export type ReviewBody = {
  question_id: string;
  language: CodingLanguage;
  code: string;
};

export function reviewBody(
  questionId: string,
  language: CodingLanguage,
  code: string,
): ReviewBody {
  return { question_id: questionId, language, code };
}

export type CodingTurn = { role: "user" | "assistant"; content: string };

export type FollowupBody<R = unknown> = ReviewBody & {
  review: R;
  message: string;
  history: CodingTurn[];
};

/** 追问请求体：只带最近 MAX_HISTORY 条历史（后端也会裁，但别先浪费上行）。 */
export function followupBody<R>(
  questionId: string,
  language: CodingLanguage,
  code: string,
  review: R,
  message: string,
  history: CodingTurn[],
): FollowupBody<R> {
  return {
    question_id: questionId,
    language,
    code,
    review,
    message: message.trim(),
    history: history.slice(-MAX_HISTORY),
  };
}

/** 追问输入是否可发送。 */
export function canSendFollowup(message: string, sending: boolean): boolean {
  return !sending && message.trim().length > 0;
}

export function severityLabel(severity: string): string {
  if (severity === "error") return "错误";
  if (severity === "suggestion") return "建议";
  return "警告";
}

export function lineLabel(line: number | null | undefined): string {
  return typeof line === "number" && line > 0 ? `第 ${line} 行` : "行号未给出";
}

/** 点评是否针对旧代码（用户改过代码后提示重新提交）。 */
export function isReviewStale(reviewedCode: string | null, currentCode: string): boolean {
  return reviewedCode !== null && reviewedCode !== currentCode;
}

/** 超长代码的提示文案（未超长返回 null）。 */
export function codeLengthHint(code: string): string | null {
  if (code.length <= MAX_CODE_CHARS) return null;
  return `代码超过 ${MAX_CODE_CHARS} 字，请精简后再提交`;
}

export function codingErrorText(reason: unknown): string {
  return reason instanceof Error && reason.message
    ? reason.message
    : "代码点评失败，请稍后重试";
}
