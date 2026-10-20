"use client";

import { MessageImages } from "@/components/message-images";
import { BEHAVIORAL_DOMAIN, domainLabel } from "@/lib/constants";
import { cn } from "@/lib/utils";

export type ChatItem = {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** 题目元数据：由 question 事件挂到刚到达的面试官消息上 */
  tag?: { index: number; domain: string; difficulty: string };
  /** 候选人消息附带的截图（P2-M6）：image_id 列表，渲染走鉴权端点 */
  images?: string[];
  /** 正在逐字吐出（显示光标） */
  typing?: boolean;
};

/**
 * 单条消息：面试官左侧、候选人右侧的时间线条目。
 *
 * 不再是「聊天气泡」：面试官的话直接落在纸上，候选人的回答是一块带左侧钴蓝线的
 * 回答板；左侧 29px 的节点与竖线把整场面试读成一条记录。
 */
export function MessageBubble({
  item,
  interviewId,
  last = false,
}: {
  item: ChatItem;
  interviewId: string;
  last?: boolean;
}) {
  const isUser = item.role === "user";

  return (
    <article className="relative grid grid-cols-[29px_minmax(0,1fr)] gap-3.5 pb-6">
      {!last ? (
        <span
          aria-hidden
          className="absolute top-[31px] bottom-0 left-[14px] w-px bg-room-line"
        />
      ) : null}

      <div className="flex flex-col items-center pt-0.5">
        <span
          className={cn(
            "relative z-[1] flex size-[29px] items-center justify-center border border-room-line-strong bg-room-panel text-[10px] font-semibold text-room-ink",
            isUser ? "rounded-full bg-room-panel-3 text-[9px] text-room-muted" : "rounded-lg",
          )}
        >
          {isUser ? "你" : "M"}
        </span>
      </div>

      <div className="min-w-0">
        <div className="flex min-h-[29px] flex-wrap items-center gap-2">
          <span className="text-[11.5px] font-medium text-room-ink">
            {isUser ? "你" : "码达面试官"}
          </span>
          <span className="font-mono text-[8px] tracking-[0.08em] text-room-faint uppercase">
            {isUser ? "Candidate" : "Interviewer"}
          </span>
          {item.tag ? (
            <span className="flex flex-wrap items-center gap-1.5 font-mono text-[9px] text-room-faint">
              <span className="h-2 w-px bg-room-line-strong" />
              <span className="text-room-ink">第 {item.tag.index} 题</span>
              <span className="h-2 w-px bg-room-line-strong" />
              <span>{domainLabel(item.tag.domain)}</span>
              {/* 行为题不显示难度（P1-M11 D3）：难度不参与行为面出题，是死数据 */}
              {item.tag.domain !== BEHAVIORAL_DOMAIN ? (
                <>
                  <span className="h-2 w-px bg-room-line-strong" />
                  <span>{item.tag.difficulty}</span>
                </>
              ) : null}
            </span>
          ) : null}
        </div>

        <div
          className={cn(
            "mt-1.5 text-[13px] leading-[1.78] text-room-ink-2",
            item.tag && "text-[14px] text-room-ink",
            isUser &&
              "rounded-lg border border-room-line border-l-2 border-l-room-accent bg-room-panel px-3.5 py-3 text-room-ink",
          )}
        >
          {item.images && item.images.length > 0 ? (
            <MessageImages interviewId={interviewId} imageIds={item.images} />
          ) : null}
          <p className="whitespace-pre-wrap">
            {item.content}
            {item.typing && <span className="caret" aria-hidden />}
          </p>
        </div>
      </div>
    </article>
  );
}
