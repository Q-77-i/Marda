"""离线评测包（P1-M12）：只被 scripts/ 下的入口 import，app 永不 import。

- `retrieval_metrics`：Recall@k / NDCG@k / MRR 纯函数
- `golden`：golden 查询集的读写与校验
- `retrieve`：四个检索变体（dense / sparse / RRF / hybrid）真栈取数
- `label_relevance`：LLM 判相关 + 人工复核产物
"""
