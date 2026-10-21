/**
 * ASR 表达复盘纯逻辑（M16）：录音采样 → 原始计数。
 *
 * 前端**只产原始计数**（时长 / 字数 / 长停顿 / 口头禅），语速与汇总由后端派生
 * （`graph/rules/speech.py` 是唯一来源）——同一批数字不在两端各算一遍。
 *
 * 所有浏览器 API 留在 asr-client/组件；这里只做数学，便于 vitest 钉死。
 */

export type SpeechCounters = {
  /** 本段录音时长（ms，stop 时冻结） */
  duration_ms: number;
  /** ASR final 转写里的字母/数字数（不含空格与标点） */
  chars: number;
  /** ≥2s 的内部静音段数 */
  pause_count: number;
  /** 这些长停顿的总时长（ms） */
  pause_ms: number;
  /** 转写中检出的口头禅次数（ASR 可能漏掉语气词） */
  filler_count: number;
  /** 本次草稿里录过的段数 */
  segments: number;
};

export type SpeechMetrics = SpeechCounters & { chars_per_min: number | null };

export type ExpressionSummary = {
  spoken_question_count: number;
  duration_ms: number;
  chars: number;
  chars_per_min: number | null;
  pause_count: number;
  pause_ms: number;
  filler_count: number;
  segments: number;
  note: string;
};

type Sample = { t: number; rms: number };

type Finalized = Omit<SpeechCounters, "segments">;

/** 长停顿阈值：≥ 2 秒（PRD/计划口径）。 */
export const LONG_PAUSE_MS = 2000;

/** 少于这么多采样点（约 1 秒）不判停顿——样本太短，百分位阈值不可靠。 */
export const MIN_SAMPLES = 20;

/** 口头禅（计划口径）：嗯/呃/额/那个/就是/然后。ASR 可能吞掉语气词，只作近似。 */
const FILLER_RE = /(嗯|呃|额|那个|就是|然后)/g;

/** 字数：Unicode 字母/数字；中文标点、空格、英文标点都不算。 */
const SPOKEN_RE = /[\p{L}\p{N}]/gu;

export function spokenCharCount(text: string): number {
  return (text.match(SPOKEN_RE) ?? []).length;
}

export function fillerCount(text: string): number {
  return (text.match(FILLER_RE) ?? []).length;
}

/**
 * 静音阈值：取本段 RMS 的 p10 × 2.5，夹在 [0.008, 0.04]。
 *
 * 固定阈值在不同麦克风/房间下会一半误判；用本段自己的低分位估环境噪声，
 * 对「安静房间小声说话」和「嘈杂环境」都更稳。仍是估算，报告里如实标注。
 */
export function silenceThreshold(rmsValues: readonly number[]): number {
  if (rmsValues.length === 0) return 0.012;
  const sorted = [...rmsValues].sort((a, b) => a - b);
  const p10 = sorted[Math.floor(sorted.length * 0.1)];
  return Math.min(0.04, Math.max(0.008, p10 * 2.5));
}

/**
 * 内部长停顿：去掉开头/结尾静音（按键延迟不是思考停顿），只数中间 ≥2s 的静音段。
 * 采样点带时间戳——后台标签页节流时样本间隔变大，按样本数算会把时长算错。
 */
export function internalPauses(
  samples: readonly Sample[],
  threshold: number,
): { count: number; ms: number } {
  if (samples.length < MIN_SAMPLES) return { count: 0, ms: 0 };

  let firstVoiced = -1;
  let lastVoiced = -1;
  for (let index = 0; index < samples.length; index += 1) {
    if (samples[index].rms >= threshold) {
      if (firstVoiced < 0) firstVoiced = index;
      lastVoiced = index;
    }
  }
  if (firstVoiced < 0 || lastVoiced <= firstVoiced) return { count: 0, ms: 0 };

  let count = 0;
  let ms = 0;
  let runStart = -1;
  for (let index = firstVoiced; index <= lastVoiced; index += 1) {
    if (samples[index].rms < threshold) {
      if (runStart < 0) runStart = index;
    } else if (runStart >= 0) {
      const duration = samples[index].t - samples[runStart].t;
      if (duration >= LONG_PAUSE_MS) {
        count += 1;
        ms += duration;
      }
      runStart = -1;
    }
  }
  return { count, ms };
}

/**
 * 一次草稿内的语音指标累加器。
 *
 * 生命周期：begin → sample* → stop（冻结时长）→ finalize(转写)（等 final 可能 8s，
 * 不能把等待算进时长）→ snapshot 给发送用 / reset 清账。
 * 智能校对真正替换草稿后调 updateLastTranscript 重算字数与口头禅。
 */
export class SpeechAccumulator {
  private startedAt: number | null = null;
  private stoppedAt: number | null = null;
  private samples: Sample[] = [];
  private finalized: Finalized[] = [];

  begin(now: number = performance.now()): void {
    this.startedAt = now;
    this.stoppedAt = null;
    this.samples = [];
  }

  sample(rms: number, now: number = performance.now()): void {
    if (this.startedAt === null || this.stoppedAt !== null) return;
    this.samples.push({ t: now, rms });
  }

  stop(now: number = performance.now()): void {
    if (this.startedAt === null || this.stoppedAt !== null) return;
    this.stoppedAt = now;
  }

  /** 放弃当前段（错误/空转写/离开页面）。 */
  cancel(): void {
    this.startedAt = null;
    this.stoppedAt = null;
    this.samples = [];
  }

  /** 收下一段：转写非空才计入；时长用 stop 冻结的值。 */
  finalize(transcript: string): void {
    const text = transcript.trim();
    if (this.startedAt === null || this.stoppedAt === null || !text) {
      this.cancel();
      return;
    }
    const pauses = internalPauses(
      this.samples,
      silenceThreshold(this.samples.map((sample) => sample.rms)),
    );
    this.finalized.push({
      duration_ms: Math.max(0, Math.round(this.stoppedAt - this.startedAt)),
      chars: spokenCharCount(text),
      pause_count: pauses.count,
      // 后端 SpeechStats 是整数（Pydantic int 会拒绝 3000.6 这种小数）：
      // 采样时间戳是浮点，停顿时长必须在这里取整，否则整条消息 422
      pause_ms: Math.round(pauses.ms),
      filler_count: fillerCount(text),
    });
    this.cancel();
  }

  /** 智能校对替换了最后一段转写：字数/口头禅跟着改（时长与停顿不变）。 */
  updateLastTranscript(transcript: string): void {
    const last = this.finalized[this.finalized.length - 1];
    const text = transcript.trim();
    if (!last || !text) return;
    last.chars = spokenCharCount(text);
    last.filler_count = fillerCount(text);
  }

  snapshot(): SpeechCounters | null {
    if (this.finalized.length === 0) return null;
    const total = this.finalized.reduce<SpeechCounters>(
      (acc, item) => ({
        duration_ms: acc.duration_ms + item.duration_ms,
        chars: acc.chars + item.chars,
        pause_count: acc.pause_count + item.pause_count,
        pause_ms: acc.pause_ms + item.pause_ms,
        filler_count: acc.filler_count + item.filler_count,
        segments: acc.segments + 1,
      }),
      { duration_ms: 0, chars: 0, pause_count: 0, pause_ms: 0, filler_count: 0, segments: 0 },
    );
    return total;
  }

  reset(): void {
    this.finalized = [];
    this.cancel();
  }
}

/** 时长文案：`2 分 10 秒` / `35 秒`。 */
export function formatSpeechDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes > 0 ? `${minutes} 分 ${seconds} 秒` : `${seconds} 秒`;
}

/** 语速文案：`186 字/分`；无有效时长时 `—`。 */
export function formatSpeechRate(charsPerMin: number | null | undefined): string {
  return typeof charsPerMin === "number" && Number.isFinite(charsPerMin)
    ? `${Math.round(charsPerMin)} 字/分`
    : "—";
}
