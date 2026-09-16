# Marda 码达

面向计算机学生的 **Agent 智能面试学习平台**。业务闭环：**模拟面试 → 发现短板 → 针对性学习**。

核心是一个 **Agent 智能面试引擎**——维护面试会话状态、自主出题、根据回答动态追问，
而不是"智能客服式"的被动问答；RAG 检索作为 Agent 的一个工具参与出题与评估。

> 校招简历副项目。主项目已完成，本项目主要展示 Agent 系统设计与多模态能力。

## 技术栈

| 层 | 选型 | 为什么 |
| --- | --- | --- |
| 编排 | **LangGraph** 1.x | 面试流程有"向后的箭头"（追问循环、难度升降、中断恢复），状态机是刚需；内置 checkpointer 让"断线续面"几乎零成本 |
| 组件 | **LangChain** 1.x | 切分器 / 加载器 / 工具装饰器，只在直线管道上用 |
| LLM | DeepSeek（`deepseek-flash` 主力 / `deepseek-v4-pro` 难题报告） | 官方 `openai` SDK + `base_url` 直连（绕开第三方封装的 `reasoning_content` 回传缺陷） |
| 嵌入 | BGE-M3（1024d，SiliconFlow / 本地） | DeepSeek 不提供 embedding API |
| 向量库 | **Qdrant** | 单容器可跑、payload 过滤与原生稀疏+RRF，阶段 2 混合检索不用换库 |
| 后端 | FastAPI + uvicorn + sse-starlette | 流式打字机输出 |
| 前端 | Next.js 15 + TS + Tailwind + shadcn/ui + Recharts | 报告页雷达图 |
| 业务库 | SQLite（阶段 1）→ PostgreSQL | checkpointer 同步升级 |

**设计原则**：确定性逻辑（阶段推进、轮数上限、追问决策、配额）全部用代码写死——可解释、可单测、UI 可回放；LLM 只负责出题、评分、追问文案这类语义任务。

## 目录结构

```
backend/            FastAPI + LangGraph
  app/
    domain.py       知识域定义（单一来源：配额 / 映射 / 报告共用）
    graph/          状态机（state / graph / nodes / rules）
    agents/         角色节点与结构化输出 schema
    tools/          RAG 检索工具
    rag/            嵌入 / Qdrant / 检索
  tests/
data/
  scripts/          语料管道：parse_md / parse_xmind / enrich / ingest
  parsed/           解析产物（gitignore）
  licenses/         语料来源清单与许可
frontend/           Next.js 15
docs/               PRD / SPEC（个人规划文档不进仓库）
```

## 快速开始

```bash
# 后端（需要 uv）
cd backend && uv sync && uv run pytest -q
uv run uvicorn app.main:app --reload          # http://127.0.0.1:8000/healthz

# 前端（需要 pnpm）
cd frontend && pnpm install && pnpm dev        # http://localhost:3000

# 向量库
docker run -d --name marda-qdrant -p 6333:6333 \
  -v "$PWD/docker/volumes/qdrant:/qdrant/storage" qdrant/qdrant:v1.19.0
```

密钥放仓库根目录 `.env`（见 `.env.example`，**不进仓库**）：

```
DEEPSEEK_API_KEY=
SILICONFLOW_API_KEY=
```

## LLM 封装

所有 LLM 调用走 [backend/app/llm.py](backend/app/llm.py) 这唯一入口：

```python
text = await chat(messages)                           # 文案：开场 / 出题 / 追问 / 结束语
score = await chat_json(messages, schema=ScoreItem)   # 结构化：评分 / 提炼 / 报告
```

- 官方 `openai` SDK + `base_url` 直连 DeepSeek，**所有调用显式关 thinking**（思考模式与结构化输出冲突会 400）
- 结构化输出 = `json_object` + JSON Schema 注入 prompt + Pydantic 校验，校验失败重请求 1 次
- 两层重试：网络层（429/5xx/超时）指数退避 3 次；失败统一抛 `LLMError(retryable=…)` 供 SSE error 映射
- smoke（真实 API，验两条通路）：`cd backend && uv run python scripts/smoke_llm.py`

## 语料管道

```bash
.venv/bin/python data/scripts/parse_md.py       # 题库 md → 结构化 JSON（351 → 合并 342 条）
.venv/bin/python data/scripts/parse_xmind.py    # xmind 解析 + 与 md 交叉对账（题干重合 100%）
.venv/bin/python data/scripts/enrich.py         # LLM 补 key_points / follow_ups（可断点续跑）
.venv/bin/python data/scripts/ingest.py         # → SQLite + Qdrant 双写（幂等 upsert）
```

**语料合规**：入库语料必须有明确 license，每条带 `source`/`license`/`url` 三要素；
无 license、非商用（NC）、来源不明的语料一律不入库。详见 [data/licenses/语料来源清单.md](data/licenses/语料来源清单.md)。

## 开发进度

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| T1 | 脚手架（FastAPI + Next.js + shadcn/ui） | ✅ |
| T2 | 语料管道（解析 / 富化 / 入库，342 题，完整率 100%） | ✅ |
| T3 | LLM 封装（关 thinking、JSON 校验、两层重试） | ✅ |
| T4 | 面试状态机（五阶段 + 追问决策 + 配额） | ⬜ |
| T5 | API（路由 + SSE 流式） | ⬜ |
| T6 | 前端三页面 + 流式联调 | ⬜ |
| T7 | 部署验收 | ⬜ |

## 文档

- [docs/PRD.md](docs/PRD.md) — 需求（FR-01~24、核心业务规则、验收标准）
- [docs/SPEC.md](docs/SPEC.md) — 技术规格（状态机 schema、API 契约、数据库、测试计划）
