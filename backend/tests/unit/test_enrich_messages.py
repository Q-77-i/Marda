"""富化 prompt 的域分支单测（不调 LLM）。

行为面分支是 P2-M1 第 ③ 件：那批「怎么答」的建议题 key_points 为空、覆盖率指标恒 1.0；
补的要点必须是**讲述结构**（SPEC §4.12）而不是技术知识点——分支有没有接上、
有没有挤掉算法题分支，都由这组用例钉住。
"""

from __future__ import annotations

from enrich import ALGORITHM_EXTRA, BEHAVIORAL_EXTRA, _build_messages


def _question(domain: str) -> dict:
    return {
        "question": "一道题？",
        "domain": domain,
        "topic": "话题",
        "difficulty": "L2",
        "answer": "参考答案正文。",
    }


def test_行为面走讲述要点分支():
    user = _build_messages(_question("behavioral"))[1]["content"]
    assert BEHAVIORAL_EXTRA.strip() in user
    assert "讲述结构" in user
    assert ALGORITHM_EXTRA.strip() not in user


def test_算法题仍走算法分支():
    user = _build_messages(_question("algorithms"))[1]["content"]
    assert ALGORITHM_EXTRA.strip() in user
    assert BEHAVIORAL_EXTRA.strip() not in user


def test_技术域不加额外约束():
    user = _build_messages(_question("rag"))[1]["content"]
    assert ALGORITHM_EXTRA.strip() not in user
    assert BEHAVIORAL_EXTRA.strip() not in user
