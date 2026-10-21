"use client";

import Editor, { loader, type OnMount } from "@monaco-editor/react";
import { useCallback, useEffect, useRef } from "react";

import { codingLanguage, type CodingLanguage } from "@/lib/coding";

/**
 * Monaco 包装（M15）：**只经 next/dynamic(ssr:false) 挂载**（它依赖 window/worker）。
 *
 * 资源走同源 `/monaco/vs`（scripts/copy-monaco-assets.mjs 从 node_modules 复制），
 * 不走 @monaco-editor/react 默认的 jsdelivr CDN——离线/国内网络也能用。
 * 配置必须在首次 mount 前完成，所以放在模块顶层而不是组件 effect 里。
 */
loader.config({ paths: { vs: "/monaco/vs" } });

export function CodeEditor({
  value,
  language,
  onChange,
  open,
}: {
  value: string;
  language: CodingLanguage;
  onChange: (value: string) => void;
  /** 工作台从 hidden 变可见时补一次 layout（Monaco 的自动布局在 display:none 下测不到尺寸）。 */
  open: boolean;
}) {
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null);
  const meta = codingLanguage(language);

  const handleMount = useCallback<OnMount>((editor) => {
    editorRef.current = editor;
  }, []);

  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => editorRef.current?.layout());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  return (
    <Editor
      key={meta.value}
      height="100%"
      language={meta.monaco}
      value={value}
      onChange={(next) => onChange(next ?? "")}
      onMount={handleMount}
      theme="vs-dark"
      loading={
        <div className="flex h-full items-center justify-center text-[11px] text-room-muted">
          正在加载编辑器…
        </div>
      }
      options={{
        minimap: { enabled: false },
        fontFamily: "Geist Mono, ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: 13,
        lineHeight: 20,
        wordWrap: "on",
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 4,
        insertSpaces: true,
        renderLineHighlight: "line",
        padding: { top: 12, bottom: 12 },
        lineNumbersMinChars: 3,
        smoothScrolling: true,
        contextmenu: false,
      }}
    />
  );
}
