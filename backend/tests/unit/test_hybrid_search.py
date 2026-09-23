"""hybrid_search 编排单测：fake embed/qdrant/ranker 注入，覆盖双路 RRF 形状、
候选保序、rerank 顺序决定输出、跳过与失败语义。

不打真实 Qdrant/嵌入服务/SiliconFlow（探针真调验证）；SQLite join 本身
由 test_question_search 的 fetch_by_ids 用例覆盖，这里 monkeypatch。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.tools import hybrid_search
from app.tools.embedding import Embedding


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """不依赖仓库 .env（get_settings 需要假密钥）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET", "t" * 32)
    hybrid_search.get_settings.cache_clear()
    yield
    hybrid_search.get_settings.cache_clear()


class FakeEmbedder:
    """每次返回同一向量；记录收到的文本。"""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.vector = Embedding(dense=[0.5] * 4, sparse={7: 0.8, 3: 0.2})

    async def embed(self, texts):
        self.texts = list(texts)
        return [self.vector] * len(texts)


class FakeQdrant:
    """记录 query_points 入参，返回预置候选点（按给定顺序）。"""

    def __init__(self, qids: list[str]) -> None:
        self._qids = qids
        self.kwargs: dict | None = None

    async def query_points(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            points=[SimpleNamespace(payload={"question_id": qid}, score=1.0) for qid in self._qids]
        )


class FakeRanker:
    """记录 (query, documents, top_n)，返回预置排序结果。"""

    def __init__(self, result: list[tuple[int, float]]) -> None:
        self._result = result
        self.calls: list = []

    async def __call__(self, query, documents, *, top_n=None):
        self.calls.append((query, documents, top_n))
        return list(self._result)


def _row(qid: str) -> dict:
    return {"question_id": qid, "question": f"{qid} 的题干？", "key_points": [f"{qid} 要点"]}


@pytest.fixture
def install(monkeypatch):
    """注入 fakes；fetch_by_ids 故意乱序返回，验证候选保序逻辑。"""

    def _install(qids: list[str], rows: list[dict] | None = None) -> dict:
        embedder, qdrant, ranker = FakeEmbedder(), FakeQdrant(qids), FakeRanker([])
        calls: dict = {}

        def fake_fetch(db_path, ids):
            calls["ids"] = ids
            return list(reversed(rows or [_row(q) for q in ids]))  # 乱序模拟 SQLite IN

        monkeypatch.setattr(hybrid_search, "fetch_by_ids", fake_fetch)
        return {"embedder": embedder, "qdrant": qdrant, "ranker": ranker, "calls": calls}

    return _install


async def test_prefetch双路与融合形状(install):
    f = install(["q1"])

    await hybrid_search.hybrid_search(
        "查询文本", k=3, embedder=f["embedder"], qclient=f["qdrant"], ranker=f["ranker"], db_path=Path("x"),
    )

    kwargs = f["qdrant"].kwargs
    assert kwargs["collection_name"] == "questions"
    assert kwargs["limit"] == hybrid_search.RRF_CANDIDATES
    assert kwargs["with_payload"] is True
    prefetch = kwargs["prefetch"]
    assert [p.using for p in prefetch] == ["dense", "sparse"]
    assert [p.limit for p in prefetch] == [hybrid_search.RRF_CANDIDATES] * 2
    assert prefetch[0].query == f["embedder"].vector.dense  # dense 路直接用查询向量
    assert prefetch[1].query.indices == [3, 7]  # sparse 键转 int 升序
    assert prefetch[1].query.values == [0.2, 0.8]
    assert isinstance(kwargs["query"], hybrid_search.qm.FusionQuery)
    assert kwargs["query"].fusion == hybrid_search.qm.Fusion.RRF
    assert f["embedder"].texts == ["查询文本"]  # embed 只调一次


async def test_候选保序进rerank与输出键(install):
    f = install(["q3", "q1", "q2"])
    f["ranker"]._result = [(0, 0.9), (2, 0.7), (1, 0.6)]  # 全量返回

    result = await hybrid_search.hybrid_search(
        "q", k=3, embedder=f["embedder"], qclient=f["qdrant"], ranker=f["ranker"], db_path=Path("x"),
    )

    # join 乱序返回，但 rerank 的文档必须按候选序（q3, q1, q2）
    assert f["ranker"].calls[0][1] == ["q3 的题干？\nq3 要点", "q1 的题干？\nq1 要点", "q2 的题干？\nq2 要点"]
    assert f["ranker"].calls[0][0] == "q"
    # rerank 顺序决定输出顺序；输出行 = join 行透传（键同 search_questions）
    assert [r["question_id"] for r in result] == ["q3", "q2", "q1"]
    assert result[0] == _row("q3")


async def test_rerank_top_n取k与候选数较小者(install):
    f = install(["q1", "q2", "q3", "q4"])
    f["ranker"]._result = [(0, 0.9)]

    await hybrid_search.hybrid_search("q", k=2, embedder=f["embedder"], qclient=f["qdrant"], ranker=f["ranker"], db_path=Path("x"))

    assert f["ranker"].calls[0][2] == 2


async def test_候选1条跳过rerank(install):
    f = install(["q1"])

    result = await hybrid_search.hybrid_search("q", k=3, embedder=f["embedder"], qclient=f["qdrant"], ranker=f["ranker"], db_path=Path("x"))

    assert f["ranker"].calls == []
    assert [r["question_id"] for r in result] == ["q1"]


async def test_无候选返回空且不查库(install):
    f = install([])

    result = await hybrid_search.hybrid_search("q", embedder=f["embedder"], qclient=f["qdrant"], ranker=f["ranker"], db_path=Path("x"))

    assert result == []
    assert "ids" not in f["calls"]
    assert f["ranker"].calls == []


async def test_rerank抛错透传(install):
    f = install(["q1", "q2"])

    async def boom(query, documents, *, top_n=None):
        raise RuntimeError("SiliconFlow 挂了")

    with pytest.raises(RuntimeError, match="SiliconFlow"):
        await hybrid_search.hybrid_search("q", embedder=f["embedder"], qclient=f["qdrant"], ranker=boom, db_path=Path("x"))


async def test_payload缺question_id的脏数据跳过(install):
    f = install(["q1"])

    class DirtyQdrant(FakeQdrant):
        async def query_points(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                points=[
                    SimpleNamespace(payload={"domain": "rag"}, score=1.0),  # 缺 question_id
                    SimpleNamespace(payload={"question_id": "q1"}, score=1.0),
                ]
            )

    await hybrid_search.hybrid_search(
        "q", embedder=f["embedder"], qclient=DirtyQdrant([]), ranker=f["ranker"], db_path=Path("x"),
    )

    assert f["calls"]["ids"] == ["q1"]  # 脏点不进候选


async def test_空query报错(install):
    f = install([])
    with pytest.raises(ValueError, match="查询文本"):
        await hybrid_search.hybrid_search("   ", embedder=f["embedder"], qclient=f["qdrant"], ranker=f["ranker"], db_path=Path("x"))
