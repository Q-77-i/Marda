"use client";

import { VideoOff } from "lucide-react";
import { useEffect, useRef } from "react";
import { cn } from "cn";

import { CAMERA_CAPTION } from "@/lib/camera";

/**
 * 面试间舞台（界面重塑）：亮场默认、可切暗场。
 *
 * ≥xl：面试官卡在左、我在右，对话流夹在中间；空间语言 = 「左边的人在说，右边的人在听」，
 * 与对话记录的左右分栏是同一套语言；窄屏回落为顶部卡片（`StageCard`）。
 * 两个安置点共用同一对 tile 组件，形态只有一处定义。
 *
 * FR-27 红线不变：画面只经 `video.srcObject` 进 DOM——没有 canvas、没有抓帧、没有上传。
 */

/** 面试官 tile：monogram 人像框 + 名字 + 状态徽标 + 播报波形。 */
export function InterviewerTile({
  chip,
  speaking = false,
  className,
}: {
  chip: string;
  speaking?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "relative flex aspect-video w-full flex-col items-center justify-center gap-2 overflow-hidden rounded-lg border border-room-line bg-room-panel",
        className,
      )}
    >
      <span
        aria-hidden
        className="absolute inset-0 bg-[radial-gradient(circle_at_1px_1px,var(--room-line)_1px,transparent_0)] bg-[length:18px_18px] opacity-60"
      />
      <span
        aria-hidden
        className="relative flex size-11 items-center justify-center rounded-full border border-room-line-strong bg-room-panel-2 text-base font-semibold text-room-ink"
      >
        码
      </span>
      <div className="relative flex flex-col items-center gap-0.5 text-center">
        <span className="text-[10.5px] text-room-ink">码达面试官 / AI</span>
        <span className="font-mono text-[8px] tracking-[0.06em] text-room-muted">
          {chip}
        </span>
      </div>
      {speaking ? (
        <div className="room-wave absolute bottom-2.5 left-3" aria-hidden>
          <span />
          <span />
          <span />
          <span />
          <span />
        </div>
      ) : null}
    </div>
  );
}

/** 「我」的 tile：画面 / 同尺寸占位（关闭不是消失，入口留在自己位置上）。 */
export function CandidateTile({
  stream,
  supported,
  starting,
  onToggle,
  remembered = false,
  className,
}: {
  stream: MediaStream | null;
  supported: boolean;
  starting: boolean;
  onToggle: () => void;
  /** 上次开过摄像头：只把按钮文案改成「恢复摄像头」，不自动取流 */
  remembered?: boolean;
  className?: string;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const node = videoRef.current;
    if (node) node.srcObject = stream;
    return () => {
      if (node) node.srcObject = null;
    };
  }, [stream]);

  return (
    <div
      className={cn(
        "relative aspect-video w-full overflow-hidden rounded-lg border border-room-line bg-room-panel",
        className,
      )}
    >
      {stream ? (
        <>
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            aria-label="本机摄像头画面"
            className="h-full w-full scale-x-[-1] object-cover"
          />
          <span className="absolute bottom-1 left-1 max-w-[calc(100%-8px)] truncate rounded bg-room-bg/85 px-1.5 py-0.5 font-mono text-[8px] leading-tight text-room-muted">
            我 / {CAMERA_CAPTION}
          </span>
        </>
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-1.5 px-2">
          <VideoOff className="size-5 text-room-faint" strokeWidth={1.6} />
          <span className="text-[9.5px] text-room-muted">本机画面未开启</span>
          {supported ? (
            <button
              type="button"
              onClick={onToggle}
              disabled={starting}
              className="text-[9.5px] text-room-accent underline underline-offset-2 hover:text-room-ink disabled:opacity-50"
            >
              {starting ? "连接中…" : remembered ? "恢复摄像头" : "开启摄像头"}
            </button>
          ) : (
            <span className="text-center text-[9px] leading-tight text-room-faint">
              当前浏览器不支持摄像头
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 窄屏顶部舞台卡（<xl 回落形态）：一张卡片包两个 tile。
 * 卡片宽 = 两 tile + 内边距（`max-w-[496]`），左右留白；亮暗场共用同一对 tile。
 */
export function StageCard({
  chip,
  speaking,
  stream,
  supported,
  starting,
  onToggle,
  remembered = false,
}: {
  chip: string;
  speaking?: boolean;
  stream: MediaStream | null;
  supported: boolean;
  starting: boolean;
  onToggle: () => void;
  remembered?: boolean;
}) {
  return (
    <div className="px-4 pt-3 md:px-6">
      <section
        aria-label="顶部舞台"
        className="mx-auto w-full max-w-[496px] rounded-xl border border-room-line bg-room-panel-2 p-2 sm:p-3"
      >
        <div className="flex gap-2 sm:gap-3">
          <InterviewerTile chip={chip} speaking={speaking} className="flex-1" />
          <CandidateTile
            stream={stream}
            supported={supported}
            starting={starting}
            onToggle={onToggle}
            remembered={remembered}
            className="flex-1"
          />
        </div>
      </section>
    </div>
  );
}
