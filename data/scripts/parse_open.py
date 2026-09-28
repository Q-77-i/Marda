"""解析开源语料 → 结构化 JSON（SPEC §8.1 多源 provenance）。

四个源各一个 adapter（源 = WenQu 登记表的 MIT ANSWERS 档，清单见 data/licenses/语料来源清单.md）：
  ai-agents-from-zero       单文件题库：`### Qn-m. 题干` + 正文（止于「常见追问」，难度取元信息行）
  FAQ_Of_LLM_Interview      形态 1「题单 + 编号答案段」/ 形态 2「编号问题行 + 围栏答案」
  ai-agent-interview-guide  `docs/01-面试八股文/*.md`：四种问答标记混排
  llm-interview-guide       全站叙述页的 `**Q：…**` 内联问答（目录 → 域映射）

只有「题 + 答」齐全的才收录：FAQ 形态 1 里题单有题、后面却没有答案段的编号不采（计数进报告）。
域未启用（如 behavioral）或无答案 → `status=draft`，由 bank.finalize_status 判定。

跨源同题干合并走 bank.merge_exact_duplicates；近似重复只报告不合并（决策 E）。
新增开源源须在 bank.SOURCE_PRIORITY 登记，否则排在所有已知源之后。

用法：python data/scripts/parse_open.py [--raw data/raw] [--out data/parsed/questions_open.json]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Callable, Final

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path
from bank import (
    SOURCE_AGENT_GUIDE,
    SOURCE_FAQ,
    SOURCE_FROM_ZERO,
    SOURCE_LLM_GUIDE,
    finalize_status,
    find_duplicates,
    merge_exact_duplicates,
    new_question,
    print_stats,
)
from mapping import DOMAIN_DIFFICULTY_OVERRIDE

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW = REPO_ROOT / "data" / "raw"
DEFAULT_OUT = REPO_ROOT / "data" / "parsed" / "questions_open.json"

LICENSE = "MIT"
REPO: Final[dict[str, dict[str, str]]] = {
    SOURCE_FROM_ZERO: {
        "dir": "ai-agents-from-zero",
        "url": "https://github.com/didilili/ai-agents-from-zero",
    },
    SOURCE_FAQ: {
        "dir": "FAQ_Of_LLM_Interview",
        "url": "https://github.com/aceliuchanghong/FAQ_Of_LLM_Interview",
    },
    SOURCE_AGENT_GUIDE: {
        "dir": "ai-agent-interview-guide",
        "url": "https://github.com/bcefghj/ai-agent-interview-guide",
    },
    SOURCE_LLM_GUIDE: {
        "dir": "llm-interview-guide",
        "url": "https://github.com/Meko1/llm-interview-guide",
    },
}
# 解析顺序 = 同分时「先导入者优先」的次序（bank.rank）
SOURCE_ORDER: Final = (SOURCE_FROM_ZERO, SOURCE_FAQ, SOURCE_AGENT_GUIDE, SOURCE_LLM_GUIDE)

FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def clean_md(text: str) -> str:
    """去图片、链接转文本——只在围栏之外动（代码块里的方括号/圆括号有语义）。"""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence:
            if IMG_RE.search(line):
                line = IMG_RE.sub("", line)
                if not line.strip():
                    continue
            line = LINK_RE.sub(r"\1", line)
        out.append(line)
    return "\n".join(out).strip()


def _scan(text: str):
    """逐行扫描，区分「结构行」与「正文行」：围栏内的行一律是正文。

    各源 adapter 共用——这些语料的答案里都有代码块，围栏内的 `### ` / `N. ` 不能当结构。
    """
    in_fence = False
    for line in text.splitlines():
        line = line.rstrip()
        if FENCE_RE.match(line):
            in_fence = not in_fence
            yield "body", line
            continue
        yield ("body" if in_fence else "line"), line


def _make(
    text: str,
    *,
    topic: str,
    domain: str,
    source: str,
    relpath: str,
    answer: str,
    difficulty: str | None = None,
) -> dict:
    """按源登记信息组装题目记录（difficulty 缺省按域覆盖表，再缺省 L2）。"""
    record = new_question(
        text,
        topic=topic,
        domain=domain,
        difficulty=difficulty or DOMAIN_DIFFICULTY_OVERRIDE.get(domain) or "L2",
        source=source,
        license=LICENSE,
        url=REPO[source]["url"],
        source_detail=relpath,
    )
    record["answer"] = answer
    finalize_status(record)
    return record


class _Ask:
    """起题 → 累积答案行 → 收题（清洗正文、定 status）。四个 adapter 共用的骨架。"""

    def __init__(self, build: Callable[[str, str], dict]) -> None:
        self._build = build
        self._text: str | None = None
        self._body: list[str] = []
        self.items: list[dict] = []

    @property
    def active(self) -> bool:
        return self._text is not None

    def start(self, text: str) -> None:
        self.close()
        self._text, self._body = text, []

    def feed(self, line: str) -> None:
        if self._text is not None:
            self._body.append(line)

    def close(self) -> None:
        if self._text is not None:
            self.items.append(self._build(self._text, clean_md("\n".join(self._body))))
        self._text, self._body = None, []


# ---------------------------------------------------------------- ai-agents-from-zero

FROM_ZERO_FILE = "AI智能体与大模型应用开发面试题库.md"
FROM_ZERO_CHAPTER_RE = re.compile(r"^##\s+(\d+)、\s*(.+?)\s*$")
FROM_ZERO_QUESTION_RE = re.compile(r"^###\s+Q\d+-\d+[.、]\s*(.+?)\s*$")
FROM_ZERO_FOLLOWUP_RE = re.compile(r"^\*\*常见追问：\*\*")
FROM_ZERO_DIFFICULTY_RE = re.compile(r"\*\*难度：\*\*\s*`([^`]+)`")
FROM_ZERO_META_RE = re.compile(r"^\*\*(?:重要度|考察点|对应章节)：\*\*")
FROM_ZERO_DIFFICULTY: Final[dict[str, str]] = {"基础": "L1", "中等": "L2", "较难": "L3"}
# 章节 → 知识域：第 11、12 章是项目表达与岗位认知（行为面，阶段二启用 → draft）
FROM_ZERO_CHAPTER_DOMAIN: Final[dict[str, str]] = {
    "1": "agent-architecture",
    "2": "agent-architecture",
    "3": "rag",
    "4": "rag",
    "5": "tool-use",
    "6": "agent-architecture",
    "7": "agent-architecture",
    "8": "engineering-observability",
    "9": "engineering-observability",
    "10": "agent-architecture",
    "11": "behavioral",
    "12": "behavioral",
}


def parse_from_zero(text: str, *, relpath: str) -> tuple[list[dict], list[str]]:
    """单文件题库：每题的元信息（重要度/难度/考察点/对应章节）在「常见追问」之后，答案取之前。"""
    domain = topic = None
    meta = {"difficulty": None}
    ask = _Ask(
        lambda q, a: _make(
            q,
            topic=topic,
            domain=domain,
            source=SOURCE_FROM_ZERO,
            relpath=relpath,
            answer=a,
            difficulty=meta["difficulty"],
        )
    )
    in_tail = False  # 进入元信息区：答案已结束，只剩难度要读

    for kind, line in _scan(text):
        if kind == "body":
            if not in_tail:
                ask.feed(line)
            continue

        chapter = FROM_ZERO_CHAPTER_RE.match(line)
        if chapter:
            ask.close()
            number, topic = chapter.group(1), chapter.group(2)
            domain = FROM_ZERO_CHAPTER_DOMAIN.get(number)
            if domain is None:
                raise ValueError(f"未知章节「{number}、{topic}」，请补进 FROM_ZERO_CHAPTER_DOMAIN：{relpath}")
            continue

        question = FROM_ZERO_QUESTION_RE.match(line)
        if question:
            if domain is None:
                raise ValueError(f"题目出现在章节标题之前（缺 `## N、` 行）：{relpath}")
            ask.start(question.group(1))
            meta["difficulty"], in_tail = None, False
            continue

        # 难度与重要度同在一行（`**重要度：**… | **难度：**`中等``），必须先取难度
        difficulty = FROM_ZERO_DIFFICULTY_RE.search(line)
        if difficulty:
            label = difficulty.group(1).strip()
            if label not in FROM_ZERO_DIFFICULTY:
                raise ValueError(f"未知难度「{label}」，请补进 FROM_ZERO_DIFFICULTY：{relpath}")
            meta["difficulty"], in_tail = FROM_ZERO_DIFFICULTY[label], True
            continue
        if FROM_ZERO_FOLLOWUP_RE.match(line) or FROM_ZERO_META_RE.match(line):
            in_tail = True
            continue
        if not in_tail:
            ask.feed(line)

    ask.close()
    return ask.items, []


# ---------------------------------------------------------------- FAQ_Of_LLM_Interview

FAQ_LIST_RE = re.compile(r"^(\d+)\s*[.、]\s*(\S.*)$")
FAQ_SECTION_RE = re.compile(r"^##\s*(\d+)\s*[.、]?\s*(.*)$")
FAQ_FORM1_PREFIX = "3-面试问题记录/2026_interview_log/docs/"

# 逐文件采集清单：(topic, domain)。不在此表的文件一律不采，理由见模块报告与语料来源清单。
# 未收录的典型：按公司组织的面经（题单 + 「问题解析」段，解析段编号只是关键词标签、还可能
# 与题单错位）、CV/工具用法笔记（`### What/Why` 分节 + 围栏说明）、参数手册。
FAQ_FILES: Final[dict[str, tuple[str, str]]] = {
    FAQ_FORM1_PREFIX + "agent相关.md": ("agent相关", "agent-architecture"),
    FAQ_FORM1_PREFIX + "rag优化.md": ("rag优化", "rag"),
    FAQ_FORM1_PREFIX + "transformer相关.md": ("transformer相关", "cs-fundamentals"),
    FAQ_FORM1_PREFIX + "强化学习相关.md": ("强化学习相关", "cs-fundamentals"),
    FAQ_FORM1_PREFIX + "微调相关.md": ("微调相关", "cs-fundamentals"),
    FAQ_FORM1_PREFIX + "必会算法题目.md": ("必会算法题目", "algorithms"),
    "1-大模型应用基础/Transformer模型结构.md": ("Transformer 模型结构", "cs-fundamentals"),
    "1-大模型应用基础/训练与推理.md": ("训练与推理", "cs-fundamentals"),
    "1-大模型应用基础/大模型的泛化能力.md": ("大模型的泛化能力", "cs-fundamentals"),
    "2-大模型优化技术/微调优化.md": ("微调优化", "cs-fundamentals"),
}


def parse_faq_form1(
    text: str, *, topic: str, domain: str, relpath: str
) -> tuple[list[dict], list[str]]:
    """形态 1：题单 + 编号答案段，按编号配对。

    题干取题单文本（那是真实面试里被问的那句），答案段标题只是文章标题、内容才是答案。
    只有两边都有的编号成题——有题无答案段的不采（编号进报告，便于后续补采）。
    """
    listed: dict[int, str] = {}
    sections: dict[int, list[str]] = {}
    current: int | None = None
    in_head = True

    for kind, line in _scan(text):
        if kind == "body":
            if current is not None:
                sections[current].append(line)
            continue
        section = FAQ_SECTION_RE.match(line)
        if section:
            in_head = False
            current = int(section.group(1))
            sections[current] = []
            continue
        if in_head:
            if line.strip() == "---":
                continue
            item = FAQ_LIST_RE.match(line)
            if item:
                listed[int(item.group(1))] = item.group(2).strip()
            continue
        if current is not None:
            sections[current].append(line)

    questions = [
        _make(
            question,
            topic=topic,
            domain=domain,
            source=SOURCE_FAQ,
            relpath=relpath,
            answer=clean_md("\n".join(sections[number])),
        )
        for number, question in sorted(listed.items())
        if number in sections
    ]
    missing = [f"#{number}" for number in sorted(listed) if number not in sections]
    notes = [f"题单有题、无答案段，未采 {len(missing)} 条：{'、'.join(missing)}"] if missing else []
    return questions, notes


def parse_faq_form2(
    text: str, *, topic: str, domain: str, relpath: str
) -> tuple[list[dict], list[str]]:
    """形态 2：编号问题行 + 紧随的围栏答案块。

    围栏语言是 `text`（或无语言）说明是散文答案 → 剥壳只留正文；带语言的（如 `python`）
    是代码示例，原样保留围栏。围栏里成段的编号列表因「问题行后必须紧跟围栏」而不成题。
    """
    lines = text.splitlines()
    questions: list[dict] = []
    index, total = 0, len(lines)
    in_fence = False  # 围栏内的编号行（答案里成段的列表）不是问题行

    while index < total:
        if FENCE_RE.match(lines[index]):
            in_fence = not in_fence
            index += 1
            continue
        item = None if in_fence else FAQ_LIST_RE.match(lines[index])
        if not item:
            index += 1
            continue
        cursor = index + 1
        while cursor < total and not lines[cursor].strip():
            cursor += 1
        if cursor >= total or not FENCE_RE.match(lines[cursor]):
            index += 1
            continue

        lang = lines[cursor].strip().lstrip("`~").strip().lower()
        end = cursor + 1
        while end < total and not FENCE_RE.match(lines[end]):
            end += 1
        if end >= total:
            raise ValueError(f"围栏未闭合（第 {cursor + 1} 行起）：{relpath}")
        inner = lines[cursor + 1 : end]
        answer = "\n".join(inner) if lang in {"", "text"} else "\n".join(lines[cursor : end + 1])
        questions.append(
            _make(
                item.group(2).strip(),
                topic=topic,
                domain=domain,
                source=SOURCE_FAQ,
                relpath=relpath,
                answer=clean_md(answer),
            )
        )
        index = end + 1

    return questions, []


# ---------------------------------------------------------------- ai-agent-interview-guide

AGENT_GUIDE_DIR = "docs/01-面试八股文"
AGENT_GUIDE_QUESTION_RE = re.compile(
    r"^(?:#{3,4}\s+|\*\*)\s*(?:面试\s*)?Q\d*\s*[：:.、]\s*(.+?)\s*\*{0,2}$"
)
# 答案标记三种：`**A：**`、`**A**：`、`**标准答案 A：**`；`**标准答案（A）**` 是同一类的第四种
AGENT_GUIDE_ANSWER_RE = re.compile(
    r"^\*{0,2}(?:标准答案\s*)?(?:A|[（(]\s*A\s*[）)])\*{0,2}\s*[：:]\*{0,2}\s*(.*)$"
)
AGENT_GUIDE_STOP_RE = re.compile(r"^(?:\*\*追问|#{2,4}\s|---\s*$)")
# 逐文件采集清单：文件 → 知识域（文件名去掉序号即 topic）
AGENT_GUIDE_FILES: Final[dict[str, str]] = {
    "01-基础概念.md": "agent-architecture",
    "02-核心框架.md": "agent-architecture",
    "03-RAG技术.md": "rag",
    "04-工具调用.md": "tool-use",
    "05-记忆系统.md": "memory",
    "06-多智能体.md": "agent-architecture",
    "07-大模型基础.md": "cs-fundamentals",
    "08-工程化实践.md": "engineering-observability",
    "09-Prompt工程.md": "agent-architecture",
}


def parse_agent_guide(
    text: str, *, topic: str, domain: str, relpath: str
) -> tuple[list[dict], list[str]]:
    """八股文：`**Qn：…**`、`**Q：…**`、`### Qn：…`、`**面试 Qn：…**` 四种问法。

    答案标记同样有两种（`**A：**`/`**A**：`，还有 `**标准答案 A：**`）；遇到「追问」块、
    下一个标题或分节线即止。题在、答案空 → 空答案（draft），不静默丢弃。
    """
    ask = _Ask(
        lambda q, a: _make(
            q, topic=topic, domain=domain, source=SOURCE_AGENT_GUIDE, relpath=relpath, answer=a
        )
    )
    stopped = False  # 答案已结束（撞上追问块/标题），后续行不再计入

    for kind, line in _scan(text):
        if kind == "body":
            if not stopped:
                ask.feed(line)
            continue

        question = AGENT_GUIDE_QUESTION_RE.match(line)
        if question:
            ask.start(question.group(1).strip())
            stopped = False
            continue
        if not ask.active:  # 尚无题目：标题/正文都不采
            continue
        if AGENT_GUIDE_STOP_RE.match(line):
            stopped = True
            continue
        if stopped:
            continue
        answer = AGENT_GUIDE_ANSWER_RE.match(line)
        if answer:
            ask.feed(answer.group(1).strip())
            continue
        ask.feed(line)

    ask.close()
    return ask.items, []


# ---------------------------------------------------------------- llm-interview-guide

LLM_GUIDE_DOCS = "docs"
LLM_GUIDE_QUESTION_RE = re.compile(r"^\*\*Q[：:]\s*(.*?)\*\*\s*(.*)$")
LLM_GUIDE_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")
LLM_GUIDE_HEADING_RE = re.compile(r"^#{1,6}\s")
# 目录 → 知识域：模型层/预训练/微调/多模态属计算机基础理论（阶段二启用 → draft）
LLM_GUIDE_DIR_DOMAIN: Final[dict[str, str]] = {
    "agent": "agent-architecture",
    "prompt": "agent-architecture",
    "claude-code": "agent-architecture",
    "rag": "rag",
    "engineering": "engineering-observability",
    "evaluation": "engineering-observability",
    "inference": "engineering-observability",
    "basics": "cs-fundamentals",
    "beginner": "cs-fundamentals",
    "models": "cs-fundamentals",
    "multimodal": "cs-fundamentals",
    "pretraining": "cs-fundamentals",
    "finetuning": "cs-fundamentals",
    "advanced": "cs-fundamentals",
}
# `docs/interview/` 逐文件采集：只有这几页是 `**Q：**` 问答，其余是行业垂直/治理 playbook、路线图
LLM_GUIDE_INTERVIEW_FILES: Final[dict[str, str]] = {
    "foundation-qna.md": "cs-fundamentals",
    "framework-workflow-qna.md": "agent-architecture",
    "rag-memory-eval-qna.md": "rag",
    "inference-cost-qna.md": "engineering-observability",
    "finetuning-platform-qna.md": "cs-fundamentals",
}
LLM_GUIDE_SKIP_DIRS: Final = frozenset({"superpowers", "public"})


def parse_llm_guide(text: str, *, domain: str, relpath: str) -> tuple[list[dict], list[str]]:
    """叙述页内联问答：`**Q：题干？**` 后跟同行答案或换行答案，直到下一个 Q 或标题。

    topic 取页面 H1（页标题就是知识点名）；答案里的图片去掉、相对链接退化成纯文本。
    """
    title: str | None = None
    ask = _Ask(
        lambda q, a: _make(
            q, topic=title or Path(relpath).stem, domain=domain,
            source=SOURCE_LLM_GUIDE, relpath=relpath, answer=a,
        )
    )

    for kind, line in _scan(text):
        if kind == "body":
            ask.feed(line)
            continue

        question = LLM_GUIDE_QUESTION_RE.match(line)
        if question:
            ask.start(question.group(1).strip())
            ask.feed(question.group(2).strip())  # 答案常与题干同行
            continue
        if LLM_GUIDE_HEADING_RE.match(line):
            ask.close()
            heading = LLM_GUIDE_TITLE_RE.match(line)
            if heading and title is None:
                title = heading.group(1)
            continue
        ask.feed(line)

    ask.close()
    return ask.items, []


# ---------------------------------------------------------------- 目录级：走采集清单

def _walk_from_zero(raw_root: Path) -> tuple[list[dict], list[str]]:
    path = raw_root / REPO[SOURCE_FROM_ZERO]["dir"] / FROM_ZERO_FILE
    return parse_from_zero(path.read_text(encoding="utf-8"), relpath=FROM_ZERO_FILE)


def _walk_faq(raw_root: Path) -> tuple[list[dict], list[str]]:
    root = raw_root / REPO[SOURCE_FAQ]["dir"]
    questions: list[dict] = []
    notes: list[str] = []
    skipped = 0
    for path in sorted(root.rglob("*.md")):
        if ".git" in path.parts:
            continue
        relpath = path.relative_to(root).as_posix()
        if relpath not in FAQ_FILES:
            skipped += 1
            continue
        topic, domain = FAQ_FILES[relpath]
        text = path.read_text(encoding="utf-8")
        parser = parse_faq_form1 if relpath.startswith(FAQ_FORM1_PREFIX) else parse_faq_form2
        parsed, parsed_notes = parser(text, topic=topic, domain=domain, relpath=relpath)
        questions += parsed
        notes += [f"{relpath}：{note}" for note in parsed_notes]
    if skipped:
        notes.append(
            f"{skipped} 个文件不在采集清单，未采（面经题单+关键词解析段、CV/工具笔记、参数手册等）"
        )
    return questions, notes


def _walk_agent_guide(raw_root: Path) -> tuple[list[dict], list[str]]:
    root = raw_root / REPO[SOURCE_AGENT_GUIDE]["dir"] / AGENT_GUIDE_DIR
    questions: list[dict] = []
    for name, domain in AGENT_GUIDE_FILES.items():
        path = root / name
        topic = re.sub(r"^\d+[-、]\s*", "", path.stem)
        parsed, _ = parse_agent_guide(
            path.read_text(encoding="utf-8"), topic=topic, domain=domain, relpath=f"{AGENT_GUIDE_DIR}/{name}"
        )
        questions += parsed
    notes = [
        f"{name}：不在采集清单（长文/非问答页），未采"
        for name in sorted(p.name for p in root.glob("*.md"))
        if name not in AGENT_GUIDE_FILES
    ]
    return questions, notes


def _walk_llm_guide(raw_root: Path) -> tuple[list[dict], list[str]]:
    docs = raw_root / REPO[SOURCE_LLM_GUIDE]["dir"] / LLM_GUIDE_DOCS
    questions: list[dict] = []
    notes: list[str] = []
    skipped_site = 0     # 站点首页/关于页
    skipped_interview = 0  # `docs/interview/` 下不是 `**Q：**` 问答的页面
    for path in sorted(docs.rglob("*.md")):
        relpath = path.relative_to(docs).as_posix()
        if len(path.relative_to(docs).parts) < 2:
            skipped_site += 1
            continue
        head = path.relative_to(docs).parts[0]
        if head in LLM_GUIDE_SKIP_DIRS:
            continue
        if head == "interview":
            domain = LLM_GUIDE_INTERVIEW_FILES.get(path.name)
            if domain is None:
                skipped_interview += 1
                continue
        else:
            domain = LLM_GUIDE_DIR_DOMAIN.get(head)
            if domain is None:
                raise ValueError(f"未知目录「{head}」，请补进 LLM_GUIDE_DIR_DOMAIN：{relpath}")
        parsed, _ = parse_llm_guide(path.read_text(encoding="utf-8"), domain=domain, relpath=relpath)
        questions += parsed
    if skipped_site:
        notes.append(f"{skipped_site} 个站点页（首页/关于），未采")
    if skipped_interview:
        notes.append(
            f"interview/ 下 {skipped_interview} 页未采（不是 `**Q：**` 问答页：行业垂直/治理 playbook、"
            "路线图、`## 追问链` 与 `## Qn：` 体、速记与真题清单）"
        )
    return questions, notes


WALKERS: Final[dict[str, Callable[[Path], tuple[list[dict], list[str]]]]] = {
    SOURCE_FROM_ZERO: _walk_from_zero,
    SOURCE_FAQ: _walk_faq,
    SOURCE_AGENT_GUIDE: _walk_agent_guide,
    SOURCE_LLM_GUIDE: _walk_llm_guide,
}


def parse_source(source: str, raw_root: Path) -> tuple[list[dict], list[str]]:
    return WALKERS[source](raw_root)


def parse_all(raw_root: Path) -> tuple[list[dict], list[str], dict[str, int]]:
    """按 SOURCE_ORDER 依次解析（同分时先导入者优先），返回（题目, 报告行, 每源题量）。"""
    questions: list[dict] = []
    notes: list[str] = []
    counts: dict[str, int] = {}
    for source in SOURCE_ORDER:
        parsed, parsed_notes = parse_source(source, raw_root)
        counts[source] = len(parsed)
        questions += parsed
        notes += [f"[{source}] {note}" for note in parsed_notes]
    return questions, notes, counts


def main() -> None:
    parser = argparse.ArgumentParser(description="解析开源语料（4 源）→ 结构化 JSON")
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    questions, notes, counts = parse_all(args.raw)

    print("各源题量（逐文件，供抽查）：")
    for source in SOURCE_ORDER:
        mine = [q for q in questions if q["source"] == source]
        enabled = sum(1 for q in mine if q["status"] == "enabled")
        print(f"  {source:<26} {counts[source]:>4} 题（enabled {enabled}）")
        per_file: dict[str, int] = {}
        for q in mine:
            per_file[q["sources"][0]["source_detail"]] = per_file.get(
                q["sources"][0]["source_detail"], 0
            ) + 1
        for name, num in per_file.items():
            print(f"      {num:>4}  {name}")

    if notes:
        print("\n未采说明：")
        for note in notes:
            print(f"  {note}")

    raw_total = len(questions)
    questions, merge_report = merge_exact_duplicates(questions)
    if merge_report:
        print(f"\n跨源同题干合并：{raw_total} → {len(questions)} 条（{len(merge_report)} 组）")
        for item in merge_report:
            print(f"  [{item['question_id']}] 「{item['question'][:44]}」")
            print(f"      保留 {item['kept']}")
            for dropped in item["dropped"]:
                print(f"      合并 {dropped}")
        print()

    print_stats(questions)

    duplicates = find_duplicates(questions)
    print(f"\n相似度 ≥0.9 的重复候选：{len(duplicates)} 对（供人工确认，不自动处理）")
    for id_a, id_b, ratio in duplicates:
        text_a = next(q["question"] for q in questions if q["question_id"] == id_a)
        text_b = next(q["question"] for q in questions if q["question_id"] == id_b)
        print(f"  {ratio}  「{text_a[:40]}」 ↔ 「{text_b[:40]}」")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "origin": str(args.raw),
            "raw_total": raw_total,
            "total": len(questions),
            "merged": len(merge_report),
            "per_source": counts,
        },
        "questions": questions,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出：{args.out}")


if __name__ == "__main__":
    main()
