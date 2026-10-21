"""自建算法题面目录（M15 补强）：id / 标题 / 描述 / 示例的完整性。"""

from __future__ import annotations

import re

from app.agents.coding_questions import CODING_PROBLEMS, format_problem


def test_目录每道题都有标题描述与示例():
    assert len(CODING_PROBLEMS) >= 10
    for question_id, problem in CODING_PROBLEMS.items():
        assert re.fullmatch(r"q_[0-9a-f]{12}", question_id), question_id
        assert problem.title.strip()
        assert len(problem.description) >= 20, question_id
        assert problem.examples, question_id
        for example in problem.examples:
            assert "输入" in example and "输出" in example, question_id


def test_目录标题不重复():
    titles = [problem.title for problem in CODING_PROBLEMS.values()]
    assert len(titles) == len(set(titles))


def test_题面格式化含标题描述示例与约束():
    text = format_problem(CODING_PROBLEMS["q_346abeed8c62"])

    assert "【反转链表】" in text
    assert "输入" in text and "输出" in text
    assert "约束" in text
