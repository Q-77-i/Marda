/**
 * 白板工作台纯逻辑（P2-M14）。
 *
 * Excalidraw 的渲染/导出在浏览器里，这里只放能单测的判据：
 * 「什么情况下可提交」「草稿为空时发什么说明」。
 */

/** 用户没写说明时的默认提交语：明确告诉面试官“看这张图”，而不是发一张无上下文的图。 */
export const BOARD_DEFAULT_TEXT = "这是我的架构图，请结合图点评。";

/** 有图元才可提交；上传中不可重复点（避免同一张图落两次盘）。 */
export function canSubmitBoard(elements: number, attaching: boolean): boolean {
  return elements > 0 && !attaching;
}

/** 优先用用户当前草稿，空白则用默认说明。 */
export function boardSubmitText(draft: string): string {
  const text = draft.trim();
  return text || BOARD_DEFAULT_TEXT;
}

/** 白板场景快照（Excalidraw `serializeAsJSON` 产物的子集）；用于刷新恢复。 */
export type SavedWhiteboardScene = {
  elements: unknown[];
  appState: Record<string, unknown>;
  files: Record<string, unknown>;
};

/**
 * 解析 localStorage 里的白板场景（纯函数，便于 vitest）。
 * 格式坏/版本不对/没有 elements → 返回 null（画布按空白开始，不炸工作台）。
 */
export function parseWhiteboardScene(raw: string | null): SavedWhiteboardScene | null {
  if (!raw) return null;
  try {
    const data = JSON.parse(raw) as {
      elements?: unknown;
      appState?: unknown;
      files?: unknown;
    };
    if (!Array.isArray(data.elements)) return null;
    return {
      elements: data.elements,
      appState:
        data.appState && typeof data.appState === "object"
          ? (data.appState as Record<string, unknown>)
          : {},
      files:
        data.files && typeof data.files === "object"
          ? (data.files as Record<string, unknown>)
          : {},
    };
  } catch {
    return null;
  }
}
