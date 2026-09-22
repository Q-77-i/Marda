# Marda 码达

面向计算机学生的 **Agent 智能面试学习平台**。业务闭环：**模拟面试 → 发现短板 → 针对性学习**。

核心是一个 **Agent 智能面试引擎**——维护面试会话状态、自主出题、根据回答动态追问，
而不是"智能客服式"的被动问答；RAG 检索作为 Agent 的一个工具参与出题与评估。

> 校招简历副项目。主项目已完成，本项目主要展示 Agent 系统设计与多模态能力。

## 技术栈

| 层 | 选型 | 为什么 |
| --- | --- | --- |
| 编排 | **LangGraph** 1.2.11 | 面试流程有"向后的箭头"（追问循环、难度升降、中断恢复），状态机是刚需；内置 checkpointer 让"断线续面"几乎零成本 |
| 组件 | **LangChain** 1.4.0 | 切分器 / 加载器 / 工具装饰器，只在直线管道上用 |
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
    api/            路由（SSE 流）
    service.py      服务层（图单例 / 事件翻译 / 落库编排）
    db.py           业务库持久化（interviews / answers / reports）
    rag/            嵌入 / Qdrant / 检索
  tests/
data/
  scripts/          语料管道：parse_md / parse_xmind / enrich / ingest
  parsed/           解析产物（gitignore）
  licenses/         语料来源清单与许可
frontend/           Next.js 15（app 路由 / lib 纯逻辑 / components）
  lib/              sse 流解析 / typewriter 队列 / api 封装 / 展示格式化
  components/       面试页客户端 / 报告页客户端 / 图表 / UI 基础件
docs/               PRD / SPEC（个人规划文档不进仓库）
```

## 快速开始

### 一键起（演示 / 验收，Docker）

```bash
cp .env.example .env          # 填 DEEPSEEK_API_KEY / SILICONFLOW_API_KEY
docker compose up -d --build  # → http://localhost:8080
```

`nginx（唯一入口） → web（Next standalone）/ api（uvicorn） → qdrant` 四个容器；业务库与 checkpointer 落宿主机 `data/`，容器重建不丢。换端口：`MARDA_PORT=9000 docker compose up -d`。

### 开发模式（热重载）

```bash
docker compose up -d qdrant                     # 只起向量库（回环 6333）
cd backend && uv sync && uv run uvicorn app.main:app --reload    # http://127.0.0.1:8000/healthz
cd frontend && pnpm install && pnpm dev         # http://localhost:3000
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

## 面试状态机（LangGraph）

[backend/app/graph/](backend/app/graph/) 是面试引擎核心，五阶段（开场 → 自我介绍 → 技术问答 → 场景深挖 → 反问）全部由状态机驱动：

- **确定性规则全在代码**（[graph/rules/](backend/app/graph/rules/)）：追问决策（PRD §4.2：澄清 ≤1 / 遗漏 <70% 阈值 ≤2 / 单题总追问 ≤3）、难度连击升降档、知识域配额（largest remainder）、阶段推进、结束门槛（≥60% 题量），全部有单测钉死状态转移
- **LLM 只产出文案与评分**（[graph/nodes/](backend/app/graph/nodes/)）：出题（题库检索 → 难度放宽 → LLM 生成三级降级）、评分（五维 1-5 结构化）、追问文案、场景题（结合候选人项目经历定制）、报告
- **断线续面**：checkpointer（SQLite）以 `thread_id = interview_id` 持久化，中断后 resume 状态一致（集成测试覆盖）
- **单 interrupt 点**：每次用户消息 = 一次 resume，route 按 phase 纯代码分发

```bash
uv run pytest -q                                    # 118 个测试（规则 36 + 图集成 8 + API 12 + …）
uv run python scripts/smoke_graph.py                # 真实 DeepSeek + Qdrant 跑一场短面试
```

## API（SSE 流式）

[backend/app/api/](backend/app/api/) 提供面试 REST API（SPEC §7），服务层 [backend/app/service.py](backend/app/service.py) 持有图单例并翻译 SSE 事件：

- `POST /api/interviews` 创建场次并流式执行开场（首事件 `meta` 携带 `interview_id`）
- `POST /api/interviews/{id}/messages` 发送回答，流式返回 `delta`（面试官文案）/ `question`（新题）/ `meta`（进度）/ `done`（结束）/ `error`
- `GET /api/interviews/{id}` 会话恢复（checkpoint 为权威）；`GET /api/interviews/{id}/report` 报告；`GET /api/interviews` 历史列表
- `DELETE /api/interviews/{id}` 物理删除场次（业务库三表 + checkpointer 线程，进行中也允许；前端每条记录带确认弹窗删除按钮）
- 面试结束后一次落库：answers / reports / interviews 收尾（[backend/app/db.py](backend/app/db.py)，SPEC §8）
- 打字机效果由前端客户端渲染（delta 为完整文案），15s 心跳走 sse-starlette 内置 ping

```bash
uv run uvicorn app.main:app --reload              # 起服务
uv run python scripts/smoke_api.py                # 真实链路走 HTTP 跑一场短面试 + 落库验证
```

## 前端（三页面 + 流式联调）

[frontend/](frontend/) 是 Next.js 15 App Router，三个页面：仪表盘 `/`（新建 + 历史）、面试页 `/interview/[id]`、报告页 `/report/[id]`。
请求走同源 `/api/*`（[next.config.ts](frontend/next.config.ts) rewrites → 后端），免 CORS 配置。

- **SSE 走 POST**：`EventSource` 只支持 GET，所以 [lib/sse.ts](frontend/lib/sse.ts) 用 `fetch` + 手动分帧；兼容 `\n\n` / `\r\n\r\n`、跳过 `: ping` 心跳注释、`TextDecoder` 用 `stream: true` 兜住中文跨 chunk 截断
- **打字机在前端**：后端 `delta` 发的是完整文案，前端 [TypewriterQueue](frontend/lib/typewriter.ts) 按 30ms 节拍自适应吐字（FIFO，前一题吐完才吐下一题）；纯逻辑类，单测钉死连发顺序性
- **报告图表**：Recharts 雷达图（五维 1-5）+ 横向条形图（短板域换警示色**并附文字标注**，不靠颜色单独表意）；配色经调色板校验器六项检查（明暗双模式），图表颜色用 `getComputedStyle` 运行时读 CSS 变量（recharts 写的是 SVG 属性，`var()` 不解析）并跟随 `prefers-color-scheme` 重读
- **刷新恢复**：挂载时拉 `GET /api/interviews/{id}` 从 checkpoint 重建消息列表；已结束的场次直接跳报告页
- **错误路径**：网络层失败（后端没起）与 HTTP 4xx 都转成中文文案 + 重试按钮，重试不重复插入用户消息
- **输入细节**：Enter 发送、Shift + Enter 换行；输入法组字中的回车（含 macOS 上用回车"上屏"的那一次）只上屏、不发送——`isComposing` 在这些浏览器里会先变 false，所以组字状态是自己维护的；**输入框随内容长高，涨到约 40% 视口高封顶后框内滚动**（高度由 `fitInput()` 按 `scrollHeight` 算，上限取自 `max-h-[40dvh]`），长高时消息区保持贴底。**高度不依赖 CSS `field-sizing`**——该属性 2026-06 才进 Baseline 全浏览器可用，Safari 18 / 旧版浏览器直接忽略，框会永远停在 `rows` 那么高
- **题量语义 = 全场问答轮次**（T7a-R1）：用户选的 N 就是会被问的 N 轮（内部组成 N−1 技术 + 1 场景由引擎决定，不对用户暴露），`answered_count ≤ question_count` 恒成立，进度与报告自然一致（旧版"场景题额外 +1"导致的 `16/15` 问题从根上消除，封顶仅作旧数据兼容）。**题型语义由后端定义**（T7a）：逐题点评 payload 每条带 `question_type`（tech/scenario）与 `number`（计入轮次的题型按序编号），前端 `commentLabels()` 只消费不推断（非技术题型显示「第 N 题 · 场景题」，未知题型显示原值）；历史 payload 按 domain/位置兜底。每条面试记录带**物理删除按钮**（确认弹窗 → DELETE 接口，三表 + checkpointer 线程一并清除）

```bash
cd frontend && pnpm test          # vitest：SSE 解析 + 打字机队列 + 展示格式化（40 个）
pnpm lint && pnpm build
```

## 部署：本地一键起（Docker Compose）

阶段 1 的部署形态就是这份编排 + 本地一键起（演示/验收即 `docker compose up -d --build`）；**服务器部署与部署方案随阶段 3 再定**（PRD §8），届时同一份编排直接复用。

```
浏览器 → nginx:8080 ─┬─ /api/* → api:8000（uvicorn + LangGraph）
                     └─ 其余   → web:3000（Next standalone）
                                  qdrant:6333（仅内网 + 本机回环；无鉴权，不对外暴露）
```

- **nginx 是唯一入口**，本地与线上同构——`/api` 段关 `proxy_buffering`（SSE 打字机的前提）+ `proxy_read_timeout 300s`，这条最大的部署风险在本地就验证掉
- **数据**：`./data`（业务库 + checkpointer）与 `./docker/volumes/qdrant` 挂宿主机，容器重建不丢
- **密钥**：`.env` 经 compose `env_file` 注入，不进镜像不进仓库
- **阶段 3 再定部署方案**（是否上云、服务器选型届时评估）。唯一值得提前记的一条：**镜像是分架构的**——Mac（arm64）本地构建的镜像在 amd64 服务器上跑不了，要么在服务器上构建，要么 `buildx --platform linux/amd64`

## 开发进度

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| T1 | 脚手架（FastAPI + Next.js + shadcn/ui） | ✅ |
| T2 | 语料管道（解析 / 富化 / 入库，342 题，完整率 100%） | ✅ |
| T3 | LLM 封装（关 thinking、JSON 校验、两层重试） | ✅ |
| T4 | 面试状态机（五阶段 + 追问决策 + 配额 + checkpoint 续面） | ✅ |
| T5 | API（路由 + SSE 流式 + 落库，118 测试） | ✅ |
| T6 | 前端三页面 + 流式联调（打字机 / 雷达图 / 断线恢复） | ✅ |
| T7a | 题型语义 + 轮次语义建模（question_type/number 契约、题量 = 问答轮次、删除接口） | ✅ |
| T7b | 容器化与演示就绪（compose 一键起：nginx + web + api + qdrant） | ✅ |

## 文档

- [docs/PRD.md](docs/PRD.md) — 需求（FR-01~24、核心业务规则、验收标准）
- [docs/SPEC.md](docs/SPEC.md) — 技术规格（状态机 schema、API 契约、数据库、测试计划）
