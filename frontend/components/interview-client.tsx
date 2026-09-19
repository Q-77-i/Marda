"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { MessageBubble, type ChatItem } from "@/components/message-bubble";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  dispatcher,
  getSession,
  sendMessage,
  type Phase,
  type SSEHandlers,
} from "@/lib/api";
import { END_COMMAND, PHASE_LABELS } from "@/lib/constants";
import { progressLabel } from "@/lib/format";
import { TypewriterQueue } from "@/lib/typewriter";

/** 吐字节拍：每 30ms 一次，字符数随积压自适应，长文案不落后于流。 */
const TICK_MS = 30;

export function InterviewClient({ interviewId }: { interviewId: string }) {
  const router = useRouter();
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [phase, setPhase] = useState<Phase>("intro");
  const [answered, setAnswered] = useState(0);
  const [total, setTotal] = useState(10);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reportReady, setReportReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failedInput, setFailedInput] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const queueRef = useRef(new TypewriterQueue());
  const streamingRef = useRef(false);
  const busyRef = useRef(false);
  const idRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const nearBottomRef = useRef(true);

  const nextId = () => `m${idRef.current++}`;

  /* 恢复会话（验收 4：刷新后历史不丢）；已结束直接进报告页 */
  useEffect(() => {
    let active = true;
    getSession(interviewId)
      .then((session) => {
        if (!active) return;
        if (session.status === "finished") {
          router.replace(`/report/${interviewId}`);
          return;
        }
        setMessages(
          session.chat_history.map((m) => ({
            id: nextId(),
            role: m.role,
            content: m.content,
          })),
        );
        setPhase(session.phase);
        setAnswered(session.answered_count);
        setTotal(session.question_count);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "会话加载失败");
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [interviewId, router]);

  /* 吐字循环：busy 期间运行，队列排空且流已关闭时收尾 */
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => {
      const queue = queueRef.current;
      if (!queue.idle) {
        const step = Math.max(2, Math.ceil(queue.pendingLength / 16));
        const entry = queue.tick(step);
        if (entry) {
          setMessages((prev) =>
            prev.map((m) => (m.id === entry.id ? { ...m, content: entry.text } : m)),
          );
        }
      } else if (!streamingRef.current) {
        setBusy(false);
        busyRef.current = false;
      }
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [busy]);

  /* 自动贴底：仅在用户本就位于底部时跟随，避免打断向上翻阅 */
  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !nearBottomRef.current) return;
    node.scrollTop = node.scrollHeight;
  }, [messages]);

  /* 报告就绪且面试官说完 → 跳转报告页 */
  useEffect(() => {
    if (reportReady && !busy) router.push(`/report/${interviewId}`);
  }, [reportReady, busy, interviewId, router]);

  const handleScroll = () => {
    const node = scrollRef.current;
    if (!node) return;
    nearBottomRef.current =
      node.scrollHeight - node.scrollTop - node.clientHeight < 120;
  };

  const handlers: SSEHandlers = {
    delta: ({ text }) => {
      const id = nextId();
      queueRef.current.push(id, text);
      setMessages((prev) => [...prev, { id, role: "assistant", content: "" }]);
    },
    question: (q) => {
      // 出题节点同一更新内先发 delta 再发 question → 挂到最后一条面试官消息上
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (!last || last.role !== "assistant" || last.tag) return prev;
        const copy = [...prev];
        copy[copy.length - 1] = {
          ...last,
          tag: { index: q.index, domain: q.domain, difficulty: q.difficulty },
        };
        return copy;
      });
    },
    meta: (m) => {
      setPhase(m.phase);
      setAnswered(m.answered_count);
      setTotal(m.question_count);
    },
    error: (e) => setError(e.message),
    done: () => setReportReady(true),
  };

  const submit = useCallback(
    async (content: string, appendUser: boolean) => {
      const text = content.trim();
      if (!text || busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      setFailedInput(null);
      if (appendUser) {
        setMessages((prev) => [...prev, { id: nextId(), role: "user", content: text }]);
      }
      streamingRef.current = true;
      try {
        await sendMessage(interviewId, text, dispatcher(handlers));
      } catch (err) {
        setError(err instanceof Error ? err.message : "网络异常，请重试");
        setFailedInput(text);
        queueRef.current.clear(); // 丢弃未吐完的残缺文案，避免与错误提示混淆
      } finally {
        streamingRef.current = false;
        if (queueRef.current.idle) {
          setBusy(false);
          busyRef.current = false;
        }
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [interviewId],
  );

  const handleSend = () => {
    const text = draft;
    setDraft("");
    void submit(text, true);
  };

  const handleEnd = () => void submit(END_COMMAND, true);

  const handleRetry = () => {
    if (failedInput) void submit(failedInput, false);
  };

  const finished = reportReady;
  const lastIsUser = messages.length === 0 || messages[messages.length - 1].role === "user";
  const showThinking = busy && lastIsUser && !finished;

  return (
    <div className="flex h-[100dvh] flex-col">
      <AppHeader
        right={
          <div className="flex items-center gap-3">
            <span className="hidden text-xs text-muted-foreground sm:inline">
              {PHASE_LABELS[phase]}
            </span>
            <span className="tabular text-xs font-medium">
              {progressLabel(answered, total)}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={handleEnd}
              disabled={busy || finished || loading}
            >
              结束面试
            </Button>
          </div>
        }
      />

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6 sm:px-6">
          {loading && (
            <p className="text-sm text-muted-foreground">正在载入面试…</p>
          )}
          {!loading && messages.length === 0 && !error && (
            <p className="text-sm text-muted-foreground">
              面试尚未开始，请返回首页重新创建。
            </p>
          )}
          {messages.map((item, index) => (
            <MessageBubble
              key={item.id}
              item={{
                ...item,
                typing:
                  busy && index === messages.length - 1 && item.role === "assistant",
              }}
            />
          ))}
          {showThinking && <ThinkingBubble />}
        </div>
      </div>

      <div className="border-t bg-background">
        <div className="mx-auto flex max-w-3xl flex-col gap-2 px-4 py-3 sm:px-6">
          {error && (
            <div
              className="flex items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2"
              role="alert"
            >
              <span className="text-sm text-destructive">{error}</span>
              {failedInput && (
                <Button variant="outline" size="sm" onClick={handleRetry}>
                  重试
                </Button>
              )}
            </div>
          )}

          {finished ? (
            <p className="py-2 text-sm text-muted-foreground">
              面试已结束，正在生成能力报告…
            </p>
          ) : (
            <>
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                disabled={busy || loading}
                rows={3}
                placeholder="输入你的回答，Enter 发送，Shift + Enter 换行"
                className="resize-none"
              />
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  {busy ? "面试官正在回应…" : "答案越具体，评分与点评越准确"}
                </span>
                <Button onClick={handleSend} disabled={busy || loading || !draft.trim()}>
                  发送
                </Button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** 提交后、首个 delta 到达前的等待指示（面试官思考中）。 */
function ThinkingBubble() {
  return (
    <div className="flex justify-start">
      <div className="flex items-center gap-1 rounded-xl rounded-tl-sm bg-muted px-3.5 py-3">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="size-1.5 animate-pulse rounded-full bg-muted-foreground"
            style={{ animationDelay: `${i * 160}ms` }}
          />
        ))}
      </div>
    </div>
  );
}
