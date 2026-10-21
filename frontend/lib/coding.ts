/**
 * 代码工作台纯逻辑（M15）：语言表、提交判据、请求体、点评展示文案。
 *
 * 页面里的分支进不了 vitest（同 P2-M3 的教训），凡是能做成纯函数的都放这里；
 * Monaco / fetch / React 状态留在组件里。
 */

import type { CodeReview, CodingQuestion } from "@/lib/api";

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

// ---- 刷新恢复：代码工作台快照（M15 体验补强） ----

export type SavedCodeWorkbench = {
  version: 1;
  question: CodingQuestion;
  language: CodingLanguage;
  code: string;
  review: CodeReview | null;
  reviewedCode: string | null;
  turns: CodingTurn[];
  followupDraft: string;
};

function validReview(value: unknown): value is CodeReview {
  if (!value || typeof value !== "object") return false;
  const review = value as Partial<CodeReview>;
  return (
    Array.isArray(review.issues)
    && Array.isArray(review.edge_cases)
    && !!review.complexity
    && typeof review.follow_up === "string"
  );
}

/**
 * 解析 localStorage 里的代码工作台快照（纯函数，便于 vitest）。
 *
 * 刷新恢复只欠一个「别把用户代码/点评弄丢」；快照是本地数据，格式坏了就整份丢弃、
 * 重新抽题，绝不能因为一份旧 JSON 把工作台炸掉。旧版本没有 description/examples
 * 字段的题目在这里归一为空，前端按「只有标题」渲染。
 */
export function parseSavedCodeWorkbench(raw: string | null): SavedCodeWorkbench | null {
  if (!raw) return null;
  try {
    const data = JSON.parse(raw) as Partial<SavedCodeWorkbench>;
    if (data.version !== 1) return null;
    const question = data.question;
    if (
      !question
      || typeof question.question_id !== "string"
      || typeof question.text !== "string"
    ) {
      return null;
    }
    if (typeof data.code !== "string" || !Array.isArray(data.turns)) return null;

    const language = CODING_LANGUAGES.some((item) => item.value === data.language)
      ? (data.language as CodingLanguage)
      : "python";
    const normalizedQuestion: CodingQuestion = {
      ...question,
      description: typeof question.description === "string" ? question.description : "",
      examples: Array.isArray(question.examples)
        ? question.examples.filter((example): example is string => typeof example === "string")
        : [],
      constraints: typeof question.constraints === "string" ? question.constraints : "",
      topic: typeof question.topic === "string" ? question.topic : "",
      difficulty: typeof question.difficulty === "string" ? question.difficulty : "",
      domain: typeof question.domain === "string" ? question.domain : "algorithms",
    };
    const turns = data.turns.filter(
      (turn): turn is CodingTurn =>
        !!turn
        && (turn.role === "user" || turn.role === "assistant")
        && typeof turn.content === "string",
    );

    return {
      version: 1,
      question: normalizedQuestion,
      language,
      code: data.code,
      review: validReview(data.review) ? data.review : null,
      reviewedCode: typeof data.reviewedCode === "string" ? data.reviewedCode : null,
      turns,
      followupDraft: typeof data.followupDraft === "string" ? data.followupDraft : "",
    };
  } catch {
    return null;
  }
}
