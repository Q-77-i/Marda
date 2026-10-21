"""代码工作台 API 集成测试（M15）：随机题安全投影 / 点评 / 追问 / 错误路径。"""

from __future__ import annotations

import pytest

from app import llm
from app.config import get_settings
from bank_fixture import create_tables, insert_questions, question_row

REVIEW_BODY = {
    "issues": [{
        "severity": "error", "line": 3,
        "reason": "未处理空链表", "fix": "先判 head is None",
    }],
    "edge_cases": [{"input": "head=[]", "expected": "返回 None", "note": "空链表"}],
    "complexity": {"time": "O(n)", "space": "O(1)", "correct": True, "note": ""},
    "follow_up": "只有一个节点时返回什么？",
}


def seed_algorithm_question(question_id: str = "q_algo", **kwargs) -> str:
    """在 tmp 业务库里建题库表并造一道算法题（业务库默认没有 questions 表）。"""
    path = get_settings().db_path
    create_tables(path)
    insert_questions(path, [question_row(question_id, domain="algorithms", **kwargs)])
    return question_id


async def test_随机题需要登录(anon_client):
    r = await anon_client.get("/api/coding/questions/random")
    assert r.status_code == 401


async def test_随机题只返回安全字段(client):
    seed_algorithm_question()

    r = await client.get("/api/coding/questions/random")

    assert r.status_code == 200
    item = r.json()
    assert set(item) == {
        "question_id", "text", "description", "examples", "constraints",
        "topic", "difficulty", "domain",
    }
    assert item["domain"] == "algorithms"
    assert "答案" not in item and "key_points" not in item
    # 不在自建目录里的题：题面字段为空，前端只显示题库原文
    assert item["description"] == "" and item["examples"] == []


async def test_随机题_目录内的题带完整题面(client):
    """M15 补强：题库里的算法题只有标题也能做——自建目录补描述/示例/约束。"""
    seed_algorithm_question("q_346abeed8c62")

    r = await client.get("/api/coding/questions/random")

    assert r.status_code == 200
    item = r.json()
    assert item["text"] == "反转链表"
    assert "反转" in item["description"]
    assert item["examples"] and "输入" in item["examples"][0]
    assert item["constraints"]


async def test_随机题_没有算法题时404(client):
    create_tables(get_settings().db_path)

    r = await client.get("/api/coding/questions/random")

    assert r.status_code == 404
    assert "算法题" in r.json()["detail"]


async def test_代码点评_happy_path(client, fake_llm):
    seed_algorithm_question()

    r = await client.post("/api/coding/reviews", json={
        "question_id": "q_algo",
        "language": "python",
        "code": "def reverse(head):\n    while head:\n        head = head.next\n",
    })

    assert r.status_code == 200
    body = r.json()
    assert body["question_id"] == "q_algo"
    assert body["review"]["edge_cases"][0]["input"] == "head=[]"
    assert body["review"]["follow_up"]
    # 内部参考只进 prompt（题目答案 shape 见 bank_fixture），不出 API
    assert "答案" not in body["review"]
    system = fake_llm.calls[-1]["system"]
    assert "代码评审官" in system
    assert "q_algo 答案" in system  # 内部参考
    assert "head = head.next" in system


async def test_代码点评_目录内的题用完整题面(client, fake_llm):
    seed_algorithm_question("q_346abeed8c62")

    r = await client.post("/api/coding/reviews", json={
        "question_id": "q_346abeed8c62",
        "language": "python",
        "code": "def reverse(head):\n    return head\n",
    })

    assert r.status_code == 200
    system = fake_llm.calls[-1]["system"]
    assert "【反转链表】" in system
    assert "输入" in system and "输出" in system
    assert "约束" in system


async def test_代码点评_题目不存在404(client):
    create_tables(get_settings().db_path)

    r = await client.post("/api/coding/reviews", json={
        "question_id": "q_missing", "language": "python", "code": "x = 1",
    })

    assert r.status_code == 404


async def test_代码点评_别的域的题404(client):
    path = get_settings().db_path
    create_tables(path)
    insert_questions(path, [question_row("q_rag", domain="rag")])

    r = await client.post("/api/coding/reviews", json={
        "question_id": "q_rag", "language": "python", "code": "x = 1",
    })

    assert r.status_code == 404


@pytest.mark.parametrize("language", ["ruby", "go", ""])
async def test_代码点评_非法语言422(client, language):
    r = await client.post("/api/coding/reviews", json={
        "question_id": "q_algo", "language": language, "code": "x = 1",
    })
    assert r.status_code == 422


async def test_代码点评_超长代码422(client):
    r = await client.post("/api/coding/reviews", json={
        "question_id": "q_algo", "language": "python", "code": "x" * 8001,
    })
    assert r.status_code == 422


async def test_代码点评_空白代码400(client):
    seed_algorithm_question()

    r = await client.post("/api/coding/reviews", json={
        "question_id": "q_algo", "language": "python", "code": "   ",
    })

    assert r.status_code == 400
    assert "不能为空" in r.json()["detail"]


async def test_代码点评_LLM失败503中文(client, monkeypatch):
    seed_algorithm_question()

    async def boom(*args, **kwargs):
        raise llm.LLMError("upstream down", retryable=True)

    monkeypatch.setattr(llm, "chat_json", boom)

    r = await client.post("/api/coding/reviews", json={
        "question_id": "q_algo", "language": "python", "code": "x = 1",
    })

    assert r.status_code == 503
    assert isinstance(r.json()["detail"], str) and "暂时不可用" in r.json()["detail"]


async def test_代码追问_happy_path(client, fake_llm):
    seed_algorithm_question()

    r = await client.post("/api/coding/followups", json={
        "question_id": "q_algo",
        "language": "java",
        "code": "ListNode reverse(ListNode head) { return head; }",
        "review": REVIEW_BODY,
        "message": "空链表为什么有问题？",
        "history": [{"role": "user", "content": "先问一句"}],
    })

    assert r.status_code == 200
    assert r.json()["answer"] == "先检查空链表和单节点，再进入双指针循环。"
    system = fake_llm.calls[-1]["system"]
    assert "代码辅导官" in system
    assert "空链表为什么有问题？" in system
    assert "先问一句" in system


async def test_代码追问_历史超限422(client):
    history = [{"role": "user", "content": f"第{i}条"} for i in range(11)]

    r = await client.post("/api/coding/followups", json={
        "question_id": "q_algo", "language": "python", "code": "x = 1",
        "review": REVIEW_BODY, "message": "继续", "history": history,
    })

    assert r.status_code == 422


async def test_代码追问_LLM失败503(client, monkeypatch):
    seed_algorithm_question()

    async def boom(*args, **kwargs):
        raise llm.LLMError("upstream down", retryable=True)

    monkeypatch.setattr(llm, "chat", boom)

    r = await client.post("/api/coding/followups", json={
        "question_id": "q_algo", "language": "python", "code": "x = 1",
        "review": REVIEW_BODY, "message": "继续", "history": [],
    })

    assert r.status_code == 503
    assert "暂时不可用" in r.json()["detail"]
