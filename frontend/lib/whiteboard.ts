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
