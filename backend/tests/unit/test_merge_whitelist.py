"""近似重复白名单（P2-M1）：两份清单的格式与边界。

清单一处实现、两处应用：`parse_md`（个人库内部、解析期）+ `combine`（跨源、合并期）。
两份清单**不能重叠**——同一个 id 被两处各并一次，第二处会因「匹配不到」直接报错；
用 question_id 而不是题干：题干原文不进 git（语料红线）。
"""

from __future__ import annotations

from combine import APPROVED_MERGE_PAIRS
from parse_md import APPROVED_MERGE_PAIRS as PERSONAL_PAIRS


def test_清单条目格式合法():
    for name, pairs in (("combine", APPROVED_MERGE_PAIRS), ("parse_md", PERSONAL_PAIRS)):
        for id_a, id_b in pairs:
            assert id_a.startswith("q_") and id_b.startswith("q_"), name
            assert id_a != id_b, name


def test_两份清单不重叠():
    cross = {qid for pair in APPROVED_MERGE_PAIRS for qid in pair}
    personal = {qid for pair in PERSONAL_PAIRS for qid in pair}
    assert not cross & personal
