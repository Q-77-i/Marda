"""ingest 的 Qdrant 侧单测：命名双向量布局、重建语义、点结构与校验。

fake client/embedder 注入，不打真实 Qdrant、不做真实嵌入（服务 smoke 走容器）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from qdrant_client.models import SparseVectorParams, VectorParams

import ingest
from app.tools.embedding import Embedding


class FakeQdrant:
    """记录 delete/create/upsert 入参；get_collections 返回预置集合名。"""

    def __init__(self, existing: list[str] | None = None) -> None:
        self._existing = list(existing or [])
        self.deleted: list[str] = []
        self.created: dict | None = None
        self.points: list = []

    async def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=n) for n in self._existing])

    async def delete_collection(self, collection_name):
        self.deleted.append(collection_name)
        self._existing.remove(collection_name)

    async def create_collection(self, **kwargs):
        self.created = kwargs

    async def upsert(self, **kwargs):
        self.points = list(kwargs["points"])


class FakeEmbedder:
    """按文本数返回等量向量；sparse 故意乱序，验证写库前排序。"""

    def __init__(self, *, dim: int = ingest.VECTOR_SIZE, drop: int = 0) -> None:
        self.dim = dim
        self.drop = drop
        self.texts: list[str] = []

    async def embed(self, texts):
        self.texts = list(texts)
        kept = texts[: len(texts) - self.drop]
        return [Embedding(dense=[0.1] * self.dim, sparse={7: 0.5, 3: 0.25}) for _ in kept]


@pytest.fixture
def questions():
    return [
        {
            "question_id": "q_aaa",
            "question": "题干一？",
            "key_points": ["要点甲", "要点乙"],
            "topic": "检索",
            "domain": "rag",
            "difficulty": "L1",
            "company": None,
            "round": "一面",
        },
        {
            "question_id": "q_bbb",
            "question": "题干二？",
            "key_points": [],
            "topic": "记忆",
            "domain": "memory",
            "difficulty": "L2",
            "company": "字节跳动",
            "round": "二面",
        },
    ]


async def test_重建命名双向量(questions):
    client, embedder = FakeQdrant(existing=["questions"]), FakeEmbedder()

    await ingest.write_qdrant(questions, None, client=client, embedder=embedder)

    assert client.deleted == ["questions"]  # 旧布局（无名 dense）不兼容 → 先删
    assert client.created["collection_name"] == "questions"
    assert client.created["vectors_config"] == {
        ingest.DENSE: VectorParams(size=ingest.VECTOR_SIZE, distance="Cosine")
    }
    assert client.created["sparse_vectors_config"] == {ingest.SPARSE: SparseVectorParams()}


async def test_首次创建不删(questions):
    client = FakeQdrant(existing=[])

    await ingest.write_qdrant(questions, None, client=client, embedder=FakeEmbedder())

    assert client.deleted == []


async def test_点结构双向量与升序_sparse(questions):
    client = FakeQdrant()

    await ingest.write_qdrant(questions, None, client=client, embedder=FakeEmbedder())

    first = client.points[0]
    assert first.id == ingest.point_id("q_aaa")  # 内容寻址，重跑幂等
    assert set(first.vector) == {ingest.DENSE, ingest.SPARSE}
    assert first.vector[ingest.DENSE] == pytest.approx([0.1] * ingest.VECTOR_SIZE)
    assert first.vector[ingest.SPARSE].indices == [3, 7]  # 升序
    assert first.vector[ingest.SPARSE].values == [0.25, 0.5]
    assert first.payload["question_id"] == "q_aaa"


async def test_嵌入文本走_doc_text(questions):
    embedder = FakeEmbedder()

    await ingest.write_qdrant(questions, None, client=FakeQdrant(), embedder=embedder)

    assert embedder.texts == ["题干一？\n要点甲\n要点乙", "题干二？"]


async def test_向量数不一致报错(questions):
    with pytest.raises(RuntimeError, match="与题目数 2 不一致"):
        await ingest.write_qdrant(questions, None, client=FakeQdrant(), embedder=FakeEmbedder(drop=1))


async def test_维度不符报错(questions):
    with pytest.raises(RuntimeError, match="dense 维度 8"):
        await ingest.write_qdrant(questions, None, client=FakeQdrant(), embedder=FakeEmbedder(dim=8))
