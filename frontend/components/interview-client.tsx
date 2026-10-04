"use client";

import { cn } from "cn";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppHeader } from "@/components/app-header";
import { CandidateTile, InterviewerTile, StageCard } from "@/components/interview-room";
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
import { Separator } from "@/components/ui/separator";
import { StatusBanner } from "@/components/ui/status-banner";
import { Textarea } from "@/components/ui/textarea";
import {
  dispatcher,
  getSession,
  sendMessage,
  type Phase,
  type Session,
  type SSEHandlers,
} from "@/lib/api";
import { AsrRecorder } from "@/lib/asr-client";
import {
  CAMERA_HINT,
  CAMERA_MODE_KEY,
  cameraDisabled,
  cameraErrorMessage,
  stopStream,
} from "@/lib/camera";
import { openCamera } from "@/lib/camera-client";
import { END_COMMAND, PHASE_LABELS } from "@/lib/constants";
import { interviewerChip } from "@/lib/interview-room";
import { degradedNotice } from "@/lib/degrade";
import { progressLabel } from "@/lib/format";
import { compressImage, uploadImage } from "@/lib/image-client";
import { reconcile, type PendingTurn } from "@/lib/recovery";
import { ATTACH_HINT, ATTACH_TITLE, IMAGE_ACCEPT, pickError } from "@/lib/vision";
import {
  UnauthorizedError,
  notifyUnauthorized,
  redirectToLogin,
  setUnauthorizedHandler,
} from "@/lib/session";
import { Speaker } from "@/lib/tts";
import {
  EMPTY_TRANSCRIPT_HINT,
  VOICE_MODE_KEY,
  blocksSubmit,
  mergeTranscript,
  micDisabled,
  micErrorMessage,
} from "@/lib/voice";
import {
  StreamBuffer,
  applyStreamAction,
  type StreamAction,
} from "@/lib/stream-render";
import { TypewriterQueue } from "@/lib/typewriter";

/** 吐字节拍：每 30ms 一次，字符数随积压自适应，长文案不落后于流。 */
const TICK_MS = 30;

/** 新建面试官气泡（applyStreamAction 的回调：保住 ChatItem 的字段形状）。 */
const createAssistant = (id: string, content: string): ChatItem => ({
  id,
  role: "assistant",
  content,
});

/** 待发送的截图（P2-M6）：压缩后的 blob（要上传的那个）与预览 objectURL。 */
type Attachment = { blob: Blob; url: string };

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
  /* 降级原因（P2-M9）：服务端去重发出（同因只发一次），这里按序累积 */
  const [degraded, setDegraded] = useState<string[]>([]);
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [draft, setDraft] = useState("");
  /* 语音通道（P2-M5 FR-24）：默认关，一键开启；记忆在 localStorage（不可用时仅本次有效） */
  const [voiceMode, setVoiceMode] = useState(false);
  const [recording, setRecording] = useState(false);
  const [connecting, setConnecting] = useState(false);
  /* 图片通道（P2-M6 FR-26）：选择的截图先压缩（预览即上传物），发送时上传拿 id 再发消息 */
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attaching, setAttaching] = useState(false);
  /* 摄像头通道（P2-M7 FR-27）：默认关 + 记忆；画面只在本机，帧不上传、不落库、AI 不看 */
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null);
  const [cameraStarting, setCameraStarting] = useState(false);
  /* 面试间舞台：面试官 tile 的「正在播报」徽标（TTS 状态回调驱动） */
  const [speakerSpeaking, setSpeakerSpeaking] = useState(false);

  const queueRef = useRef(new TypewriterQueue());
  const composingRef = useRef(false);
  const streamingRef = useRef(false);
  const busyRef = useRef(false);
  const idRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null);
  const cameraStartingRef = useRef(false); // 连点防护：starting 期间第二次点击直接拦住
  const cameraRestoreTriedRef = useRef(false); // 记忆的「开」每次进页面只自动恢复一次
  const nearBottomRef = useRef(true);
  const messagesRef = useRef<ChatItem[]>([]);
  const pendingRef = useRef<PendingTurn | null>(null);

  /* 语音：voiceMode 走 ref——submit 是稳定回调（deps 只有 interviewId），
     闭包里的 state 是旧的，而 delta 到达时要按「此刻」的开关决定播不播 */
  const voiceModeRef = useRef(false);
  const recorderRef = useRef<AsrRecorder | null>(null);
  const draftBaseRef = useRef(""); // 开录时的草稿：转写整体替换它之后那一段
  const ttsWarnedRef = useRef(false);
  const speakerRef = useRef<Speaker | null>(null);
  if (speakerRef.current === null) {
    speakerRef.current = new Speaker(
      (message) => {
        // 播报失败只提示一次，不打断面试（三档降级：edge-tts → speechSynthesis → 纯文字）
        if (ttsWarnedRef.current) return;
        ttsWarnedRef.current = true;
        setNotice(`语音播报不可用（${message}），题目请以文字为准。`);
      },
      // 面试官 tile 的「正在播报」徽标（P2-M7 舞台）：回调只在真的起变化时触发
      setSpeakerSpeaking,
    );
  }

  const nextId = useCallback(() => `m${idRef.current++}`, []);

  /** 释放附件预览的 objectURL（移除 / 发送成功 / 卸载时都要收）。 */
  const releaseAttachments = (items: Attachment[]) => {
    for (const item of items) URL.revokeObjectURL(item.url);
  };

  /** 选择截图 → 逐张校验 + 压缩 → 进预览（压缩后即上传物，预览所见即所传）。 */
  const handlePick = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      setError(null);
      let count = attachments.length;
      const added: Attachment[] = [];
      for (const file of Array.from(files)) {
        const problem = pickError(file, count);
        if (problem) {
          setNotice(problem);
          break;
        }
        try {
          const blob = await compressImage(file);
          added.push({ blob, url: URL.createObjectURL(blob) });
          count += 1;
        } catch {
          setNotice("这张图片读不出来，请换一张（支持 PNG / JPEG / WebP）。");
          break;
        }
      }
      if (added.length > 0) setAttachments((prev) => [...prev, ...added]);
    },
    [attachments.length],
  );

  const removeAttachment = useCallback((index: number) => {
    setAttachments((prev) => {
      const target = prev[index];
      if (target) URL.revokeObjectURL(target.url);
      return prev.filter((_, i) => i !== index);
    });
  }, []);

  /* 浏览器能力探测（只读，SSR 安全）：没有 getUserMedia / AudioWorklet 就不给麦克风按钮 */
  const micSupported =
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function" &&
    typeof AudioWorkletNode !== "undefined";

  /* 摄像头能力探测（同款只读探测；非安全上下文拿不到 mediaDevices） */
  const cameraSupported =
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function";

  const cameraOn = cameraStream !== null;

  /** 记忆摄像头开关（localStorage；隐私模式等不可用时静默为「本次有效」）。 */
  const rememberCamera = (on: boolean) => {
    try {
      window.localStorage.setItem(CAMERA_MODE_KEY, on ? "1" : "0");
    } catch {
      // 存储不可用：偏好只本次有效，不影响功能
    }
  };

  /** 关画面——三处收摊（开关 / 离开页面 / 面试结束）都经 stopStream 单一出口。 */
  const stopCamera = useCallback(() => {
    stopStream(cameraStreamRef.current);
    cameraStreamRef.current = null;
    setCameraStream(null);
  }, []);

  /**
   * 开画面。失败只提示、**不改偏好**（偏好只由用户的显式开关动作写入），
   * 文案按 DOMException 分流（M5 语音同款降级链：明说原因、给出路）。
   */
  const startCamera = useCallback(async () => {
    if (cameraStreamRef.current || cameraStartingRef.current) return;
    cameraStartingRef.current = true;
    setCameraStarting(true);
    setNotice(null);
    try {
      const stream = await openCamera();
      cameraStreamRef.current = stream;
      setCameraStream(stream);
      rememberCamera(true);
    } catch (err) {
      setNotice(cameraErrorMessage(err));
    } finally {
      cameraStartingRef.current = false;
      setCameraStarting(false);
    }
  }, []);

  const toggleCamera = useCallback(() => {
    if (cameraStreamRef.current) {
      stopCamera();
      rememberCamera(false);
      return;
    }
    void startCamera();
  }, [startCamera, stopCamera]);

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
          // 图片通道（P2-M6）：刷新恢复/只读回放同一条渲染路径——图不会「刷新就没」
          images: m.image_ids,
        })),
      );
      setPhase(session.phase);
      setAnswered(session.answered_count);
      setTotal(session.question_count);
      setReadonly(session.status === "finished");
      // 降级原因以会话状态为准（P2-M9）：刷新/中途进入也能看到横幅；
      // 与实时事件按序去重合并（事件可能先到）
      setDegraded((prev) => {
        const merged = [...(session.degraded_reasons ?? [])];
        for (const reason of prev) if (!merged.includes(reason)) merged.push(reason);
        return merged;
      });
    },
    [nextId],
  );

  /* 接管 401 处置：默认的「直接踢回登录页」在答题中途体感太差，改为弹确认后再跳 */
  useEffect(() => {
    setUnauthorizedHandler(() => setExpired(true));
    return () => setUnauthorizedHandler(null);
  }, []);

  /* 语音模式偏好（默认关）：存储不可用（隐私模式）静默当没开过 */
  useEffect(() => {
    try {
      voiceModeRef.current = window.localStorage.getItem(VOICE_MODE_KEY) === "1";
      setVoiceMode(voiceModeRef.current);
    } catch {
      voiceModeRef.current = false;
    }
  }, []);

  /* 摄像头偏好（默认关 + 记忆，P2-M7 决策②）：若记住「开」则进面试页自动恢复
     （浏览器已授权时无弹窗）；会话加载完成、且是进行中的场次才恢复；
     只尝试一次——重同步（reconcile）与重渲染都不再触发 */
  useEffect(() => {
    if (loading || readonly || reportReady || error || cameraRestoreTriedRef.current) return;
    cameraRestoreTriedRef.current = true;
    let remembered = false;
    try {
      remembered = window.localStorage.getItem(CAMERA_MODE_KEY) === "1";
    } catch {
      remembered = false;
    }
    if (remembered) void startCamera();
  }, [loading, readonly, reportReady, error, startCamera]);

  /* 面试结束（报告就绪）或只读回放：摄像头必须真的关掉，不只是藏起来 */
  useEffect(() => {
    if ((reportReady || readonly) && cameraStreamRef.current) stopCamera();
  }, [reportReady, readonly, stopCamera]);

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
    const cap = parseFloat(getComputedStyle(ta).maxHeight) || Infinity; // 上限单一来源 = max-h-40 / md:max-h-34
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

  /** 流式缓冲：分片/终稿的配对全在它里面（纯逻辑，vitest 覆盖），这里只落列表。 */
  const streamRef = useRef<StreamBuffer | null>(null);
  if (streamRef.current === null) streamRef.current = new StreamBuffer(nextId);
  const streamBuf = streamRef.current;

  const applyAction = (action: StreamAction) => {
    if (action.kind === "legacy") {
      // 旧打字机路径：无分片的消息（旧后端 / 未来的非流式节点）逐字吐
      queueRef.current.push(action.id, action.text);
    }
    setMessages((prev) => applyStreamAction(prev, action, createAssistant));
  };

  /** 新面试官消息终稿 → 播报（语音模式开着才播；TtsLayer 失败只提示一次）。 */
  const speak = (text: string) => {
    if (voiceModeRef.current) void speakerRef.current?.speak(text);
  };

  const persistVoiceMode = (next: boolean) => {
    voiceModeRef.current = next;
    setVoiceMode(next);
    try {
      window.localStorage.setItem(VOICE_MODE_KEY, next ? "1" : "0");
    } catch {
      // 隐私模式等存储不可用：只本次有效，不影响功能
    }
  };

  const toggleVoiceMode = () => {
    const next = !voiceModeRef.current;
    persistVoiceMode(next);
    if (!next) {
      speakerRef.current?.stop();
      return;
    }
    // 打开就播当前这道题：用户开语音模式，十有八九是想听正在问的题
    const lastAssistant = messagesRef.current.filter((m) => m.role === "assistant").pop();
    if (lastAssistant) speak(lastAssistant.content);
  };

  /**
   * 语音作答（P2-M5 决策②：点击开始、再点结束）。
   *
   * 转写**实时落输入框、不自动发送**（PRD FR-24：发送前可编辑——ASR 错字不能让评分官背锅）。
   * 开录时已有的草稿作底稿，转写整体替换其后那一段（partial 是累计文本，叠加会串片）。
   */
  const startRecording = useCallback(async () => {
    if (recorderRef.current) return;
    speakerRef.current?.stop(); // 开麦即停播（轻量打断）
    setNotice(null);
    draftBaseRef.current = draft;
    setConnecting(true);
    const recorder = new AsrRecorder({
      onPartial: (text) => setDraft(mergeTranscript(draftBaseRef.current, text)),
      onFinal: (text) => {
        setDraft(mergeTranscript(draftBaseRef.current, text));
        if (!text.trim()) setNotice(EMPTY_TRANSCRIPT_HINT);
        recorderRef.current = null;
        setRecording(false);
        setConnecting(false);
      },
      onError: (message) => {
        setNotice(message);
        recorderRef.current = null;
        setRecording(false);
        setConnecting(false);
      },
    });
    try {
      await recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
    } catch (err) {
      recorder.cancel();
      if (err instanceof UnauthorizedError) notifyUnauthorized();
      else setNotice(micErrorMessage(err));
    } finally {
      setConnecting(false);
    }
  }, [draft]);

  const stopRecording = useCallback(async () => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    setConnecting(true); // 等末段转写回来（超时由客户端兜底）
    await recorder.stop();
    setConnecting(false);
  }, []);

  /* 附件镜像：卸载清理要读「此刻」的附件列表（卸载回调闭包里读不到 state） */
  const attachmentsRef = useRef<Attachment[]>([]);
  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  /* 离开页面时收摊：麦克风轨道、AudioContext、WS、正在播的音频、附件预览 URL、摄像头轨道一个都不留 */
  useEffect(
    () => () => {
      recorderRef.current?.cancel();
      speakerRef.current?.stop();
      for (const item of attachmentsRef.current) URL.revokeObjectURL(item.url);
      stopStream(cameraStreamRef.current);
    },
    [],
  );

  const degradedBadge = degradedNotice(degraded);

  const handlers: SSEHandlers = {
    // 流式三件套（P2-M4）：start 开气泡 → chunk 追加 → delta 用终稿结算
    delta_start: () => applyAction(streamBuf.start()),
    delta_chunk: ({ text }) => applyAction(streamBuf.chunk(text)),
    delta: ({ text }) => {
      applyAction(streamBuf.delta(text));
      speak(text);
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
    /// 降级提示（P2-M9）：不是错误、不清空输入——只把原因挂上横幅，面试继续
    degraded: ({ reason }) => setDegraded((prev) => (prev.includes(reason) ? prev : [...prev, reason])),
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
        streamRef.current?.reset(); // 气泡 id 也一并换了，缓冲里的旧 id 必须作废
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
    async (content: string, appendUser: boolean, images: string[] = []) => {
      const text = content.trim();
      if (!text || busyRef.current) return;
      // 发送前本地已有的候选人气泡数 = 服务端用户消息条数的下限（对账基准）
      const baseline = messagesRef.current.filter((m) => m.role === "user").length;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      applyPending(null);
      if (appendUser) {
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: "user", content: text, images: images.length > 0 ? images : undefined },
        ]);
      }
      const failed = (message: string) => {
        setError(message);
        // images 入 pending：重试重发同一批 id（图已上传，不重复落盘）
        const turn: PendingTurn = { text, baseline, images: images.length > 0 ? images : undefined };
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
          images,
        );
      } catch (err) {
        // 401 由全局确认框接管，不再重复提示（重试也只会再 401）
        if (!(err instanceof UnauthorizedError)) {
          failed(err instanceof Error ? err.message : "网络异常，请重试");
        }
        queueRef.current.clear(); // 丢弃未吐完的残缺文案，避免与错误提示混淆
      } finally {
        streamingRef.current = false;
        /* 没收敛到终稿的流式气泡 = 该消息从未落 checkpoint（节点抛错则状态不提交）：
           留着就是「看得见、刷新就没」的假消息，收走（重试会重跑该节点、重新流一遍） */
        const leftover = streamBuf.abandon();
        if (leftover.length > 0) {
          setMessages((prev) => prev.filter((m) => !leftover.includes(m.id)));
        }
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
    void submit(stuck.text, false, stuck.images ?? []);
    setNotice(noticeText);
    return true;
  };

  /**
   * 发送（P2-M6 起可附截图）：**先传图拿 id，再提交消息**。
   *
   * 上传失败 → 错误提示 + 附件与草稿都保留（不静默丢图、不把没图的回答发出去）；
   * 上传成功但消息流失败/卡住 → 重试复用同一批 id（PendingTurn 带 images），不重复落盘。
   */
  const sendWithAttachments = async (text: string, files: Attachment[]) => {
    if (attaching) return;
    if (files.length === 0) {
      setDraft("");
      void submit(text, true, []);
      return;
    }
    setAttaching(true);
    setError(null);
    try {
      const ids: string[] = [];
      for (const file of files) ids.push(await uploadImage(interviewId, file.blob));
      releaseAttachments(files);
      setAttachments([]);
      setDraft("");
      void submit(text, true, ids);
    } catch (err) {
      // 401 由全局确认框接管；其余（超限/网络）给文案，附件保留可重试
      if (!(err instanceof UnauthorizedError)) {
        setError(err instanceof Error ? err.message : "图片上传失败，请重试");
      }
    } finally {
      setAttaching(false);
    }
  };

  const handleSend = () => {
    if (flushPending("上一轮的回答还没提交成功，已先为你重发；看到面试官回应后再发这条。")) {
      return;
    }
    setNotice(null); // 提示语只服务它那一次动作，新一轮动作即收走
    void sendWithAttachments(draft, attachments);
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
    void submit(stuck.text, false, stuck.images ?? []); // appendUser=false：气泡已经在列表里
  };

  const finished = reportReady;
  const lastIsUser = messages.length === 0 || messages[messages.length - 1].role === "user";
  const showThinking = busy && lastIsUser && !finished;

  /* 面试官 tile 的状态徽标（P2-M7 舞台）：全部由现有状态推导，纯函数在 lib 里有 vitest */
  const roomChip = interviewerChip({
    ended: readonly || reportReady,
    speaking: speakerSpeaking,
    recording,
    thinking: showThinking,
    busy,
  });

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
        width="narrow"
        right={
          <div className="flex items-center gap-3">
            <span className="hidden text-xs text-muted-foreground sm:inline">
              {PHASE_LABELS[phase]}
            </span>
            <span className="tabular-nums text-xs font-medium">
              {progressLabel(answered, total)}
            </span>
            {!readonly && (
              <Button
                variant={voiceMode ? "default" : "outline"}
                size="sm"
                aria-pressed={voiceMode}
                onClick={toggleVoiceMode}
                disabled={loading || finished}
                title="面试官消息用语音播报；作答可点下方「语音作答」"
              >
                语音模式
              </Button>
            )}
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
                disabled={busy || finished || loading || recording}
              >
                结束面试
              </Button>
            )}
          </div>
        }
      />

      {/* 面试间（P2-M7 改版④）：≥xl 是「左中右」——面试官卡在左、我在右、对话流夹在中间
          （空间语言 = 左边的人在说、右边的人在听，与气泡左右对齐同构）；两侧等宽 ⇒
          中间区域的中心 = 页面中心，对话列与输入区因此自动对齐、不需要改任何既有宽度。
          <xl 塞不下侧卡（768 对话列 + 两卡 + 留白 > 视口），回落为顶部卡片。
          两个安置点共用同一对 tile 组件——形态只有一处定义 */}
      <section aria-label="面试间" className="flex min-h-0 flex-1 justify-center">
        {/* 左护栏：**贴着对话流**——DOM 间距 8px + 对话列自带 24px 内边距 = 视觉 32px；
            三栏成组居中，外侧留白由视口自动吸收（1280 下 48px、1440 下 128px，天然大于内侧
            ⇒ 读作「护栏」而不是「贴边」） */}
        <div className="hidden items-center pr-2 xl:flex">
          <div className="w-[200px] 2xl:w-[240px]">
            <InterviewerTile chip={roomChip} />
          </div>
        </div>

        <div className="flex min-h-0 w-full max-w-3xl flex-col">
          {/* <xl 回落：顶部舞台卡（同一对 tile，换个安置点） */}
          <div className="xl:hidden">
            <StageCard
              chip={roomChip}
              stream={cameraStream}
              supported={cameraSupported}
              starting={cameraStarting}
              onToggle={toggleCamera}
            />
          </div>

          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="min-h-0 flex-1 overflow-y-auto"
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
                  interviewId={interviewId}
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
        </div>

        <div className="hidden items-center pl-2 xl:flex">
          <div className="w-[200px] 2xl:w-[240px]">
            <CandidateTile
              stream={cameraStream}
              supported={cameraSupported}
              starting={cameraStarting}
              onToggle={toggleCamera}
            />
          </div>
        </div>
      </section>

      <div className="border-t bg-background">
        <div className="mx-auto flex max-w-3xl flex-col gap-2 px-4 py-3 sm:px-6">
          {degradedBadge && <StatusBanner tone="warning">{degradedBadge}</StatusBanner>}
          {notice && <StatusBanner>{notice}</StatusBanner>}

          {error && (
            <StatusBanner
              tone="error"
              action={
                pending ? (
                  <Button variant="outline" size="sm" onClick={handleRetry}>
                    重试
                  </Button>
                ) : null
              }
            >
              {error}
            </StatusBanner>
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
              {attachments.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <div className="flex flex-wrap gap-2">
                    {attachments.map((item, index) => (
                      <div key={item.url} className="relative">
                        {/* eslint-disable-next-line @next/next/no-img-element -- 本地预览 blob */}
                        <img
                          src={item.url}
                          alt={`待发送截图 ${index + 1}`}
                          className="max-h-24 w-auto rounded-lg ring-1 ring-foreground/15"
                        />
                        <button
                          type="button"
                          onClick={() => removeAttachment(index)}
                          aria-label={`移除第 ${index + 1} 张截图`}
                          className="absolute -right-1.5 -top-1.5 flex size-5 items-center justify-center rounded-full bg-foreground text-xs text-background"
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                  {/* 如实交代图的用途（用户口径 2026-10-04）：别让用户以为传了白传 */}
                  <p className="text-xs text-muted-foreground">{ATTACH_HINT}</p>
                </div>
              )}
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
                // 随内容自增长、**封顶 6 行**（初始 2 行由 min-h-16 兜底）：md 起 text-sm/leading-5 →
                // 6×20px + 上下 padding 16px = 136px（max-h-34）；窄屏 text-base → 6×24px+16px = 160px（max-h-40）。
                // 再长就框内滚动，不挤占会话流
                className="max-h-40 min-h-16 resize-none overflow-y-auto md:max-h-34"
              />
              {/* 面试间控制条（P2-M7）：房间控件（语音 / 摄像头）+ 工具（截图）｜主操作（发送）。
                  flex-wrap：窄屏放不下时整组换行，不让 flex 把按钮压变形（M3 的教训） */}
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                <span className="text-xs text-muted-foreground">
                  {connecting
                    ? "正在连接语音…"
                    : recording
                      ? "正在听你说，说完点「结束录音」"
                      : cameraStarting
                        ? "正在打开摄像头…"
                        : busy
                          ? "面试官正在回应…"
                          : "答案越具体，评分与点评越准确"}
                </span>
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    variant={recording ? "default" : "outline"}
                    onClick={recording ? () => void stopRecording() : () => void startRecording()}
                    /* 录音中不禁用（否则用户按不停、录音收不了尾）——判据在 lib/voice.ts 里，有 vitest */
                    disabled={micDisabled({
                      recording,
                      connecting,
                      supported: micSupported,
                      busy,
                      finished,
                    })}
                    title={
                      micSupported
                        ? "点击开始录音，再点结束；转写会填进输入框，可修改后再发送"
                        : "当前浏览器不支持录音，请用文字作答"
                    }
                  >
                    {recording ? "结束录音" : "语音作答"}
                  </Button>
                  <Button
                    variant={cameraOn ? "default" : "outline"}
                    aria-pressed={cameraOn}
                    onClick={toggleCamera}
                    /* 开着的时候必须可点（那就是「关摄像头」）——判据在 lib/camera.ts 里，有 vitest */
                    disabled={
                      cameraDisabled({
                        on: cameraOn,
                        starting: cameraStarting,
                        supported: cameraSupported,
                      }) || loading
                    }
                    title={
                      cameraOn
                        ? "关闭本机画面"
                        : cameraSupported
                          ? `开启本机画面：${CAMERA_HINT}`
                          : "当前浏览器不支持摄像头，或页面不在安全上下文中"
                    }
                  >
                    {cameraStarting ? "连接中…" : "摄像头"}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => fileRef.current?.click()}
                    disabled={attaching || busy || loading || finished || recording}
                    title={ATTACH_TITLE}
                  >
                    {attaching ? "上传中…" : "附截图"}
                  </Button>
                  <input
                    ref={fileRef}
                    type="file"
                    accept={IMAGE_ACCEPT}
                    multiple
                    hidden
                    onChange={(e) => {
                      void handlePick(e.target.files);
                      e.target.value = ""; // 清空才能连选同一张
                    }}
                  />
                  {/* 工具与主操作之间留一道分隔（面试间控制条的房间感） */}
                  <div className="flex h-5 items-center" aria-hidden>
                    <Separator orientation="vertical" />
                  </div>
                  <Button
                    onClick={handleSend}
                    disabled={attaching || busy || loading || !draft.trim() || blocksSubmit(recording)}
                  >
                    发送
                  </Button>
                </div>
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
