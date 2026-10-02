/**
 * 语音通道纯逻辑（P2-M5 FR-24）：音频数学、连接地址、转写落框、降级文案。
 *
 * 页面里的分支进不了 vitest（P2-M3 的教训），凡是能做成纯函数的一律放这里：
 * 浏览器 API（getUserMedia / AudioWorklet / WebSocket）留在 lib/asr-client.ts 与组件里，
 * 「算什么、连哪里、填什么、说什么」这四件事都在本文件。
 */

/** 上游要的音频参数（SPEC §7 语音契约）：PCM 16k / 16bit / 单声道。 */
export const TARGET_SAMPLE_RATE = 16000;

/** 每片音频时长（ms）——100ms 与后端 FRAME_BYTES=3200 对齐（16k×2B×0.1s）。 */
export const CHUNK_MS = 100;

/**
 * 任意采样率 → 16k 单声道（均值降采样：先按整数倍分组取均值，余数丢弃）。
 *
 * 不做插值/重采样滤波器：语音识别对这点高频损失不敏感，而均值法**零依赖、可单测**。
 * 浏览器 AudioContext 常见 48k/44.1k，都落在这里。
 */
export function downsampleTo16k(input: Float32Array, sampleRate: number): Float32Array {
  if (sampleRate <= TARGET_SAMPLE_RATE) return input.slice();
  const ratio = sampleRate / TARGET_SAMPLE_RATE;
  const outLength = Math.floor(input.length / ratio);
  const out = new Float32Array(outLength);
  for (let i = 0; i < outLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += input[j];
    out[i] = end > start ? sum / (end - start) : 0;
  }
  return out;
}

/** Float32 [-1,1] → Int16 小端 PCM（先夹紧再缩放：超范围样本不环绕成噪声）。 */
export function floatToPcm16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, input[i]));
    out[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return out;
}

/** Int16Array → 小端字节（WebSocket 发二进制帧用）。 */
export function pcmBytes(samples: Int16Array): Uint8Array {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < samples.length; i += 1) view.setInt16(i * 2, samples[i], true);
  return bytes;
}

/** 每片样本数（CHUNK_MS 对应 16k 采样）。 */
export const CHUNK_SAMPLES = (TARGET_SAMPLE_RATE * CHUNK_MS) / 1000;

/**
 * ASR WebSocket 地址。
 *
 * 浏览器 WebSocket 不能自定义请求头 → token 只能进 query（SPEC §11 记了 Log 暴露的代价）。
 * 生产/容器：同源 `/api/asr`（nginx 转发带 Upgrade 头）；`next dev` 的 rewrites **不代理
 * WS upgrade**（已查实），故 3000 端口一律直连后端 8000——与 next.config.ts 的
 * BACKEND_ORIGIN 默认值同一个约定。
 */
export function asrUrl(
  location: { protocol: string; hostname: string; port: string; host?: string },
  token: string,
): string {
  const secure = location.protocol === "https:";
  const scheme = secure ? "wss" : "ws";
  const dev = location.port === "3000"; // next dev
  const host = dev ? `${location.hostname}:8000` : location.host ?? `${location.hostname}`;
  return `${scheme}://${host}/api/asr?token=${encodeURIComponent(token)}`;
}

/**
 * 转写文本落到输入框：**录音开始时已存在的草稿是底稿，转写整体替换它之后的那一段**。
 *
 * 上游的 partial 是「这段音频到目前为止」的累计文本，直接追加会把每片都叠上去；
 * 底稿与转写之间留一个换行（用户手打的文字与说的话是两段，不该粘成一行）。
 */
export function mergeTranscript(base: string, transcript: string): string {
  const spoken = transcript.trim();
  if (!spoken) return base;
  const head = base.replace(/\s+$/, "");
  return head ? `${head}\n${spoken}` : spoken;
}

/** 录音按钮的可用状态（组件据此渲染；纯函数便于钉死优先级）。 */
export type VoiceUiState = {
  recording: boolean;
  connecting: boolean;
  supported: boolean;
  busy: boolean;
};

/** 录音键是否可点：面试官说话时（busy）不给开麦；不支持/连接中不给点。 */
export function canStartRecording(state: VoiceUiState): boolean {
  return state.supported && !state.recording && !state.connecting && !state.busy;
}

/** 发送/结束面试是否该被录音挡住：录着音就发，会把「还没说完」的半截话发出去。 */
export function blocksSubmit(recording: boolean): boolean {
  return recording;
}

/**
 * 录音键的禁用判据——**「能不能开始」与「能不能结束」是两条规则**，别用同一个函数的反。
 *
 * 浏览器验收抓到的真 bug（2026-10-02）：按钮 disabled 写成了 `!canStartRecording(...)`，
 * 而它在录音中恒为 false → 反过来说录音中**恒禁用** → 用户按不停，录音永远收不了尾。
 */
export function micDisabled(state: VoiceUiState & { finished: boolean }): boolean {
  if (state.finished) return true;
  if (state.recording) return false; // 录音中永远可点（这就是「结束录音」）
  return !canStartRecording(state);
}

/** 语音不可用时的落点文案（降级链：不给用户死胡同，明说改走文字）。 */
export function micErrorMessage(reason: unknown): string {
  const name = reason instanceof DOMException ? reason.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "未获得麦克风权限，已切回文字作答（可在浏览器地址栏重新允许）。";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return "没有找到可用的麦克风，请用文字作答。";
  }
  if (reason instanceof Error && reason.message) return reason.message;
  return "语音连接失败，请用文字作答。";
}

/** 录音结束但一个字都没转出来（环境太吵 / 没说话）时的提示。 */
export const EMPTY_TRANSCRIPT_HINT = "没有听清，请再说一次，或直接用文字作答。";

/** 语音模式开关的持久化键（localStorage；不可用时静默降级为「本次有效」）。 */
export const VOICE_MODE_KEY = "marda.voiceMode";
