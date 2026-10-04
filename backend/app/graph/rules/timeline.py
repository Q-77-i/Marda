"""节点时间线派生（P2-M12 / SPEC §4.7）：checkpoint 历史 → 逐步节点记录。

**零写入**：全部从 LangGraph checkpointer 已有的存档派生（`aget_state_history` +
`alist`），不建表、不改 state、不改节点——存档本来就是按步写的，缺的只是读它的人。

三个判据的来源（都不是猜的，P2-M12 探针在真库 + 受控 FakeLLM 场次上核过）：

- **node**：来自**前一个存档的 `next`**（该存档之后要执行的节点）。checkpoint 的
  `metadata` 里没有 `writes`（只有 source/step/parents），writes 表的 `task_id` 是
  UUID——`next` 是唯一现成的节点名来源。
- **duration_ms**：相邻存档的 `ts` 间隔（≈ 该步执行时间 + 落盘开销；`pause` 步天然
  包含用户思考与作答时间，页面上如实说明）。
- **writes**：**该存档上记录的写入通道 ∩ 这一步真的变了的通道**。两者缺一不可——
  只取值 diff：langgraph 默认 `durability="async"`（异步落盘），节点对 Pydantic 对象的
  原地变更（如 judge 合并回答到 `current_question`）会渗进上一步的存储值，diff 会把
  下一步的改动记在上一步头上（真库实测：每个 pause 步都凭空多出「题目」）；
  只取记录：节点会把 `degraded_reasons`/未变的 `difficulty` 原样回传，摘要里会凭空
  多出「降级记录」（健康场次也一样）。交集两头都滤掉。

本函数只吃纯数据（service 层把 StateSnapshot / CheckpointTuple 拍平），故可单测。
"""

from __future__ import annotations

from datetime import datetime

# 节点中文名（页面展示）。图里的节点一个不能少——漏了页面就显示英文标识符，
# 单测反查图节点集合。
NODE_LABELS = {
    "intro": "开场白",
    "profile": "自我介绍提炼",
    "ask": "出题",
    "pause": "等待输入",
    "judge": "评分",
    "followup": "追问",
    "advance": "换题",
    "closing_invite": "邀请反问",
    "answer_candidate": "回答反问",
    "refuse_end": "结束被挽留",
    "report": "生成报告",
}

# 状态通道 → 中文摘要（**声明序即摘要展示序**：先题目、再答题、后记账）。
# 多个通道可折叠成同一个词——判分同时改 answered_count 与 answered_questions，
# 摘要里只该出现一次「答题记录」。
STATE_FIELD_LABELS = {
    "current_question": "题目",
    "answered_questions": "答题记录",
    "answered_count": "答题记录",
    "user_input": "你的输入",
    "asked_ids": "已问题目",
    "chat_history": "对话记录",
    "current_images": "附图",
    "phase": "阶段",
    "difficulty": "难度",
    "consecutive_good": "难度",
    "consecutive_bad": "难度",
    "candidate_profile": "候选人画像",
    "closing_question_count": "反问计数",
    "report": "能力报告",
    "status": "场次状态",
    "trace_log": "决策记录",
    "degraded_reasons": "降级记录",
    "interview_id": "场次信息",
    "position": "场次信息",
    "question_count": "场次信息",
    "user_id": "场次信息",
    "interview_type": "场次信息",
    "difficulty_locked": "场次信息",
    "resume_id": "场次信息",
}

# checkpoint 的内部痕迹（调度边、中断、恢复值）：不是业务状态变化，不进摘要
_INTERNAL_CHANNELS = {"__interrupt__", "__resume__", "__start__", "__error__"}
_INTERNAL_PREFIXES = ("branch:to:",)

# 轮次归属：出题**开启**新的一轮；评分/追问/换题服务当前轮；等待输入（含结束被挽留）
# 等的是**手上那道题**——追问后的那次等待仍属同一轮，不是下一轮（answered_count 只
# 在首答判分时 +1，光看它会把「补答」算成下一题）。
_ROUND_OPEN = "ask"
_ROUND_CURRENT = {"judge", "followup", "advance"}
_ROUND_WAIT = {"pause", "refuse_end"}

# 「手上有题」的阶段：advance 换成 CLOSING 时并不清 current_question（题还在 state 里），
# 光看它会把反问阶段的等待也算成第 N 题。
_QUESTION_PHASES = {"warmup", "project", "tech_base", "behavioral"}

# LangGraph 的虚拟入口节点：不是业务节点，不列
_VIRTUAL_NODES = {"__start__"}


def _parse_ts(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_internal(channel: str) -> bool:
    return channel in _INTERNAL_CHANNELS or channel.startswith(_INTERNAL_PREFIXES)


def _changed(before: dict, after: dict) -> set[str]:
    """两步之间真的变了的通道（浅比较；值已在 service 层归一成纯数据）。"""
    return {
        key for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    }


def _writes_summary(channels: list[str]) -> list[str]:
    """写入通道 → 中文摘要：按通道表声明序输出（稳定、且不随存档里的写入顺序漂），
    折叠同义通道、去重；未知通道按名字典序附在末尾（原值兜底，不猜）。"""
    present = {channel for channel in channels if not _is_internal(channel)}
    labels: list[str] = []
    for channel, label in STATE_FIELD_LABELS.items():
        if channel in present and label not in labels:
            labels.append(label)
    for channel in sorted(present - STATE_FIELD_LABELS.keys()):
        labels.append(channel)
    return labels


def _round_of(node: str, values_after: dict, current_round: int | None) -> int | None:
    """该步服务的问答轮次（1 起）；给不出就 None（开场/提炼/收尾/没有题在手）。

    `current_round` 是前面各步累积下来的「手上前一道题」——等待输入沿用它（追问补充
    仍属同一轮），出题开启新一轮，评分/追问/换题把它对齐到 answered_count。
    """
    answered = values_after.get("answered_count")
    answered = answered if isinstance(answered, int) else 0
    has_question = (
        values_after.get("current_question") is not None
        and values_after.get("phase") in _QUESTION_PHASES
    )
    if node == _ROUND_OPEN:
        return answered + 1 if has_question else None
    if node in _ROUND_CURRENT:
        return answered or None
    if node in _ROUND_WAIT:
        return current_round if has_question else None
    return None  # 开场/提炼/邀请反问/回答反问/收尾


def node_timeline(steps: list[dict]) -> list[dict]:
    """checkpoint 历史（**时间升序**）→ 节点时间线。

    入参每项 = `{"ts": ISO|None, "next": [节点名], "writes": [通道名], "values": {state}}`
    （writes = 该存档上记录的写入通道，values = 该存档的状态）。

    每行 = 一次节点执行：node 取**前一个存档的 next**（所以最后一条存档本身不产生行），
    duration 取两次存档的时间差。跳过两处：虚拟入口（`__start__`）与无法归属到单一
    节点的步（`next` 为空/多个——本图不产生，防的是将来改图）。
    """
    rows: list[dict] = []
    current_round: int | None = None
    for index in range(len(steps) - 1):
        current, following = steps[index], steps[index + 1]
        next_nodes = current.get("next") or []
        if len(next_nodes) != 1 or next_nodes[0] in _VIRTUAL_NODES:
            continue
        node = next_nodes[0]

        started, ended = _parse_ts(current.get("ts")), _parse_ts(following.get("ts"))
        duration_ms = None
        if started is not None and ended is not None:
            duration_ms = max(0, round((ended - started).total_seconds() * 1000))

        changed = _changed(current.get("values") or {}, following.get("values") or {})
        written = [c for c in (current.get("writes") or []) if c in changed]
        values_after = following.get("values") or {}
        round_no = _round_of(node, values_after, current_round)
        if node == _ROUND_OPEN or node in _ROUND_CURRENT:
            current_round = round_no  # 出题开新一轮 / 评分追问换题对齐到当前轮

        rows.append({
            "seq": len(rows),
            "node": node,
            "node_label": NODE_LABELS.get(node, node),
            "round": round_no,
            "duration_ms": duration_ms,
            "writes": _writes_summary(written),
        })
    return rows
