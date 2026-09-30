"""人工改判表单测：改判语义（`bank`）+ 落库补齐（`apply_overrides`）。

改判表本身也是被测对象——它是**内容哈希**寻址（题干改一个字就失效），
所以「未命中必须报出来」与「条目格式合法」都要有测试兜着。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from apply_overrides import apply_to_sqlite
from bank import apply_overrides, load_overrides

from app.domain import DOMAIN_LABELS, ENABLED_DOMAINS

REPO_CURATION = (
    Path(__file__).resolve().parents[3] / "data" / "curation" / "question_overrides.json"
)


def _write(tmp_path: Path, overrides: list[dict]) -> Path:
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"overrides": overrides}, ensure_ascii=False), encoding="utf-8")
    return path


def _question(qid: str, domain: str = "agent-architecture", status: str = "enabled") -> dict:
    return {
        "question_id": qid,
        "domain": domain,
        "status": status,
        "answer": "答案够长了够长了够长了",
    }


# ---- 改判表读取 ----


def test_文件不存在等于无改判(tmp_path):
    assert load_overrides(tmp_path / "nope.json") == {}


def test_条目缺理由直接报错(tmp_path):
    """没有理由的改判，三个月后没人敢删——所以宁可当场炸。"""
    path = _write(tmp_path, [{"question_id": "q_1", "domain": "behavioral"}])

    with pytest.raises(ValueError, match="reason"):
        load_overrides(path)


# ---- 改判语义 ----


def test_换域后状态跟随重算():
    """换到未启用域（行为面）→ status 自动转 draft，不需要条目再写一遍。"""
    questions = [_question("q_1")]

    unmatched = apply_overrides(questions, {"q_1": {"domain": "behavioral", "reason": "项目叙事题"}})

    assert questions[0]["domain"] == "behavioral"
    assert questions[0]["status"] == "draft"
    assert unmatched == []


def test_显式状态优先于域推导():
    """条目直接置 draft（追问残片）时，域不动、也不因域启用而被改回 enabled。"""
    questions = [_question("q_1")]

    apply_overrides(questions, {"q_1": {"status": "draft", "reason": "追问残片"}})

    assert questions[0] == {**_question("q_1"), "status": "draft"}


def test_换到已启用域仍保持_enabled():
    """改判不等于下架：换到仍启用的域时题目照常参与出题。"""
    questions = [_question("q_1")]

    apply_overrides(questions, {"q_1": {"domain": "rag", "reason": "归域错了"}})

    assert (questions[0]["domain"], questions[0]["status"]) == ("rag", "enabled")


def test_未命中的条目必须报出来():
    """question_id 是内容哈希：题干一改条目就失效，静默失效等于题库长回原样。"""
    overrides = {
        "q_alive": {"domain": "behavioral", "reason": "项目叙事题"},
        "q_dead": {"domain": "behavioral", "reason": "项目叙事题"},
    }

    unmatched = apply_overrides([_question("q_alive")], overrides)

    assert unmatched == ["q_dead"]


def test_改判幂等():
    overrides = {"q_1": {"domain": "behavioral", "reason": "项目叙事题"}}
    questions = [_question("q_1")]

    apply_overrides(questions, overrides)
    first = dict(questions[0])
    apply_overrides(questions, overrides)

    assert questions[0] == first


# ---- 落库补齐（SQLite 侧）----


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "marda.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE questions (id TEXT PRIMARY KEY, question TEXT, answer TEXT,"
            " domain TEXT, status TEXT, user_id TEXT)"
        )
        conn.executemany(
            "INSERT INTO questions VALUES (?,?,?,?,?,NULL)",
            [
                ("q_1", "请介绍你的 Agent 项目", "答案够长了够长了够长了", "agent-architecture", "enabled"),
                ("q_2", "这套架构有什么借鉴？", "答案够长了够长了够长了", "agent-architecture", "enabled"),
                ("q_keep", "什么是 ReAct？", "答案够长了够长了够长了", "planning-reasoning", "enabled"),
            ],
        )
    return path


OVERRIDES = {
    "q_1": {"domain": "behavioral", "reason": "项目叙事题"},
    "q_2": {"status": "draft", "reason": "追问残片"},
    "q_ghost": {"domain": "behavioral", "reason": "已失效的条目"},
}


def test_改动落库且明细可读(db):
    changes, unmatched = apply_to_sqlite(db, OVERRIDES, dry_run=False)

    assert [c["question_id"] for c in changes] == ["q_1", "q_2"]
    assert changes[0]["was"] == "agent-architecture/enabled"
    assert (changes[0]["domain"], changes[0]["status"]) == ("behavioral", "draft")
    assert changes[0]["question"] == "请介绍你的 Agent 项目"  # 明细带题面，人读得懂
    assert unmatched == ["q_ghost"]
    with sqlite3.connect(db) as conn:
        rows = dict(conn.execute("SELECT id, domain || '/' || status FROM questions"))
    assert rows == {
        "q_1": "behavioral/draft",
        "q_2": "agent-architecture/draft",
        "q_keep": "planning-reasoning/enabled",  # 未改判的题一字不动
    }


def test_预演不写库(db):
    apply_to_sqlite(db, OVERRIDES, dry_run=True)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT domain FROM questions WHERE id='q_1'").fetchone()[0] == (
            "agent-architecture"
        )


def test_重复跑不改动也不报错(db):
    apply_to_sqlite(db, OVERRIDES, dry_run=False)

    changes, _ = apply_to_sqlite(db, OVERRIDES, dry_run=False)

    assert changes == []  # 幂等：第二次没有可改的


# ---- 仓库里的真实改判表 ----


def test_仓库改判表条目合法():
    """文件是手写的，键名写错会让改判静默失效——这里把格式钉死。"""
    payload = json.loads(REPO_CURATION.read_text(encoding="utf-8"))

    overrides = payload["overrides"]
    assert overrides, "改判表不该是空的"
    for item in overrides:
        assert item["question_id"].startswith("q_"), item
        assert item.get("reason"), item
        assert item.get("domain") or item.get("status"), item
        if item.get("domain"):
            assert item["domain"] in DOMAIN_LABELS, item
        if item.get("status"):
            assert item["status"] in {"enabled", "draft"}, item


def test_仓库改判表把项目叙事题移出技术域():
    """M9 的整改口径：技术域只放「不依赖候选人自述经历即可作答」的题。"""
    payload = json.loads(REPO_CURATION.read_text(encoding="utf-8"))

    for item in payload["overrides"]:
        target = item.get("domain")
        if target is None:  # 只置 draft 的（追问残片）不算换域
            continue
        assert target == "behavioral", item
        assert "behavioral" not in ENABLED_DOMAINS, "行为面一旦启用，这些题会重新入池，需重新过一遍"
