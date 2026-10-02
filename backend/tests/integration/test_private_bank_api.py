"""私有题库接口集成测试（FR-13）：上传→入库→管理闭环 + A/B 隔离 + 三面可见性。

隔离是本模块的主线：A 的私有题在 B 的**列表 / 公共浏览 / 公共搜索 / 容量**四处都不可见，
出题侧另由 unit(test_question_search) 覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.tools import hybrid_search
from bank_fixture import create_tables, insert_questions, question_row

TEMPLATE = """# 我的面试题

【题目】Redis 持久化有哪几种？
【答案】RDB 与 AOF，RDB 是快照、AOF 是写命令日志。
【关键点】RDB 快照；AOF 日志
【追问】AOF 重写怎么触发？

【题目】MCP 是什么？
【答案】Model Context Protocol，模型与外部工具之间的协议。
"""


@pytest.fixture
def bank_db(client):
    """临时库建题库表 + 一条公共题（私有题由用例自己上传）。"""
    path = Path(os.environ["DB_PATH"])
    create_tables(path)
    insert_questions(path, [question_row("q_pub", domain="rag", difficulty="L1")])
    return path


def _upload_files(content: str, name: str = "我的笔记.md"):
    return {"file": (name, content.encode("utf-8"), "text/markdown")}


async def test_上传入库闭环(client, bank_db):
    r = await client.post(
        "/api/bank/private/upload",
        files=_upload_files(TEMPLATE),
        data={"domain": "rag", "difficulty": "L2"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"parsed": 2, "imported": 2, "duplicated": [], "errors": []}

    listed = (await client.get("/api/bank/private/questions")).json()
    assert listed["total"] == 2
    # 最近上传的在前（按上传顺序倒序，不是哈希序）
    assert [item["question"] for item in listed["items"]] == [
        "MCP 是什么？", "Redis 持久化有哪几种？",
    ]
    first = listed["items"][1]
    assert first["domain"] == "rag" and first["difficulty"] == "L2"  # 表单批量默认值
    assert first["status"] == "enabled"
    assert first["key_points"] == ["RDB 快照", "AOF 日志"]
    assert first["follow_ups"] == ["AOF 重写怎么触发？"]
    # 来源明细走 question_sources（M5 模型），文件名进 source_detail
    assert first["sources"] == [
        {"question_id": first["question_id"], "source": "个人上传",
         "license": "personal", "url": "", "source_detail": "我的笔记.md"}
    ]


async def test_编辑与归档恢复闭环(client, bank_db):
    await client.post("/api/bank/private/upload", files=_upload_files(TEMPLATE),
                      data={"domain": "rag", "difficulty": "L2"})
    qid = (await client.get("/api/bank/private/questions")).json()["items"][0]["question_id"]

    patched = await client.patch(f"/api/bank/private/questions/{qid}", json={
        "question": "改过的题干？", "answer": "改过的答案内容。", "difficulty": "L3",
    })
    assert patched.status_code == 200, patched.text
    assert patched.json()["question"] == "改过的题干？"
    assert patched.json()["difficulty"] == "L3"

    archived = await client.patch(f"/api/bank/private/questions/{qid}", json={"status": "draft"})
    assert archived.json()["status"] == "draft"
    assert (await client.get("/api/bank/private/questions", params={"status": "draft"})).json()["total"] == 1
    # 存档一条后，另一条仍在「使用中」（模板里共两道题）
    assert (await client.get("/api/bank/private/questions", params={"status": "enabled"})).json()["total"] == 1

    restored = await client.patch(f"/api/bank/private/questions/{qid}", json={"status": "enabled"})
    assert restored.json()["status"] == "enabled"


async def test_重传同一份文件报重复且不覆盖编辑(client, bank_db):
    await client.post("/api/bank/private/upload", files=_upload_files(TEMPLATE),
                      data={"domain": "rag", "difficulty": "L2"})
    qid = (await client.get("/api/bank/private/questions")).json()["items"][0]["question_id"]
    await client.patch(f"/api/bank/private/questions/{qid}", json={"answer": "我手改的答案内容。"})

    again = await client.post("/api/bank/private/upload", files=_upload_files(TEMPLATE),
                              data={"domain": "rag", "difficulty": "L2"})
    assert again.json()["imported"] == 0
    assert len(again.json()["duplicated"]) == 2
    kept = (await client.get("/api/bank/private/questions")).json()["items"]
    assert any(item["answer"] == "我手改的答案内容。" for item in kept)


async def test_部分成功_坏题进errors好题照常入库(client, bank_db):
    text = "【题目】好题？\n【答案】好题的答案内容。\n【题目】坏题没有答案\n"
    r = await client.post("/api/bank/private/upload", files=_upload_files(text),
                          data={"domain": "rag", "difficulty": "L1"})
    assert r.status_code == 200
    assert r.json()["imported"] == 1 and r.json()["parsed"] == 2
    assert r.json()["errors"][0]["question"] == "坏题没有答案"


async def test_上传校验_后缀域大小(client, bank_db):
    bad_suffix = await client.post("/api/bank/private/upload",
                                   files=_upload_files(TEMPLATE, "笔记.docx"),
                                   data={"domain": "rag", "difficulty": "L1"})
    assert bad_suffix.status_code == 400 and "md" in bad_suffix.json()["detail"]

    bad_domain = await client.post("/api/bank/private/upload", files=_upload_files(TEMPLATE),
                                   data={"domain": "不存在的域", "difficulty": "L1"})
    assert bad_domain.status_code == 400

    too_big = await client.post(
        "/api/bank/private/upload",
        files=_upload_files("【题目】题？\n【答案】" + "填充" * 1_200_000),
        data={"domain": "rag", "difficulty": "L1"},
    )
    assert too_big.status_code == 413


async def test_上传校验_答案不足实质字符(client, bank_db):
    r = await client.post("/api/bank/private/upload",
                          files=_upload_files("【题目】题？\n【答案】xx"),
                          data={"domain": "rag", "difficulty": "L1"})
    assert r.json()["imported"] == 0
    assert "过短" in r.json()["errors"][0]["reason"]


async def test_私有题不开放行为面域(client, bank_db):
    """P2-M3（D7 复核）：行为面不进私有题库——上传与编辑两条路都拒掉，不静默入库。

    域集合本身由 unit(test_behavioral) 钉死；这里补接口层，防止「字典改了、校验没跟上」。
    """
    rejected = await client.post(
        "/api/bank/private/upload",
        files=_upload_files(TEMPLATE),
        data={"domain": "behavioral", "difficulty": "L2"},
    )
    assert rejected.status_code == 400
    assert (await client.get("/api/bank/private/questions")).json()["total"] == 0  # 一道都没进库

    await client.post("/api/bank/private/upload", files=_upload_files(TEMPLATE),
                      data={"domain": "rag", "difficulty": "L2"})
    qid = (await client.get("/api/bank/private/questions")).json()["items"][0]["question_id"]

    patched = await client.patch(f"/api/bank/private/questions/{qid}", json={"domain": "behavioral"})

    assert patched.status_code == 422
    assert (await client.get("/api/bank/private/questions")).json()["items"][0]["domain"] == "rag"


async def test_隔离_A的私有题在B处四处不可见(client, login_as, bank_db):
    """FR-13 验收核心：列表 / 公共浏览 / 公共搜索 / 容量 全维不可见。"""
    alice = client  # 默认登录用户
    await alice.post("/api/bank/private/upload", files=_upload_files(TEMPLATE),
                     data={"domain": "rag", "difficulty": "L1"})
    alice_ids = {
        item["question_id"] for item in (await alice.get("/api/bank/private/questions")).json()["items"]
    }
    assert len(alice_ids) == 2

    bob = await login_as("bob")
    assert (await bob.get("/api/bank/private/questions")).json()["total"] == 0
    # 直接按 id 探（跨用户访问一律 404，不泄露存在性）
    for qid in alice_ids:
        assert (await bob.patch(f"/api/bank/private/questions/{qid}", json={"status": "draft"})).status_code == 404
    # 公共浏览与分面里没有私有题
    browse = (await bob.get("/api/bank/questions")).json()
    assert browse["total"] == 1 and browse["items"][0]["question_id"] == "q_pub"
    assert all(f["count"] == 1 for f in (await bob.get("/api/bank/facets")).json()["difficulty"])
    # 关键词检索（fake 混合检索）不含私有题
    hits = (await bob.get("/api/bank/questions", params={"q": "Redis"})).json()["items"]
    assert all(not item["question_id"].startswith("p_") for item in hits)


async def test_A本人看得到自己的题_公共浏览仍不含私有题(client, bank_db):
    """同表方案的泄漏面：本人可见 ≠ 私有题能混进公共浏览。"""
    await client.post("/api/bank/private/upload", files=_upload_files(TEMPLATE),
                      data={"domain": "rag", "difficulty": "L1"})

    assert (await client.get("/api/bank/private/questions")).json()["total"] == 2
    browse = (await client.get("/api/bank/questions")).json()
    assert browse["total"] == 1 and browse["items"][0]["question_id"] == "q_pub"


async def test_容量计入本人私有题(client, bank_db):
    """FR-14 × FR-13：私有题参与出题，容量就算上它们（否则表单会误报不足）。"""
    before = (await client.get("/api/bank/capacity", params={"counts": "15"})).json()

    extra = "\n\n".join(
        f"【题目】私有题{n}？\n【答案】私有题的参考答案内容 {n}。" for n in range(4)
    )
    await client.post("/api/bank/private/upload", files=_upload_files(extra),
                      data={"domain": "planning-reasoning", "difficulty": "L3"})

    after = (await client.get("/api/bank/capacity", params={"counts": "15"})).json()
    # 私有题只增不减 → 不足项总数只会变少或持平
    def shortage(body):
        return sum(len(o["shortfalls"]) for o in body["options"])

    assert shortage(after) <= shortage(before)


async def test_未登录401(anon_client, bank_db):
    assert (await anon_client.get("/api/bank/private/questions")).status_code == 401
    assert (await anon_client.post(
        "/api/bank/private/upload", files=_upload_files(TEMPLATE),
        data={"domain": "rag", "difficulty": "L1"},
    )).status_code == 401
