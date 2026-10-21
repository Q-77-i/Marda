import { describe, expect, it } from "vitest";

import {
  CODING_LANGUAGES,
  MAX_CODE_CHARS,
  MAX_HISTORY,
  canSendFollowup,
  canSubmitReview,
  codeLengthHint,
  codingLanguage,
  followupBody,
  isReviewStale,
  lineLabel,
  reviewBody,
  severityLabel,
  starterCode,
} from "@/lib/coding";

describe("代码工作台语言表", () => {
  it("四种语言都有 Monaco id 与初始模板", () => {
    expect(CODING_LANGUAGES.map((item) => item.value)).toEqual([
      "python",
      "java",
      "cpp",
      "javascript",
    ]);
    for (const item of CODING_LANGUAGES) {
      expect(item.label.length).toBeGreaterThan(0);
      expect(item.monaco.length).toBeGreaterThan(0);
      expect(starterCode(item.value).length).toBeGreaterThan(0);
    }
  });

  it("未知语言回落到第一项", () => {
    expect(codingLanguage("ruby").value).toBe("python");
    expect(codingLanguage(null).value).toBe("python");
  });
});

describe("提交判据", () => {
  it("空/纯空白/超长/请求中都不能提交", () => {
    expect(canSubmitReview("   ", { reviewing: false, loadingQuestion: false })).toBe(false);
    expect(
      canSubmitReview("x".repeat(MAX_CODE_CHARS + 1), {
        reviewing: false,
        loadingQuestion: false,
      }),
    ).toBe(false);
    expect(canSubmitReview("x = 1", { reviewing: true, loadingQuestion: false })).toBe(false);
    expect(canSubmitReview("x = 1", { reviewing: false, loadingQuestion: true })).toBe(false);
    expect(canSubmitReview("x = 1", { reviewing: false, loadingQuestion: false })).toBe(true);
  });

  it("超长提示只在超长时出现", () => {
    expect(codeLengthHint("x".repeat(MAX_CODE_CHARS))).toBeNull();
    expect(codeLengthHint("x".repeat(MAX_CODE_CHARS + 1))).toContain("超过");
  });
});

describe("请求体", () => {
  it("点评体保留原始代码（缩进不能丢）", () => {
    const code = "def f():\n    return 1\n";
    expect(reviewBody("q1", "python", code)).toEqual({
      question_id: "q1",
      language: "python",
      code,
    });
  });

  it("追问体裁剪消息并只带最近 N 条历史", () => {
    const history = Array.from({ length: MAX_HISTORY + 3 }, (_, i) => ({
      role: "user" as const,
      content: `第 ${i} 条`,
    }));
    const body = followupBody("q1", "java", "x", { issues: [] }, "  为什么？  ", history);

    expect(body.message).toBe("为什么？");
    expect(body.history).toHaveLength(MAX_HISTORY);
    expect(body.history[0].content).toBe("第 3 条");
    expect(body.history.at(-1)?.content).toBe(`第 ${MAX_HISTORY + 2} 条`);
  });

  it("追问消息非空才可发送", () => {
    expect(canSendFollowup("   ", false)).toBe(false);
    expect(canSendFollowup("为什么", true)).toBe(false);
    expect(canSendFollowup("为什么", false)).toBe(true);
  });
});

describe("点评展示", () => {
  it("严重度与行号文案", () => {
    expect(severityLabel("error")).toBe("错误");
    expect(severityLabel("warning")).toBe("警告");
    expect(severityLabel("suggestion")).toBe("建议");
    expect(lineLabel(3)).toBe("第 3 行");
    expect(lineLabel(null)).toBe("行号未给出");
  });

  it("代码改动后旧点评标为过期", () => {
    expect(isReviewStale(null, "x = 1")).toBe(false);
    expect(isReviewStale("x = 1", "x = 1")).toBe(false);
    expect(isReviewStale("x = 1", "x = 2")).toBe(true);
  });
});
