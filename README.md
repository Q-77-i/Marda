# Marda 码达

面向计算机学生的 **Agent 智能面试学习平台**。业务闭环：**模拟面试 → 发现短板 → 针对性学习**。

核心是一个 **Agent 智能面试引擎**——维护面试会话状态、自主出题、根据回答动态追问，
而不是"智能客服式"的被动问答；RAG 检索作为 Agent 的一个工具参与出题与评估。

> 校招简历副项目。主项目已完成，本项目主要展示 Agent 系统设计与多模态能力。

## 技术栈

| 层 | 选型 | 关键理由 |
| --- | --- | --- |
| 编排 | **LangGraph** 1.2.11 | 追问循环 + 断线续面需要状态机，checkpointer 原生支持 |
| 组件 | **LangChain** 1.4.0 | 切分器 / 加载器 / 工具装饰器，只在直线管道上用 |
| LLM | DeepSeek（`deepseek-flash` 主力 / `deepseek-v4-pro` 难题报告） | 双档控成本，快档跑高频、深度档跑报告 |
| 嵌入 | BGE-M3（1024d，SiliconFlow / 本地） | 一次前向出稠密+稀疏，省掉独立 BM25 |
| 向量库 | **Qdrant** | 原生稀疏+服务端 RRF，阶段2 混合检索不用换库 |
| 后端 | FastAPI + uvicorn + sse-starlette | Python 生态 + SSE 原生支持 |
| 前端 | Next.js 15 + TS + Tailwind + shadcn/ui + Recharts | App Router + AI 生态组件最全 |
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

所有 LLM 调用走 [backend/app/llm.py](backend/app/llm.py) 这唯一入口（文案 `chat` / 结构化 `chat_json`，显式关 thinking + json_object + Pydantic 校验）。DeepSeek 坑位清单见 [CLAUDE.md](CLAUDE.md)。

## 语料管道

```bash
.venv/bin/python data/scripts/parse_md.py       # 题库 md → 结构化 JSON
.venv/bin/python data/scripts/parse_xmind.py    # xmind 解析 + 与 md 交叉对账
.venv/bin/python data/scripts/enrich.py         # LLM 补 key_points / follow_ups（可断点续跑）
.venv/bin/python data/scripts/ingest.py         # → SQLite + Qdrant 双写（幂等 upsert）
```

**语料合规**：入库语料必须有明确 license，每条带 `source`/`license`/`url` 三要素；
无 license、非商用（NC）、来源不明的语料一律不入库。详见 [data/licenses/语料来源清单.md](data/licenses/语料来源清单.md)。

## 面试状态机（LangGraph）

[backend/app/graph/](backend/app/graph/) 是面试引擎核心，五阶段（开场 → 自我介绍 → 技术问答 → 场景深挖 → 反问）全部由状态机驱动：

- **确定性规则全在代码**（[graph/rules/](backend/app/graph/rules/)）：追问决策、难度连击升降档、知识域配额、阶段推进、结束门槛，全部有单测钉死状态转移
- **LLM 只产出文案与评分**（[graph/nodes/](backend/app/graph/nodes/)）：出题（题库检索 → 难度放宽 → LLM 生成三级降级）、评分（五维 1-5 结构化）、追问文案、场景题（结合候选人项目经历定制）、报告
- **断线续面**：checkpointer（SQLite）以场次为粒度持久化，中断后 resume 状态一致（集成测试覆盖）

```bash
uv run pytest -q                                    # 后端 143 个测试
uv run python scripts/smoke_graph.py                # 真实 DeepSeek + Qdrant 跑一场短面试
```

## API（SSE 流式）

[backend/app/api/](backend/app/api/) 提供面试 REST API，契约定在 SPEC §7：SSE 流式（`meta` / `delta` / `question` / `done` / `error` 事件，首事件携带 `interview_id`）、会话恢复、报告与历史查询、场次物理删除；过程状态以 checkpoint 为权威，结束一次落库三表。

```bash
uv run python scripts/smoke_api.py                # 真实链路走 HTTP 跑一场短面试 + 落库验证
```

## 前端（三页面 + 流式联调）

[frontend/](frontend/) 是 Next.js 15 App Router，三个页面：仪表盘 `/`（新建 + 历史）、面试页 `/interview/[id]`、报告页 `/report/[id]`。请求走同源 `/api/*`（[next.config.ts](frontend/next.config.ts) rewrites → 后端），免 CORS 配置。

- **SSE 走 POST**：`EventSource` 只支持 GET，[lib/sse.ts](frontend/lib/sse.ts) 用 `fetch` + 手动分帧，兼容心跳注释与中文跨 chunk 截断
- **打字机在前端**：后端 `delta` 发完整文案，前端 [TypewriterQueue](frontend/lib/typewriter.ts) 逐字渲染（FIFO，前一题吐完才吐下一题；单测钉死顺序性）
- **报告图表**：Recharts 雷达图（五维 1-5）+ 横向条形图（短板域警示色**并附文字标注**，不靠颜色单独表意）；配色经调色板校验器明暗双模式检查
- **逐题复盘（FR-25）**：报告页每题一张复盘卡——我的回答（按 `【追问补充】` 标记分成「首答 / 追问补充 N」，不混成一大段）、五维得分、关键点覆盖对比（✓ 覆盖 / ✗ 遗漏）、题库题参考答案折叠展示（场景题无权威答案不渲染）；历史报告缺这些字段时退化为「题干 + 点评」
- **只读回放（FR-25）**：已结束场次进面试页即完整回放（复用会话恢复接口，隐藏输入框、顶栏换「查看报告」），报告页与回放页互链；结束当刻仍自动跳报告。完整是有前提的——对话历史在状态里全量保留、不截断（截断会让 SSE 差分失效、面试官文案漏发，长场次尤其明显）
- **刷新恢复与错误路径**：刷新后从 checkpoint 重建消息列表，已结束场次进只读回放；网络失败与 HTTP 4xx 均转中文文案 + 重试按钮，重试不重复插入消息
- **输入体验**：Enter 发送、Shift + Enter 换行，输入法"上屏回车"不误发送；输入框随内容长高，约 40% 视口高封顶后框内滚动
- **题量与记录**：题量 = 全场问答轮次（选 N 就是 N 轮，进度与报告自然一致）；历史记录带物理删除（确认弹窗）

```bash
cd frontend && pnpm test          # vitest：SSE 解析 + 打字机队列 + 展示格式化（48 个）
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

阶段 1 demo 已完成（T1–T7b）；阶段 2（P1）进行中：**P1-M1 面试复盘与回放已完成**（逐题复盘卡 / 只读回放 / 报告走 v4-pro），后续 M2–M12 见 [docs/PRD.md](docs/PRD.md) §8.1。

## 文档

- [docs/PRD.md](docs/PRD.md) — 需求（FR-01~24、核心业务规则、验收标准）
- [docs/SPEC.md](docs/SPEC.md) — 技术规格（状态机 schema、API 契约、数据库、测试计划）
