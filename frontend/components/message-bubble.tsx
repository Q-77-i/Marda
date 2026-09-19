"use client";

import { motion, useReducedMotion } from "framer-motion";

import { domainLabel } from "@/lib/constants";

export type ChatItem = {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** 题目元数据：由 question 事件挂到刚到达的面试官消息上 */
  tag?: { index: number; domain: string; difficulty: string };
  /** 正在逐字吐出（显示光标） */
  typing?: boolean;
};

/** 单条消息：面试官左侧中性底，候选人右侧强调色底。 */
export function MessageBubble({ item }: { item: ChatItem }) {
  const reduce = useReducedMotion();
  const isUser = item.role === "user";

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 220, damping: 26 }}
      className={isUser ? "flex justify-end" : "flex justify-start"}
    >
      <div
        className={[
          "max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
          isUser
            ? "rounded-tr-sm bg-primary text-primary-foreground"
            : "rounded-tl-sm bg-muted text-foreground",
        ].join(" ")}
      >
        {item.tag && (
          <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
            <span className="tabular font-medium text-foreground">
              第 {item.tag.index} 题
            </span>
            <span>{domainLabel(item.tag.domain)}</span>
            <span>{item.tag.difficulty}</span>
          </div>
        )}
        <p className="whitespace-pre-wrap">
          {item.content}
          {item.typing && <span className="caret" aria-hidden />}
        </p>
      </div>
    </motion.div>
  );
}
