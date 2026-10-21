"use client";

import {
  Excalidraw,
  exportToBlob,
  getNonDeletedElements,
  serializeAsJSON,
} from "@excalidraw/excalidraw";
import type {
  AppState,
  BinaryFiles,
  ExcalidrawImperativeAPI,
  ExcalidrawInitialDataState,
} from "@excalidraw/excalidraw/types";
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import "@excalidraw/excalidraw/index.css";
import { useEffect, useMemo, useRef } from "react";

import { parseWhiteboardScene } from "@/lib/whiteboard";

/**
 * 白板画布（P2-M14）：Excalidraw 的最小封装。
 *
 * 只暴露两件事：`exportPng()` 给工作台导出提交、`refresh()` 给「收起再打开」时重算尺寸。
 * 画布**始终用亮色/白底**：导出物是给模型看的，白底深字比暗色更稳；房间的暗场只影响外壳。
 *
 * 资源路径：字体由 scripts/copy-excalidraw-assets.mjs 复制到 public/excalidraw-assets，
 * 避免生产环境依赖 esm.run / CDN（国内网络与离线容器都可能拿不到）。
 */
if (typeof window !== "undefined") {
  (window as Window & { EXCALIDRAW_ASSET_PATH?: string }).EXCALIDRAW_ASSET_PATH =
    "/excalidraw-assets/";
}

export type WhiteboardHandle = {
  /** 导出当前画布为 PNG；没有图元时返回 null。 */
  exportPng: () => Promise<Blob | null>;
  /** 容器从 display:none 恢复后重算尺寸（Excalidraw 的已知行为）。 */
  refresh: () => void;
};

export function Whiteboard({
  onElementsChange,
  onReady,
  storageKey,
}: {
  onElementsChange: (count: number) => void;
  onReady?: (handle: WhiteboardHandle) => void;
  /** 按场次保存的场景键（刷新恢复）；由工作台传 `marda.whiteboard.{interviewId}` */
  storageKey: string;
}) {
  const apiRef = useRef<ExcalidrawImperativeAPI | null>(null);
  const elementsRef = useRef<readonly ExcalidrawElement[]>([]);
  const appStateRef = useRef<AppState | null>(null);
  const filesRef = useRef<BinaryFiles>({});
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const saved = useMemo(() => {
    if (typeof window === "undefined") return null;
    try {
      return parseWhiteboardScene(window.localStorage.getItem(storageKey));
    } catch {
      return null;
    }
  }, [storageKey]);

  /* 刷新恢复：Excalidraw 的 serializeAsJSON 是官方场景格式；500ms 防抖落 localStorage。
     隐私模式/配额不足静默失败，画布照常可用。 */
  const scheduleSave = (
    elements: readonly ExcalidrawElement[],
    appState: AppState,
    files: BinaryFiles,
  ) => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      try {
        window.localStorage.setItem(
          storageKey,
          serializeAsJSON(elements, appState, files, "local"),
        );
      } catch {
        // 存储不可用：只是刷新后不恢复
      }
    }, 500);
  };

  useEffect(() => {
    const flush = () => {
      try {
        window.localStorage.setItem(
          storageKey,
          serializeAsJSON(
            elementsRef.current,
            appStateRef.current ?? {},
            filesRef.current,
            "local",
          ),
        );
      } catch {
        // 同上：恢复是增强，不阻塞白板
      }
    };
    const onBeforeUnload = () => flush();
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      if (saveTimer.current) clearTimeout(saveTimer.current);
      flush();
    };
  }, [storageKey]);

  const initialData = useMemo<ExcalidrawInitialDataState>(
    () =>
      saved
        ? {
            elements: saved.elements as ExcalidrawInitialDataState["elements"],
            appState: saved.appState as ExcalidrawInitialDataState["appState"],
            files: saved.files as ExcalidrawInitialDataState["files"],
          }
        : {
            appState: {
              viewBackgroundColor: "#ffffff",
              showWelcomeScreen: false,
              theme: "light",
            },
          },
    [saved],
  );

  const handle = useMemo<WhiteboardHandle>(
    () => ({
      async exportPng() {
        const elements = elementsRef.current;
        if (elements.length === 0) return null;
        return exportToBlob(
          {
            elements: getNonDeletedElements(elements),
            appState: {
              ...(appStateRef.current ?? {}),
              viewBackgroundColor: "#ffffff",
              exportBackground: true,
              exportWithDarkMode: false,
            },
            files: filesRef.current,
          },
          { mimeType: "image/png", quality: 1, exportPadding: 24 },
        );
      },
      refresh() {
        apiRef.current?.refresh();
      },
    }),
    [],
  );

  useEffect(() => {
    onReady?.(handle);
  }, [handle, onReady]);

  return (
    <div className="whiteboard-shell h-full w-full">
      <Excalidraw
        excalidrawAPI={(api) => {
          apiRef.current = api;
        }}
        initialData={initialData}
        theme="light"
        langCode="zh-CN"
        autoFocus
        onChange={(elements, appState, files) => {
          elementsRef.current = elements;
          appStateRef.current = appState;
          filesRef.current = files;
          onElementsChange(getNonDeletedElements(elements).length);
          scheduleSave(elements, appState, files);
        }}
        renderTopRightUI={() => null}
        UIOptions={{
          canvasActions: {
            loadScene: false,
            saveToActiveFile: false,
            export: false,
            saveAsImage: false,
            clearCanvas: false,
            changeViewBackgroundColor: false,
            toggleTheme: false,
          },
          tools: { image: false },
        }}
      />
    </div>
  );
}
