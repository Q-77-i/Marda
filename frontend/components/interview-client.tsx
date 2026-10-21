"use client";

import {
  ArrowUp,
  Check,
  Code2,
  ImagePlus,
  Mic,
  Moon,
  Pencil,
  ShieldCheck,
  Square,
  Sun,
  Video,
  Volume2,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "cn";

import { CandidateTile, InterviewerTile, StageCard } from "@/components/interview-room";
import { CodeWorkbench } from "@/components/code-workbench";
import { InterviewWorkbench } from "@/components/interview-workbench";
import { MessageBubble, type ChatItem } from "@/components/message-bubble";
import { RoomTabs } from "@/components/room-tabs";
import { VoiceWave, type VoiceWaveHandle } from "@/components/voice-wave";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Textarea } from "@/components/ui/textarea";
import {
  correctTranscript,
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
import { phaseSteps } from "@/lib/interview-phases";
import { degradedNotice } from "@/lib/degrade";
import { SpeechAccumulator, type SpeechCounters } from "@/lib/expression";
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
  applyCorrection,
  blocksSubmit,
  mergeTranscript,
  micDisabled,
  micErrorMessage,
} from "@/lib/voice";
import { micLabel, voiceWaveState } from "@/lib/voice-wave";
import { latestQuestionText, panelFromSearch, type RoomPanel, type RoomTab } from "@/lib/room-tabs";
import { boardSubmitText } from "@/lib/whiteboard";
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

/** 把打开的工作台写进页面 URL（刷新恢复）；replaceState 不产生历史记录。 */
function writePanelQuery(panel: RoomPanel | null) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (panel) url.searchParams.set("panel", panel);
  else url.searchParams.delete("panel");
  window.history.replaceState(null, "", url.toString());
}

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
  /* 已停止录音、正在等最终转写（P2-M13）：独立态，避免「结束录音」按钮一直挂着 */
  const [transcribing, setTranscribing] = useState(false);
  /* 图片通道（P2-M6 FR-26）：选择的截图先压缩（预览即上传物），发送时上传拿 id 再发消息 */
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attaching, setAttaching] = useState(false);
  /* 右栏 Tab（P2-M13）：状态 / 白板 / 代码；白板工作台首次打开后保持挂载，收起不丢画布 */
  const [roomTab, setRoomTab] = useState<RoomTab>("status");
  const [boardOpen, setBoardOpen] = useState(false);
  const [boardMounted, setBoardMounted] = useState(false);
  /* 代码工作台（M15）：同样首次打开后保持挂载；与白板互斥（都是全屏覆盖层） */
  const [codeOpen, setCodeOpen] = useState(false);
  const [codeMounted, setCodeMounted] = useState(false);
  /* 摄像头通道（P2-M7 FR-27）：默认关 + 记忆；画面只在本机，帧不上传、不落库、AI 不看 */
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null);
  const [cameraStarting, setCameraStarting] = useState(false);
  /* 上次是否开过摄像头：只用于按钮文案「恢复摄像头」，**绝不自动取流** */
  const [cameraRemembered, setCameraRemembered] = useState(false);
  /* 面试间舞台：面试官 tile 的「正在播报」徽标（TTS 状态回调驱动） */
  const [speakerSpeaking, setSpeakerSpeaking] = useState(false);
  /* 左栏会话信息：岗位来自会话快照；类型不回传，故只展示岗位与阶段 */
  const [position, setPosition] = useState("AI 面试");
  /* 面试间配色：默认亮场，用户可切暗场并记忆（localStorage 不可用时仅本次有效） */
  const [roomTheme, setRoomTheme] = useState<"light" | "dark">("light");

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
  const panelRestoredRef = useRef(false); // 刷新恢复工作台面板只试一次
  const nearBottomRef = useRef(true);
  const messagesRef = useRef<ChatItem[]>([]);
  const pendingRef = useRef<PendingTurn | null>(null);
  /* 输入框最新值（智能校对回来时判断用户有没有改过/发出去） */
  const draftRef = useRef("");
  /* 当前题目（校对语境；会话回放没有实时 tag，回退到最后一条面试官消息） */
  const currentQuestionRef = useRef<string | null>(null);

  /* 语音：voiceMode 走 ref——submit 是稳定回调（deps 只有 interviewId），
     闭包里的 state 是旧的，而 delta 到达时要按「此刻」的开关决定播不播 */
  const voiceModeRef = useRef(false);
  const recorderRef = useRef<AsrRecorder | null>(null);
  const waveRef = useRef<VoiceWaveHandle>(null);
  /* 表达复盘（M16）：录音期间累积原始计数；发送时快照进消息，随草稿清账 */
  const speechRef = useRef<SpeechAccumulator>(new SpeechAccumulator());
  const draftBaseRef = useRef(""); // 开录时的草稿：转写整体替换它之后那一段
  const ttsWarnedRef = useRef(false);
  const fallbackWarnedRef = useRef(false);
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
      () => {
        // 系统音音色明显差：降级发生时明确提示，不静默切换
        if (fallbackWarnedRef.current) return;
        fallbackWarnedRef.current = true;
        setNotice("语音合成异常，已临时切换系统语音；题目以文字为准。");
      },
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

  /** 记忆摄像头开关（localStorage；隐私模式等不可用时静默为「本次有效」）。
   *  只驱动按钮提示（「恢复摄像头」），**不再触发自动取流**。 */
  const rememberCamera = (on: boolean) => {
    setCameraRemembered(on);
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
      setPosition(session.position || "AI 面试");
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

  /* 面试间配色偏好（默认亮场）：存储不可用（隐私模式）静默当没开过 */
  useEffect(() => {
    try {
      if (window.localStorage.getItem("marda.room.theme") === "dark") {
        setRoomTheme("dark");
      }
    } catch {
      setRoomTheme("light");
    }
  }, []);

  /* 摄像头偏好（P2-M7）：只读「上次是否开过」用于按钮提示（「恢复摄像头」），
     **不自动调 getUserMedia**——Safari 在页面加载时无用户手势取流会重新弹权限框，
     之前授权过也照弹。用户点击摄像头按钮后再取流，权限框只在主动操作时出现。 */
  useEffect(() => {
    try {
      setCameraRemembered(window.localStorage.getItem(CAMERA_MODE_KEY) === "1");
    } catch {
      setCameraRemembered(false);
    }
  }, []);

  /* 刷新恢复：打开中的工作台写在 URL ?panel=；会话加载完成后再开。
     白板在只读/已结束场次不能开（它会提交消息）；代码工作台可以。 */
  useEffect(() => {
    if (loading || error || panelRestoredRef.current) return;
    panelRestoredRef.current = true;
    const panel = panelFromSearch(window.location.search);
    if (panel === "code") {
      setRoomTab("code");
      setCodeMounted(true);
      setCodeOpen(true);
    } else if (panel === "board" && !readonly && !reportReady) {
      setRoomTab("board");
      setBoardMounted(true);
      setBoardOpen(true);
    } else if (panel === "board") {
      writePanelQuery(null);
    }
  }, [loading, error, readonly, reportReady]);

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

  const toggleRoomTheme = () => {
    setRoomTheme((prev) => {
      const next = prev === "light" ? "dark" : "light";
      try {
        window.localStorage.setItem("marda.room.theme", next);
      } catch {
        // 隐私模式等存储不可用：只本次有效
      }
      return next;
    });
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
    setTranscribing(false);
    const recorder = new AsrRecorder({
      /* 真实振幅 → 输入框左侧声波（P2-M13）：命令式写入，20Hz 不重渲染整页 */
      onLevel: (level) => waveRef.current?.setLevel(level),
      /* 原始 RMS → 表达复盘（M16）：与动效共用采样，只记静音/长停顿 */
      onSample: (rms) => speechRef.current.sample(rms),
      onPartial: (text) => {
        const next = mergeTranscript(draftBaseRef.current, text);
        draftRef.current = next;
        setDraft(next);
      },
      onFinal: (text) => {
        const base = draftBaseRef.current;
        const merged = mergeTranscript(base, text);
        draftRef.current = merged;
        setDraft(merged);
        if (!text.trim()) {
          setNotice(EMPTY_TRANSCRIPT_HINT);
          speechRef.current.cancel();
        } else {
          speechRef.current.finalize(text);
        }
        recorderRef.current = null;
        setRecording(false);
        setTranscribing(false);
        setConnecting(false);
        /* 第二层：ASR final 后做一次保守 LLM 校对。只在用户没改过/没发出去时替换；
           失败保留原文，不打断面试。 */
        if (text.trim()) {
          void correctTranscript(text, currentQuestionRef.current)
            .then((corrected) => {
              const next = applyCorrection(base, text, corrected, draftRef.current);
              if (next === draftRef.current) return;
              // 校对真的替换了草稿 → 表达指标的字数/口头禅跟着改（时长/停顿不变）
              speechRef.current.updateLastTranscript(corrected);
              draftRef.current = next;
              setDraft(next);
              setNotice("已智能纠正转写，请确认后发送。");
            })
            .catch(() => {
              // 校对是增强不是必需：失败保留原文，不弹错误
            });
        }
      },
      onError: (message) => {
        speechRef.current.cancel();
        setNotice(message);
        recorderRef.current = null;
        setRecording(false);
        setTranscribing(false);
        setConnecting(false);
      },
    });
    try {
      await recorder.start();
      speechRef.current.begin();
      recorderRef.current = recorder;
      setRecording(true);
    } catch (err) {
      recorder.cancel();
      speechRef.current.cancel();
      setRecording(false);
      setTranscribing(false);
      if (err instanceof UnauthorizedError) notifyUnauthorized();
      else setNotice(micErrorMessage(err));
    } finally {
      setConnecting(false);
    }
  }, [draft]);

  const stopRecording = useCallback(async () => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    /* 停录后立刻进入「整理中」：按钮不能再写「结束录音」，
       发送也要被挡住，等 final 把最后一段转写落框 */
    setRecording(false);
    setConnecting(false);
    setTranscribing(true);
    /* 时长在 stop 冻结：final 可能还要等 8s，不能把等待算成说话时长（M16） */
    speechRef.current.stop();
    try {
      await recorder.stop();
    } finally {
      setTranscribing(false);
      setConnecting(false);
    }
  }, []);

  /* 附件镜像：卸载清理要读「此刻」的附件列表（卸载回调闭包里读不到 state） */
  const attachmentsRef = useRef<Attachment[]>([]);
  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  /* 输入框镜像：智能校对回来时判断「用户有没有在等待期间改过/发出去」 */
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  /* 智能校对需要当前题目做语境（刷新/回放时最后一条面试官消息就是当前题） */
  useEffect(() => {
    currentQuestionRef.current = latestQuestionText(messages, true);
  }, [messages]);

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
    // 流式三件套（P2-M4）：start 开气泡 → chunk 追加 → delta 用终稿结算。
    // 语音播报同时吃这条流：首句一到标点就开始 TTS（P2-M13 优化），不等 final。
    delta_start: () => {
      if (voiceModeRef.current) speakerRef.current?.begin();
      applyAction(streamBuf.start());
    },
    delta_chunk: ({ text }) => {
      if (voiceModeRef.current) speakerRef.current?.push(text);
      applyAction(streamBuf.chunk(text));
    },
    delta: ({ text }) => {
      if (voiceModeRef.current) speakerRef.current?.finish(text);
      applyAction(streamBuf.delta(text));
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
    async (
      content: string,
      appendUser: boolean,
      images: string[] = [],
      speech?: SpeechCounters,
    ) => {
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
        // images/speech 入 pending：重试重发同一批数据（图已上传，指标也不该丢）
        const turn: PendingTurn = {
          text,
          baseline,
          images: images.length > 0 ? images : undefined,
          speech,
        };
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
          speech,
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
    void submit(stuck.text, false, stuck.images ?? [], stuck.speech);
    setNotice(noticeText);
    return true;
  };

  /**
   * 发送（P2-M6 起可附截图）：**先传图拿 id，再提交消息**。
   *
   * 上传失败 → 错误提示 + 附件与草稿都保留（不静默丢图、不把没图的回答发出去）；
   * 上传成功但消息流失败/卡住 → 重试复用同一批 id（PendingTurn 带 images），不重复落盘。
   */
  const sendWithAttachments = async (
    text: string,
    files: Attachment[],
    speech?: SpeechCounters,
  ): Promise<boolean> => {
    if (attaching) return false;
    if (files.length === 0) {
      draftRef.current = "";
      setDraft("");
      speechRef.current.reset();
      void submit(text, true, [], speech);
      return true;
    }
    setAttaching(true);
    setError(null);
    try {
      const ids: string[] = [];
      for (const file of files) ids.push(await uploadImage(interviewId, file.blob));
      releaseAttachments(files);
      setAttachments([]);
      draftRef.current = "";
      setDraft("");
      speechRef.current.reset();
      void submit(text, true, ids, speech);
      return true;
    } catch (err) {
      // 401 由全局确认框接管；其余（超限/网络）给文案，附件保留可重试
      if (!(err instanceof UnauthorizedError)) {
        setError(err instanceof Error ? err.message : "图片上传失败，请重试");
      }
      return false;
    } finally {
      setAttaching(false);
    }
  };

  const handleSend = () => {
    // Enter 也会走到这里：录音/整理中不能把半截转写发出去（发送键禁用只是视觉层）
    if (recording || transcribing) return;
    if (flushPending("上一轮的回答还没提交成功，已先为你重发；看到面试官回应后再发这条。")) {
      return;
    }
    setNotice(null); // 提示语只服务它那一次动作，新一轮动作即收走
    const speech = speechRef.current.snapshot();
    void sendWithAttachments(draft, attachments, speech ?? undefined);
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
    // appendUser=false：气泡已经在列表里；speech 跟着重发，别把重试变成「没说过话」
    void submit(stuck.text, false, stuck.images ?? [], stuck.speech);
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

  const currentQuestion = latestQuestionText(messages, true);
  const waveState = voiceWaveState({ recording, connecting, transcribing });

  /** 打开白板工作台：首次打开后保持挂载（收起不丢画布）；结束/只读场次不给开。 */
  const openBoard = () => {
    if (readonly || finished || loading) return;
    setCodeOpen(false);
    setRoomTab("board");
    setBoardMounted(true);
    setBoardOpen(true);
    writePanelQuery("board");
  };

  /** 打开代码工作台（M15）：独立练习，**只读回放也允许**（不发消息、不写本场记录）。 */
  const openCode = () => {
    if (loading) return;
    setBoardOpen(false);
    setRoomTab("code");
    setCodeMounted(true);
    setCodeOpen(true);
    writePanelQuery("code");
  };

  /** 右栏切 Tab：白板/代码都打开各自的工作台（右侧 320px 放不下画布/编辑器）。 */
  const handleRoomTab = (tab: RoomTab) => {
    setRoomTab(tab);
    if (tab === "board") openBoard();
    if (tab === "code") openCode();
  };

  /**
   * 白板提交：导出 PNG → 走既有图片通道上传 → 作为下一条回答的附件发送。
   * 与截图完全同一条管线（后端零改动）；失败时保留工作台与画布，可重试。
   */
  const submitBoard = async (blob: Blob): Promise<boolean> => {
    if (busy || loading || finished || readonly || attaching) return false;
    if (
      flushPending(
        "上一轮的回答还没提交成功，已先为你重发；看到面试官回应后再提交白板。",
      )
    ) {
      return false;
    }
    const file: Attachment = { blob, url: URL.createObjectURL(blob) };
    const ok = await sendWithAttachments(boardSubmitText(draft), [file]);
    if (!ok) URL.revokeObjectURL(file.url);
    return ok;
  };

  const phaseState = phaseSteps(phase);
  const composerHint =
    waveState === "connecting"
      ? "正在连接语音…"
      : waveState === "recording"
        ? "正在听你说，说完点「结束录音」"
        : waveState === "transcribing"
          ? "正在整理转写，稍等…"
          : cameraStarting
            ? "正在打开摄像头…"
            : busy
              ? "面试官正在回应…"
              : "答案越具体，评分与点评越准确";

  return (
    <div
      className={cn(
        "room flex h-[100dvh] flex-col overflow-hidden bg-room-bg text-room-ink xl:grid xl:grid-cols-[264px_minmax(0,1fr)_320px]",
        roomTheme === "dark" && "room-dark",
      )}
    >
      {/* 登录过期（补充点 ①）：只留「重新登录」一个出口——留在页面上每个请求都会 401 */}
      <AlertDialog open={expired}>
        <AlertDialogContent className="border border-room-line bg-room-panel text-room-ink ring-0">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-room-ink">登录已过期</AlertDialogTitle>
            <AlertDialogDescription className="text-room-muted">
              需要重新登录才能继续。已提交的回答都已保存，重新登录后可回到本场面试继续作答。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="border-room-line bg-room-panel-2">
            <AlertDialogAction onClick={redirectToLogin}>重新登录</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 左栏：会话、阶段、结束 */}
      <aside className="hidden h-full min-h-0 flex-col overflow-y-auto border-r border-rail-line bg-rail text-rail-foreground [&>*]:shrink-0 xl:flex">
        <Link href="/" className="flex items-center gap-2.5 px-3.5 py-5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-sm font-bold tracking-[-0.05em] text-primary-foreground">
            M
          </span>
          <span className="flex flex-col leading-none">
            <span className="text-[13.5px] font-semibold tracking-[-0.01em] text-white">
              Marda 码达
            </span>
            <span className="mt-1 font-mono text-[9px] tracking-[0.14em] text-rail-muted uppercase">
              Interview Room
            </span>
          </span>
        </Link>

        <div className="mx-3.5 mt-1 rounded-lg border border-rail-line bg-white/[0.025] p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 font-mono text-[8.5px] tracking-[0.1em] text-[#8ee0b6] uppercase">
              <span
                className={cn(
                  "size-1 rounded-full bg-current",
                  !readonly && !finished && "animate-pulse",
                )}
              />
              {readonly ? "Read-only" : finished ? "Finishing" : "Live session"}
            </span>
            <span className="font-mono text-[8px] text-rail-muted">
              {progressLabel(answered, total)}
            </span>
          </div>
          <div className="mt-2 truncate text-[13px] font-semibold text-[#f0eee8]">
            {position}
          </div>
          <div className="mt-1 font-mono text-[8.5px] text-rail-muted">
            {PHASE_LABELS[phase]}
          </div>
          <div className="mt-3">
            <div className="flex items-baseline justify-between gap-2 font-mono text-[9px] text-rail-muted">
              <span>进度</span>
              <span>
                <b className="font-medium text-[#e9e8e2]">{answered}</b> / {total} 题
              </span>
            </div>
            <div className="mt-1.5 h-[3px] overflow-hidden rounded-full bg-white/10">
              <span
                className="block h-full rounded-full bg-[#8ba1ff] transition-[width]"
                style={{
                  width: `${total ? Math.min(100, (answered / total) * 100) : 0}%`,
                }}
              />
            </div>
          </div>
        </div>

        <div className="mt-5 px-3.5 font-mono text-[8.5px] tracking-[0.14em] text-[#666a71] uppercase">
          Interview phases
        </div>
        <div className="mt-2 px-3.5">
          {phaseState.steps.map((step, index) => {
            const done =
              index < phaseState.currentIndex ||
              (phaseState.finished && index <= phaseState.currentIndex);
            const active = index === phaseState.currentIndex && !phaseState.finished;
            return (
              <div
                key={step.key}
                className="relative grid grid-cols-[18px_minmax(0,1fr)] gap-2.5 px-2 pb-4 last:pb-0"
              >
                {index < phaseState.steps.length - 1 ? (
                  <span
                    aria-hidden
                    className="absolute top-4 bottom-0 left-[16px] w-px bg-white/10"
                  />
                ) : null}
                <span
                  className={cn(
                    "relative z-[1] flex size-[17px] items-center justify-center rounded-full border bg-rail font-mono text-[8px]",
                    done
                      ? "border-[#3d6b56] bg-[#1c2a24] text-[#7fcea8]"
                      : active
                        ? "border-[#8ba1ff] bg-[#1a2140] text-[#b9c6ff]"
                        : "border-[#3a3e46] text-[#70747b]",
                  )}
                >
                  {done ? (
                    <Check className="size-2.5" strokeWidth={2} />
                  ) : (
                    String(index + 1).padStart(2, "0")
                  )}
                </span>
                <span className="flex flex-col gap-0.5">
                  <span
                    className={cn(
                      "text-[11.5px] leading-tight",
                      done
                        ? "text-[#b9bdc4]"
                        : active
                          ? "font-medium text-white"
                          : "text-[#8f939a]",
                    )}
                  >
                    {step.label}
                  </span>
                  <span
                    className={cn(
                      "font-mono text-[8px]",
                      active ? "text-[#8ba1ff]" : "text-[#5f636a]",
                    )}
                  >
                    {done
                      ? step.key === "finished"
                        ? "已生成"
                        : "已完成"
                      : active
                        ? "进行中"
                        : "待进入"}
                  </span>
                </span>
              </div>
            );
          })}
        </div>

        <div className="mt-auto flex flex-col gap-2.5 p-3.5">
          {readonly ? (
            <Link
              href={`/report/${interviewId}`}
              className="flex h-[38px] w-full items-center justify-center gap-2 rounded-lg border border-white/20 text-[12px] text-[#d7d9de] transition-colors hover:border-white/40 hover:bg-white/5"
            >
              查看报告
            </Link>
          ) : (
            <button
              type="button"
              onClick={handleEnd}
              disabled={busy || finished || loading || recording || transcribing}
              className="flex h-[38px] w-full items-center justify-center gap-2 rounded-lg border border-white/20 text-[12px] text-[#d7d9de] transition-colors hover:border-white/40 hover:bg-white/5 disabled:pointer-events-none disabled:opacity-45"
            >
              <Square className="size-3.5" strokeWidth={1.8} />
              结束面试
            </button>
          )}
          <div className="flex items-center justify-center gap-1.5 font-mono text-[8px] text-rail-muted">
            <ShieldCheck className="size-3" strokeWidth={1.8} />
            回答已自动保存
          </div>
        </div>
      </aside>

      {/* 中栏：顶栏 + 窄屏舞台 + 对话 + 控制台 */}
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex h-[62px] flex-none items-center justify-between gap-3 border-b border-room-line px-4 md:px-6">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-room-accent text-[12px] font-bold tracking-[-0.05em] text-room-bg xl:hidden">
              M
            </span>
            <div className="min-w-0">
              <div className="flex items-baseline gap-2">
                <strong className="truncate text-[12.5px] font-medium">
                  {PHASE_LABELS[phase]}
                </strong>
                <span className="font-mono text-[9.5px] text-room-muted">
                  {progressLabel(answered, total)}
                </span>
              </div>
              <div className="truncate font-mono text-[8.5px] text-room-faint">
                {position}
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              onClick={toggleRoomTheme}
              aria-pressed={roomTheme === "dark"}
              title="切换面试间配色"
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-room-line px-2 text-[10.5px] text-room-muted transition-colors hover:border-room-line-strong hover:text-room-ink"
            >
              {roomTheme === "dark" ? (
                <Moon className="size-3.5" strokeWidth={1.8} />
              ) : (
                <Sun className="size-3.5" strokeWidth={1.8} />
              )}
              <span className="hidden sm:inline">
                {roomTheme === "dark" ? "暗场" : "亮场"}
              </span>
            </button>
            {!readonly ? (
              <button
                type="button"
                aria-pressed={voiceMode}
                onClick={toggleVoiceMode}
                disabled={loading || finished}
                title="面试官消息用语音播报；作答可点下方「语音作答」"
                className={cn(
                  "inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-[10.5px] transition-colors disabled:pointer-events-none disabled:opacity-45",
                  voiceMode
                    ? "border-room-accent bg-room-accent-soft text-room-accent"
                    : "border-room-line text-room-muted hover:border-room-line-strong hover:text-room-ink",
                )}
              >
                <Volume2 className="size-3.5" strokeWidth={1.8} />
                <span className="hidden sm:inline">语音播报</span>
              </button>
            ) : null}
            <button
              type="button"
              onClick={openBoard}
              disabled={readonly || finished || loading}
              title="打开白板"
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-room-line px-2 text-[10.5px] text-room-muted transition-colors hover:border-room-line-strong hover:text-room-ink disabled:pointer-events-none disabled:opacity-45 xl:hidden"
            >
              <Pencil className="size-3.5" strokeWidth={1.8} />
              <span className="hidden sm:inline">白板</span>
            </button>
            <button
              type="button"
              onClick={openCode}
              disabled={loading}
              title="打开代码工作台"
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-room-line px-2 text-[10.5px] text-room-muted transition-colors hover:border-room-line-strong hover:text-room-ink disabled:pointer-events-none disabled:opacity-45 xl:hidden"
            >
              <Code2 className="size-3.5" strokeWidth={1.8} />
              <span className="hidden sm:inline">代码</span>
            </button>
            {readonly ? (
              <Link
                href={`/report/${interviewId}`}
                className="inline-flex h-7 items-center rounded-md bg-room-accent px-2.5 text-[10.5px] font-medium text-room-bg xl:hidden"
              >
                查看报告
              </Link>
            ) : (
              <button
                type="button"
                onClick={handleEnd}
                disabled={busy || finished || loading || recording || transcribing}
                className="inline-flex h-7 items-center gap-1.5 rounded-md border border-room-line px-2 text-[10.5px] text-room-muted transition-colors hover:border-room-line-strong hover:text-room-ink disabled:pointer-events-none disabled:opacity-45 xl:hidden"
              >
                <Square className="size-3" strokeWidth={1.8} />
                <span className="hidden sm:inline">结束</span>
              </button>
            )}
          </div>
        </header>

        {/* 窄屏顶部舞台：保留摄像头入口，宽屏改由右栏承载 */}
        <div className="xl:hidden">
          <StageCard
            chip={roomChip}
            speaking={speakerSpeaking}
            stream={cameraStream}
            supported={cameraSupported}
            starting={cameraStarting}
            onToggle={toggleCamera}
            remembered={cameraRemembered}
          />
        </div>

        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="min-h-0 flex-1 overflow-y-auto px-4 py-5 md:px-6"
        >
          <div className="mx-auto flex max-w-[720px] flex-col">
            {loading && (
              <p className="text-xs text-room-muted">正在载入面试…</p>
            )}
            {!loading && messages.length === 0 && !error && (
              <p className="text-xs text-room-muted">
                面试尚未开始，请返回首页重新创建。
              </p>
            )}
            {messages.map((item, index) => (
              <MessageBubble
                key={item.id}
                interviewId={interviewId}
                last={index === messages.length - 1 && !showThinking}
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

        <div className="flex-none border-t border-room-line bg-room-bg px-4 py-3 md:px-6">
          <div className="mx-auto flex max-w-[720px] flex-col gap-2">
            {degradedBadge && <RoomBanner tone="warning">{degradedBadge}</RoomBanner>}
            {notice && (
              <RoomBanner onDismiss={() => setNotice(null)}>{notice}</RoomBanner>
            )}
            {error && (
              <RoomBanner
                tone="error"
                action={
                  pending ? (
                    <button
                      type="button"
                      onClick={handleRetry}
                      className="h-7 shrink-0 rounded-md border border-room-line px-2.5 text-[10.5px] text-room-ink transition-colors hover:bg-room-panel-2"
                    >
                      重试
                    </button>
                  ) : null
                }
              >
                {error}
              </RoomBanner>
            )}

            {readonly ? (
              <p className="py-2 text-xs text-room-muted">
                本场面试已结束，以上为完整回放。
                <Link
                  href={`/report/${interviewId}`}
                  className="ml-1 text-room-accent underline underline-offset-2"
                >
                  查看能力报告
                </Link>
              </p>
            ) : finished ? (
              <p className="py-2 text-xs text-room-muted">
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
                            className="max-h-24 w-auto rounded-lg ring-1 ring-room-line"
                          />
                          <button
                            type="button"
                            onClick={() => removeAttachment(index)}
                            aria-label={`移除第 ${index + 1} 张截图`}
                            className="absolute -top-1.5 -right-1.5 flex size-5 items-center justify-center rounded-full bg-room-ink text-[11px] text-room-bg"
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                    <p className="text-[10.5px] text-room-muted">{ATTACH_HINT}</p>
                  </div>
                )}

                <div className="rounded-lg border border-room-line-strong bg-room-panel transition-colors focus-within:border-room-accent focus-within:ring-2 focus-within:ring-room-accent/20">
                  <Textarea
                    ref={inputRef}
                    value={draft}
                    onChange={(e) => {
                      draftRef.current = e.target.value;
                      setDraft(e.target.value);
                      // 用户把草稿清空 = 放弃这段作答：别让录制指标挂到下一段手打消息上（M16）
                      if (!e.target.value.trim() && !recording && !transcribing) {
                        speechRef.current.reset();
                      }
                    }}
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
                    className="max-h-40 min-h-16 resize-none overflow-y-auto border-0 bg-transparent px-3.5 pt-3 pb-1 text-[12.5px] shadow-none focus-visible:border-transparent focus-visible:ring-0 md:max-h-34"
                  />
                  <div className="flex flex-wrap items-center justify-between gap-2 px-2 pb-2">
                    <div className="flex items-center gap-0.5">
                      <RoomTool
                        icon={Mic}
                        label={micLabel(waveState)}
                        pressed={recording}
                        onClick={
                          recording
                            ? () => void stopRecording()
                            : () => void startRecording()
                        }
                        disabled={micDisabled({
                          recording,
                          connecting,
                          transcribing,
                          supported: micSupported,
                          busy,
                          finished,
                        })}
                        title={
                          micSupported
                            ? "点击开始录音，再点结束；转写会填进输入框，可修改后再发送"
                            : "当前浏览器不支持录音，请用文字作答"
                        }
                      />
                      <VoiceWave ref={waveRef} state={waveState} className="ml-1" />
                      <RoomTool
                        icon={Video}
                        label={
                          cameraStarting
                            ? "连接中…"
                            : cameraRemembered && !cameraOn
                              ? "恢复摄像头"
                              : "摄像头"
                        }
                        pressed={cameraOn}
                        onClick={toggleCamera}
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
                      />
                      <RoomTool
                        icon={ImagePlus}
                        label={attaching ? "上传中…" : "附截图"}
                        onClick={() => fileRef.current?.click()}
                        disabled={attaching || busy || loading || finished || recording}
                        title={ATTACH_TITLE}
                      />
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
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[8.5px] text-room-faint">
                        {progressLabel(answered, total)}
                      </span>
                      <span aria-hidden className="h-4 w-px bg-room-line" />
                      <button
                        type="button"
                        onClick={handleSend}
                        disabled={
                          attaching ||
                          busy ||
                          loading ||
                          !draft.trim() ||
                          blocksSubmit(recording || transcribing)
                        }
                        className="inline-flex h-[30px] items-center gap-1.5 rounded-md bg-room-accent px-3 text-[11.5px] font-medium text-room-bg transition-colors hover:bg-room-accent/90 disabled:pointer-events-none disabled:opacity-45"
                      >
                        发送
                        <ArrowUp className="size-3.5" strokeWidth={1.8} />
                      </button>
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between gap-2 px-1 font-mono text-[8.5px] text-room-faint">
                  <span className="truncate">{composerHint}</span>
                  <span className="hidden shrink-0 sm:inline">
                    Enter 发送 / Shift + Enter 换行
                  </span>
                </div>
              </>
            )}
          </div>
        </div>
      </main>

      {/* 右栏：舞台、信号、答题提示 */}
      <aside className="hidden min-h-0 flex-col gap-3 overflow-y-auto border-l border-room-line bg-room-bg p-4 [&>*]:shrink-0 xl:flex">
        <div className="flex items-center justify-between gap-2 px-0.5">
          <span className="font-mono text-[8.5px] tracking-[0.14em] text-room-faint uppercase">
            Stage / live
          </span>
          <span className="font-mono text-[8px] text-room-faint">仅本机</span>
        </div>

        <div className="overflow-hidden rounded-lg border border-room-line bg-room-panel">
          <InterviewerTile
            chip={roomChip}
            speaking={speakerSpeaking}
            className="rounded-none border-0"
          />
          <div className="flex items-center justify-between border-t border-room-line px-2.5 py-2 font-mono text-[8px] text-room-muted">
            <span>Interviewer</span>
            <span
              className={cn(
                "flex items-center gap-1.5",
                speakerSpeaking && "text-room-accent",
              )}
            >
              <span className="size-1 rounded-full bg-current" />
              {speakerSpeaking ? "Speaking" : "Idle"}
            </span>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-room-line bg-room-panel">
          <CandidateTile
            stream={cameraStream}
            supported={cameraSupported}
            starting={cameraStarting}
            onToggle={toggleCamera}
            remembered={cameraRemembered}
            className="rounded-none border-0"
          />
          <div className="flex items-center justify-between border-t border-room-line px-2.5 py-2 font-mono text-[8px] text-room-muted">
            <span>本机画面</span>
            <span className="flex items-center gap-1.5">
              <span className="size-1 rounded-full bg-current" />
              {cameraOn ? "Camera on" : "Camera off"}
            </span>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-room-line bg-room-panel">
          <RoomTabs value={roomTab} onChange={handleRoomTab} />
          <div
            id="room-tab-panel"
            role="tabpanel"
            aria-labelledby={`room-tab-${roomTab}`}
          >
            {roomTab === "status" ? (
              <>
                {currentQuestion ? (
                  <div className="border-b border-room-line px-3 py-2.5">
                    <div className="font-mono text-[8px] tracking-[0.12em] text-room-faint uppercase">
                      Current question
                    </div>
                    <p className="mt-1 line-clamp-3 text-[10.5px] leading-relaxed text-room-ink">
                      {currentQuestion}
                    </p>
                  </div>
                ) : null}
                {[
                  ["当前阶段", PHASE_LABELS[phase]],
                  ["已答题目", `${answered} / ${total}`],
                  ["语音播报", voiceMode ? "开启" : "关闭"],
                  ["摄像头", cameraOn ? "开启" : "关闭"],
                  ["本场状态", readonly ? "只读回放" : finished ? "生成报告" : "进行中"],
                ].map(([label, value], index) => (
                  <div
                    key={label}
                    className={cn(
                      "flex items-center justify-between gap-2 px-3 py-2 text-[10.5px]",
                      index > 0 && "border-t border-room-line",
                    )}
                  >
                    <span className="text-room-muted">{label}</span>
                    <b className="font-mono text-[9.5px] font-medium text-room-ink">
                      {value}
                    </b>
                  </div>
                ))}
              </>
            ) : roomTab === "board" ? (
              <div className="px-3 py-3.5">
                <div className="flex items-start gap-2.5">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-room-line bg-room-panel-2 text-room-accent">
                    <Pencil className="size-4" strokeWidth={1.7} />
                  </span>
                  <div className="min-w-0">
                    <p className="text-[11px] font-medium text-room-ink">架构图白板</p>
                    <p className="mt-1 text-[10px] leading-relaxed text-room-muted">
                      画完导出成图片，随下一条回答提交；面试官会结合图点评。
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={openBoard}
                  disabled={readonly || finished || loading}
                  className="mt-3 h-8 w-full rounded-md bg-room-accent text-[11px] font-medium text-room-bg transition-colors hover:bg-room-accent/90 disabled:pointer-events-none disabled:opacity-45"
                >
                  {boardOpen ? "回到白板" : "打开白板"}
                </button>
                {readonly || finished ? (
                  <p className="mt-2 text-[9.5px] leading-relaxed text-room-faint">
                    面试已结束，无法再提交白板。
                  </p>
                ) : null}
              </div>
            ) : roomTab === "code" ? (
              <div className="px-3 py-3.5">
                <div className="flex items-start gap-2.5">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-room-line bg-room-panel-2 text-room-accent">
                    <Code2 className="size-4" strokeWidth={1.7} />
                  </span>
                  <div className="min-w-0">
                    <p className="text-[11px] font-medium text-room-ink">机试代码工作台</p>
                    <p className="mt-1 text-[10px] leading-relaxed text-room-muted">
                      题库随机算法题 + 结构化点评与追问；独立练习，不进面试评分与报告。
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={openCode}
                  disabled={loading}
                  className="mt-3 h-8 w-full rounded-md bg-room-accent text-[11px] font-medium text-room-bg transition-colors hover:bg-room-accent/90 disabled:pointer-events-none disabled:opacity-45"
                >
                  {codeOpen ? "回到代码工作台" : "打开代码工作台"}
                </button>
                {readonly || finished ? (
                  <p className="mt-2 text-[9.5px] leading-relaxed text-room-faint">
                    回放/已结束场次也可独立练习；代码不写入本场记录。
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-room-line bg-room-panel">
          <div className="border-b border-room-line px-3 py-2 font-mono text-[8.5px] tracking-[0.12em] text-room-faint uppercase">
            Answer cues
          </div>
          {[
            ["01", "先给结论，再补约束与取舍。"],
            ["02", "主动说出失败路径与兜底方案。"],
            ["03", "用项目里的具体数字支撑判断。"],
          ].map(([index, text]) => (
            <div
              key={index}
              className="grid grid-cols-[18px_minmax(0,1fr)] gap-1.5 border-t border-room-line px-3 py-2 first:border-t-0"
            >
              <span className="font-mono text-[8px] text-room-accent">{index}</span>
              <p className="text-[10px] leading-relaxed text-room-muted">{text}</p>
            </div>
          ))}
        </div>

        <p className="px-0.5 text-[9.5px] leading-relaxed text-room-faint">
          摄像头画面只在本机渲染，不上传、不落库，模型不会读取。截图/白板会随回答提交，用于评分与追问。
        </p>
      </aside>

      {boardMounted ? (
        <InterviewWorkbench
          open={boardOpen}
          onClose={() => {
            setBoardOpen(false);
            writePanelQuery(null);
          }}
          question={currentQuestion}
          error={error}
          notice={notice}
          disabled={busy || loading || finished || readonly}
          attaching={attaching}
          onSubmit={submitBoard}
          storageKey={`marda.whiteboard.${interviewId}`}
        />
      ) : null}

      {codeMounted ? (
        <CodeWorkbench
          open={codeOpen}
          onClose={() => {
            setCodeOpen(false);
            writePanelQuery(null);
          }}
          storageKey={`marda.coding.${interviewId}`}
        />
      ) : null}
    </div>
  );
}

/** 提交后、首个 delta 到达前的等待指示（面试官思考中）。 */
function ThinkingBubble() {
  return (
    <div className="flex items-start gap-3.5 pb-6">
      <div className="flex size-[29px] items-center justify-center rounded-lg border border-room-line-strong bg-room-panel text-[10px] font-semibold">
        M
      </div>
      <div className="mt-1.5 flex h-8 w-fit items-center gap-1 rounded-lg border border-room-line bg-room-panel px-3">
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="size-1 animate-pulse rounded-full bg-room-muted"
            style={{ animationDelay: `${index * 160}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

function RoomBanner({
  tone = "info",
  children,
  action,
  onDismiss,
}: {
  tone?: "info" | "warning" | "error";
  children: ReactNode;
  action?: ReactNode;
  onDismiss?: () => void;
}) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-[11px] leading-relaxed",
        tone === "error"
          ? "border-room-danger/30 bg-room-danger/10 text-room-danger"
          : tone === "warning"
            ? "border-room-accent/30 bg-room-accent-soft text-room-ink"
            : "border-room-line bg-room-panel text-room-muted",
      )}
    >
      <span className="min-w-0">{children}</span>
      <span className="flex shrink-0 items-center gap-1">
        {action}
        {onDismiss ? (
          <button
            type="button"
            onClick={onDismiss}
            aria-label="关闭提示"
            className="flex size-6 items-center justify-center rounded text-[15px] leading-none text-room-muted transition-colors hover:bg-room-panel-2 hover:text-room-ink"
          >
            ×
          </button>
        ) : null}
      </span>
    </div>
  );
}

function RoomTool({
  icon: Icon,
  label,
  pressed,
  onClick,
  disabled,
  title,
}: {
  icon: LucideIcon;
  label: string;
  pressed?: boolean;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[10.5px] transition-colors",
        pressed
          ? "bg-room-accent-soft text-room-accent"
          : "text-room-muted hover:bg-room-panel-2 hover:text-room-ink",
        "disabled:pointer-events-none disabled:opacity-45",
      )}
    >
      <Icon className="size-3.5" strokeWidth={1.8} />
      <span>{label}</span>
    </button>
  );
}
