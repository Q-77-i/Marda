"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { cn } from "cn";

import {
  levelToBars,
  smoothLevel,
  voiceStatusText,
  type VoiceWaveState,
} from "@/lib/voice-wave";

export type VoiceWaveHandle = {
  /** 由 AsrRecorder 的 onLevel 驱动；命令式写入，避免 20Hz 重渲染整页面。 */
  setLevel: (level: number) => void;
};

/**
 * 候选人语音动效（P2-M13）：输入框左侧的 5 根柱。
 *
 * 柱高来自**真实麦克风 RMS**，不是固定的 CSS 假动画——这样它同时是一个
 * 「麦克风到底有没有在收音」的指示器：静音时柱条贴在底部，说话时才抬起来。
 * 动画只改 transform（合成器友好），并尊重 prefers-reduced-motion。
 */
export const VoiceWave = forwardRef<
  VoiceWaveHandle,
  { state: VoiceWaveState; className?: string }
>(function VoiceWave({ state, className }, ref) {
  const [bars, setBars] = useState(() => levelToBars(0));
  const levelRef = useRef(0);
  const frameRef = useRef<number | null>(null);
  const reducedRef = useRef(false);

  useImperativeHandle(
    ref,
    () => ({
      setLevel(level: number) {
        if (reducedRef.current) return;
        levelRef.current = level;
        if (frameRef.current !== null) return;
        frameRef.current = requestAnimationFrame(() => {
          frameRef.current = null;
          const target = levelToBars(levelRef.current);
          setBars((previous) =>
            previous.map((value, index) => smoothLevel(value, target[index], 0.4)),
          );
        });
      },
    }),
    [],
  );

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    reducedRef.current = media.matches;
    const onChange = (event: MediaQueryListEvent) => {
      reducedRef.current = event.matches;
    };
    media.addEventListener("change", onChange);
    return () => {
      media.removeEventListener("change", onChange);
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    };
  }, []);

  /* 停止录音/连接中/整理中：柱条回到底部；录音中才跟随实时振幅。 */
  useEffect(() => {
    if (state !== "recording") setBars(levelToBars(0));
  }, [state]);

  if (state === "idle") return null;

  return (
    <span
      className={cn(
        "inline-flex h-7 items-center rounded-md border border-room-line bg-room-panel-2 px-2",
        className,
      )}
      title={voiceStatusText(state)}
    >
      <span
        aria-hidden
        className={cn(
          "flex h-4 items-end gap-[3px]",
          state !== "recording" && "animate-pulse",
        )}
      >
        {bars.map((scale, index) => (
          <span
            key={index}
            className={cn(
              "block h-4 w-[3px] origin-bottom rounded-full transition-transform duration-75 ease-out",
              state === "recording" ? "bg-room-accent" : "bg-room-muted",
            )}
            style={{ transform: `scaleY(${scale})` }}
          />
        ))}
      </span>
      <span role="status" className="sr-only">
        {voiceStatusText(state)}
      </span>
    </span>
  );
});
