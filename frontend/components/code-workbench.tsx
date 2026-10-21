"use client";

import dynamic from "next/dynamic";
import {
  AlertCircle,
  CheckCircle2,
  Code2,
  MessageSquare,
  RefreshCw,
  Send,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "cn";

import {
  CODING_LANGUAGES,
  MAX_CODE_CHARS,
  canSendFollowup,
  canSubmitReview,
  codeLengthHint,
  codingErrorText,
  codingLanguage,
  followupBody,
  isReviewStale,
  lineLabel,
  reviewBody,
  severityLabel,
  starterCode,
  type CodingLanguage,
  type CodingTurn,
} from "@/lib/coding";
import {
  getRandomCodingQuestion,
  postCodingFollowup,
  postCodingReview,
  type CodeReview,
  type CodingQuestion,
} from "@/lib/api";

/** Monaco 体积大且依赖 window，只有第一次打开代码工作台才加载。 */
const CodeEditor = dynamic(
  () => import("@/components/code-editor").then((module) => module.CodeEditor),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center text-[11px] text-room-muted">
        正在加载编辑器…
      </div>
    ),
  },
);

/**
 * 代码工作台（M15）：右侧 320px 放不下编辑器，所以工具在覆盖中右栏的工作台里展开。
 *
 * 独立于面试状态机：自己抽题、自己点评、不读写 interview。首次打开后保持挂载
 * （open=false 只是 hidden），收起再打开不丢代码与点评。
 */
export function CodeWorkbench({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [question, setQuestion] = useState<CodingQuestion | null>(null);
  const [loadingQuestion, setLoadingQuestion] = useState(false);
  const [questionError, setQuestionError] = useState<string | null>(null);
  const [language, setLanguage] = useState<CodingLanguage>("python");
  const [code, setCode] = useState(() => starterCode("python"));
  const [review, setReview] = useState<CodeReview | null>(null);
  const [reviewedCode, setReviewedCode] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [turns, setTurns] = useState<CodingTurn[]>([]);
  const [followupDraft, setFollowupDraft] = useState("");
  const [sendingFollowup, setSendingFollowup] = useState(false);
  const [followupError, setFollowupError] = useState<string | null>(null);
  const loadingRef = useRef(false);

  const loadQuestion = useCallback(async (lang: CodingLanguage) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoadingQuestion(true);
    setQuestionError(null);
    try {
      const next = await getRandomCodingQuestion();
      setQuestion(next);
      setCode(starterCode(lang));
      setReview(null);
      setReviewedCode(null);
      setReviewError(null);
      setTurns([]);
      setFollowupDraft("");
      setFollowupError(null);
    } catch (err) {
      setQuestionError(codingErrorText(err));
    } finally {
      loadingRef.current = false;
      setLoadingQuestion(false);
    }
  }, []);

  useEffect(() => {
    if (!open || question !== null || questionError) return;
    void loadQuestion(language);
  }, [open, question, questionError, language, loadQuestion]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const handleNewQuestion = () => {
    const pristine = code.trim() === "" || code === starterCode(language);
    if (!pristine && !window.confirm("换题会清空当前代码，确认换题？")) return;
    void loadQuestion(language);
  };

  const handleLanguage = (next: CodingLanguage) => {
    if (next === language) return;
    setLanguage(next);
    // 代码还是当前语言的空白模板时才换模板；已经写了代码就保留（只换语法高亮）
    setCode((current) =>
      current.trim() === "" || current === starterCode(language) ? starterCode(next) : current,
    );
    setReview(null);
    setReviewedCode(null);
    setTurns([]);
    setFollowupError(null);
  };

  const handleReview = async () => {
    if (!question || !canSubmitReview(code, { reviewing, loadingQuestion })) return;
    setReviewing(true);
    setReviewError(null);
    setFollowupError(null);
    try {
      const result = await postCodingReview(reviewBody(question.question_id, language, code));
      setReview(result.review);
      setReviewedCode(code);
      setTurns([]);
    } catch (err) {
      setReviewError(codingErrorText(err));
    } finally {
      setReviewing(false);
    }
  };

  const handleFollowup = async () => {
    if (!question || !review || !canSendFollowup(followupDraft, sendingFollowup)) return;
    const message = followupDraft.trim();
    setSendingFollowup(true);
    setFollowupError(null);
    try {
      const result = await postCodingFollowup(
        followupBody(question.question_id, language, code, review, message, turns),
      );
      setTurns((prev) => [
        ...prev,
        { role: "user", content: message },
        { role: "assistant", content: result.answer },
      ]);
      setFollowupDraft("");
    } catch (err) {
      setFollowupError(codingErrorText(err));
    } finally {
      setSendingFollowup(false);
    }
  };

  const stale = review !== null && isReviewStale(reviewedCode, code);
  const lengthHint = codeLengthHint(code);
  const meta = codingLanguage(language);

  return (
    <section
      role="dialog"
      aria-modal="true"
      aria-label="代码工作台"
      className={cn(
        "fixed inset-0 z-30 flex flex-col border-l border-room-line bg-room-bg text-room-ink xl:left-[264px]",
        !open && "hidden",
      )}
    >
      <header className="flex h-[62px] flex-none items-center justify-between gap-3 border-b border-room-line px-4 md:px-6">
        <div className="flex min-w-0 items-baseline gap-2">
          <strong className="text-[12.5px] font-medium">代码工作台</strong>
          <span className="font-mono text-[8.5px] text-room-faint">Code practice</span>
          <span className="hidden truncate text-[10.5px] text-room-muted sm:inline">
            {question ? `${question.topic} · ${question.difficulty}` : "题库随机算法题"}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <label className="flex items-center gap-1.5 text-[10px] text-room-muted">
            <span className="hidden sm:inline">语言</span>
            <select
              value={language}
              onChange={(event) => handleLanguage(event.target.value as CodingLanguage)}
              className="h-7 rounded-md border border-room-line bg-room-panel px-1.5 text-[10.5px] text-room-ink outline-none focus:border-room-accent"
              aria-label="选择语言"
            >
              {CODING_LANGUAGES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={handleNewQuestion}
            disabled={loadingQuestion}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-room-line px-2 text-[10.5px] text-room-muted transition-colors hover:border-room-line-strong hover:text-room-ink disabled:pointer-events-none disabled:opacity-45"
          >
            <RefreshCw className="size-3.5" strokeWidth={1.8} />
            <span className="hidden sm:inline">换一题</span>
          </button>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-room-line px-2 text-[10.5px] text-room-muted transition-colors hover:border-room-line-strong hover:text-room-ink"
          >
            <X className="size-3.5" strokeWidth={1.8} />
            收起
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <div className="flex min-h-[420px] min-w-0 flex-1 flex-col p-3 lg:min-h-0">
          <div className="mb-2 flex-none rounded-lg border border-room-line bg-room-panel px-3 py-2">
            {loadingQuestion ? (
              <p className="text-[11px] text-room-muted">正在抽题…</p>
            ) : questionError ? (
              <div className="flex items-center justify-between gap-3">
                <p role="alert" className="text-[11px] text-room-danger">
                  {questionError}
                </p>
                <button
                  type="button"
                  onClick={() => void loadQuestion(language)}
                  className="shrink-0 rounded-md border border-room-line px-2 py-1 text-[10px] text-room-muted hover:text-room-ink"
                >
                  重试
                </button>
              </div>
            ) : question ? (
              <>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="rounded border border-room-line bg-room-panel-2 px-1.5 py-0.5 font-mono text-[8.5px] text-room-muted">
                    {question.difficulty}
                  </span>
                  <span className="text-[9.5px] text-room-faint">{question.topic}</span>
                  <span className="font-mono text-[8.5px] text-room-faint">题库随机题</span>
                </div>
                <p className="mt-1.5 max-h-24 overflow-y-auto text-[11.5px] leading-relaxed text-room-ink-2">
                  {question.text}
                </p>
              </>
            ) : (
              <p className="text-[11px] text-room-muted">暂无题目</p>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-room-line bg-[#1e1e1e]">
            <CodeEditor value={code} language={language} onChange={setCode} open={open} />
          </div>

          <div className="mt-2 flex flex-none flex-wrap items-center justify-between gap-2">
            <p className="text-[10px] leading-relaxed text-room-faint">
              代码只在本工作台使用，不进入面试评分与报告。
            </p>
            <div className="flex items-center gap-2">
              {lengthHint ? (
                <span className="text-[10px] text-room-danger">{lengthHint}</span>
              ) : (
                <span
                  className={cn(
                    "font-mono text-[9px] text-room-faint",
                    code.length > MAX_CODE_CHARS * 0.9 && "text-room-danger",
                  )}
                >
                  {code.length}/{MAX_CODE_CHARS}
                </span>
              )}
              <button
                type="button"
                onClick={() => void handleReview()}
                disabled={!canSubmitReview(code, { reviewing, loadingQuestion })}
                className="inline-flex h-8 items-center gap-1.5 rounded-md bg-room-accent px-3 text-[11.5px] font-medium text-room-bg transition-colors hover:bg-room-accent/90 disabled:pointer-events-none disabled:opacity-45"
              >
                <Code2 className="size-3.5" strokeWidth={1.8} />
                {reviewing ? "点评中…" : "提交点评"}
              </button>
            </div>
          </div>
        </div>

        <aside className="flex w-full shrink-0 flex-col border-t border-room-line bg-room-panel lg:w-[360px] lg:border-t-0 lg:border-l">
          <div className="flex flex-none items-center justify-between border-b border-room-line px-3 py-2">
            <span className="font-mono text-[8.5px] tracking-[0.12em] text-room-faint uppercase">
              Code review
            </span>
            {meta.label !== "Python" ? (
              <span className="font-mono text-[8.5px] text-room-faint">{meta.label}</span>
            ) : null}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
            {reviewing ? (
              <div className="space-y-2" aria-label="正在生成点评">
                {[0, 1, 2, 3].map((index) => (
                  <div
                    key={index}
                    className="h-4 animate-pulse rounded bg-room-panel-2"
                    style={{ width: `${88 - index * 12}%` }}
                  />
                ))}
              </div>
            ) : reviewError ? (
              <div className="rounded-lg border border-room-line bg-room-panel-2 p-3">
                <p role="alert" className="text-[10.5px] text-room-danger">
                  {reviewError}
                </p>
                <button
                  type="button"
                  onClick={() => void handleReview()}
                  className="mt-2 rounded-md border border-room-line px-2 py-1 text-[10px] text-room-muted hover:text-room-ink"
                >
                  重新提交
                </button>
              </div>
            ) : review ? (
              <ReviewBody review={review} stale={stale} turns={turns} />
            ) : (
              <EmptyReview questionError={questionError} />
            )}
          </div>

          {review && !reviewing ? (
            <div className="flex-none border-t border-room-line px-3 py-3">
              {stale ? (
                <p className="mb-2 text-[10px] text-room-accent">
                  代码已修改，重新提交可获得新点评。
                </p>
              ) : null}
              {followupError ? (
                <p role="alert" className="mb-2 text-[10px] text-room-danger">
                  {followupError}
                </p>
              ) : null}
              <div className="flex items-end gap-2">
                <textarea
                  value={followupDraft}
                  onChange={(event) => setFollowupDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void handleFollowup();
                    }
                  }}
                  rows={2}
                  placeholder="追问这次的点评…（Enter 发送，Shift+Enter 换行）"
                  className="min-h-[38px] flex-1 resize-none rounded-md border border-room-line bg-room-bg px-2 py-1.5 text-[10.5px] leading-relaxed text-room-ink outline-none transition-colors placeholder:text-room-faint focus:border-room-accent"
                />
                <button
                  type="button"
                  onClick={() => void handleFollowup()}
                  disabled={!canSendFollowup(followupDraft, sendingFollowup)}
                  aria-label="发送追问"
                  className="inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-room-accent text-room-bg transition-colors hover:bg-room-accent/90 disabled:pointer-events-none disabled:opacity-45"
                >
                  <Send className="size-3.5" strokeWidth={1.8} />
                </button>
              </div>
            </div>
          ) : null}
        </aside>
      </div>
    </section>
  );
}

function EmptyReview({ questionError }: { questionError: string | null }) {
  return (
    <div className="flex h-full min-h-[180px] flex-col items-center justify-center gap-2 text-center">
      <Code2 className="size-6 text-room-faint" strokeWidth={1.4} />
      <p className="text-[10.5px] leading-relaxed text-room-muted">
        {questionError
          ? "题目没抽到，先点上面的「重试」"
          : "提交代码后给出问题清单、具体反例、复杂度判断，并留一个追问。"}
      </p>
    </div>
  );
}

function ReviewBody({
  review,
  stale,
  turns,
}: {
  review: CodeReview;
  stale: boolean;
  turns: CodingTurn[];
}) {
  return (
    <div className="flex flex-col gap-3.5">
      <section>
        <SectionLabel>问题</SectionLabel>
        {review.issues.length === 0 ? (
          <p className="mt-1.5 flex items-start gap-1.5 text-[10.5px] text-room-success">
            <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.8} />
            未发现明确问题，继续看下面的边界与复杂度。
          </p>
        ) : (
          <ul className="mt-1.5 flex flex-col gap-2.5">
            {review.issues.map((issue, index) => (
              <li
                key={`${issue.line}-${index}`}
                className="rounded-md border border-room-line bg-room-panel-2 px-2.5 py-2"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span
                    className={cn(
                      "rounded border px-1.5 py-0.5 font-mono text-[8.5px]",
                      issue.severity === "error"
                        ? "border-room-danger/40 text-room-danger"
                        : issue.severity === "suggestion"
                          ? "border-room-line-strong text-room-muted"
                          : "border-room-accent/40 text-room-accent",
                    )}
                  >
                    {severityLabel(issue.severity)}
                  </span>
                  <span className="font-mono text-[8.5px] text-room-faint">
                    {lineLabel(issue.line)}
                  </span>
                </div>
                <p className="mt-1.5 text-[10.5px] leading-relaxed text-room-ink-2">
                  {issue.reason}
                </p>
                <p className="mt-1 text-[10px] leading-relaxed text-room-muted">
                  修法：{issue.fix}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionLabel>具体反例</SectionLabel>
        <ul className="mt-1.5 flex flex-col gap-2">
          {review.edge_cases.map((edge, index) => (
            <li key={index} className="rounded-md border border-room-line px-2.5 py-2">
              <p className="font-mono text-[10px] leading-relaxed text-room-ink-2">
                输入：{edge.input}
              </p>
              <p className="mt-1 font-mono text-[10px] leading-relaxed text-room-muted">
                期望：{edge.expected}
              </p>
              {edge.note ? (
                <p className="mt-1 text-[9.5px] leading-relaxed text-room-faint">{edge.note}</p>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <SectionLabel>复杂度</SectionLabel>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10.5px] text-room-muted">
          <span>
            时间 <b className="font-mono text-room-ink-2">{review.complexity.time}</b>
          </span>
          <span>
            空间 <b className="font-mono text-room-ink-2">{review.complexity.space}</b>
          </span>
          <span
            className={cn(
              "inline-flex items-center gap-1 text-[9.5px]",
              review.complexity.correct ? "text-room-success" : "text-room-danger",
            )}
          >
            {review.complexity.correct ? (
              <CheckCircle2 className="size-3" strokeWidth={1.8} />
            ) : (
              <AlertCircle className="size-3" strokeWidth={1.8} />
            )}
            {review.complexity.correct ? "判断与实现一致" : "复杂度与实现不一致"}
          </span>
        </div>
        {review.complexity.note ? (
          <p className="mt-1 text-[9.5px] leading-relaxed text-room-faint">
            {review.complexity.note}
          </p>
        ) : null}
      </section>

      <section>
        <SectionLabel>面试官追问</SectionLabel>
        <p className="mt-1.5 flex items-start gap-1.5 rounded-md border border-room-accent/30 bg-room-accent-soft px-2.5 py-2 text-[10.5px] leading-relaxed text-room-ink-2">
          <MessageSquare className="mt-0.5 size-3.5 shrink-0 text-room-accent" strokeWidth={1.8} />
          {review.follow_up}
        </p>
      </section>

      {turns.length > 0 ? (
        <section>
          <SectionLabel>追问记录</SectionLabel>
          <ul className="mt-1.5 flex flex-col gap-2">
            {turns.map((turn, index) => (
              <li
                key={index}
                className={cn(
                  "rounded-md px-2.5 py-2 text-[10.5px] leading-relaxed",
                  turn.role === "user"
                    ? "ml-4 bg-room-accent-soft text-room-ink-2"
                    : "mr-4 border border-room-line bg-room-panel-2 text-room-ink-2",
                )}
              >
                <span className="mb-1 block font-mono text-[8px] text-room-faint">
                  {turn.role === "user" ? "我" : "辅导官"}
                </span>
                {turn.content}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {stale ? (
        <p className="text-[9.5px] leading-relaxed text-room-faint">
          以上点评对应你提交时的代码；改过代码后请重新提交。
        </p>
      ) : null}
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="font-mono text-[8px] tracking-[0.12em] text-room-faint uppercase">
      {children}
    </div>
  );
}
