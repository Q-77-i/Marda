from pathlib import Path

import pytest

from parse_md import merge_approved_pairs, parse_file, parse_text

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_bank.md"


@pytest.fixture(scope="module")
def questions():
    return parse_file(FIXTURE)


def test_题量(questions):
    assert len(questions) == 5


def test_双重编号被清洗(questions):
    q = questions[0]
    assert q["question"] == "双重编号的问题？"
    assert q["round"] == "一面"
    assert q["round_confidence"] == "推断"
    assert q["company"] == "字节跳动"
    assert q["topic"] == "Agent 认知与架构"
    assert q["domain"] == "agent-architecture"
    assert q["difficulty"] == "L1"
    assert q["status"] == "enabled"
    assert q["answer"] == "这是答案第一段。"


def test_代码块内标题不切题且答案完整(questions):
    q = questions[1]
    assert q["question"] == "带代码块的问题？"
    # 围栏内的 `#### ` 不能切成新题
    assert len([x for x in questions if x["round"] == "一面"]) == 3
    assert "def f():" in q["answer"]
    assert "#### 这行在围栏内" in q["answer"]
    assert q["answer"].endswith("代码块之后的答案正文。")
    # 合规三要素固定为题库名；面经出处与原帖 URL 单列，便于溯源
    assert q["source"] == "个人题库-牛客补充版"
    assert q["source_detail"] == "字节跳动一面面经｜27届秋招"
    assert q["url"] == "https://www.nowcoder.com/discuss/123456"


def test_手撕算法域默认难度_L2(questions):
    q = questions[2]
    assert q["question"] == "手撕：反转链表"
    assert q["domain"] == "algorithms"
    assert q["difficulty"] == "L2"  # 覆盖 一面→L1
    assert q["status"] == "enabled"
    assert "三指针" in q["answer"]


def test_无答案原题置_draft(questions):
    q = questions[3]
    assert q["question"] == "没有答案的原题？"
    assert q["domain"] == "memory"
    assert q["answer"] == ""
    assert q["status"] == "draft"


def test_行为面域阶段一置_draft(questions):
    q = questions[4]
    assert q["domain"] == "behavioral"
    assert q["status"] == "draft"
    assert q["answer"] == "先做三年技术，再考虑带团队。"


def test_字段完整(questions):
    required = {
        "question_id", "question", "answer", "topic", "domain", "difficulty",
        "company", "round", "source", "source_detail", "license", "url", "status",
    }
    for q in questions:
        assert required <= set(q), q["question"]


def test_question_id_确定性(questions):
    again = parse_file(FIXTURE)
    assert [q["question_id"] for q in questions] == [q["question_id"] for q in again]


def test_未知_topic_报错不静默():
    bad = "# 一面\n\n## 某公司\n\n### 量子计算\n\n#### 1. 未知主题的题？\n\n答案。\n"
    with pytest.raises(ValueError, match="未知 topic"):
        parse_text(bad)


def test_白名单合并近似重复():
    """同题异写（题干细微差异 → 不同 question_id）按 APPROVED_MERGE_PAIRS 合并，择优保留。"""
    text = """# 二面

## 字节跳动

### 工程化与可观测

#### LLM 推理优化做了哪些？用过 Continuous Batching、KV Cache、vLLM 吗？线上高峰吞吐量多少？

> 【轮次：明确】

Continuous Batching：请求级动态组批，不等整个 batch 跑完就插入新请求。

#### LLM 推理优化做过哪些工作？用过 continuous batching、KV Cache、vLLM 吗？线上高峰吞吐量多少？

> 【轮次：明确】

（同上一题，原帖为同一场面试的重复记录）

答题框架：显存 → 计算 → 调度。

#### Prompt 调优遇到「修好一类、坏了另一类」怎么解决？

> 【轮次：明确】

本质是评测集不够正交——在用一组纠缠的样本做单点修补。

#### Prompt 调优 “修好一类、坏了另一类” 怎么解决？

> 【原题保留，未收录答案】
"""
    questions = parse_text(text)
    assert len(questions) == 4

    merged, report = merge_approved_pairs(questions)
    assert len(merged) == 2
    assert len(report) == 2
    # 对一：两题都 enabled，保留答案更长的"做了哪些"（答案不是占位引用）
    llm = next(q for q in merged if "推理优化" in q["question"])
    assert llm["question"].startswith("LLM 推理优化做了哪些")
    assert llm["round_confidence"] == "明确"
    # 对二：enabled 优先于无答案 draft，保留有答案的「遇到」
    prompt = next(q for q in merged if "调优" in q["question"])
    assert prompt["question"].startswith("Prompt 调优遇到")
    assert prompt["status"] == "enabled"
    # company/round 聚合去重（两对同公司同轮次，聚合结果不变）
    assert llm["company"] == prompt["company"] == "字节跳动"
    assert report[0]["dropped"][0].startswith("字节跳动/")


def test_白名单题干不存在则报错():
    """题库改动后白名单前缀匹配不到题时，宁可报错也不能静默跳过。"""
    with pytest.raises(ValueError, match="白名单"):
        merge_approved_pairs([])

