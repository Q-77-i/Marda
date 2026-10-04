"""节点时间线派生（P2-M12 / SPEC §4.7）：纯函数，输入是 service 拍平的 checkpoint 步骤。

判据都贴着「数据从哪来」写：node 来自**前一个存档的 next**、duration 来自**相邻时间戳间隔**、
writes 来自**该存档记录的写入 ∩ 这一步真变了的通道**——两者缺一不可，理由见
`rules/timeline.py` 顶部（异步落盘会把原地变更渗进上一步的值；节点又会把
`degraded_reasons` 这类字段原样回传）。
"""

from __future__ import annotations

from app.graph.rules.timeline import node_timeline


def step(
    *,
    ts: str | None,
    next: list[str] | None = None,
    writes: list[str] | None = None,
    values: dict | None = None,
) -> dict:
    """一条 checkpoint 步骤（service 拍平后的形状）。"""
    return {
        "ts": ts,
        "next": next if next is not None else [],
        "writes": writes or [],
        "values": values or {},
    }


def vals(
    answered: int = 0, question: dict | None = None, phase: str = "tech_base", **extra
) -> dict:
    return {"answered_count": answered, "current_question": question, "phase": phase, **extra}


def test_逐步派生_节点取前一个存档的next_时长取相邻间隔():
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(
            ts="2026-10-05T00:00:01+00:00", next=["intro"], writes=["chat_history", "phase"],
            values=vals(phase="intro", chat_history=[]),
        ),
        step(
            ts="2026-10-05T00:00:02+00:00", next=["pause"], writes=["user_input"],
            values=vals(phase="warmup", chat_history=[{"role": "assistant"}], user_input=""),
        ),
        step(
            ts="2026-10-05T00:00:02.500000+00:00", next=[],
            values=vals(phase="warmup", chat_history=[{"role": "assistant"}], user_input="你好"),
        ),
    ]
    rows = node_timeline(steps)
    # __start__ 是 LangGraph 的虚拟入口，不列；终态存档不产生行
    assert [r["node"] for r in rows] == ["intro", "pause"]
    assert rows[0]["duration_ms"] == 1000  # intro：01→02
    assert rows[1]["duration_ms"] == 500  # pause：02→02.5
    assert rows[0]["writes"] == ["对话记录", "阶段"]
    assert rows[1]["writes"] == ["你的输入"]


def test_序号连续_跳过虚拟节点后从零起():
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(ts="2026-10-05T00:00:01+00:00", next=["intro"]),
        step(ts="2026-10-05T00:00:02+00:00", next=["ask"]),
        step(ts="2026-10-05T00:00:03+00:00", next=[]),
    ]
    assert [r["seq"] for r in node_timeline(steps)] == [0, 1]


def test_时间戳缺失或不可解析_时长为None不猜():
    steps = [
        step(ts=None, next=["intro"]),
        step(ts="不是时间", next=["ask"]),
        step(ts="2026-10-05T00:00:03+00:00", next=[]),
    ]
    rows = node_timeline(steps)
    assert [r["duration_ms"] for r in rows] == [None, None]


def test_只有一条存档给空表():
    assert node_timeline([]) == []
    assert node_timeline([step(ts="2026-10-05T00:00:00+00:00", next=["intro"])]) == []


def test_并行或缺失的next不归属_整行不列():
    """next 不是单一节点时无法归属到某一步（本图不会出现，防的是将来改图）。"""
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["a", "b"], writes=["phase"]),
        step(ts="2026-10-05T00:00:01+00:00", next=["intro"]),
        step(ts="2026-10-05T00:00:02+00:00", next=[]),
    ]
    rows = node_timeline(steps)
    assert [r["node"] for r in rows] == ["intro"]
    assert rows[0]["seq"] == 0


def test_轮次归属_出题开新一轮_追问后的等待仍属同一题():
    """轮次取该步**之后**的状态（存档存的是「该步之前」，见 service._timeline_step）：
    出题那一步之后题已在手、answered 未 +1 → 第 1 题；判分之后 answered=1 → 第 1 题。
    真库实测同形状：next=judge 的存档 answered=14 → 下一步 next=advance 的存档 answered=15。

    **追问后的等待仍属同一题**：answered_count 只在首答判分时 +1，光看它会把补答算成下一题。
    """
    q1, q2 = {"text": "q1"}, {"text": "q2"}
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(ts="2026-10-05T00:00:01+00:00", next=["intro"], values=vals(phase="intro")),
        step(ts="2026-10-05T00:00:02+00:00", next=["pause"], values=vals(phase="warmup")),
        step(ts="2026-10-05T00:00:03+00:00", next=["profile"], values=vals(phase="warmup")),
        step(ts="2026-10-05T00:00:04+00:00", next=["ask"], values=vals(phase="warmup")),
        step(ts="2026-10-05T00:00:05+00:00", next=["pause"], values=vals(question=q1)),
        step(ts="2026-10-05T00:00:06+00:00", next=["judge"], values=vals(question=q1)),
        step(ts="2026-10-05T00:00:07+00:00", next=["followup"], values=vals(answered=1, question=q1)),
        step(ts="2026-10-05T00:00:08+00:00", next=["pause"], values=vals(answered=1, question=q1)),
        step(ts="2026-10-05T00:00:09+00:00", next=["judge"], values=vals(answered=1, question=q1)),
        step(ts="2026-10-05T00:00:10+00:00", next=["advance"], values=vals(answered=1, question=q1)),
        step(ts="2026-10-05T00:00:11+00:00", next=["ask"], values=vals(answered=1, question=q1)),
        step(ts="2026-10-05T00:00:12+00:00", next=["pause"], values=vals(answered=1, question=q2)),
        step(ts="2026-10-05T00:00:13+00:00", next=[], values=vals(answered=1, question=q2)),
    ]
    by_node: dict[str, list] = {}
    for row in node_timeline(steps):
        by_node.setdefault(row["node"], []).append(row["round"])
    assert by_node["intro"] == [None]
    assert by_node["profile"] == [None]
    # 开场那次还没题在手上；等第 1 题首答、等第 1 题补答（仍是第 1 题）、等第 2 题
    assert by_node["pause"] == [None, 1, 1, 2]
    assert by_node["ask"] == [1, 2]  # 出题开新一轮
    assert by_node["judge"] == [1, 1]  # 评分服务当前题（首答与补答同题）
    assert by_node["followup"] == [1]
    assert by_node["advance"] == [1]


def test_反问阶段的等待不给轮次():
    """advance 换成 CLOSING 时并不清 current_question，光看它会把反问阶段的等待
    也算成第 N 题——判据要连阶段一起看。"""
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(ts="2026-10-05T00:00:01+00:00", next=["advance"], values=vals(answered=2, question={"text": "q2"})),
        step(
            ts="2026-10-05T00:00:02+00:00", next=["closing_invite"],
            values=vals(answered=2, question={"text": "q2"}, phase="closing"),
        ),
        step(
            ts="2026-10-05T00:00:03+00:00", next=["pause"],
            values=vals(answered=2, question={"text": "q2"}, phase="closing"),
        ),
        step(ts="2026-10-05T00:00:04+00:00", next=[], values=vals(answered=2, phase="closing")),
    ]
    rounds = {r["node"]: r["round"] for r in node_timeline(steps)}
    assert rounds["advance"] == 2  # 换题那一步仍服务第 2 题
    assert rounds["closing_invite"] is None
    assert rounds["pause"] is None  # 等的是反问，不是第 2 题


def test_开场与收尾节点不给轮次():
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(ts="2026-10-05T00:00:01+00:00", next=["intro"], values=vals()),
        step(ts="2026-10-05T00:00:02+00:00", next=["profile"], values=vals()),
        step(
            ts="2026-10-05T00:00:03+00:00", next=["closing_invite"],
            values=vals(answered=2, question={"text": "q2"}),
        ),
        step(
            ts="2026-10-05T00:00:04+00:00", next=["report"],
            values=vals(answered=2, question={"text": "q2"}),
        ),
        step(ts="2026-10-05T00:00:05+00:00", next=[], values=vals(answered=2)),
    ]
    rounds = {r["node"]: r["round"] for r in node_timeline(steps)}
    assert rounds["intro"] is None
    assert rounds["profile"] is None
    assert rounds["closing_invite"] is None
    assert rounds["report"] is None


def test_等待输入时没有题在手_不给轮次():
    """开场后等自我介绍：pause 但 current_question 为空 → 不属于任何一题。"""
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(ts="2026-10-05T00:00:01+00:00", next=["intro"], values=vals()),
        step(ts="2026-10-05T00:00:02+00:00", next=["pause"], values=vals()),
        step(ts="2026-10-05T00:00:03+00:00", next=[], values=vals()),
    ]
    rows = node_timeline(steps)
    assert [r["node"] for r in rows] == ["intro", "pause"]
    assert [r["round"] for r in rows] == [None, None]


def test_写入摘要_折叠同义通道并去重():
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(
            ts="2026-10-05T00:00:01+00:00",
            next=["judge"],
            writes=["answered_count", "answered_questions", "current_question",
                    "difficulty", "consecutive_good", "trace_log"],
            values=vals(question={"text": "q"}, difficulty="L1", consecutive_good=0,
                        answered_questions=[], trace_log=[]),
        ),
        step(
            ts="2026-10-05T00:00:02+00:00",
            next=[],
            values=vals(answered=1, question={"text": "q", "score": {"technical_depth": 4}},
                        difficulty="L1", consecutive_good=1,
                        answered_questions=[{"text": "q"}], trace_log=[{"type": "judge"}]),
        ),
    ]
    # 顺序 = 通道表声明序（先题目、再答题，后记账），与存档里的写入顺序无关
    assert node_timeline(steps)[0]["writes"] == ["题目", "答题记录", "难度", "决策记录"]


def test_记录里写了但没真变_不进摘要():
    """节点会把 degraded_reasons / 未变的 difficulty 原样回传（健康场次也一样）——
    摘要说的是「这一步变了什么」，不是「节点返回了哪些字段」。"""
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(
            ts="2026-10-05T00:00:01+00:00", next=["intro"],
            writes=["chat_history", "degraded_reasons", "difficulty"],
            values=vals(chat_history=[], degraded_reasons=[], difficulty="L1"),
        ),
        step(
            ts="2026-10-05T00:00:02+00:00", next=[],
            values=vals(chat_history=[{"role": "assistant"}], degraded_reasons=[], difficulty="L1"),
        ),
    ]
    assert node_timeline(steps)[0]["writes"] == ["对话记录"]


def test_值变了但不在记录写入里_不进摘要():
    """异步落盘的原地变更会渗进上一步的存储值（真库实测：judge 合并回答到
    current_question，每个 pause 步的值 diff 里都凭空多出「题目」）。
    归属只认该存档记录的写入，diff 只用来去掉没真变的。"""
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(
            ts="2026-10-05T00:00:01+00:00", next=["pause"],
            writes=["user_input"],
            values=vals(question={"text": "q", "answer": None}, user_input=""),
        ),
        step(
            ts="2026-10-05T00:00:02+00:00", next=[],
            # 值里 current_question 变了（渗漏），但它不在 pause 记录的写入里
            values=vals(question={"text": "q", "answer": "我的回答"}, user_input="我的回答"),
        ),
    ]
    assert node_timeline(steps)[0]["writes"] == ["你的输入"]


def test_内部通道不进摘要():
    """LangGraph 的调度/中断/恢复痕迹不是业务状态变化。"""
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(
            ts="2026-10-05T00:00:01+00:00", next=["pause"],
            writes=["__interrupt__", "__resume__", "branch:to:judge", "user_input"],
            values=vals(user_input=""),
        ),
        step(ts="2026-10-05T00:00:02+00:00", next=[], values=vals(user_input="你好")),
    ]
    assert node_timeline(steps)[0]["writes"] == ["你的输入"]


def test_未知节点与未知通道_原值兜底不猜():
    steps = [
        step(ts="2026-10-05T00:00:00+00:00", next=["__start__"]),
        step(
            ts="2026-10-05T00:00:01+00:00", next=["新节点"], writes=["新通道"],
            values=vals(新通道="旧"),
        ),
        step(ts="2026-10-05T00:00:02+00:00", next=[], values=vals(新通道="新")),
    ]
    row = node_timeline(steps)[0]
    assert row["node"] == "新节点" and row["node_label"] == "新节点"
    assert row["writes"] == ["新通道"]


def test_节点中文名齐全():
    """图里的每个节点都要有中文名——漏一个页面就会显示英文标识符。"""
    from app.graph.rules.timeline import NODE_LABELS

    graph_nodes = {
        "intro", "profile", "ask", "judge", "followup", "advance",
        "closing_invite", "answer_candidate", "refuse_end", "report", "pause",
    }
    assert graph_nodes <= set(NODE_LABELS)
