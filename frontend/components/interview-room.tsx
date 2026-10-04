"use client";

import { useEffect, useRef } from "react";
import { cn } from "cn";

import { CAMERA_CAPTION } from "@/lib/camera";

/**
 * 面试间舞台（P2-M7 改版④）：**左中右「对视」形态**。
 *
 * ≥xl：面试官卡在左、我在右，对话流夹在中间——空间语言 = 「左边的人在说，右边的人在听」，
 * 与气泡的左右对齐（面试官左、我右）是同一套语言；窄屏回落为顶部卡片（`StageCard`）。
 * 两个安置点共用同一对 tile 组件，形态只有一处定义。
 *
 * FR-27 红线不变：画面只经 `video.srcObject` 进 DOM——没有 canvas、没有抓帧、没有上传。
 */

/** 面试官 tile：monogram 人像框 + 名字 + 状态徽标（状态由 `interviewerChip` 派生）。 */
export function InterviewerTile({ chip, className }: { chip: string; className?: string }) {
  return (
    <div
      className={cn(
        "flex aspect-video w-full flex-col items-center justify-center gap-1.5 overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10",
        className,
      )}
    >
      <span
        aria-hidden
        className="flex size-10 items-center justify-center rounded-full bg-primary/10 text-base font-semibold text-primary sm:size-12 sm:text-lg"
      >
        码
      </span>
      <div className="flex flex-col items-center gap-0.5 text-center">
        <span className="text-xs font-medium">码达面试官 · AI</span>
        <span className="text-xs text-muted-foreground">{chip}</span>
      </div>
    </div>
  );
}

/** 「我」的 tile：画面 / 同尺寸占位（关闭不是消失——对等感不塌、入口在自己位置上）。 */
export function CandidateTile({
  stream,
  supported,
  starting,
  onToggle,
  className,
}: {
  stream: MediaStream | null;
  supported: boolean;
  starting: boolean;
  onToggle: () => void;
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
        "relative aspect-video w-full overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10",
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
          <span className="absolute bottom-1 left-1 max-w-[calc(100%-8px)] truncate rounded-md bg-background/85 px-1.5 py-0.5 text-[10px] leading-tight text-muted-foreground ring-1 ring-foreground/10">
            我 · {CAMERA_CAPTION}
          </span>
        </>
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-1.5 px-2">
          <span
            aria-hidden
            className="flex size-10 items-center justify-center rounded-full bg-muted text-base font-semibold text-muted-foreground sm:size-12 sm:text-lg"
          >
            我
          </span>
          {supported ? (
            <button
              type="button"
              onClick={onToggle}
              disabled={starting}
              className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground disabled:opacity-50"
            >
              {starting ? "连接中…" : "开启摄像头"}
            </button>
          ) : (
            <span className="text-center text-[10px] leading-tight text-muted-foreground">
              当前浏览器不支持摄像头
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 顶部舞台卡（<xl 回落形态）：一张卡片包两个 tile。
 * 卡片宽 = 两 tile + 内边距（`max-w-[496]`），左右留白——留白是物件感的关键；
 * 同一个 `--stage` 底色铺满整条带会被读成「区域色差」，缩进卡片里就是「物件的材质」。
 */
export function StageCard({
  chip,
  stream,
  supported,
  starting,
  onToggle,
}: {
  chip: string;
  stream: MediaStream | null;
  supported: boolean;
  starting: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="px-4 pt-4 sm:px-6">
      <section
        aria-label="顶部舞台"
        className="mx-auto w-full max-w-[496px] rounded-2xl bg-stage p-2 ring-1 ring-foreground/10 sm:p-3"
      >
        <div className="flex gap-2 sm:gap-3">
          <InterviewerTile chip={chip} className="flex-1" />
          <CandidateTile
            stream={stream}
            supported={supported}
            starting={starting}
            onToggle={onToggle}
            className="flex-1"
          />
        </div>
      </section>
    </div>
  );
}
