/**
 * 打字机缓冲与队列（SPEC §7：delta 事件是完整文案，逐字动画由前端渲染）。
 *
 * 纯逻辑、与 React 解耦：页面用定时器调 tick()，本类只管"已吐/未吐"边界。
 * 关键约束：多个 delta 连发（评分 → 追问）时字符顺序不能乱、不能丢，
 * 且必须逐条吐完再吐下一条（一个 delta = 一条面试官消息）。
 */

/** 单条消息的逐字缓冲。 */
export class Typewriter {
  private pending = "";
  private revealed = "";

  /** 新文本追加到待吐队列尾部（顺序由追加顺序保证）。 */
  append(chunk: string): void {
    this.pending += chunk;
  }

  /** 吐至多 count 个字符（按码点切，避免劈开 emoji/代理对）。 */
  tick(count: number): void {
    if (count <= 0 || this.pending === "") return;
    const chars = Array.from(this.pending);
    const take = chars.slice(0, count).join("");
    this.revealed += take;
    this.pending = chars.slice(count).join("");
  }

  /** 立即补全（跳过动画 / 流已结束）。 */
  flush(): void {
    this.revealed += this.pending;
    this.pending = "";
  }

  get text(): string {
    return this.revealed;
  }

  get pendingLength(): number {
    return Array.from(this.pending).length;
  }

  get drained(): boolean {
    return this.pending === "";
  }

  reset(): void {
    this.pending = "";
    this.revealed = "";
  }
}

/** 队列元素：一条待展示（或正在展示）的面试官消息。 */
export type QueueEntry = { id: string; text: string };

/**
 * 多消息打字机队列：按到达顺序逐条吐出，队首吐完才轮到下一条。
 * 页面据此渲染"面试官正在说话"的效果，并判断何时恢复输入框。
 */
export class TypewriterQueue {
  private items: { id: string; tw: Typewriter }[] = [];

  /** 入队一条新消息（一个 delta 事件 = 一条消息）。 */
  push(id: string, text: string): void {
    const tw = new Typewriter();
    tw.append(text);
    this.items.push({ id, tw });
  }

  /** 从队首吐字；返回该条消息的最新文本（已吐部分），队空返回 null。 */
  tick(count: number): QueueEntry | null {
    const head = this.items[0];
    if (!head) return null;
    head.tw.tick(count);
    const entry = { id: head.id, text: head.tw.text };
    if (head.tw.drained) this.items.shift();
    return entry;
  }

  /** 队首剩余字符数（页面据此自适应吐字速度）。 */
  get pendingLength(): number {
    return this.items[0]?.tw.pendingLength ?? 0;
  }

  /** 是否已无待吐内容（页面据此恢复输入框）。 */
  get idle(): boolean {
    return this.items.length === 0;
  }

  clear(): void {
    this.items = [];
  }
}
