"""代码工作台消息构造单测（M15）：生产与测试共用的入口。"""

from __future__ import annotations

from app.agents.coding import MAX_HISTORY, followup_messages, review_messages
from app.agents.schemas import CodeReview


def _review() -> CodeReview:
    return CodeReview.model_validate({
        "issues": [{
            "severity": "error", "line": 3,
            "reason": "未处理空链表", "fix": "先判 head is None",
        }],
        "edge_cases": [{"input": "head=[]", "expected": "返回 None", "note": "空链表"}],
        "complexity": {"time": "O(n)", "space": "O(1)", "correct": True, "note": ""},
        "follow_up": "只有一个节点时 prev 是什么？",
    })


def test_点评消息_带题目语言代码与内部参考():
    messages = review_messages(
        question="反转链表",
        language="python",
        code="def reverse(head): ...",
        reference="迭代法：prev/curr 双指针",
        key_points=["空链表", "单节点"],
    )

    assert len(messages) == 1
    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "代码评审官" in system
    assert "反转链表" in system and "python" in system and "def reverse" in system
    assert "迭代法" in system and "空链表" in system  # 内部参考只在 prompt
    assert "具体输入与期望输出" in system  # 反例硬要求写进了模板


def test_追问消息_带上次点评历史与问题():
    history = [
        {"role": "user", "content": "为什么空链表会炸？"},
        {"role": "assistant", "content": "看 while 条件"},
    ]
    messages = followup_messages(
        question="反转链表",
        language="java",
        code="ListNode reverse(ListNode head) { ... }",
        review=_review(),
        message="那单节点呢？",
        history=history,
    )

    system = messages[0]["content"]
    assert "代码辅导官" in system
    assert "那单节点呢？" in system
    assert "为什么空链表会炸？" in system and "看 while 条件" in system
    assert "未处理空链表" in system  # 上次点评 JSON 进上下文
    assert "java" in system


def test_追问历史_只带最近N条():
    history = [
        {"role": "user", "content": f"第{i}条"} for i in range(MAX_HISTORY + 3)
    ]
    system = followup_messages(
        question="题目", language="python", code="code",
        review=_review(), message="继续", history=history,
    )[0]["content"]

    assert "第0条" not in system and "第1条" not in system and "第2条" not in system
    assert f"第{MAX_HISTORY + 2}条" in system


def test_追问消息_历史为空不炸():
    system = followup_messages(
        question="题目", language="cpp", code="int main(){}",
        review=_review(), message="继续",
    )[0]["content"]
    assert "【对话历史】\n（无）" in system


def test_点评消息_参考为空时给占位():
    system = review_messages(question="题目", language="python", code="x")[0]["content"]
    assert "（无）" in system
