/**
 * 面试官语音播报（P2-M5 FR-24）：消息终稿 → `POST /api/tts` → MP3 → 播放。
 *
 * 为什么独立通道而不是骑在面试 SSE 流上（P2-M5 决策①）：播报失败只影响播报，
 * 不污染面试流、不拖长它的生命周期，报告页/回放页将来也能复用。
 *
 * 降级链（决策④）：后端 edge-tts → 浏览器 speechSynthesis → 纯文字
 * （两档都失败才提示一次，不打断面试；面试官文字照常显示）。
 *
 * 播报链路（P2-M13 优化）：POST 票据 → `<audio src=…/tts/stream/{ticket}>` 边收边播，
 * 不再等整段 MP3 下载完；首块到达即出声（edge-tts 首块约 2s）。
 */

import { authorizedFetch, responseError } from "@/lib/http";

/** 与后端 tools/tts.MAX_TEXT 对齐（超长后端截断，前端不必重复截）。 */
const MAX_TEXT = 1000;

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

export class Speaker {
  private audio: HTMLAudioElement | null = null;
  private controller: AbortController | null = null;
  private browserUtterance: SpeechSynthesisUtterance | null = null;
  private browserSpeaking = false;

  /**
   * `onSpeakingChange` 供面试间舞台用（P2-M7：面试官 tile 的「正在播报」徽标）——
   * 只在**真的起了变化**时回调（空停不算），避免徽标无谓闪烁。
   */
  constructor(
    private readonly onError?: (message: string) => void,
    private readonly onSpeakingChange?: (speaking: boolean) => void,
  ) {}

  /** 正在播报？（页面据此决定是否让位：开始录音时先停播） */
  get speaking(): boolean {
    return (
      (this.audio !== null && !this.audio.paused && !this.audio.ended) ||
      this.browserSpeaking
    );
  }

  /** 播一段文本；已在播的先停（同一时刻只播一条，面试官不会两个人同时说话）。 */
  async speak(text: string): Promise<void> {
    const payload = text.trim().slice(0, MAX_TEXT);
    this.stop();
    if (!payload) return;
    const controller = new AbortController();
    this.controller = controller;
    try {
      const response = await authorizedFetch("/api/tts/ticket", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: payload }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const data = (await response.json()) as { ticket?: string };
      if (!data.ticket) throw new Error("语音播报链接生成失败");
      const audio = new Audio(`/api/tts/stream/${data.ticket}`);
      this.audio = audio;
      audio.onended = () => this.cleanup();
      audio.onerror = () => {
        if (this.audio !== audio) return; // 已被 stop / 新播报替换
        this.cleanup();
        void this.speakWithBrowser(payload).then((spoken) => {
          if (!spoken) this.onError?.("语音播报失败");
        });
      };
      await audio.play();
      this.onSpeakingChange?.(true);
    } catch (err) {
      this.cleanup();
      if (err instanceof DOMException && err.name === "AbortError") return; // 主动停播不是错误
      // 第一档失败 → 第二档：浏览器内置 speechSynthesis（M5 三档降级的本意）
      const spoken = await this.speakWithBrowser(payload);
      if (!spoken) {
        this.onError?.(err instanceof Error ? err.message : "语音播报失败");
      }
    }
  }

  stop(): void {
    this.controller?.abort();
    this.controller = null;
    this.cleanup();
    if (this.browserSpeaking || this.browserUtterance) {
      this.browserSpeaking = false;
      this.browserUtterance = null;
      if (typeof window !== "undefined") window.speechSynthesis?.cancel();
      this.onSpeakingChange?.(false);
    }
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
      timer = setTimeout(() => finish(true), Math.min(60000, Math.max(5000, text.length * 180)));
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

  private cleanup(): void {
    const wasActive = this.audio !== null;
    if (this.audio) {
      const audio = this.audio;
      // 先摘掉回调再清 src：removeAttribute/load 会触发 error，不能误走降级
      this.audio = null;
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
      audio.load(); // 中止正在进行的流式请求
    }
    if (wasActive) this.onSpeakingChange?.(false);
  }
}
