"""难度重标注（P2-M1）单测：读表 / 应用（`bank`）+ 落库补齐（`apply_difficulty`）。

与 test_curation 同款纪律：重标注表是**内容哈希**寻址（题干改一个字就失效），
所以「未命中必须报出来」与「非法档位必须报错」都要有测试兜着；
仓库里那份真表本身也是被测对象（格式、档位取值）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from apply_difficulty import apply_to_sqlite
from bank import DIFFICULTY_LEVELS, apply_difficulty, load_difficulty

REPO_ANNOTATIONS = (
    Path(__file__).resolve().parents[3] / "data" / "curation" / "difficulty_annotations.json"
)


def _write(tmp_path: Path, levels: dict[str, str]) -> Path:
    path = tmp_path / "difficulty.json"
    path.write_text(json.dumps({"levels": levels}, ensure_ascii=False), encoding="utf-8")
    return path


def _db(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    path = tmp_path / "questions.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE questions (id TEXT PRIMARY KEY, difficulty TEXT NOT NULL)")
        conn.executemany("INSERT INTO questions (id, difficulty) VALUES (?, ?)", rows)
    return path


# ---- 读表 ----


def test_文件不存在等于无标注(tmp_path):
    assert load_difficulty(tmp_path / "nope.json") == {}


def test_非法档位直接报错(tmp_path):
    """三档是引擎的难度序，混进别的值会在出题端静默抽不到题。"""
    path = _write(tmp_path, {"q_aaa": "L4"})
    with pytest.raises(ValueError, match="非法档位"):
        load_difficulty(path)


def test_读表返回_id_到档位(tmp_path):
    path = _write(tmp_path, {"q_aaa": "L1", "q_bbb": "L3"})
    assert load_difficulty(path) == {"q_aaa": "L1", "q_bbb": "L3"}


# ---- 应用 ----


def _question(qid: str, difficulty: str = "L2") -> dict:
    return {"question_id": qid, "difficulty": difficulty, "status": "enabled"}


def test_按_id_覆盖难度():
    questions = [_question("q_aaa", "L2"), _question("q_bbb", "L1")]
    unmatched = apply_difficulty(questions, {"q_aaa": "L3"})
    assert unmatched == []
    assert [q["difficulty"] for q in questions] == ["L3", "L1"]  # 未标注的不动


def test_未命中的条目必须报出来():
    """题干一改 id 就变，条目失效不能静默。"""
    unmatched = apply_difficulty([_question("q_aaa")], {"q_aaa": "L1", "q_gone": "L2"})
    assert unmatched == ["q_gone"]


# ---- 落库补齐（apply_difficulty.py）----


def test_改动落库且明细可读(tmp_path):
    db = _db(tmp_path, [("q_aaa", "L2"), ("q_bbb", "L2")])
    changes, unmatched = apply_to_sqlite(db, {"q_aaa": "L3"}, dry_run=False)
    assert unmatched == []
    assert changes == [{"question_id": "q_aaa", "level": "L3", "was": "L2"}]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT difficulty FROM questions WHERE id='q_aaa'").fetchone()[0] == "L3"


def test_预演不写库(tmp_path):
    db = _db(tmp_path, [("q_aaa", "L2")])
    changes, _ = apply_to_sqlite(db, {"q_aaa": "L1"}, dry_run=True)
    assert len(changes) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT difficulty FROM questions WHERE id='q_aaa'").fetchone()[0] == "L2"


def test_分块后未命中判定不受分块影响(tmp_path, monkeypatch):
    """回归（P2-M1 实测踩到）：分块查询时若把整表 levels 传给单块，未命中会把全表都算进去。

    1002 条跑起来就是「未命中 1002 条」的假警报——不静默的报错机制自己失真，比不报还坏。
    """
    import apply_difficulty as module

    monkeypatch.setattr(module, "CHUNK", 2)
    db = _db(tmp_path, [("q_aaa", "L2"), ("q_bbb", "L2"), ("q_ccc", "L2")])
    changes, unmatched = apply_to_sqlite(
        db, {"q_aaa": "L1", "q_bbb": "L1", "q_ccc": "L1", "q_gone": "L3"}, dry_run=False
    )
    assert unmatched == ["q_gone"]
    assert len(changes) == 3


def test_重复跑不改动也不报错(tmp_path):
    db = _db(tmp_path, [("q_aaa", "L2")])
    apply_to_sqlite(db, {"q_aaa": "L1"}, dry_run=False)
    changes, unmatched = apply_to_sqlite(db, {"q_aaa": "L1"}, dry_run=False)
    assert changes == [] and unmatched == []


# ---- 仓库里的真表 ----


def test_仓库难度标注表条目合法():
    payload = json.loads(REPO_ANNOTATIONS.read_text(encoding="utf-8"))
    levels = payload["levels"]
    assert levels, "标注表不该为空"
    assert all(value in DIFFICULTY_LEVELS for value in levels.values())
    assert all(qid.startswith("q_") for qid in levels)
    # 三档都要有——全堆在 L2 正是这次要修的毛病
    assert set(levels.values()) == set(DIFFICULTY_LEVELS)
