"use client";

import { cn } from "cn";

import { ROOM_TABS, type RoomTab } from "@/lib/room-tabs";

/**
 * 右栏 Tab 条（P2-M13）：状态 / 白板 / 代码。
 *
 * 数据与就绪态在 lib/room-tabs.ts（可单测）；这里只渲染。
 * 代码 Tab 本轮未就绪，渲染成不可点，避免发一个“点进去是空的”入口。
 */
export function RoomTabs({
  value,
  onChange,
}: {
  value: RoomTab;
  onChange: (tab: RoomTab) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="面试间工具"
      className="flex gap-5 border-b border-room-line px-3"
    >
      {ROOM_TABS.map((tab) => {
        const active = tab.value === value;
        return (
          <button
            key={tab.value}
            type="button"
            role="tab"
            id={`room-tab-${tab.value}`}
            aria-selected={active}
            aria-controls="room-tab-panel"
            disabled={!tab.ready}
            title={tab.ready ? tab.label : `${tab.label}工作台即将上线`}
            onClick={() => onChange(tab.value)}
            className={cn(
              "relative py-2.5 text-[10.5px] transition-colors",
              active
                ? "font-medium text-room-ink"
                : "text-room-muted hover:text-room-ink",
              "disabled:pointer-events-none disabled:opacity-40",
            )}
          >
            {tab.label}
            {active ? (
              <span
                aria-hidden
                className="absolute right-0 -bottom-px left-0 h-0.5 bg-room-accent"
              />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
