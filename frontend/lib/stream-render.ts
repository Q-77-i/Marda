/**
 * 流式渲染缓冲（P2-M4）：把 delta_start / delta_chunk / delta 收敛成「对消息列表做什么」。
 *
 * 后端的到达顺序不是「一条消息一条消息地流」：同一节点内**所有分片先到齐**，
 * 节点结束才一次性到齐那几条 delta（终稿）。所以「delta 到了就结一条」配不上对——
 * 出题节点一次跑出两条消息（答错缓冲 + 题目）时，谁是谁只能靠 delta_start 划边界；
 * 而 delta 与 delta_start 就是按序 FIFO 配对。这段状态机放这里：纯逻辑、可 vitest
 * （页面里的分支进不了测试，见 P2-M3 的教训）。
 *
 * 四条口径：
 * - `chunk()` 追加给**最近一条未结算**的消息（分片天然属于当前正在说的那条）；
 * - `delta()` 结算**最早一条未结算**的消息，用终稿全文替换累积文本——服务端 `chat()`
 *   会 strip 两侧空白，分片拼接与终稿可能差几个不可见字符，以终稿为准，前端不二次拼接；
 * - `delta()` 没有未结算的 → `legacy`：该消息没走流式（或旧后端），交回页面走旧打字机——
 *   PRD「旧打字机兼容」的落点；
 * - `abandon()` 收走所有未结算气泡：**没收敛到终稿的消息从未落 checkpoint**（节点抛错则
 *   状态不提交），留在列表里就是「看得见、刷新就没」的假消息。
 */

export type StreamAction =
  /** 开一条新的面试官消息（气泡先空着，等分片填）。 */
  | { kind: "open"; id: string }
  /** 追加增量到某条消息。 */
  | { kind: "append"; id: string; text: string }
  /** 用终稿全文替换累积文本（结算，不再动画）。 */
  | { kind: "settle"; id: string; text: string }
  /** 没有流式分片的消息：新建气泡、正文交给旧打字机逐字吐。 */
  | { kind: "legacy"; id: string; text: string };

/** 消息列表元素的最小形状（与组件里的 ChatItem 结构兼容，泛型保住各自的扩展字段）。 */
export type StreamMessage = { id: string; role: string; content: string };

export class StreamBuffer {
  private open: { id: string; text: string }[] = [];

  constructor(private readonly makeId: () => string) {}

  /** delta_start：开一条新消息。 */
  start(): StreamAction {
    const id = this.makeId();
    this.open.push({ id, text: "" });
    return { kind: "open", id };
  }

  /** delta_chunk：追加到最近一条未结算消息；一条都没开就兜底开一条（协议外的形状不该丢字）。 */
  chunk(text: string): StreamAction {
    let last = this.open[this.open.length - 1];
    if (!last) {
      last = { id: this.makeId(), text: "" };
      this.open.push(last);
    }
    last.text += text;
    return { kind: "append", id: last.id, text };
  }

  /** delta（终稿）：结算最早一条未结算消息；没有 → 走旧打字机。 */
  delta(text: string): StreamAction {
    const first = this.open.shift();
    if (first) return { kind: "settle", id: first.id, text };
    return { kind: "legacy", id: this.makeId(), text };
  }

  /** 错误 / 流结束：收走未结算气泡的 id（页面据此把它们从列表删掉），返回是否删过。 */
  abandon(): string[] {
    const ids = this.open.map((entry) => entry.id);
    this.open = [];
    return ids;
  }

  /** 是否还有没说完的消息（页面收尾判据：有就不能算「面试官说完了」）。 */
  get hasOpen(): boolean {
    return this.open.length > 0;
  }

  reset(): void {
    this.open = [];
  }
}

/**
 * 把动作落到消息列表上（纯函数）：`create` 由调用方给，保证新气泡带上页面自己的字段。
 *
 * append 时列表里没有那条消息（例如分片先于任何状态到达）就地补一条——
 * 宁可多一条气泡，也不静默丢字。
 */
export function applyStreamAction<T extends StreamMessage>(
  messages: T[],
  action: StreamAction,
  create: (id: string, content: string) => T,
): T[] {
  switch (action.kind) {
    case "open":
    case "legacy":
      return [...messages, create(action.id, "")];
    case "append":
      if (!messages.some((m) => m.id === action.id)) {
        return [...messages, create(action.id, action.text)];
      }
      return messages.map((m) =>
        m.id === action.id ? { ...m, content: m.content + action.text } : m,
      );
    case "settle":
      return messages.map((m) => (m.id === action.id ? { ...m, content: action.text } : m));
  }
}
