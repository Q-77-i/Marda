/**
 * 面试官语音播报（P2-M5 FR-24）：消息终稿 → `POST /api/tts` → MP3 → 播放。
 *
 * 为什么独立通道而不是骑在面试 SSE 流上（P2-M5 决策①）：播报失败只影响播报，
 * 不污染面试流、不拖长它的生命周期，报告页/回放页将来也能复用。
 *
 * 降级链（决策④）：后端 edge-tts → 浏览器 speechSynthesis → 纯文字
 * （两档都失败才提示一次，不打断面试；面试官文字照常显示）。
 *
 * 播报链路（P2-M13 优化）：
 * - 票据 + `<audio>` 流式播放，去掉「整段 MP3 下载完再播」的等待；
 * - **首句提前合成**：LLM 还在流式输出时，首句一到标点就开始 TTS，用剩余生成时间
 *   掩盖 edge-tts 的首包耗时；剩余文本在 final 时合成并按序接上；
 * - 当前句播出时预取下一段，避免句间等待。
 */

import { authorizedFetch, responseError } from "@/lib/http";

/** 与后端 tools/tts.MAX_TEXT 对齐（超长后端截断，前端不必重复截）。 */
const MAX_TEXT = 1000;

/** 句子结束符：中文/英文句末 + 分号 + 换行；冒号也切开（面试官常「过渡语：问题」）。 */
const SENTENCE_END = "。！？!?；;\n：:";
const CLOSERS = "”’」』）)]】";

/**
 * 从流式缓冲区切出首句；剩余部分留给 final。短于 minChars 的碎片并到下一句，
 * 避免为「好。」单独发一次 TTS。没有完整句子返回 null。
 */
export function takeFirstSentence(
  buffer: string,
  minChars = 4,
): { sentence: string; rest: string } | null {
  for (let index = 0; index < buffer.length; index += 1) {
    if (!SENTENCE_END.includes(buffer[index])) continue;
    let end = index + 1;
    while (end < buffer.length && CLOSERS.includes(buffer[end])) end += 1;
    const candidate = buffer.slice(0, end).trim();
    const content = candidate.replace(/[\s，。！？!?；;：:,.!?;:]/g, "");
    if (content.length >= minChars) {
      return { sentence: candidate, rest: buffer.slice(end) };
    }
    index = end - 1;
  }
  return null;
}

/**
 * 浏览器音色选择：优先 zh-CN，其次任意 zh；没有中文音色返回 null
 * （交给系统默认音色，仍按 zh-CN 朗读）。
 */
export function chooseChineseVoice<T extends { lang: string }>(
  voices: readonly T[],
): T | null {
  const lang = (voice: T) => voice.lang.toLowerCase().replace("_", "-");
  return (
    voices.find((voice) => lang(voice) === "zh-cn") ??
    voices.find((voice) => lang(voice).startsWith("zh")) ??
    null
  );
}

type SpeechSegment = { text: string; audio: HTMLAudioElement | null };

export class Speaker {
  private current: SpeechSegment | null = null;
  private queue: { text: string }[] = [];
  private prepared: { text: string; audio: HTMLAudioElement } | null = null;
  private generation = 0;
  private preparing = false;
  private streamActive = false;
  private firstStarted = false;
  private streamBuffer = "";
  private browserUtterance: SpeechSynthesisUtterance | null = null;
  private browserSpeaking = false;

  /**
   * `onSpeakingChange` 供面试间舞台用（P2-M7：面试官 tile 的「正在播报」徽标）——
   * 只在**真的起了变化**时回调（空停不算），避免徽标无谓闪烁。
   */
  constructor(
    private readonly onError?: (message: string) => void,
    private readonly onSpeakingChange?: (speaking: boolean) => void,
    /** edge-tts 失败、浏览器系统音接上时提示一次（系统音音色差，不能静默切换） */
    private readonly onFallback?: () => void,
  ) {}

  /** 正在播报？（页面据此决定是否让位：开始录音时先停播） */
  get speaking(): boolean {
    return this.current !== null || this.browserSpeaking;
  }

  /** 一次性播报整段（切换语音模式时播当前题）：首句立即合成，剩余接上。 */
  speak(text: string): void {
    const payload = text.trim().slice(0, MAX_TEXT);
    this.begin();
    if (!payload) return;
    this.push(payload);
    this.finish(payload);
  }

  /** 一条面试官消息的流式起点：停掉上一段，准备接分句。 */
  begin(): void {
    this.stop();
    this.streamActive = true;
    this.firstStarted = false;
    this.streamBuffer = "";
  }

  /** 流式分片：首句一到标点就入队（开始 TTS），其余继续攒。 */
  push(text: string): void {
    if (!this.streamActive) return;
    this.streamBuffer += text;
    if (this.firstStarted) return;
    const first = takeFirstSentence(this.streamBuffer);
    if (!first) return;
    this.firstStarted = true;
    this.streamBuffer = first.rest;
    this.enqueue(first.sentence);
  }

  /** 终稿：把剩余文本合成并接在首句后；没有流式起点则按一次性播报兜底。 */
  finish(text: string): void {
    if (!this.streamActive) {
      this.speak(text);
      return;
    }
    const tail = this.streamBuffer.trim() || (!this.firstStarted ? text.trim() : "");
    this.streamBuffer = "";
    this.streamActive = false;
    if (tail) this.enqueue(tail);
    this.pump();
  }

  stop(): void {
    this.generation += 1;
    this.streamActive = false;
    this.firstStarted = false;
    this.streamBuffer = "";
    this.queue = [];
    this.preparing = false;
    if (this.prepared) {
      this.disposeAudio(this.prepared.audio);
      this.prepared = null;
    }
    if (this.current) {
      const item = this.current;
      this.current = null;
      if (item.audio) this.disposeAudio(item.audio);
    }
    if (this.browserSpeaking || this.browserUtterance) {
      this.browserSpeaking = false;
      this.browserUtterance = null;
      if (typeof window !== "undefined") window.speechSynthesis?.cancel();
    }
    this.onSpeakingChange?.(false);
  }

  private enqueue(text: string): void {
    const clean = text.trim();
    if (!clean) return;
    this.queue.push({ text: clean });
    if (this.current) {
      void this.prepareNext();
      return;
    }
    this.pump();
  }

  private pump(): void {
    if (this.current) return;
    if (this.prepared) {
      const prepared = this.prepared;
      this.prepared = null;
      if (this.queue[0]?.text === prepared.text) {
        this.queue.shift();
        this.playAudio(prepared.text, prepared.audio);
      } else {
        this.disposeAudio(prepared.audio);
        this.pump();
      }
      return;
    }
    const next = this.queue.shift();
    if (!next) {
      this.onSpeakingChange?.(false);
      return;
    }
    void this.startSegment(next.text);
  }

  private async startSegment(text: string): Promise<void> {
    const generation = this.generation;
    /* 先占住 current：createAudio 是异步的。若此时 finish/enqueue 再触发一次 pump，
       第二个 segment 会并发启动，随后被 current 判定丢弃 —— 用户只听到第一句
       （2026-10-21 实测：点“语音播报”后只有「欢迎回来…」）。 */
    const pending: SpeechSegment = { text, audio: null };
    this.current = pending;
    const audio = await this.createAudio(text);
    if (generation !== this.generation || this.current !== pending) {
      if (audio) this.disposeAudio(audio);
      return;
    }
    this.current = null;
    if (!audio) {
      await this.fallbackBrowser(text, generation);
      return;
    }
    this.playAudio(text, audio);
  }

  private playAudio(text: string, audio: HTMLAudioElement, retried = false): void {
    const item: SpeechSegment = { text, audio };
    this.current = item;
    let watchdog: ReturnType<typeof setTimeout> | null = null;
    const clearWatchdog = () => {
      if (watchdog) clearTimeout(watchdog);
      watchdog = null;
    };
    const advance = () => {
      clearWatchdog();
      if (this.current !== item) return;
      this.finishCurrent(item);
      this.pump();
    };
    audio.onended = advance;
    audio.onerror = () => {
      clearWatchdog();
      if (this.current !== item) return;
      this.finishCurrent(item);
      void this.recoverSegment(text, retried);
    };
    /* 流式 MP3 没有 Content-Length，个别浏览器不派发 ended：按文本估时兜底推进，
       避免“第一句播完后再也没有下一句”。 */
    const armWatchdog = () => {
      clearWatchdog();
      const seconds =
        Number.isFinite(audio.duration) && audio.duration > 0
          ? audio.duration
          : Math.max(4, text.length * 0.35);
      watchdog = setTimeout(
        () => {
          if (this.current !== item) return;
          if (audio.ended || audio.paused) advance();
          else armWatchdog();
        },
        Math.max(4000, seconds * 1000 + 4000),
      );
    };
    audio.onloadedmetadata = armWatchdog;
    if (audio.readyState >= 1) armWatchdog();
    void audio
      .play()
      .then(() => {
        if (this.current !== item) return;
        this.onSpeakingChange?.(true);
        void this.prepareNext();
      })
      .catch(() => {
        clearWatchdog();
        if (this.current !== item) return;
        this.finishCurrent(item);
        void this.recoverSegment(text, retried);
      });
  }

  /** 播放失败先换一张新票据重试一次（API 重启 / 票据过期 / worker 落错），再降级系统音。 */
  private async recoverSegment(text: string, retried: boolean): Promise<void> {
    const generation = this.generation;
    if (!retried) {
      const audio = await this.createAudio(text);
      if (generation === this.generation && !this.current && audio) {
        this.playAudio(text, audio, true);
        return;
      }
      if (audio) this.disposeAudio(audio);
    }
    await this.fallbackBrowser(text, generation);
  }

  private async fallbackBrowser(text: string, generation: number): Promise<void> {
    if (generation !== this.generation || this.current) return;
    const item: SpeechSegment = { text, audio: null };
    this.current = item;
    void this.prepareNext();
    const spoken = await this.speakWithBrowser(text);
    if (generation !== this.generation) return;
    if (this.current === item) this.current = null;
    if (!spoken) this.onError?.("语音播报失败");
    else this.onFallback?.();
    this.pump();
  }

  private finishCurrent(item: SpeechSegment): void {
    if (this.current !== item) return;
    this.current = null;
    if (item.audio) this.disposeAudio(item.audio);
  }

  /** 当前段播放时预取下一段的音频（票据 + 流式 URL），避免句间等首包。 */
  private async prepareNext(): Promise<void> {
    if (this.preparing || this.prepared || !this.current || this.queue.length === 0) {
      return;
    }
    const segment = this.queue[0];
    const generation = this.generation;
    this.preparing = true;
    const audio = await this.createAudio(segment.text);
    this.preparing = false;
    if (
      !audio ||
      generation !== this.generation ||
      !this.current ||
      this.queue[0] !== segment
    ) {
      if (audio) this.disposeAudio(audio);
      return;
    }
    this.prepared = { text: segment.text, audio };
  }

  private async createAudio(text: string): Promise<HTMLAudioElement | null> {
    try {
      const response = await authorizedFetch("/api/tts/ticket", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const data = (await response.json()) as { ticket?: string };
      if (!data.ticket) throw new Error("语音播报链接生成失败");
      const audio = new Audio(`/api/tts/stream/${data.ticket}`);
      audio.preload = "auto";
      audio.load();
      return audio;
    } catch {
      return null;
    }
  }

  private disposeAudio(audio: HTMLAudioElement): void {
    audio.onended = null;
    audio.onerror = null;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  }

  /** 第二档：浏览器 speechSynthesis。成功（含用户主动打断）返回 true，真正失败才 false。 */
  private async speakWithBrowser(text: string): Promise<boolean> {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      return false;
    }
    const synth = window.speechSynthesis;
    if (!synth) return false;
    const voices = await this.loadVoices(synth);
    const voice = chooseChineseVoice(voices);
    const utterance = new SpeechSynthesisUtterance(text);
    if (voice) {
      utterance.voice = voice;
      utterance.lang = voice.lang;
    } else {
      utterance.lang = "zh-CN";
    }
    utterance.rate = 1;

    return new Promise<boolean>((resolve) => {
      let settled = false;
      let timer: ReturnType<typeof setTimeout> | null = null;
      const finish = (ok: boolean) => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        this.browserUtterance = null;
        this.browserSpeaking = false;
        this.onSpeakingChange?.(false);
        resolve(ok);
      };
      utterance.onend = () => finish(true);
      utterance.onerror = (event) => {
        // 用户主动 stop() 会触发 interrupted/canceled：不算失败，不弹「不可用」
        finish(event.error !== "interrupted" && event.error !== "canceled");
      };
      this.browserUtterance = utterance;
      this.browserSpeaking = true;
      this.onSpeakingChange?.(true);
      // 兜底：某些浏览器不派发 onend/onerror，按字数估时避免 Promise 永久挂起
      timer = setTimeout(
        () => finish(true),
        Math.min(60000, Math.max(5000, text.length * 180)),
      );
      try {
        synth.cancel();
        synth.speak(utterance);
      } catch {
        finish(false);
      }
    });
  }

  /** Chrome 首次 getVoices() 常为空：等一次 voiceschanged（最多 800ms）。 */
  private loadVoices(synth: SpeechSynthesis): Promise<SpeechSynthesisVoice[]> {
    const current = synth.getVoices();
    if (current.length > 0) return Promise.resolve(current);
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve(synth.getVoices()), 800);
      synth.addEventListener(
        "voiceschanged",
        () => {
          clearTimeout(timer);
          resolve(synth.getVoices());
        },
        { once: true },
      );
    });
  }
}
