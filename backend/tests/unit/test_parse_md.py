from pathlib import Path

import pytest

import parse_md  # 模块本体：白名单要用 monkeypatch 换掉（条目是 id，测试不依赖真实题库）
from parse_md import (
    SOURCE_NAME,
    finalize_status,
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


def test_行为面域可出题故置_enabled(questions):
    """P1-M11 起行为面进 ASKABLE_DOMAINS：有实质答案的行为题照常 enabled（进行为面池子）。"""
    q = questions[4]
    assert q["domain"] == "behavioral"
    assert q["status"] == "enabled"
    assert q["answer"] == "先做三年技术，再考虑带团队。"


def test_占位答案与空代码块不当作可用答案():
    """源里「答案：xx」是没填的占位、空代码块是没写的代码——都按没有答案处理（draft）。

    判据是答案的实质字符数（去掉代码围栏行与空白），阈值 5：实测占位/空壳在 0～2 字，
    而题库里最短的真答案 8 字、真实语料 15 字，中间空档很宽。
    """
    stub = make_record(source=SOURCE_NAME, answer="xx")
    hollow_fence = make_record(source=SOURCE_NAME, answer="```python\n\n```")
    real_short = make_record(source=SOURCE_NAME, answer="验证集网格搜索；或 RRF 避免调权。")

    for record in (stub, hollow_fence, real_short):
        finalize_status(record)

    assert stub["status"] == "draft"
    assert hollow_fence["status"] == "draft"
    assert real_short["status"] == "enabled"  # 短但言之有物（19 字）


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


def test_合并后_company_round_缺失仍为_None():
    """没有公司/轮次的语料（开源题库）合并后不能变成空串——空串入库存的是 '' 不是 NULL。"""
    a = make_record(source="源甲", answer="答案")
    b = make_record(source="源乙", answer="答案")
    for record in (a, b):
        record["company"] = None
        record["round"] = None

    merged, _ = merge_exact_duplicates([a, b])

    assert merged[0]["company"] is None
    assert merged[0]["round"] is None


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


def test_白名单合并近似重复(monkeypatch):
    """同题异写（题干细微差异 → 不同 question_id）按白名单合并，择优保留。

    白名单条目是 **question_id**（题干原文不进 git，见语料红线），故这里用**合成题目**
    造两对、把白名单 monkeypatch 成它们的 id——测的是合并机制本身，不依赖真实题库内容。
    """
    text = """# 二面

## 字节跳动

### 工程化与可观测

#### 工单系统批量导入接口响应慢，怎么定位？

> 【轮次：明确】

先看导入批次大小与锁竞争，再用火焰图看序列化开销。

#### 工单系统的批量导入接口响应慢怎么定位？

> 【轮次：明确】

先看导入批次大小与锁竞争，再用火焰图看序列化开销；另外要查数据库批量写入是否被逐条提交拖慢，以及消息队列有没有积压。

#### 线上告警噪音太多，怎么治理？

> 【轮次：明确】

按服务分级定阈值，先压制没有行动项的告警。

#### 线上告警噪音特别多该怎么治理？

> 【原题保留，未收录答案】
"""
    questions = parse_text(text)
    assert len(questions) == 4

    # 前两条一对、后两条一对（parse_text 保序）；白名单用它们的 id，与真实条目同形
    monkeypatch.setattr(parse_md, "APPROVED_MERGE_PAIRS", [
        (questions[0]["question_id"], questions[1]["question_id"]),
        (questions[2]["question_id"], questions[3]["question_id"]),
    ])

    merged, report = merge_approved_pairs(questions)
    assert len(merged) == 2
    assert len(report) == 2
    # 对一：两题都有实质答案，保留答案更长的那条
    ticket = next(q for q in merged if "工单" in q["question"])
    assert ticket["question"].startswith("工单系统的批量导入")
    assert ticket["round_confidence"] == "明确"
    # 对二：有答案的 enabled 优先于占位答案的 draft
    alert = next(q for q in merged if "告警" in q["question"])
    assert alert["question"].startswith("线上告警噪音太多")
    assert alert["status"] == "enabled"
    # company/round 聚合去重（两对同公司同轮次，聚合结果不变）
    assert ticket["company"] == alert["company"] == "字节跳动"
    assert "字节跳动/" in report[0]["dropped"][0]  # 报告行含来源前缀，见 bank.describe


def test_白名单题干不存在则报错():
    """题库改动后白名单条目匹配不到题时，宁可报错也不能静默跳过。"""
    with pytest.raises(ValueError, match="白名单"):
        merge_approved_pairs([])

