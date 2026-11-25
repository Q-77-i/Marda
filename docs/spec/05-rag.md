> 本文是 SPEC 的 §5 正文；全局索引见 [SPEC.md](SPEC.md)。章节号冻结，不因拆分改变。

## 5. RAG

### 5.1 向量层

- 分块：**每题一 doc**；嵌入文本 = **题干 + 关键点**（关键点是答案的要点提炼，题库搜索按考点召回靠它；答案全文过长会稀释题干）。文本格式单一来源 = `tools/embedding.py::question_doc_text(question, key_points)`，ingest 与 hybrid_search 共用（文本漂移会让 rerank 打分对象与嵌入对象不一致）。
- 嵌入服务：本地 BGE-M3 **独立容器**（`backend/embedding_service/`，权重 build 时烤进镜像；SiliconFlow 嵌入退场，只留 rerank）。契约：

| 端点 | 约定 |
| --- | --- |
| `POST /embed` | 请求 `{"texts": [str, …]}`，1 ≤ 条数 ≤ 128（超限 413） |
| 响应 200 | `{"dense": [[float,…], …], "sparse": [{token_id: weight}, …], "dim": int}` |
| `GET /healthz` | `{"status": "ok", "model": …, "loaded": bool}` |
| 失败 | 503 = 模型未加载（启动窗口期）；非 200 由客户端 `raise_for_status` 上抛 |

  - dense 为 L2 归一 1024d；sparse 的 token_id 是 JSON key（字符串）、已剔零、按 token_id 升序，调用方转 int 升序后作 Qdrant SparseVector。
  - `dim` = dense 实际维度，**客户端据此校验**——模型配置漂移在客户端报错，而不是把错位向量写进库。
  - 推理 max_length 512、inner batch 8、CPU 同步 def 走线程池（不堵 /healthz）。
  - api 侧客户端 `tools/embedding.py`（`EMBEDDING_URL`，容器内 `http://embedding:8091`）：按 64 分批保序（超时 300s），响应条数 / 维度不符 → RuntimeError。
- Qdrant collection `questions`：**命名双向量** `dense`（1024d cosine，L2 归一）+ `sparse`（BGE-M3 lexical weights）；payload = {question_id, domain, topic, difficulty, round, company}。
- `ingest.py`：parsed JSON → SQLite + Qdrant 双写；**Qdrant 侧每次 drop 重建**（命名双向量布局是 RRF prefetch 的硬前提，不支持无名字段在线改命名；出题检索走 payload 过滤、不碰向量，重建无停机影响），SQLite 侧按 question_id upsert + `DELETE NOT IN` 全量同步。
- `search_questions(domain, difficulty, exclude_ids, k=3)`：**出题场景不走向量**——无查询文本。payload 过滤 + 随机取 k + SQLite join 完整题目。

### 5.2 混合检索

- `hybrid_search(query, *, k=5)`（`tools/hybrid_search.py`）：query 本地 embed → Qdrant Query API `prefetch`（dense + sparse 各 limit 30）→ `FusionQuery(Fusion.RRF)` 融合候选 30 → SQLite join（payload 只存过滤字段，且 rerank 需要文档文本）→ rerank → top k。输出键同 search_questions，仅 enabled 题；join 后按候选序重排（SQLite IN 查询不保序）；空 query 报 ValueError。
- **筛选下推到两路 prefetch**：`filters` 接受 `domain/difficulty/company/round`，构造 `Filter` 后**每一路 prefetch 都要挂**。参数名是 `query_filter`（写 `filter` 直接抛 `Unknown arguments`）；挂在顶层是错的——`limit` 是 prefetch 级的，两路会先各取满 30 条全集候选再融合，无关域的候选把名额吃光。无筛选时不构造空 `Filter`（空 Filter 与 None 语义不同）。单测钉死三条：两路都挂、空值维度不生成条件、无筛选时 `prefetch[i].filter is None`。
- 候选 ≤1 时跳过 rerank；**rerank 失败直接抛**。Rerank 文档 = 题干 + 关键点（与嵌入文本同一函数）。
- **`rerank=False` 跳过 rerank（P2-M2）**：直接返回 RRF 融合序前 k 条，给**已知的多漏点长查询**用（判据在调用方，检索层不猜）。走 rerank 时 query 先经 `_rerank_query` 剥掉首段域标签（实测 −0.152 → −0.042：标签是同域所有候选的共性词、域约束本就由 `filters` 承担；剥完为空则原样；无「；」的短查询逐字不变）。
- rerank 客户端（`tools/rerank.py`）：httpx 直调 `POST {siliconflow_base_url}/rerank`（Bearer 鉴权，超时 30s），请求 `{model: "BAAI/bge-reranker-v2-m3", query, documents, top_n, return_documents: false}`；响应 `results: [{index, relevance_score}]`——客户端校验 index 在范围内且唯一、score 为有限数，否则 RuntimeError；空 documents 不发请求。
- 消费方：题库搜索（FR-12）/ 学习推荐（FR-20）。
