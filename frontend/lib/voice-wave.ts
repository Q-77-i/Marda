/**
 * 候选人语音动效纯逻辑（P2-M13）：振幅计算、柱条映射、平滑、状态与文案。
 *
 * 浏览器 API（AnalyserNode / rAF）留在 `lib/asr-client.ts` 与组件里；
 * 这里只放能进 vitest 的数学与分支——「柱条多高、什么时候显示、按钮写什么」。
 */

export type VoiceWaveState = "idle" | "connecting" | "recording" | "transcribing";

/** 5 根柱：中间高、两侧低，和面试官播报波形的视觉语言一致但颜色不同（候选人是主蓝）。 */
export const WAVE_BAR_COUNT = 5;

/** 静音时柱条仍保留约三分之一高度：完全压平会让人以为组件没渲染。 */
export const WAVE_MIN_SCALE = 0.32;

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/** Waveform RMS（0..1）：空缓冲返回 0，不产生 NaN。 */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let index = 0; index < samples.length; index += 1) {
    sum += samples[index] * samples[index];
  }
  return Math.sqrt(sum / samples.length);
}

/** 对称权重：中间 1、向两侧线性降到 0.55（柱条看起来像一段波，而不是等高均衡器）。 */
function weightAt(index: number, count: number): number {
  if (count <= 1) return 1;
  const center = (count - 1) / 2;
  const distance = Math.abs(index - center) / center;
  return 1 - 0.45 * distance;
}

/** 振幅 → 每根柱的 scaleY（下限 WAVE_MIN_SCALE，上限 1）。 */
export function levelToBars(level: number, count: number = WAVE_BAR_COUNT): number[] {
  const safe = clamp01(level);
  const bars: number[] = [];
  for (let index = 0; index < count; index += 1) {
    const weight = weightAt(index, count);
    bars.push(Math.min(1, WAVE_MIN_SCALE + (1 - WAVE_MIN_SCALE) * safe * weight));
  }
  return bars;
}

/** 指数平滑：麦克风振幅逐帧跳变，直接渲染会抖成噪声。 */
export function smoothLevel(previous: number, next: number, factor = 0.35): number {
  const start = Number.isFinite(previous) ? clamp01(previous) : clamp01(next);
  const target = Number.isFinite(next) ? clamp01(next) : start;
  const alpha = Number.isFinite(factor) ? Math.min(1, Math.max(0, factor)) : 0.35;
  return start + (target - start) * alpha;
}

/**
 * 原始 RMS → 可视电平（0..1）：噪声地板 + 平方根压缩。
 *
 * 真实说话的 RMS 常在 0.03–0.2，线性映射到柱高几乎看不出变化（实测假麦克风
 * 只从 0.18 动到 0.188）；平方根把低电平抬起来，正常说话就能撑起大半柱。
 */
export function voiceLevel(value: number): number {
  const safe = clamp01(value);
  if (safe < 0.01) return 0;
  return Math.min(1, Math.sqrt(safe) * 2.0);
}

/**
 * 四态优先级：录音 > 整理 > 连接 > 空闲。
 *
 * 「整理」是 M13 补的显式状态：`stop()` 之后还要等上游 final，旧实现把这段时间
 * 混在 connecting 里、按钮却仍写「结束录音」，用户会以为按不停。
 */
export function voiceWaveState(state: {
  recording: boolean;
  connecting: boolean;
  transcribing: boolean;
}): VoiceWaveState {
  if (state.recording) return "recording";
  if (state.transcribing) return "transcribing";
  if (state.connecting) return "connecting";
  return "idle";
}

export function micLabel(state: VoiceWaveState): string {
  if (state === "recording") return "结束录音";
  if (state === "transcribing") return "整理中…";
  if (state === "connecting") return "连接中…";
  return "语音作答";
}

/** 读屏状态文本（柱条本身 aria-hidden，不能只靠视觉传达）。 */
export function voiceStatusText(state: VoiceWaveState): string {
  if (state === "recording") return "正在录音";
  if (state === "transcribing") return "正在整理转写";
  if (state === "connecting") return "正在连接语音";
  return "";
}
