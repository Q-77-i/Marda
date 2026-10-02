/**
 * 浏览器侧录音 → ASR 客户端（P2-M5 FR-24）。
 *
 * 链路：getUserMedia → AudioContext + AudioWorklet（public/asr-worklet.js）→ 降采样 16k →
 * 每 100ms 一片二进制帧 → `WS /api/asr?token=…`；上行还有一条 `{"type":"stop"}` 收尾消息，
 * 下行是 partial / final / error 三种 JSON。
 *
 * 纯逻辑（音频数学、地址、落框规则）在 lib/voice.ts；这里只管浏览器 API 的接线。
 * **不落任何音频**：帧发出去就丢，页面关掉即无痕（P2 红线）。
 */

import { UnauthorizedError, getToken, notifyUnauthorized } from "@/lib/session";
import {
  CHUNK_SAMPLES,
  TARGET_SAMPLE_RATE,
  asrUrl,
  downsampleTo16k,
  floatToPcm16,
  pcmBytes,
} from "@/lib/voice";

/** 停止后等 final 的上限（与后端 finish 超时同量级）：超时用最后一段 partial 收尾。 */
const FINAL_TIMEOUT_MS = 8000;

export type AsrHandlers = {
  /** 累计转写（上游给的是「整段音频到目前为止」，页面按替换落框）。 */
  onPartial: (text: string) => void;
  onFinal: (text: string) => void;
  onError: (message: string) => void;
};

export class AsrRecorder {
  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private pending: Uint8Array[] = []; // WS 建连期间攒下的片（连上就补发，不丢开头）
  private tail: number[] = []; // 不足一片的余量样本（跨 buffer 续接）
  private lastPartial = "";
  private settled = false;
  private opened = false;
  private finalTimer: ReturnType<typeof setTimeout> | null = null;
  private settledResolve: (() => void) | null = null;
  /** stop() 等它：settle（final / error / 超时兜底）发生后收摊完毕才返回。 */
  private readonly settledPromise = new Promise<void>((resolve) => {
    this.settledResolve = resolve;
  });

  constructor(private readonly handlers: AsrHandlers) {}

  /** 开麦 + 建连。任何一步失败都抛错（页面按 micErrorMessage 转文案）。 */
  async start(): Promise<void> {
    const token = getToken();
    if (!token) throw new UnauthorizedError();
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    await this.openSocket(token);
    await this.openCapture();
  }

  private openSocket(token: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(asrUrl(window.location, token));
      ws.binaryType = "arraybuffer";
      this.ws = ws;
      ws.onopen = () => {
        this.opened = true;
        for (const chunk of this.pending) ws.send(chunk);
        this.pending = [];
        resolve();
      };
      ws.onmessage = (event) => this.onMessage(event);
      ws.onerror = () => {
        // 建连阶段的错误交给 onclose 统一处理；这里只挡「还没 resolve 就失败」的情况
        if (ws.readyState !== WebSocket.OPEN) reject(new Error("语音连接失败，请用文字作答。"));
      };
      ws.onclose = (event) => {
        if (event.code === 4401) notifyUnauthorized(); // 登录过期：走既有确认框
        // 建连就没成：start() 的 reject 已经报了，这里不再重复弹一遍
        if (this.opened) this.settle("error", "语音连接已断开，请重试或改用文字。");
      };
    });
  }

  private async openCapture(): Promise<void> {
    const context = new AudioContext();
    this.context = context;
    if (context.state === "suspended") await context.resume(); // Safari 等需要手势后显式恢复
    await context.audioWorklet.addModule("/asr-worklet.js");
    const node = new AudioWorkletNode(context, "asr-capture");
    this.node = node;
    node.port.onmessage = (event: MessageEvent<Float32Array>) => this.pushAudio(event.data);
    const source = context.createMediaStreamSource(this.stream as MediaStream);
    source.connect(node);
    // 不接 destination：避免自己的声音被回放出去（回声/啸叫）
  }

  /** 采集到的 Float32（设备采样率）→ 16k → 攒满一片就发。 */
  private pushAudio(input: Float32Array): void {
    const resampled = downsampleTo16k(input, this.context?.sampleRate ?? TARGET_SAMPLE_RATE);
    for (const sample of resampled) this.tail.push(sample);
    while (this.tail.length >= CHUNK_SAMPLES) {
      const slice = new Float32Array(this.tail.splice(0, CHUNK_SAMPLES));
      this.send(pcmBytes(floatToPcm16(slice)));
    }
  }

  private send(payload: Uint8Array): void {
    const ws = this.ws;
    if (!ws) return;
    if (ws.readyState === WebSocket.OPEN) ws.send(payload);
    else if (ws.readyState === WebSocket.CONNECTING) this.pending.push(payload);
    // CLOSING/CLOSED：丢弃（音频是过程数据，丢一片不炸整场面试）
  }

  private onMessage(event: MessageEvent): void {
    if (typeof event.data !== "string") return;
    let payload: { type?: string; text?: string; message?: string };
    try {
      payload = JSON.parse(event.data);
    } catch {
      return; // 单条解析失败不中断整段
    }
    if (payload.type === "partial") {
      this.lastPartial = payload.text ?? "";
      this.handlers.onPartial(this.lastPartial);
    } else if (payload.type === "final") {
      this.settle("final", payload.text ?? this.lastPartial);
    } else if (payload.type === "error") {
      this.settle("error", payload.message ?? "语音识别失败，请重试或改用文字。");
    }
  }

  /**
   * 结束并取最终文本：发 `stop` → **等 final 到达** → 收摊。
   *
   * 注意不能发完就收摊——上游的 final 是在收到 stop（末帧）之后才回来的，提前关 WS
   * 等于把最后一段转写扔掉。超时兜底用最后一段 partial（与后端 finish 同口径）。
   */
  async stop(): Promise<void> {
    if (this.settled) {
      await this.teardown();
      return;
    }
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "stop" }));
      this.finalTimer = setTimeout(
        () => this.settle("final", this.lastPartial),
        FINAL_TIMEOUT_MS,
      );
    } else {
      this.settle("final", this.lastPartial);
    }
    await this.settledPromise;
  }

  /** 放弃本次录音（关页面/切走）：不发停止信号，直接收摊。 */
  cancel(): void {
    if (this.settled) return;
    this.settled = true;
    if (this.finalTimer) clearTimeout(this.finalTimer);
    void this.teardown().finally(() => this.settledResolve?.());
  }

  private settle(kind: "final" | "error", text: string): void {
    if (this.settled) return;
    this.settled = true;
    if (this.finalTimer) clearTimeout(this.finalTimer);
    if (kind === "final") this.handlers.onFinal(text);
    else this.handlers.onError(text);
    void this.teardown().finally(() => this.settledResolve?.());
  }

  /** 收摊：麦克风轨道、AudioContext、WS 一个都不留（不关轨道浏览器会一直显示录音中）。 */
  private async teardown(): Promise<void> {
    this.node?.port.close();
    this.node = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    if (this.context && this.context.state !== "closed") {
      await this.context.close().catch(() => undefined);
    }
    this.context = null;
    const ws = this.ws;
    this.ws = null;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      ws.close();
    }
  }
}
