"""图片通道集成（P2-M6 FR-26）：端点校验 / 随消息发图 / 回放 / 删除清理 / 旧 checkpoint 兼容。

覆盖三条硬口径：
- **无图场次零回归**：所有 LLM 调用的消息列表只有 system（与接入前逐字一致）；
- **图跟随回答**：带图消息让评分/追问/出题节点收到附件消息，chat_history 带 image_ids；
- **旧 checkpoint 兼容**（2026-10-04 用户点名的风险）：把真实 checkpoint 改成「旧形状」
  （无 current_images 通道、题记录无 image_ids）后，get_session 与 resume 必须照常工作。
"""

from __future__ import annotations

import json
import sqlite3

from app.config import get_settings
from app.graph.graph import make_serde
from app.tools import images

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64

# 与 test_api.py 同一条最短完赛路径（2 题：1 项目 + 1 技术，每题一次深挖，2 个反问）
TURNS = ["我是应届生，做过 RAG 项目", "第一题回答……", "第一题深挖补充……",
         "第二题回答……", "第二题深挖补充……", "请问团队技术栈？", "晋升路径？"]


async def _events(response) -> list[dict]:
    out, current = [], {}
    async for line in response.aiter_lines():
        line = line.rstrip("\r")
        if not line:
            if current:
                out.append(current)
                current = {}
            continue
        if line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
    if current:
        out.append(current)
    return out


async def _create(client, question_count: int = 2) -> str:
    async with client.stream(
        "POST", "/api/interviews",
        json={"position": "Agent/AI 工程师", "question_count": question_count},
    ) as r:
        assert r.status_code == 200
        events = await _events(r)
    return events[0]["data"]["interview_id"]


async def _send(client, interview_id: str, content: str, images_list: list[str] | None = None):
    body: dict = {"content": content}
    if images_list:
        body["images"] = images_list
    async with client.stream(
        "POST", f"/api/interviews/{interview_id}/messages", json=body,
    ) as r:
        assert r.status_code == 200, r.text
        return await _events(r)


async def _upload(client, interview_id: str, data: bytes = PNG) -> str:
    r = await client.post(
        f"/api/interviews/{interview_id}/images",
        files={"file": ("code.png", data, "image/png")},
    )
    assert r.status_code == 201, r.text
    return r.json()["image_id"]


# ---- 端点：上传 / 取回 ----


async def test_上传成功返回_id_取回字节一致(client, upload_dir):
    interview_id = await _create(client)

    image_id = await _upload(client, interview_id)

    assert images.IMAGE_ID_RE.match(image_id)
    assert (upload_dir / interview_id / f"{image_id}.png").read_bytes() == PNG
    r = await client.get(f"/api/interviews/{interview_id}/images/{image_id}")
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_上传未登录401(anon_client):
    r = await anon_client.post(
        "/api/interviews/iv-any/images", files={"file": ("a.png", PNG, "image/png")}
    )
    assert r.status_code == 401


async def test_他人场次上传与取回都404(login_as):
    alice = await login_as("alice")
    bob = await login_as("bob")
    interview_id = await _create(alice)
    image_id = await _upload(alice, interview_id)

    r = await bob.post(
        f"/api/interviews/{interview_id}/images", files={"file": ("a.png", PNG, "image/png")}
    )
    assert r.status_code == 404
    r = await bob.get(f"/api/interviews/{interview_id}/images/{image_id}")
    assert r.status_code == 404


async def test_上传校验_非图片400_超限413_坏id取回404(client, upload_dir, monkeypatch):
    interview_id = await _create(client)

    r = await client.post(
        f"/api/interviews/{interview_id}/images",
        files={"file": ("a.txt", b"<html>not an image</html>", "text/plain")},
    )
    assert r.status_code == 400 and "仅支持" in r.json()["detail"]

    r = await client.post(
        f"/api/interviews/{interview_id}/images",
        files={"file": ("big.png", PNG + b"\x00" * images.MAX_IMAGE_BYTES, "image/png")},
    )
    assert r.status_code == 413

    r = await client.get(f"/api/interviews/{interview_id}/images/{'a' * 32}")
    assert r.status_code == 404
    r = await client.get(f"/api/interviews/{interview_id}/images/../../etc/passwd")
    assert r.status_code in (404, 400)  # 路径穿越不可能命中文件


async def test_每场数量上限(client, monkeypatch):
    monkeypatch.setattr(images, "MAX_IMAGES_PER_INTERVIEW", 1)
    interview_id = await _create(client)
    await _upload(client, interview_id)

    r = await client.post(
        f"/api/interviews/{interview_id}/images",
        files={"file": ("a.png", JPEG, "image/jpeg")},
    )
    assert r.status_code == 400 and "上限" in r.json()["detail"]


async def test_上传已结束场次409(client):
    interview_id = await _create(client)
    for turn in TURNS:
        await _send(client, interview_id, turn)

    r = await client.post(
        f"/api/interviews/{interview_id}/images", files={"file": ("a.png", PNG, "image/png")}
    )
    assert r.status_code == 409


# ---- 随消息发图 ----


async def test_消息带图_节点收到附件_历史带image_ids(client, fake_llm, upload_dir):
    interview_id = await _create(client)
    image_id = await _upload(client, interview_id)

    # 图带在自我介绍上 → 提炼节点收附件；「第一题回答」再带一张 → 评分/追问/下一题
    await _send(client, interview_id, TURNS[0], [image_id])
    image_id2 = await _upload(client, interview_id, JPEG)
    await _send(client, interview_id, TURNS[1], [image_id2])

    def _attachment(messages: list[dict]) -> list[dict] | None:
        if len(messages) < 2:
            return None
        last = messages[-1]
        if last.get("role") == "user" and isinstance(last.get("content"), list):
            return last["content"]
        return None

    profile_call = next(c for c in fake_llm.calls if "提炼" in c["system"])
    assert _attachment(profile_call["messages"]) is not None  # 自我介绍带图 → 提炼收附件

    judge_calls = [c for c in fake_llm.calls if "评分官" in c["system"]]
    assert _attachment(judge_calls[0]["messages"]) is not None  # 评分官看图
    # 图是 data URI 编码（探针实测协议：image_url + base64 data URI）
    part = _attachment(judge_calls[0]["messages"])[1]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/jpeg;base64,")

    # 追问文案模板（开场白也含「追问」二字，得用深挖模板的专属措辞过滤）
    followup_calls = [c for c in fake_llm.calls if "深挖追问" in c["system"]]
    assert _attachment(followup_calls[0]["messages"]) is not None  # 追问结合图

    # 回放数据源：user 消息带 image_ids；面试官消息照旧
    session = (await client.get(f"/api/interviews/{interview_id}")).json()
    user_entries = [m for m in session["chat_history"] if m["role"] == "user"]
    assert user_entries[0]["image_ids"] == [image_id]
    assert user_entries[1]["image_ids"] == [image_id2]


async def test_无图场次_消息列表只有system_与接入前逐字一致(client, fake_llm):
    interview_id = await _create(client)
    for turn in TURNS[:3]:
        await _send(client, interview_id, turn)

    assert fake_llm.calls, "至少要有几次 LLM 调用"
    for call in fake_llm.calls:
        assert all(m["role"] == "system" for m in call["messages"]), "无图路径不许出现附件消息"

    session = (await client.get(f"/api/interviews/{interview_id}")).json()
    for entry in session["chat_history"]:
        assert "image_ids" not in entry, "无图场次的 chat_history 条目与接入前逐字一致"


async def test_消息带未知或坏格式图片400(client):
    interview_id = await _create(client)

    r = await client.post(
        f"/api/interviews/{interview_id}/messages",
        json={"content": "见截图", "images": ["a" * 32]},  # 未上传过的合法格式 id
    )
    assert r.status_code == 400 and "图片不存在或已失效" in r.json()["detail"]

    r = await client.post(
        f"/api/interviews/{interview_id}/messages",
        json={"content": "见截图", "images": ["../../etc/passwd"]},
    )
    assert r.status_code == 400


async def test_消息图片数量超限422(client):
    interview_id = await _create(client)

    r = await client.post(
        f"/api/interviews/{interview_id}/messages",
        json={"content": "见截图", "images": ["a" * 32] * (images.MAX_IMAGES_PER_MESSAGE + 1)},
    )
    assert r.status_code == 422


# ---- 报告计数 / 删除清理 / 回放 ----


async def test_完赛后报告逐题图片计数_回放仍可取图(client, upload_dir):
    interview_id = await _create(client)
    image_id = await _upload(client, interview_id)
    await _send(client, interview_id, TURNS[0])
    await _send(client, interview_id, TURNS[1], [image_id])  # 第一题带图
    for turn in TURNS[2:]:
        await _send(client, interview_id, turn)

    report = (await client.get(f"/api/interviews/{interview_id}/report")).json()["report"]
    counts = [row["image_count"] for row in report["per_question_comments"]]
    assert counts[0] == 1 and sum(counts) == 1  # 只有带图那题计数，其余 0

    # 已结束场次回放仍能取图（取回端点只校验归属，不限进行中）
    r = await client.get(f"/api/interviews/{interview_id}/images/{image_id}")
    assert r.status_code == 200


async def test_删除场次连带清理图片(client, upload_dir):
    interview_id = await _create(client)
    image_id = await _upload(client, interview_id)
    assert (upload_dir / interview_id).is_dir()

    r = await client.delete(f"/api/interviews/{interview_id}")
    assert r.status_code == 204

    assert not (upload_dir / interview_id).exists()
    assert images.image_path(upload_dir, interview_id, image_id) is None


# ---- 旧 checkpoint 兼容（用户点名风险：字段加默认值但反序列化拒绝）----


async def test_旧形状checkpoint_可恢复且视为无图(client, fake_llm):
    """把真实 checkpoint 改造成 P2-M6 之前的形状：删掉 current_images 通道、
    题记录里删掉 image_ids 键 → get_session 与 resume 必须照常（默认值补位）。"""
    interview_id = await _create(client)
    await _send(client, interview_id, TURNS[0])
    await _send(client, interview_id, TURNS[1])  # 攒出一题记录

    _strip_new_fields_from_checkpoint(interview_id)

    session = (await client.get(f"/api/interviews/{interview_id}")).json()
    assert session["answered_count"] == 1  # 旧 checkpoint 读得出来
    assert all("image_ids" not in entry for entry in session["chat_history"])

    events = await _send(client, interview_id, "深挖补充……")  # resume 照常推进
    assert any(e.get("event") == "delta" for e in events)
    session = (await client.get(f"/api/interviews/{interview_id}")).json()
    assert session["answered_count"] == 1  # 追问轮不增计数（沿用既有语义）
    # 旧记录补默认后仍参与后续（追问决策/评分都读过它，没炸就是兼容）
    assert any("评分官" in c["system"] for c in fake_llm.calls)


def _strip_new_fields_from_checkpoint(interview_id: str) -> None:
    """直改 checkpointer 库：把最新一条 checkpoint 改成「无 P2-M6 字段」的旧形状。"""
    serde = make_serde()
    path = get_settings().checkpoint_db_path
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT checkpoint_ns, checkpoint_id, type, checkpoint FROM checkpoints "
            "WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1",
            (interview_id,),
        ).fetchone()
        assert row is not None, "checkpoint 未写入"
        checkpoint_ns, checkpoint_id, type_, blob = row
        cp = serde.loads_typed((type_, blob))

        values = cp["channel_values"]
        values.pop("current_images", None)  # 新通道整体拿掉
        records = values.get("answered_questions") or []
        values["answered_questions"] = [
            {k: v for k, v in record.model_dump().items() if k != "image_ids"}
            for record in records
        ]
        cp["channel_versions"].pop("current_images", None)
        for seen in cp.get("versions_seen", {}).values():
            seen.pop("current_images", None)

        new_type, new_blob = serde.dumps_typed(cp)
        conn.execute(
            "UPDATE checkpoints SET type = ?, checkpoint = ? "
            "WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
            (new_type, new_blob, interview_id, checkpoint_ns, checkpoint_id),
        )
        conn.commit()
