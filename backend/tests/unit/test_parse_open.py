"""开源语料 adapter 的解析规则（fixture 为四源真实格式的摘录）。"""

from pathlib import Path

import pytest

from bank import SOURCE_FAQ, SOURCE_FROM_ZERO, SOURCE_LLM_GUIDE, merge_exact_duplicates
from parse_open import (
    parse_agent_guide,
    parse_faq_form1,
    parse_faq_form2,
    parse_from_zero,
    parse_llm_guide,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------- ai-agents-from-zero：### Qn-m. 题 + 正文（止于「常见追问」） ----------


@pytest.fixture(scope="module")
def from_zero():
    return parse_from_zero(read("sample_open_from_zero.md"), relpath="AI智能体与大模型应用开发面试题库.md")


def test_from_zero_题量与域(from_zero):
    questions, _ = from_zero
    assert len(questions) == 4
    assert [q["domain"] for q in questions] == ["rag", "rag", "tool-use", "behavioral"]
    assert questions[0]["topic"] == "RAG 全链路"


def test_from_zero_题干去编号_答案止于常见追问(from_zero):
    questions, _ = from_zero
    q = questions[0]
    assert q["question"] == "请描述一个完整的 RAG 流水线。"
    assert q["answer"].startswith("RAG 分离线与在线两条链路")
    # 「常见追问」是追问清单不是答案；考察点/对应章节是元信息
    assert "常见追问" not in q["answer"]
    assert "为什么不直接把全文塞进 Prompt？" not in q["answer"]
    assert "考察点" not in q["answer"]
    assert "对应章节" not in q["answer"]


def test_from_zero_围栏内的标题与难度不参与结构判断(from_zero):
    questions, _ = from_zero
    assert not any("围栏内的假题" in q["question"] for q in questions)
    q = questions[0]
    assert "### Q9-9. 围栏内的假题" in q["answer"]  # 围栏内容原样留在答案里
    assert q["difficulty"] == "L2"  # 取围栏外的 `中等`，不是围栏内的 `较难`


def test_from_zero_难度归一化与缺省(from_zero):
    questions, _ = from_zero
    assert [q["difficulty"] for q in questions] == ["L2", "L2", "L1", "L2"]  # 基础→L1，缺失→L2


def test_from_zero_行为面域可出题故置_enabled(from_zero):
    questions, _ = from_zero
    assert questions[3]["domain"] == "behavioral"
    assert questions[3]["status"] == "enabled"  # P1-M11 起行为面进 ASKABLE_DOMAINS
    assert questions[2]["status"] == "enabled"


def test_from_zero_来源四要素(from_zero):
    questions, _ = from_zero
    record = questions[0]["sources"][0]
    assert record == {
        "source": SOURCE_FROM_ZERO,
        "license": "MIT",
        "url": "https://github.com/didilili/ai-agents-from-zero",
        "source_detail": "AI智能体与大模型应用开发面试题库.md",
    }
    assert questions[0]["source"] == SOURCE_FROM_ZERO


def test_from_zero_未知章节报错不静默():
    text = "## 99、新章节\n\n### Q99-1. 新题？\n\n答案\n"
    with pytest.raises(ValueError, match="99"):
        parse_from_zero(text, relpath="x.md")


# ---------- FAQ_Of_LLM_Interview 形态 1：题单 + 编号答案段 ----------


@pytest.fixture(scope="module")
def faq_form1():
    return parse_faq_form1(
        read("sample_open_faq_form1.md"),
        topic="agent相关",
        domain="agent-architecture",
        relpath="3-面试问题记录/2026_interview_log/docs/agent相关.md",
    )


def test_form1_只采题单与答案段配对的编号(faq_form1):
    questions, notes = faq_form1
    assert [q["question"] for q in questions] == [
        "长期记忆,短期记忆怎么做的?",
        "Skills解释",
        "rag 有哪些优化手段?或者哪些阶段可以优化?",
    ]
    assert any("3" in note for note in notes)  # 未采编号要出现在报告里


def test_form1_题干取题单文本_答案是答案段正文(faq_form1):
    questions, _ = faq_form1
    q = questions[0]
    assert q["answer"].startswith("短期记忆用滑动窗口")
    assert "在agent搭建里面" not in q["answer"]  # 答案段标题不是答案正文
    assert q["domain"] == "agent-architecture" and q["topic"] == "agent相关"
    assert q["status"] == "enabled"


# ---------- FAQ 形态 2：编号问题行 + 围栏答案（```text 剥壳） ----------


@pytest.fixture(scope="module")
def faq_form2():
    return parse_faq_form2(
        read("sample_open_faq_form2.md"),
        topic="训练与推理",
        domain="cs-fundamentals",
        relpath="1-大模型应用基础/训练与推理.md",
    )


def test_form2_text围栏剥壳且内层编号不切题(faq_form2):
    questions, _ = faq_form2
    assert len(questions) == 3
    q = questions[0]
    assert q["question"] == "大模型有推理能力吗?"
    assert q["answer"].startswith("有，但本质是模式匹配")
    assert "依赖训练分布" in q["answer"]  # 围栏内的编号列表留在答案里
    assert "```" not in q["answer"]


def test_form2_带语言的代码块保留围栏(faq_form2):
    questions, _ = faq_form2
    assert questions[2]["answer"].startswith("```python")
    assert questions[1]["domain"] == "cs-fundamentals"
    assert questions[1]["status"] == "draft"  # 计算机基础域未启用，只进 SQLite


# ---------- ai-agent-interview-guide：四种问答标记 ----------


@pytest.fixture(scope="module")
def agent_guide():
    return parse_agent_guide(
        read("sample_open_agent_guide.md"),
        topic="基础概念",
        domain="agent-architecture",
        relpath="docs/01-面试八股文/01-基础概念.md",
    )


def test_agent_guide_四种标记都认(agent_guide):
    questions, _ = agent_guide
    assert [q["question"] for q in questions] == [
        "一句话说明什么是 AI Agent？",
        "这道题只留了空位？",
        "请用你自己的话定义 LLM Agent。",
        "请用一句话解释 RAG。",
        "还有一种答案标记写法？",
        "括号里的答案标记也认吗？",
    ]


def test_agent_guide_两种答案标记与追问截断(agent_guide):
    questions, _ = agent_guide
    assert questions[0]["answer"] == "以 LLM 为认知核心，结合规划、记忆与工具闭环决策的系统。"
    assert questions[3]["answer"] == "生成前先从外部知识库检索证据，再交给模型。"
    assert "追问应对" not in questions[3]["answer"]
    assert "和把全文塞进 Prompt 的区别" not in questions[3]["answer"]
    assert questions[4]["answer"] == "冒号写在粗体外面的那种。"
    # 「标准答案（A）」也是答案标记，不能残留在答案正文里
    assert questions[5]["answer"] == "带括号的 A 也算答案标记。"


def test_agent_guide_无答案置_draft_叙述段不成题(agent_guide):
    questions, _ = agent_guide
    assert questions[1]["answer"] == "" and questions[1]["status"] == "draft"
    assert all("叙述性正文" not in q["answer"] for q in questions)


# ---------- llm-interview-guide：**Q：** 内联问答 ----------


@pytest.fixture(scope="module")
def llm_guide():
    return parse_llm_guide(
        read("sample_open_llm_guide.md"),
        domain="rag",
        relpath="docs/rag/rag-basics.md",
    )


def test_llm_guide_同行答案与换行答案(llm_guide):
    questions, _ = llm_guide
    assert len(questions) == 4
    assert questions[0]["question"] == "RAG 的完整流程是什么？"
    assert questions[0]["answer"].startswith("离线：加载→切分")
    assert questions[1]["answer"].startswith("Bi-encoder 快")


def test_llm_guide_topic取H1_图片去掉链接转文本(llm_guide):
    questions, _ = llm_guide
    assert {q["topic"] for q in questions} == {"RAG 基础"}
    assert "](/rag/rag-advanced)" not in questions[1]["answer"]
    assert "RAG 进阶" in questions[1]["answer"]
    assert all("public/diagrams" not in q["answer"] for q in questions)


def test_llm_guide_无答案置_draft(llm_guide):
    questions, _ = llm_guide
    assert questions[2]["status"] == "draft"
    assert questions[3]["status"] == "enabled"


# ---------- 跨源合并（题干相同 → 同 id → 一条多源） ----------


def test_跨源同题干合并成一条多源():
    a, _ = parse_from_zero(
        "## 3、RAG 全链路\n\n### Q3-1. RAG 的完整流程是什么？\n\n"
        "离线：加载 → 切分 → 向量化 → 入库；在线：检索 → 重排 → 生成。\n",
        relpath="A.md",
    )
    b, _ = parse_llm_guide(
        "# RAG 基础\n\n**Q：RAG 的完整流程是什么？** 离线：加载→切分→入库。\n",
        domain="rag",
        relpath="B.md",
    )
    assert a[0]["question_id"] == b[0]["question_id"]
    merged, report = merge_exact_duplicates(a + b)
    assert len(merged) == 1
    assert [s["source"] for s in merged[0]["sources"]] == [SOURCE_FROM_ZERO, SOURCE_LLM_GUIDE]
    assert report[0]["kept"].startswith(SOURCE_FROM_ZERO)  # 同档 → 答案长者为答案主源
