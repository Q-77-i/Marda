# Marda 码达

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Next.js 15](https://img.shields.io/badge/Next.js-15-000000?logo=nextdotjs&logoColor=white)
![LangGraph 1.2](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Q-77-i/Marda/actions/workflows/ci.yml/badge.svg)](https://github.com/Q-77-i/Marda/actions/workflows/ci.yml)

为校招计算机学生打造的 **Agent 智能面试学习平台**：模拟面试 → 能力报告 → 短板定位 → 针对性学习，形成可复盘的闭环。

> **定位**：校招简历副项目，目标是可演示、可验收的端到端闭环，不是生产系统。P4 额外验证了多 worker + PostgreSQL + Redis 的本地多进程形态；题库仍是离线只读资产。

![面试间：左阶段栏 / 中对话时间线 / 右面试官与候选人舞台；亮场默认，可切暗场](docs/assets/interview-room.png)

> 界面为示例数据与匿名验收场次；实际内容随你的题库与报告变化。

## 能做什么

- **像真面试一样追问**：LangGraph 五阶段状态机维护整场会话（5 / 10 / 15 轮，断线可续、草稿刷新不丢）；阶段推进、轮数上限、追问与换题决策、难度升降全部是纯代码规则，可解释、可单测、可回放。LLM 只负责出题、评分与追问文案。
- **一场面试的完整链路**：创建（技术面 / 行为面，可选简历预填；同一份简历只解析一次）→ 开场、自我介绍、技术问答、项目深挖、反问 → 五维报告（含 PDF 导出）、短板定位、学习推荐、能力档案。
- **RAG 是 Agent 的工具**：出题走「题库检索 → 难度放宽 → LLM 生成」三级降级；混合检索 = dense + sparse 服务端 RRF + 按查询形态开关的 rerank。
- **多种输入通道**：文字、语音（豆包流式 ASR + edge-tts，附带语速 / 长停顿 / 口头禅复盘）、截图（视觉评分）；摄像头是 UI 模拟：帧不上传、不落库、AI 不看。右栏另有白板与代码工作台：白板走图片通道，代码工作台独立于状态机（v1 不打分、不执行、不落库）。
- **可复盘、可观测**：决策回放 + 节点时间线；一次面试 = 一个 Langfuse trace，成本按场次读回。
- **降级不静默**：并发闸门 + 熔断 + 确定性兜底；上游不可用时面试仍能走完，未评分不产 0 分，降级在报告里如实标注。
- **语料合规**：开源语料只取白名单，每题带来源四要素（`source/license/url/source_detail`）；个人题库与派生文本不进 git，提交前机械反查。

细节与取舍见 [SPEC](docs/SPEC.md)，过程记录见 [开发历程](docs/开发历程.md)。

## 界面

| 报告：五维雷达 + 短板域 | 能力档案：曲线 / 热力图 / 短板变化 |
| --- | --- |
| ![报告](docs/assets/report-charts.png) | ![能力档案](docs/assets/profile.png) |

![决策回放：节点时间线，每步的节点、耗时与状态变化](docs/assets/trace-timeline.png)

## 快速开始

```bash
cp .env.example .env          # 填 DEEPSEEK_API_KEY / SILICONFLOW_API_KEY / JWT_SECRET
docker compose up -d --build  # → http://localhost:8080
```

`nginx` 是唯一入口；默认形态 = `web` + `api`（2 worker）+ PostgreSQL + Redis + Qdrant + 本地 BGE-M3。题库与上传文件落宿主机 `data/`，PG / Redis / Qdrant 数据落 `docker/volumes/`，容器重建不丢。语音 key 可选，不填则语音通道降级为文字。依赖：Docker / Docker Compose；开发模式另需 uv 与 pnpm（Node 24）。

真实面试链路会调用 DeepSeek / SiliconFlow（产生少量费用）；测试用 FakeLLM，不需要 key。

**开发模式**（热重载，两个终端）：

```bash
# 终端 1：向量库与嵌入服务，然后起后端
docker compose up -d qdrant embedding
cd backend && uv sync && uv run uvicorn app.main:app --reload   # :8000

# 终端 2：起前端
cd frontend && pnpm install && pnpm dev                          # :3000
```

> macOS 上导出报告 PDF 还需 `brew install pango && brew install --cask font-noto-sans-cjk-sc`（见 [SPEC §4.9](docs/SPEC.md)）。

单机形态（SQLite + 单 worker）用 `docker-compose.legacy.yml`；端口、Windows、三组开关与回滚见 [P4-生产化](docs/P4-生产化.md)。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 编排 / 组件 | LangGraph 1.2 · LangChain 1.4 |
| LLM | DeepSeek（`deepseek-flash` 主力 / `deepseek-v4-pro` 报告） |
| 检索 | Qdrant 1.19 · 本地 BGE-M3（dense + sparse）· bge-reranker |
| 后端 | FastAPI · uvicorn · sse-starlette |
| 前端 | Next.js 15 · TypeScript · Tailwind · shadcn/ui · Recharts · Monaco |
| 数据 | PostgreSQL 16 · Redis 7 · SQLite（题库只读） |
| 可观测 | Langfuse |
| 语音 | 豆包流式 ASR · edge-tts |

## 项目结构

```text
backend/   FastAPI + LangGraph（状态机 / agents / RAG / API / 服务层）
frontend/  Next.js 15（页面 / lib 纯逻辑 / components）
data/      语料管道、题库、评测与上传文件
docs/      PRD / SPEC / P4-生产化 / 开发历程
```

完整目录树与逐文件职责见 [SPEC §2](docs/SPEC.md)；系统拓扑见 [SPEC §2.1](docs/SPEC.md)，设计原则是**确定性逻辑用代码写死、语义任务交给 LLM**；P4 多 worker 形态见 [P4-生产化 §2](docs/P4-生产化.md)。

## 验证

```bash
cd backend && uv run pytest -q          # 986 passed / 20 skipped（未配 PG/Redis）
cd frontend && pnpm test                # 353 passed
```

本机实测（2026-11）；真链路 smoke、离线评测与评分门禁见 [SPEC §10](docs/SPEC.md)，P4 集成、备份与回滚见 [P4-生产化 §7](docs/P4-生产化.md)。CI 四个 job：后端 pytest · 前端 lint / vitest / build · P4 集成（真 PG + Redis）· 语料守卫。

## 题库与合规

题库是离线资产：个人题库 + 四源开源语料，共 **1568 题（enabled 1096）**，按 L1 / L2 / L3 标注，每题带来源四要素（`source/license/url/source_detail`）。个人题库与派生文本不进 git；提交前跑：

```bash
backend/.venv/bin/python data/scripts/check_redline.py
```

解析、合并、入库与白名单口径见 [SPEC §6](docs/SPEC.md)。

## 可选：MCP 题库查询

题库检索可按 MCP 暴露给 Claude Code / Claude Desktop：stdio、只读、只公共题。

```bash
claude mcp add marda-bank -- uv run --directory backend python -m app.mcp_server
```

三个工具：`search_questions` / `browse_questions` / `get_question`。契约见 [SPEC §7.4](docs/SPEC.md)。

## 文档

| 文档 | 读什么 |
| --- | --- |
| [docs/PRD.md](docs/PRD.md) | 产品需求：功能、业务规则、页面、验收标准 |
| [docs/SPEC.md](docs/SPEC.md) | 技术规格：状态机、API、数据库、前端、测试口径 |
| [docs/P4-生产化.md](docs/P4-生产化.md) | 生产化：多 worker + PG + Redis、迁移、备份、已知边界 |
| [docs/开发历程.md](docs/开发历程.md) | 每个里程碑做了什么、为什么这么定、怎么验证 |
| [data/eval/summary.md](data/eval/summary.md) | 离线检索 / 评分评估结论（纯数字，不含语料文本） |
| [data/licenses/语料来源清单.md](data/licenses/语料来源清单.md) | 语料来源与许可明细 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 本地开发、分支、Issue / PR |
| [CLAUDE.md](CLAUDE.md) | 工程规约、坑位清单、语料红线 |

## License

[MIT](LICENSE) © Zhouq。仓库内不含任何第三方语料原文。
