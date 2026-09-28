from pathlib import Path

import pytest

from parse_md import (
    SOURCE_NAME,
    make_id,
    merge_approved_pairs,
    merge_exact_duplicates,
    parse_file,
    parse_text,
    source_rank,
)

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
    assert q["sources"] == [
        {
            "source": "个人题库-牛客补充版",
            "license": "personal",
            "url": "https://www.nowcoder.com/discuss/123456",
            "source_detail": "字节跳动一面面经｜27届秋招",
        }
    ]


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
    """来源四要素进 sources 明细（SPEC §8.1 拆表）；顶层只留主源 source。"""
    required = {
        "question_id", "question", "answer", "topic", "domain", "difficulty",
        "company", "round", "source", "sources", "status",
    }
    for q in questions:
        assert required <= set(q), q["question"]
        assert not ({"source_detail", "license", "url"} & set(q)), "来源字段不应留在题目顶层"
        assert q["sources"], q["question"]
        for record in q["sources"]:
            assert {"source", "license", "url", "source_detail"} <= set(record)


def make_record(
    *,
    source: str,
    answer: str,
    source_detail: str = "",
    status: str = "enabled",
    round_confidence: str = "明确",
    question: str = "什么是 Agent 的记忆分层？",
) -> dict:
    """构造一条「某来源提供的题目记录」——题干相同 → question_id 相同（跨源同题）。"""
    return {
        "question_id": make_id(question),
        "question": question,
        "answer": answer,
        "topic": "Memory",
        "domain": "memory",
        "difficulty": "L2",
        "company": "字节跳动",
        "round": "一面",
        "round_confidence": round_confidence,
        "source": source,
        "sources": [
            {
                "source": source,
                "license": "personal" if source == SOURCE_NAME else "MIT",
                "url": "",
                "source_detail": source_detail,
            }
        ],
        "status": status,
    }


def test_主源裁决_个人题库优先():
    """SPEC §8.1：主源优先级 > 答案质量——开源源答案再长也不能顶替个人题库。"""
    wenqu = make_record(source="ai-agent-interview-guide", answer="开源答案，更长更啰嗦" * 5)
    personal = make_record(source=SOURCE_NAME, answer="个人题库答案")

    merged, _ = merge_exact_duplicates([wenqu, personal])  # 输入序故意把开源源放前面

    assert len(merged) == 1
    assert merged[0]["source"] == SOURCE_NAME
    assert merged[0]["answer"] == "个人题库答案"
    assert [s["source"] for s in merged[0]["sources"]] == [SOURCE_NAME, "ai-agent-interview-guide"]
    assert source_rank(SOURCE_NAME) < source_rank("ai-agent-interview-guide")


def test_主源裁决_同优先级按可用与答案长度():
    """未登记源同档：能用 > 答案长（沿用同题择优的既有哲学）。"""
    draft = make_record(source="源甲", answer="很长的答案" * 20, status="draft")
    enabled = make_record(source="源乙", answer="短答案")
    merged, _ = merge_exact_duplicates([draft, enabled])
    assert merged[0]["source"] == "源乙"

    short = make_record(source="源甲", answer="短")
    long_ = make_record(source="源乙", answer="长" * 50)
    merged, _ = merge_exact_duplicates([short, long_])
    assert merged[0]["source"] == "源乙"


def test_主源裁决_完全同分先导入者优先():
    """导入时间这一层靠列表序稳定实现：完全同分时 min() 取首个。"""
    first = make_record(source="源甲", answer="同样长")
    second = make_record(source="源乙", answer="同样长")
    merged, _ = merge_exact_duplicates([first, second])
    assert merged[0]["source"] == "源甲"


def test_同一源多篇面经只留一条明细():
    """question_sources 主键是 (question_id, source)，同源多条只留一条（同分时先导入者）。"""
    a = make_record(source=SOURCE_NAME, answer="答案", source_detail="字节一面")
    b = make_record(source=SOURCE_NAME, answer="答案", source_detail="腾讯二面")

    merged, _ = merge_exact_duplicates([a, b])

    assert [s["source_detail"] for s in merged[0]["sources"]] == ["字节一面"]


def test_同源多记录留最优_存根不挤掉正本出处():
    """占位存根（原题保留）与有答案正本同题同源时，出处要指向正本那一篇。

    否则会出现「答案是 A 篇的、出处记成 B 篇」——溯源对不上，复盘时点进去找不到这段答案。
    """
    stub = make_record(
        source=SOURCE_NAME, answer="", status="draft", source_detail="", round_confidence=None
    )
    real = make_record(source=SOURCE_NAME, answer="答案", source_detail="字节二面")
    real["sources"][0]["url"] = "https://www.nowcoder.com/feed/main/detail/abc"

    merged, _ = merge_exact_duplicates([stub, real])  # 存根故意排前面

    assert merged[0]["status"] == "enabled"
    assert [s["source_detail"] for s in merged[0]["sources"]] == ["字节二面"]
    assert [s["url"] for s in merged[0]["sources"]] == ["https://www.nowcoder.com/feed/main/detail/abc"]


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

