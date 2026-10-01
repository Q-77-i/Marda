"""LLM 判相关 + 人工复核产物（P1-M12 会话 1）。

D2 拍板口径 = **LLM 预判 + 人工抽检**。理由：30 条 query × 40 候选纯手动标注要 2–3
小时，纯 LLM 又让「人审 golden set」说不出口；折中是 LLM 出全部草稿，再用
`render_review` 把**最该看的那几十行**摊在明面上（判定与检索排名不一致的地方），
人工扫一遍挑错 → 改 golden 文件 → 重跑指标。

判级走 `app.llm.chat_json`（json_object + Pydantic 校验），不另起一套调用：
DeepSeek 结构化输出的坑位（必须关思考、只能 json_object）已经固化在那里。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from app import llm

LABEL_TEMPLATE = """你在为检索系统做相关性标注。

【知识需求】{need}

【候选题】
{candidates}

逐题判定这道**面试题**对满足上述知识需求的价值：

- **2 直接命中**：这道题问的**就是**该需求的主题。读完它的参考答案，需求里的知识点就补上了。
- **1 相邻知识点**：与该需求属于**同一个具体子方向**，但不是同一件事（例：需求问「规划的范式」，
  候选问「规划失败的典型模式」）。
- **0 不相关**：其余全部——**包括「同属 Agent / RAG 等大主题、但具体子方向不同」**。

判定要点：
1. 先想清楚这个需求的**具体子方向**是什么，再看候选题问的是不是这一个。只共享大主题
   （都是 Agent、都是 RAG、都是工具调用）**不构成相关**，必须落到同一个子方向上。
2. 题干里出现相同词汇不等于相关；题干用词不同但问的是同一件事，才算相关。
3. 边界情况给 1 档（相邻），不要给 2 档（直接命中）。

示例（需求：「向量数据库选型」）：
- 2 →「Milvus 和 pgvector 怎么选」「向量库选型要看哪些指标」
- 1 →「向量检索的索引类型有哪些」　← 索引是选型的一个考量，但不是选型本身
- 0 →「什么是向量嵌入」「RAG 的完整流程是怎样的」　← 同属 RAG 大主题，子方向不同

只输出 JSON：{{"grades": [{{"id": "题目 id", "grade": 0|1|2}}, ...]}}。
必须覆盖**全部 {count} 道题**的 id，一个不漏、不增、不改 id。"""

MAX_MISSING_RETRY = 1  # 漏判的 id 重问一次；再漏就兜底 0 并记进缺失清单（不静默）


class _GradeItem(BaseModel):
    id: str
    grade: int = Field(ge=0, le=2)


class _BatchGrades(BaseModel):
    grades: list[_GradeItem]


def _render_candidates(candidates: Sequence[dict]) -> str:
    lines = []
    for row in candidates:
        points = "；".join(row.get("key_points") or [])[:120]
        lines.append(
            f"- id={row['question_id']}｜域={row.get('domain') or '?'}｜题干：{row['question']}"
            + (f"｜关键点：{points}" if points else "")
        )
    return "\n".join(lines)


async def _label_batch(need: str, candidates: Sequence[dict]) -> dict[str, int]:
    """一批候选题 → {id: grade}。LLM 未返回的 id 不在结果里，由调用方处置。"""
    graded = await llm.chat_json(
        [{
            "role": "system",
            "content": LABEL_TEMPLATE.format(
                need=need,
                candidates=_render_candidates(candidates),
                count=len(candidates),
            ),
        }],
        schema=_BatchGrades,
        temperature=0.0,  # 标注要可复现，不给随机性
    )
    sent = {row["question_id"] for row in candidates}
    return {item.id: item.grade for item in graded.grades if item.id in sent}  # 越界 id 直接丢弃


async def label_query(
    need: str, candidates: Sequence[dict], *, batch_size: int = 10
) -> tuple[dict[str, int], list[str]]:
    """一条 query 的全部候选 → `({id: grade}, 仍缺失的 id)`。

    分批（默认 10 题一批）：一次问 40+ 题既超 prompt 长度也容易漏项。漏判的 id 重问一次，
    仍漏则记 0 并留在缺失清单里——由调用方写进复核文件，人工能看到哪些是「没判」而非「判 0」。
    """
    grades: dict[str, int] = {}
    still_missing: list[str] = []
    for start in range(0, len(candidates), batch_size):
        chunk = list(candidates[start : start + batch_size])
        got = await _label_batch(need, chunk)
        missing = [row["question_id"] for row in chunk if row["question_id"] not in got]
        for _ in range(MAX_MISSING_RETRY):
            if not missing:
                break
            retry_rows = [row for row in chunk if row["question_id"] in missing]
            got.update(await _label_batch(need, retry_rows))
            missing = [row["question_id"] for row in chunk if row["question_id"] not in got]
        for qid in missing:
            grades[qid] = 0
        still_missing.extend(missing)
        grades.update(got)
    return grades, still_missing


# ---- 人工复核产物 ----


ATTENTION_HEAD = 5  # ✗0 却排进前 5：检索说「很相关」、标注说「不相关」，必有一边错
ATTENTION_CAP = 40  # 全文件最多列这么多条（超了给计数，不静默截断）
TOP_N = 5  # 每条 query 摊开的前 N 名
BEYOND_SAMPLES = 5  # 「未进前 N 的相关题」最多举几例（全列会淹掉正文）


def _attention(entry: dict, rows_by_id: dict[str, dict]) -> list[str]:
    """**只**列「✗0 却排进前 5」——判定与排名在同一段区间里方向相反，必有一边错。

    两版试错的结论都写在这：把「✓2 排第 6」算进来会一屏几百条（档位高但排位中等
    是常态）；把「✓2 排在 10 名开外」算进来同样跑偏——那是**检索漏检**，正是 NDCG
    要测的信号，属于指标报告的读者该看的，不是标注复核的活。

    行里必须带**题干**：只给 `q_2b4855…` 这样的 id，人工没法判断该不该改判——复核清单
    的价值全在这里。
    """
    grades, ranked = entry["grades"], entry["ranked"]
    return [
        f"`{entry['id']} #{rank}` 判不相关却排进前 {ATTENTION_HEAD}：{_truncate(rows_by_id.get(qid, {}).get('question', qid), 44)}"
        for rank, qid in enumerate(ranked[:ATTENTION_HEAD], start=1)
        if grades.get(qid, 0) == 0
    ]


def _truncate(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_review(
    entries: Sequence[dict],
    rows_by_id: dict[str, dict],
    *,
    created: str,
    extra_notes: Sequence[str] = (),
    summary: Sequence[str] = (),
) -> str:
    """复核文件：每条 query 的生产检索 top-N + 判定，附「最该看的行」。

    `entries` 元素：`{id, scene, query, domain, grades, ranked, missing}`；
    `summary` 是放在开头的结论行（如标注自一致率），`extra_notes` 是构建告警（放文末）。
    """
    total = sum(len(e["grades"]) for e in entries)
    positives = sum(1 for e in entries for g in e["grades"].values() if g == 2)
    partials = sum(1 for e in entries for g in e["grades"].values() if g == 1)
    out: list[str] = [
        "# golden 检索集 · 人工复核",
        "",
        f"生成：{created}　|　{len(entries)} 条 query　|　标注候选 {total} 条"
        f"（直接相关 {positives} / 部分相关 {partials} / 不相关 {total - positives - partials}）",
        "",
        f"**怎么读**：每条 query 下面是生产检索（hybrid = RRF + rerank）的 top-{TOP_N}，"
        "行首是排名与 LLM 判定（`✓2` 直接命中 / `✓1` 相邻 / `✗0` 不相关）。"
        "判定决定指标，所以**先扫表格**（top-5 是 NDCG@5 的全部输入），再扫"
        "「最该看的行」（判定与排名方向相反的少数几条）。发现判错的，改 "
        "`data/eval/golden/retrieval_queries.json` 里对应 id 的数字后重跑。",
        "",
        "> 池外题目一律视作不相关（池内口径）：指标跨版本可比，但不能当绝对召回率读。",
        "",
    ]
    if summary:
        out += [f"- {line}" for line in summary] + [""]
    attention = [note for entry in entries for note in _attention(entry, rows_by_id)]
    if attention:
        out += ["## 最该看的行", "", f"共 {len(attention)} 条（判定与排名方向相反，两边必有一边错）：", ""]
        out += [f"- {note}" for note in attention[:ATTENTION_CAP]]
        if len(attention) > ATTENTION_CAP:
            out.append(f"- …另有 {len(attention) - ATTENTION_CAP} 条，略（全部见 retrieval_queries.json）")
        out.append("")
    if extra_notes:
        out += ["## 构建告警", ""] + [f"- {note}" for note in extra_notes] + [""]

    for entry in entries:
        head = f"### {entry['id']} · {entry['scene']}"
        if entry.get("domain"):
            head += f" · `{entry['domain']}`"
        head += f" · 相关 {sum(1 for g in entry['grades'].values() if g >= 1)}/{len(entry['grades'])} 候选"
        out += [head, "", f"> {entry['query']}", "", "| # | 判定 | 题 |", "| - | - | - |"]
        for rank, qid in enumerate(entry["ranked"][:TOP_N], start=1):
            grade = entry["grades"].get(qid, 0)
            mark = {2: "✓2", 1: "✓1", 0: "✗0"}[grade]
            row = rows_by_id.get(qid, {})
            out.append(f"| {rank} | {mark} | {_truncate(row.get('question', qid))} |")
        beyond_all = [
            (rank, qid)
            for rank, qid in enumerate(entry["ranked"], start=1)
            if rank > TOP_N and entry["grades"].get(qid, 0) >= 1
        ]
        if beyond_all:
            head = "、".join(
                f"`#{rank}` {_truncate(rows_by_id.get(qid, {}).get('question', qid), 24)}"
                for rank, qid in beyond_all[:BEYOND_SAMPLES]
            )
            more = f"…等 {len(beyond_all)} 条" if len(beyond_all) > BEYOND_SAMPLES else ""
            out += ["", f"未进前 {TOP_N} 的相关题 {len(beyond_all)} 条：{head}{more}"]
        if entry.get("missing"):
            out += ["", f"⚠️ 未判出（按 0 计）：{'、'.join(entry['missing'])}"]
        out.append("")
    return "\n".join(out)
