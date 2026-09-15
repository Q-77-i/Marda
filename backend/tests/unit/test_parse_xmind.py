import json
import zipfile

import pytest

from parse_md import parse_text
from parse_xmind import parse_xmind


def _topic(title, children=None, notes=None):
    node = {"title": title}
    if children:
        node["children"] = {"attached": children}
    if notes:
        node["notes"] = {"plain": {"content": notes}}
    return node


SHEET = {
    "title": "一面",
    # 树：root → 轮次 → 公司 → 主题 → 题目（叶子）
    "rootTopic": _topic(
        "Agent面试",
        [
            _topic(
                "一面",
                [
                    _topic(
                        "字节跳动",
                        [
                            _topic(
                                "Agent 认知与架构",
                                [_topic("1. 1. 双重编号？", notes="【轮次：推断】依据：测试夹具\n\n这是答案。")],
                            ),
                            _topic(
                                "手撕算法",
                                [
                                    _topic(
                                        "手撕：反转链表",
                                        notes=(
                                            "【轮次：明确】｜来源：快手 Agent 岗一面面经（牛客）"
                                            " https://www.nowcoder.com/discuss/1\n\n"
                                            "```python\n# 三指针\ndef reverse(h): ...\n```"
                                        ),
                                    )
                                ],
                            ),
                        ],
                    )
                ],
            )
        ],
    ),
}


@pytest.fixture
def xmind_file(tmp_path):
    path = tmp_path / "sample.xmind"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.json", json.dumps([SHEET], ensure_ascii=False))
    return path


def test_层级与字段(xmind_file):
    questions = parse_xmind(xmind_file)
    assert len(questions) == 2

    first, second = questions
    assert first["question"] == "双重编号？"
    assert (first["round"], first["company"], first["topic"]) == ("一面", "字节跳动", "Agent 认知与架构")
    assert (first["domain"], first["difficulty"]) == ("agent-architecture", "L1")
    assert first["round_confidence"] == "推断"
    # notes 首行的元信息不能混进答案
    assert first["answer"] == "这是答案。"

    assert second["domain"] == "algorithms"
    assert second["difficulty"] == "L2"  # 手撕覆盖 一面→L1
    assert second["url"] == "https://www.nowcoder.com/discuss/1"
    assert "三指针" in second["answer"]


def test_无元信息不加下划线(xmind_file):
    """xmind 的 notes 首行【轮次：…】没有 `> ` 前缀，也要认出来。"""
    questions = parse_xmind(xmind_file)
    assert all(q["round_confidence"] in ("明确", "推断") for q in questions)
    assert all("【轮次：" not in q["answer"] for q in questions)


def test_跨格式同题干同_id(xmind_file):
    """md 与 xmind 对同一题干必须产出同一 question_id——跨格式对账的前提。"""
    md_questions = parse_text(
        "# 一面\n\n## 字节跳动\n\n### Agent 认知与架构\n\n"
        "#### 1. 1. 双重编号？\n\n> 【轮次：推断】依据：测试夹具\n\n这是答案。\n"
    )
    xmind_questions = parse_xmind(xmind_file)
    assert md_questions[0]["question_id"] == xmind_questions[0]["question_id"]
