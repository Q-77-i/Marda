/**
 * 面试间右栏 Tab 数据与派生（P2-M13）。
 *
 * 页面里的 tab 分支进不了 vitest（P2-M3 的教训），把「顺序、就绪态、未知值回落、
 * 当前题目怎么取」放在这里；组件只消费。
 */

export type RoomTab = "status" | "board" | "code";

export type RoomTabMeta = {
  value: RoomTab;
  label: string;
  /** 是否可用；未就绪的 Tab 渲染成不可点（目前三项都已就绪）。 */
  ready: boolean;
};

export const ROOM_TABS: readonly RoomTabMeta[] = [
  { value: "status", label: "状态", ready: true },
  { value: "board", label: "白板", ready: true },
  { value: "code", label: "代码", ready: true },
];

export function normalizeRoomTab(value: string | null): RoomTab {
  return ROOM_TABS.find((tab) => tab.value === value)?.value ?? "status";
}

export function roomTabLabel(value: RoomTab): string {
  return ROOM_TABS.find((tab) => tab.value === value)?.label ?? "状态";
}

/**
 * 当前题目 = 最后一条带 `question` 标签的面试官消息。
 *
 * 不能直接取最后一条 assistant：追问/点评也是 assistant，只有出题节点发的
 * `question` 事件会给消息挂 tag（见 interview-client 的 question handler）。
 *
 * `fallbackToLastAssistant`：刷新/回放时历史消息来自会话接口，没有实时流里的
 * tag；此时退回最后一条面试官消息，至少让「当前题目」不空着（M13 修复）。
 */
export function latestQuestionText(
  messages: readonly {
    role: "user" | "assistant";
    content: string;
    tag?: unknown;
  }[],
  fallbackToLastAssistant = false,
): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && message.tag) return message.content;
  }
  if (fallbackToLastAssistant) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].role === "assistant") return messages[index].content;
    }
  }
  return null;
}
