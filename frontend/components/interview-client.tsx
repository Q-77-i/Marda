"use client";

import { cn } from "cn";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { MessageBubble, type ChatItem } from "@/components/message-bubble";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  dispatcher,
  getSession,
  sendMessage,
  type Phase,
  type Session,
  type SSEHandlers,
} from "@/lib/api";
import { END_COMMAND, PHASE_LABELS } from "@/lib/constants";
import { progressLabel } from "@/lib/format";
import { reconcile, type PendingTurn } from "@/lib/recovery";
import { UnauthorizedError, redirectToLogin, setUnauthorizedHandler } from "@/lib/session";
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
  const [readonly, setReadonly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [draft, setDraft] = useState("");

  const queueRef = useRef(new TypewriterQueue());
  const composingRef = useRef(false);
  const streamingRef = useRef(false);
  const busyRef = useRef(false);
  const idRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const nearBottomRef = useRef(true);
  const messagesRef = useRef<ChatItem[]>([]);
  const pendingRef = useRef<PendingTurn | null>(null);

  const nextId = useCallback(() => `m${idRef.current++}`, []);

  /* 渲染列表的镜像：submit 是稳定回调（deps 只有 interviewId），要从里面数
     「发送前本地已有几条候选人气泡」只能读 ref */
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  /* 会话数据 → UI 状态（首次载入与失败后重同步共用，避免两处口径漂移）。 */
  const applySession = useCallback(
    (session: Session) => {
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
      setReadonly(session.status === "finished");
    },
    [nextId],
  );

  /* 接管 401 处置：默认的「直接踢回登录页」在答题中途体感太差，改为弹确认后再跳 */
  useEffect(() => {
    setUnauthorizedHandler(() => setExpired(true));
    return () => setUnauthorizedHandler(null);
  }, []);

  /* 恢复会话（验收 4：刷新后历史不丢）；已结束场次进只读回放（FR-25）。
     reconnect：答题中回来时后端附一句「我们继续刚才的」+ 题干（P1-M4.7-D） */
  useEffect(() => {
    let active = true;
    getSession(interviewId, { reconnect: true })
      .then((session) => {
        if (!active) return;
        applySession(session);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (!active) return;
        if (err instanceof UnauthorizedError) return; // 已由确认框接管
        setError(err instanceof Error ? err.message : "会话加载失败");
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [interviewId, applySession]);

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

  /* 输入框按内容长高，到 ~40% 视口高封顶（到顶后框内滚动）。
     高度自己算，不靠 CSS 的 field-sizing：Safari / 旧版浏览器直接忽略它，
     表现就是框永远只有 rows 那么高（2026-06 才全浏览器可用） */
  const fitInput = useCallback(() => {
    const ta = inputRef.current;
    if (!ta) return;
    const cap = parseFloat(getComputedStyle(ta).maxHeight) || Infinity; // 上限单一来源 = max-h-[40dvh]
    ta.style.height = "auto"; // 先归零，才量得到真实内容高
    ta.style.height = `${Math.min(ta.scrollHeight, cap)}px`;
    ta.style.overflowY = ta.scrollHeight > cap ? "auto" : "hidden";
  }, []);

  useEffect(() => {
    fitInput();
  }, [draft, fitInput]);

  useEffect(() => {
    window.addEventListener("resize", fitInput); // 视口变了，40% 的上限跟着变
    return () => window.removeEventListener("resize", fitInput);
  }, [fitInput]);

  /* 自动贴底：仅在用户本就位于底部时跟随，避免打断向上翻阅；
     输入框长高会把消息区压短（draft 也在依赖里），不同步贴底就会脱离底部 */
  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !nearBottomRef.current) return;
    node.scrollTop = node.scrollHeight;
  }, [messages, draft]);

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
    done: () => setReportReady(true),
  };

  /** pending 同时写 ref（submit 内联闭包要读）与 state（渲染要读）。 */
  const applyPending = (next: PendingTurn | null) => {
    pendingRef.current = next;
    setPending(next);
  };

  /**
   * 失败后对账（SPEC §7 `stalled`）：这条作答在服务端到底去了哪。
   *
   * 「服务端其实跑完了、只是回复没传回来」这一态**不能重发**——重发会被当成新一轮，
   * 同一份回答判两次；这种就地把漏掉的回复补进列表即可。其余情况保留「重试」，
   * 重发要么补发（从未送达）、要么把卡住的节点踢起来（失败节点已由服务端测试钉死）。
   */
  const reconcileFailure = useCallback(
    async (failed: PendingTurn) => {
      try {
        // 不带 reconnect：重连语是给「重新进页面」的，这里只是核对状态
        const session = await getSession(interviewId);
        if (reconcile(failed, session) === "resend") return;
        queueRef.current.clear(); // 列表整体换成服务端版本，未吐完的残缺文案作废
        applySession(session);
        applyPending(null);
        setError(null);
        setNotice("上一轮其实已经提交成功，只是回复没传回来——已按服务端的记录补全。");
      } catch {
        // 连会话都取不到（多半是网络整体不通）：保持「重试」，重发是安全的兜底
      }
    },
    [interviewId, applySession],
  );

  const submit = useCallback(
    async (content: string, appendUser: boolean) => {
      const text = content.trim();
      if (!text || busyRef.current) return;
      // 发送前本地已有的候选人气泡数 = 服务端用户消息条数的下限（对账基准）
      const baseline = messagesRef.current.filter((m) => m.role === "user").length;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      applyPending(null);
      if (appendUser) {
        setMessages((prev) => [...prev, { id: nextId(), role: "user", content: text }]);
      }
      const failed = (message: string) => {
        setError(message);
        const turn = { text, baseline };
        applyPending(turn);
        void reconcileFailure(turn);
      };
      streamingRef.current = true;
      try {
        await sendMessage(
          interviewId,
          text,
          dispatcher({
            ...handlers,
            /* error 事件发生在流内（HTTP 仍是 200），异常不会走到下面的 catch——
               这里必须自己记下这一轮的输入，否则横幅只剩报错、没有「重试」出口。
               重发同一文本不会重复计分：图停在失败节点上，重发 = 从断点续跑该节点 */
            error: (e) => failed(e.message),
          }),
        );
      } catch (err) {
        // 401 由全局确认框接管，不再重复提示（重试也只会再 401）
        if (!(err instanceof UnauthorizedError)) {
          failed(err instanceof Error ? err.message : "网络异常，请重试");
        }
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
    [interviewId, reconcileFailure],
  );

  /**
   * 有未落地的作答时先把它送出去（重发 = 补发从未送达的 / 踢活卡住的节点），
   * 返回 true 表示本次操作已被接管——用户要发的新内容留在输入框里，等面试官回应再发。
   * 不接管就会重演老问题：新文本进了卡住的图会被丢弃（气泡却照常追加，看着像答了）。
   */
  const flushPending = (noticeText: string): boolean => {
    const stuck = pendingRef.current;
    if (!stuck) return false;
    void submit(stuck.text, false);
    setNotice(noticeText);
    return true;
  };

  const handleSend = () => {
    if (flushPending("上一轮的回答还没提交成功，已先为你重发；看到面试官回应后再发这条。")) {
      return;
    }
    setNotice(null); // 提示语只服务它那一次动作，新一轮动作即收走
    const text = draft;
    setDraft("");
    void submit(text, true);
  };

  const handleEnd = () => {
    if (flushPending("上一轮的回答还没提交成功，已先为你重发；看到面试官回应后再结束面试。")) {
      return;
    }
    setNotice(null);
    void submit(END_COMMAND, true);
  };

  const handleRetry = () => {
    const stuck = pendingRef.current;
    if (!stuck) return;
    setNotice(null);
    void submit(stuck.text, false); // appendUser=false：气泡已经在列表里
  };

  const finished = reportReady;
  const lastIsUser = messages.length === 0 || messages[messages.length - 1].role === "user";
  const showThinking = busy && lastIsUser && !finished;

  return (
    <div className="flex h-[100dvh] flex-col">
      {/* 登录过期（补充点 ①）：只留「重新登录」一个出口——留在页面上每个请求都会 401 */}
      <AlertDialog open={expired}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>登录已过期</AlertDialogTitle>
            <AlertDialogDescription>
              需要重新登录才能继续。已提交的回答都已保存，重新登录后可回到本场面试继续作答。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={redirectToLogin}>重新登录</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AppHeader
        right={
          <div className="flex items-center gap-3">
            <span className="hidden text-xs text-muted-foreground sm:inline">
              {PHASE_LABELS[phase]}
            </span>
            <span className="tabular text-xs font-medium">
              {progressLabel(answered, total)}
            </span>
            {readonly ? (
              <Link
                href={`/report/${interviewId}`}
                className={cn(buttonVariants({ size: "sm" }))}
              >
                查看报告
              </Link>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={handleEnd}
                disabled={busy || finished || loading}
              >
                结束面试
              </Button>
            )}
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
          {notice && (
            <div
              className="rounded-lg border bg-muted/50 px-3 py-2 text-sm text-muted-foreground"
              role="status"
            >
              {notice}
            </div>
          )}

          {error && (
            <div
              className="flex items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2"
              role="alert"
            >
              <span className="text-sm text-destructive">{error}</span>
              {pending && (
                <Button variant="outline" size="sm" onClick={handleRetry}>
                  重试
                </Button>
              )}
            </div>
          )}

          {readonly ? (
            <p className="py-2 text-sm text-muted-foreground">
              本场面试已结束，以上为完整回放 ·{" "}
              <Link
                href={`/report/${interviewId}`}
                className="underline underline-offset-4"
              >
                查看能力报告
              </Link>
            </p>
          ) : finished ? (
            <p className="py-2 text-sm text-muted-foreground">
              面试已结束，正在生成能力报告…
            </p>
          ) : (
            <>
              <Textarea
                ref={inputRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onCompositionStart={() => {
                  composingRef.current = true;
                }}
                onCompositionEnd={() => {
                  // 复位延到下一个宏任务：macOS 输入法用回车"上屏"时 compositionend 先于 keydown 到达，
                  // 立刻复位会让那次回车被误判成普通回车（表现为上屏的同时把消息发出去）
                  setTimeout(() => {
                    composingRef.current = false;
                  }, 0);
                }}
                onKeyDown={(e) => {
                  if (e.key !== "Enter" || e.shiftKey) return;
                  // 组字进行中（含 Windows 输入法的 keyCode 229）：这个回车归输入法选字，放行
                  if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
                  e.preventDefault();
                  // 刚用回车把候选上屏（compositionend 已先到、isComposing 已是 false）：只上屏，不发送也不换行
                  if (composingRef.current) return;
                  handleSend();
                }}
                disabled={busy || loading}
                rows={1}
                placeholder="输入你的回答，Enter 发送，Shift + Enter 换行"
                // 高度由 fitInput() 按内容算；rows=1 让"撑不高"的浏览器也和小框起步（下限交给 min-h-16）
                className="max-h-[40dvh] resize-none overflow-y-auto"
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
