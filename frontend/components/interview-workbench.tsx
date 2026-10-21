"use client";

import dynamic from "next/dynamic";
import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "cn";

import type { WhiteboardHandle } from "@/components/whiteboard";
import { canSubmitBoard } from "@/lib/whiteboard";

/** Excalidraw 体积大，只有第一次打开白板才加载（SSR 关闭：它依赖 canvas/localStorage）。 */
const Whiteboard = dynamic(
  () => import("@/components/whiteboard").then((module) => module.Whiteboard),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center text-[11px] text-room-muted">
        正在加载白板…
      </div>
    ),
  },
);

/**
 * 白板工作台（P2-M14）：右侧 320px 放不下画布，所以工具在覆盖中右栏的工作台里展开。
 *
 * 首次打开后保持挂载（`open=false` 只是 `hidden`）：收起再打开不丢画布；
 * 提交走父组件的 `onSubmit`（导出 PNG → 现有图片通道），失败时工作台不关，可重试。
 */
export function InterviewWorkbench({
  open,
  onClose,
  question,
  error,
  notice,
  disabled,
  attaching,
  onSubmit,
  storageKey,
}: {
  open: boolean;
  onClose: () => void;
  question: string | null;
  error: string | null;
  notice: string | null;
  disabled: boolean;
  attaching: boolean;
  onSubmit: (blob: Blob) => Promise<boolean>;
  /** 白板场景按场次存 localStorage（刷新恢复） */
  storageKey: string;
}) {
  const [elementCount, setElementCount] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const boardRef = useRef<WhiteboardHandle | null>(null);

  const setBoard = useCallback((handle: WhiteboardHandle) => {
    boardRef.current = handle;
  }, []);

  useEffect(() => {
    if (!open) return;
    // 不抢焦点到「收起」按钮：白板打开后应直接能画（Excalidraw 自己 autoFocus 画布）
    const frame = requestAnimationFrame(() => boardRef.current?.refresh());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const ready = canSubmitBoard(elementCount, attaching) && !disabled && !submitting;

  const handleSubmit = async () => {
    if (!ready) return;
    setSubmitting(true);
    try {
      const blob = await boardRef.current?.exportPng();
      if (!blob) return;
      const ok = await onSubmit(blob);
      if (ok) onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section
      role="dialog"
      aria-modal="true"
      aria-label="白板工作台"
      className={cn(
        "fixed inset-0 z-30 flex flex-col border-l border-room-line bg-room-bg text-room-ink xl:left-[264px]",
        !open && "hidden",
      )}
    >
      <header className="flex h-[62px] flex-none items-center justify-between gap-3 border-b border-room-line px-4 md:px-6">
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <strong className="text-[12.5px] font-medium">白板工作台</strong>
            <span className="font-mono text-[8.5px] text-room-faint">Whiteboard</span>
          </div>
          <p className="mt-1 truncate text-[10.5px] text-room-muted">
            {question ?? "当前没有进行中的题目，可以先画架构图。"}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="hidden font-mono text-[8.5px] text-room-faint sm:inline">
            {elementCount} 个图元
          </span>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-room-line px-2 text-[10.5px] text-room-muted transition-colors hover:border-room-line-strong hover:text-room-ink"
          >
            <X className="size-3.5" strokeWidth={1.8} />
            收起
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 p-3">
        <div className="h-full min-h-[320px] overflow-hidden rounded-lg border border-room-line bg-white">
          <Whiteboard
            onElementsChange={setElementCount}
            onReady={setBoard}
            storageKey={storageKey}
          />
        </div>
      </div>

      <footer className="flex flex-none flex-col gap-2 border-t border-room-line bg-room-bg px-4 py-3 md:px-6">
        {error ? (
          <p role="alert" className="text-[10.5px] text-room-danger">
            {error}
          </p>
        ) : null}
        {notice ? (
          <p role="status" className="text-[10.5px] text-room-muted">
            {notice}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[10px] leading-relaxed text-room-faint">
            白板会作为图片随下一条回答提交；面试官会结合图点评。图与截图走同一条附件通道。
          </p>
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={!ready}
            title={
              disabled
                ? "面试已结束或面试官正在回应，暂时不能提交"
                : "导出白板并作为回答提交"
            }
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-room-accent px-3 text-[11.5px] font-medium text-room-bg transition-colors hover:bg-room-accent/90 disabled:pointer-events-none disabled:opacity-45"
          >
            {submitting || attaching ? "提交中…" : "提交给面试官"}
          </button>
        </div>
      </footer>
    </section>
  );
}
