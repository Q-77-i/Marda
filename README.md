# Marda 码达

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Next.js 15](https://img.shields.io/badge/Next.js-15-000000?logo=nextdotjs&logoColor=white)
![LangGraph 1.2](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Q-77-i/Marda/actions/workflows/ci.yml/badge.svg)](https://github.com/Q-77-i/Marda/actions/workflows/ci.yml)

为校招计算机学生打造的 **Agent 面试训练**：自主出题、动态追问、短板诊断、学习推送，把一次模拟面试变成可复盘的闭环。

核心是一个 **Agent 智能面试引擎**——维护面试会话状态、自主出题、根据回答动态追问，而不是"智能客服式"的被动问答；RAG 检索作为 Agent 的一个工具参与出题与推荐。技术面与行为面跑在同一套状态机上。

> **校招简历副项目**：目标是可演示、可验收的端到端闭环（跑一场真面试 → 出报告 → 定位短板 → 推题），不是生产系统。开发过程见 [开发历程](docs/开发历程.md)。
>
> 界面预览：示例数据与匿名验收场次；实际内容随你的题库与报告变化。

![面试间：左阶段栏 / 中对话时间线 / 右面试官与候选人舞台；亮场默认，可切暗场](docs/assets/interview-room.png)

## 演示

**创建**（技术面 / 行为面 · 5–15 轮 · 自适应或锁定难度 · 可选简历预填）→ **开场与自我介绍** → **项目深挖**（结合简历出定制题干）→ **技术问答**（同域成块、难度自适应、覆盖率不足则追问 / 答错先澄清）→ **反问** → **报告** → **学习推荐**与**能力档案**。

四条输入通道共用同一套引擎：文字 · **语音**（豆包流式识别 + edge-tts 播报，转写可改再发）· **截图**（评分官看图）· **摄像头**（UI 模拟，**帧不上传、不落库、AI 不看**）。面试页即**面试间**：左阶段栏 / 中对话时间线 / 右面试官与候选人舞台；亮场默认、可切暗场，状态徽标全部由现有状态派生——**多模态只是新增通道，图 / 状态机 / 落库 / SSE 事件表一条未动**。

右栏另有两个独立工作台：**白板**（Excalidraw 导出 PNG 走图片通道，评分官会指出架构图缺失的组件/连线）与**代码工作台**（Monaco + 题库算法题 + 结构化点评与追问，独立于面试状态机，v1 不打分、不执行、不落库）。报告新增**表达复盘**：语速 / 长停顿（≥2s）/ 口头禅 / 每题语音时长，全部来自现有 ASR，不做面部。

![报告：五维能力雷达 + 短板域；未评分不产 0 分](docs/assets/report-charts.png)

![能力档案：五维雷达 / 总分曲线 / 知识域热力图 / 短板变化](docs/assets/profile.png)

![决策回放：节点时间线，每步的节点、耗时与状态变化](docs/assets/trace-timeline.png)

## 核心亮点

- **Agent 面试引擎，不是 LLM 聊天壳**：LangGraph 状态机管编排（五阶段 + 追问循环 + 断线续面，行为面复用同一套），而**阶段推进 / 轮数上限 / 追问决策 / 域配额 / 难度升降全部是纯代码**——可解释、可单测、UI 可回放；LLM 只做出题、评分、追问文案。每次追问与换题的**原因与决策同源**（唯一实现 `explain_decision`），所以回放里的原因不是事后旁白。→ [SPEC §4](docs/SPEC.md)
- **RAG 是 Agent 的一个工具**：出题走「题库检索 → 难度放宽 → LLM 生成」三级降级；混合检索 = dense + sparse 服务端 RRF + rerank，**rerank 按查询形态开关**（多漏点长查询实测净贡献为负即不用，短查询保留）。语料 1568 题、一题可多源、来源四要素带 license。→ [SPEC §5](docs/SPEC.md)
- **质量是量出来的，不是感觉出来的**：检索侧四变体离线基线（NDCG / RAGAS，**噪声地板先量后用**）；评分侧自研一致性 harness + **门禁**（σ̄ / MAE 阈值，超限非零退出）。→ [SPEC §4.13](docs/SPEC.md)
- **全过程可回放**：决策回放事件流（出题 / 评分 / 追问 / 换题的决策与原因）+ 节点时间线（**零写入**派生自 checkpoint 历史，补上决策事件里没有的节点）；一次面试 = Langfuse 一个 trace，成本按场次读回。→ [SPEC §4.7](docs/SPEC.md)
- **多模态不止语音**：白板结构点评（评分官对架构图指出缺失组件/连线，评语进报告）、代码工作台（Monaco + 题库算法题 + 结构化 Code Review + 追问，**独立于状态机**）、表达复盘（语速/停顿/口头禅/每题时长，纯代码统计、无面部）。三个功能都只加通道与展示，不改面试状态机与评分 schema。→ [SPEC §7.5](docs/SPEC.md)
- **断了也能用**：并发闸门 + 熔断 + 降级链——上游不可用时开场 / 出题 / 评分 / 报告全部走确定性兜底（**不调 LLM**），面试照常走完并**如实标注**；**未评分不产 0 分**，能力档案排除并说明。只降级「重试治不好」的失败，内容类错误仍走用户重试。→ [SPEC §3](docs/SPEC.md)
- **语料合规是红线**：开源语料白名单 + 每条带 `source/license/url`；个人题库与私有上传只本地使用，**派生文本同样不入 git**——提交前跑 `check_redline.py` 机械反查（两侧归一成中文骨架后滑 12 字窗口比对）。

## 架构

```mermaid
flowchart LR
  B["浏览器"] --> N["nginx :8080<br/>唯一入口"]
  N -->|"/api/*"| A["FastAPI + LangGraph<br/>面试状态机（多 worker）"]
  N -->|"其余"| W["Next.js 15<br/>standalone"]
  A --> DB[("PostgreSQL<br/>业务库 + checkpoint 独立库")]
  A --> R[("Redis<br/>会话锁 / 限流 / 全局 LLM 槽")]
  A --> Q[("Qdrant<br/>dense + sparse")]
  A --> E["embedding 容器<br/>本地 BGE-M3"]
  A -.->|"出题 / 评分 / 报告"| DS["DeepSeek"]
  A -.->|"rerank"| SF["SiliconFlow"]
  A -.->|"trace 与成本"| LF["Langfuse"]
  B -.->|"录音 PCM（WS 中继）"| A
  A <-.->|"流式 ASR"| VC["豆包流式语音识别"]
  A -.->|"TTS 播报"| ET["edge-tts"]
```

**设计原则**：确定性逻辑（阶段推进、轮数上限、追问决策、配额）全部用代码写死——可解释、可单测、UI 可回放；LLM 只负责出题、评分、追问文案这类语义任务。

> **P4 生产化形态**（多 worker + PostgreSQL + Redis）见 [docs/P4-生产化.md](docs/P4-生产化.md)：上图是**本地生产形态**（业务库与 checkpointer 在 PG，跨进程状态在 Redis，api 起 2 个 worker）；题库仍在 SQLite（离线资产、运行时只读）。单机形态（SQLite + 单 worker）仍是默认开关。

## 快速开始

```bash
cp .env.example .env          # 填 DEEPSEEK_API_KEY / SILICONFLOW_API_KEY / JWT_SECRET
docker compose up -d --build  # → http://localhost:8080
```

`nginx（唯一入口）→ web（Next standalone）/ api（uvicorn × 2 worker）`，背靠 `postgres`（业务库 + checkpoint 独立库）、`redis`（会话锁 / 限流 / 全局 LLM 槽）、`qdrant` 与 `embedding`（本地 BGE-M3）；题库与上传图片落宿主机 `data/`，容器重建不丢。语音 key 可选——不填则语音通道整体降级为文字，其余功能零影响。换端口：`MARDA_PORT=9000 docker compose up -d`。

**单机形态**（不依赖 PG / Redis）：

```bash
docker compose down                              # 先停默认形态（两边端口冲突）
docker compose -f docker-compose.legacy.yml up -d --build   # SQLite + 单 worker
```

> 用**独立 compose 文件**而不是 `--profile legacy`：profile 只能「多起一个服务」，
> **换不掉已存在的服务**——`--profile legacy up` 会同时起默认形态的 api（2 worker + PG）
> 和 api-legacy，nginx 仍指向 api，所谓回滚根本没生效。两边互斥，切形态前先 `down`。

三组开关彼此独立，`.env` 里按需改即可：`DB_BACKEND` / `CHECKPOINT_BACKEND` / `COORDINATION_BACKEND`
（`sqlite|postgres` / `sqlite|postgres` / `memory|redis`）。生产形态的取舍、迁移与回滚见
[docs/P4-生产化.md](docs/P4-生产化.md)。

**开发模式**（热重载）：

```bash
brew install pango && brew install --cask font-noto-sans-cjk-sc  # macOS：报告 PDF 导出所需
docker compose up -d qdrant embedding           # 向量库 + 嵌入服务
cd backend && uv sync && uv run uvicorn app.main:app --reload    # http://127.0.0.1:8000
cd frontend && pnpm install && pnpm dev                          # http://localhost:3000
```

密钥放仓库根 `.env`（见 `.env.example`，**不进仓库**）。`JWT_SECRET` 长度不足 32 会启动即失败；`LANGFUSE_*` 可选，留空即整体降级为零开销。

> **Windows 上跑 PG 形态**：uvicorn 的默认 loop 在 Windows 上是 `ProactorEventLoop`，
> 而 psycopg 的**异步**模式（官方 PG checkpointer 就是异步的）不支持它——
> 报错 “Psycopg cannot use the 'ProactorEventLoop' to run in async mode”。
> **切 event loop policy 对 uvicorn 无效**（它的 factory 直接返回 Proactor，绕开 policy），
> 必须显式给 factory：
>
> ```bash
> uv run uvicorn app.main:app --loop app.graph.checkpointer:uvicorn_loop_factory
> ```
>
> 容器里的 compose 命令已经带上了（Linux 上默认就是 Selector，那参数是空转）。

## 技术栈

| 层 | 选型 | 关键理由 |
| --- | --- | --- |
| 编排 | **LangGraph** 1.2 | 追问循环 + 断线续面需要状态机，checkpointer 原生支持 |
| 组件 | **LangChain** 1.4 | 切分器 / 加载器 / 工具装饰器，只在直线管道上用 |
| LLM | DeepSeek（`deepseek-flash` 主力 / `deepseek-v4-pro` 报告） | 双档控成本；flash 支持视觉，评分官得以看图 |
| 嵌入 | **本地 BGE-M3**（1024d，独立容器） | 一次前向出稠密 + 稀疏，省掉独立 BM25；模型自持，重嵌不依赖外部服务 |
| 向量库 | **Qdrant** 1.19 | 原生稀疏 + 服务端 RRF，混合检索不用换库 |
| 后端 | FastAPI + uvicorn + sse-starlette | Python 生态 + SSE 原生支持 |
| 前端 | Next.js 15 + TS + Tailwind + shadcn/ui + Recharts + **Monaco** | App Router + AI 生态组件最全；代码工作台的 Monaco 走同源 `/monaco/vs` 静态资源，不依赖 CDN |
| 可观测 | **Langfuse**（云形态） | 一次面试一个 trace：调用 / 成本按场次可查，面试过程可解释 |
| 语音 | **豆包流式识别** + **edge-tts** | 交互命门用付费流式（低延迟部分转写），播报用免费够用的；都只在独立通道上，挂了降级回文字 |
| 数据 | **PostgreSQL**（业务库 + checkpointer 独立库）+ **Redis**（协调）+ SQLite（题库，只读） | 可多 worker 共享状态；快照压缩 + 保留策略让库有界 |

## 项目结构

```
backend/            FastAPI + LangGraph
  app/              状态机（graph/）/ 提示词与 schema（agents/）/ RAG 与业务工具（tools/）
                    / 路由（api/）/ 服务层与落库 / PDF 渲染 / LLM 封装（llm.py）
  evals/            离线评测包（只被 scripts 调用，app 永不 import）
  scripts/          9 个真链路 smoke + 8 个 eval 与运维入口
data/
  scripts/          语料管道（bank → parse → combine → enrich → ingest）+ 改判 / 难度标注 / 红线检查
  curation/         人工改判表与难度标注表（key 是题干内容哈希）
  licenses/         语料来源清单与许可
frontend/           Next.js 15（九个路由 / lib 纯逻辑 / components）
docs/               PRD / SPEC / 开发历程
```

完整目录树与逐文件职责见 [SPEC §2](docs/SPEC.md)。

## 文档

- [docs/开发历程.md](docs/开发历程.md) — 每个里程碑做了什么、为什么这么定、怎么验证的（含失败的尝试与修正）
- [docs/P4-生产化.md](docs/P4-生产化.md) — P4 生产化：多 worker + PostgreSQL + Redis、checkpoint 压缩与保留、迁移与回滚
- [docs/PRD.md](docs/PRD.md) — 需求：功能清单、业务规则、页面、验收标准
- [docs/SPEC.md](docs/SPEC.md) — 技术规格：状态机 schema、规则、API 契约、数据库、前端设计、测试口径、风险清单
- [CLAUDE.md](CLAUDE.md) — 工程规约：协作方式、坑位清单、架构原则、语料红线
- [data/licenses/语料来源清单.md](data/licenses/语料来源清单.md) — 语料来源与许可
- [data/eval/summary.md](data/eval/summary.md) — 离线评估结论（纯数字，不含语料文本）

## 语料管道与合规

```bash
backend/.venv/bin/python data/scripts/parse_md.py      # 题库 md → 结构化 JSON
backend/.venv/bin/python data/scripts/parse_open.py    # 开源语料（四源）→ JSON，未采项逐项进报告
backend/.venv/bin/python data/scripts/combine.py       # 合并（自带个人题库零回归校验，不过则退出）
backend/.venv/bin/python data/scripts/enrich.py        # LLM 补关键点 / 预置追问（可断点续跑）
backend/.venv/bin/python data/scripts/ingest.py        # → SQLite + Qdrant 双写（含入库护栏）
```

**语料 1568 题（enabled 1096）**：个人题库 + 四源开源语料（均带明确 license，以 Agent 岗方向为主），全库按「L1 概念 / L2 原理与选型 / L3 底层与权衡」三档标注，12 个「难度 × 题量」组合全部可供。解析与合并口径见 [SPEC §6](docs/SPEC.md)。

**合规口径**：无 license / 非商用（NC）/ 来源不明的一律不入库；一题可有多源，来源明细进 `question_sources`（**license 按源记**）。**提交前做语料红线机械反查**：

```bash
backend/.venv/bin/python data/scripts/check_redline.py      # 查「现在提交会进仓库的改动」
backend/.venv/bin/python data/scripts/check_redline.py --all  # 全量自查
```

判据是把两侧归一成「只留中文字」的骨架再滑 12 字窗口（数字 / 标点 / 英文不切碎片段），命中即非零退出。**规则靠记忆执行不了**：这条检查立起来的第一件事，就是在已入库文件里查出 15 处遗留违规；后来又靠它查出检查器自己「关键点一条都没参与比对」的覆盖面漏洞。

## 验证与评估

```bash
cd backend
uv run pytest -q                              # 904 个测试（不需要任何密钥）
uv run python scripts/smoke_api.py            # 真链路走 HTTP 跑一场：报告 / PDF / 回放 / 推荐 / 档案核对
uv run python scripts/smoke_e2e.py            # 组合场：一场里语音进 / 语音出 / 截图 / 文字全跑通
uv run python scripts/smoke_degraded.py       # 降级链：假上游一律 503，一场面试仍能走完并如实标注
uv run python scripts/smoke_mcp.py            # MCP：stdio 子进程 + 真握手，三个工具各调一次
uv run python scripts/eval_retrieval_run.py   # 检索基线：四变体 × golden（需 Qdrant + 嵌入容器）
uv run python scripts/eval_judge_gate.py      # 评分门禁：超阈值非零退出（改评分 prompt 后必跑）

cd frontend && pnpm test && pnpm lint && pnpm build   # vitest 333 个 + lint + 构建

# P4 集成（需要真 PG / 真 Redis；没配环境变量就跳过）
MARDA_TEST_DATABASE_URL=postgresql://marda:marda@localhost:5432/marda MARDA_TEST_CHECKPOINT_URL=postgresql://marda:marda@localhost:5432/marda_checkpoints MARDA_TEST_REDIS_URL=redis://localhost:6379/1   uv run pytest -m "postgres or redis" -q
```

**离线评估不进 pytest 常规套件**（要真 Qdrant + 嵌入 + LLM，且门禁余量取自指标自身波动，会被上游抖动误伤）：检索侧出 NDCG / RAGAS 指标，评分侧出 σ̄（同题同答重复评 K 次的自波动）、MAE（vs 独立标注）与三档单调性。**先量噪声地板再读差值**——低于地板的「变化」不构成结论。命令与口径见 [SPEC §4.13](docs/SPEC.md)。

**CI** 跑不需要密钥的那一半：后端 pytest · 前端 lint + vitest + build · **语料入库守卫 + 红线检查器自检**。真比对与 smoke、评测不进 CI（语料不进仓库、真 key、花钱）。

## MCP 题库查询

题库检索能力按 **MCP** 暴露给任意客户端（Claude Code / Claude Desktop 等）：stdio 传输（本地子进程，题库不经过网络）、只读、**只暴露公共题**（私有上传连 id 都不可探测）。

```bash
claude mcp add marda-bank -- uv run --directory backend python -m app.mcp_server
```

三个工具：`search_questions`（混合检索）· `browse_questions`（按域/难度/公司/面次浏览）· `get_question`（单题 + 参考答案 + 关键点 + 来源四要素）。协议代码关在 `app/mcp_server/` 一个包里（单测扫描钉死别的模块不许 import `mcp`），规范版本锁 2026-07-28。

## 部署

演示形态就是本文档的「快速开始」一条命令（本地 Docker Compose，**本地生产形态**：多 worker + PostgreSQL + Redis）。**仍不上云、不上 K8s**——这一轮要解决的是「多 worker 下状态不共享、会话能并发 resume、快照只增不减」这三个真问题，不是部署编排。两条已记下的口径：**nginx 是唯一入口**（`/api` 段关 `proxy_buffering` —— SSE 流式推送的前提，最大的部署风险在本地就验证掉）；**镜像是分架构的**（arm64 构建的镜像在 amd64 服务器上跑不了，需 `buildx --platform linux/amd64` 或在服务器上构建）。

P4 的范围、配置项、迁移与回滚、已知边界见 [docs/P4-生产化.md](docs/P4-生产化.md)。

## License

[MIT](LICENSE) © Zhouq。仓库内不含任何第三方语料原文。
