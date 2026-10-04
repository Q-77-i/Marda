/**
 * 面试官语音播报（P2-M5 FR-24）：消息终稿 → `POST /api/tts` → MP3 → 播放。
 *
 * 为什么独立通道而不是骑在面试 SSE 流上（P2-M5 决策①）：播报失败只影响播报，
 * 不污染面试流、不拖长它的生命周期，报告页/回放页将来也能复用。
 *
 * 降级链（决策④）：后端 edge-tts 不可用 → 静默跳过（只提示一次，不打断面试）——
 * 面试官的文字照常显示，用户仍可正常作答；浏览器内置 speechSynthesis 作为第二档
 * 由页面决定是否启用（见 interview-client 的 speakFallback）。
 */

import { authorizedFetch, responseError } from "@/lib/http";

/** 与后端 tools/tts.MAX_TEXT 对齐（超长后端截断，前端不必重复截）。 */
const MAX_TEXT = 1000;

export class Speaker {
  private audio: HTMLAudioElement | null = null;
  private objectUrl: string | null = null;
  private controller: AbortController | null = null;

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
    return this.audio !== null && !this.audio.paused && !this.audio.ended;
  }

  /** 播一段文本；已在播的先停（同一时刻只播一条，面试官不会两个人同时说话）。 */
  async speak(text: string): Promise<void> {
    const payload = text.trim().slice(0, MAX_TEXT);
    this.stop();
    if (!payload) return;
    const controller = new AbortController();
    this.controller = controller;
    try {
      const response = await authorizedFetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: payload }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const blob = await response.blob();
      if (blob.size === 0) throw new Error("语音合成没有返回音频");
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      this.objectUrl = url;
      this.audio = audio;
      audio.onended = () => this.cleanup();
      await audio.play();
      this.onSpeakingChange?.(true);
    } catch (err) {
      this.cleanup();
      if (err instanceof DOMException && err.name === "AbortError") return; // 主动停播不是错误
      this.onError?.(err instanceof Error ? err.message : "语音播报失败");
    }
  }

  stop(): void {
    this.controller?.abort();
    this.controller = null;
    this.audio?.pause();
    this.cleanup();
  }

  private cleanup(): void {
    const wasActive = this.audio !== null; // 空停（本来就没在播）不算变化，不回调
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
    this.audio = null;
    if (wasActive) this.onSpeakingChange?.(false);
  }
}
