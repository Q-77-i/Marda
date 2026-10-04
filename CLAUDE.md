# Marda 码达 · 项目工程规约

面向计算机学生的 Agent 智能面试学习平台（校招简历副项目）。核心：Agent 智能面试引擎（LangGraph 状态机：自主出题、动态追问、会话状态维护）+ RAG 作为 Agent 工具 + 能力评估/短板定位/学习推送。业务闭环：模拟面试 → 发现短板 → 针对性学习。

**项目已开发完成**（2026-10-05 收尾）。本文档 = 工程规约与拍板口径的现行版本；配套文档分工：

| 文档 | 角色 |
| --- | --- |
| [README.md](README.md) | 门面：定位 / 演示 / 快速开始 / 技术栈 |
| [docs/PRD.md](docs/PRD.md) | 需求：功能清单、业务规则、页面、验收标准 |
| [docs/SPEC.md](docs/SPEC.md) | 技术规格：状态机 schema、API 契约、数据库、前端设计、风险评估（**正式版，章节号被代码注释引用 100+ 处，冻结**） |
| [docs/开发历程.md](docs/开发历程.md) | 过程记录：每个里程碑做了什么、为什么这么定、怎么验证的 |
| 本文档 | 规约：协作方式、环境密钥、坑位清单、架构原则、语料红线 |

## 协作规则

- 只管本文件夹 /Users/zhouq/VibeCoding/Marda；同级姊妹项目不读、不动、不受影响
- **Git 提交与推送前必须向用户确认**。公开仓库：https://github.com/Q-77-i/Marda（SSH 已配置）
- **main 有分支保护：直推会被拒** —— 推法 = 推功能分支 → 用户在网页开 PR → CI 三个 job 全绿 → Merge。本机无 `gh` CLI、无 token，开/合 PR 只能网页操作
- **提交前跑语料红线反查**：`backend/.venv/bin/python data/scripts/check_redline.py`（查待提交改动，命中即非零退出；发布前用 `--all` 全量）。**规则靠记忆执行不了——明知规矩也要跑检查**：这条检查立起来的第一件事，就是在已入库文件里查出 15 处遗留的题库原文引用

## 环境与密钥

- `DEEPSEEK_API_KEY` 从仓库根 `.env` 读取（开发时 `source` 使用）；**密钥不进仓库、不打印、不进对话上下文**
- 模型名：`deepseek-flash`（主力，支持视觉）/ `deepseek-v4-pro`（难题与报告，不支持视觉）。旧名 `deepseek-chat` / `deepseek-reasoner` 已下线，**禁用**

## DeepSeek 坑位清单（写代码前必读）

1. **结构化输出必须显式关 thinking，且只能用 `json_object`**：v4 思考模式不支持 `tool_choice:"required"`，`with_structured_output` 会炸；`response_format={"type":"json_schema"}` 实测 400 不可用 → 结构化输出 = `json_object` + schema 注入 prompt + Pydantic 校验（已在 `llm.py` 固化）
2. 思考模式 + 工具调用必须回传上一轮 `reasoning_content`，否则 400；`langchain-deepseek` 1.1.0 未修 → 集成首选官方 `openai` SDK + `base_url=https://api.deepseek.com`，或本地 patch ChatDeepSeek 子类
3. 限流是**并发数**（flash 2500 / pro 500，账户级，可用 user_id 隔离）→ 令牌桶按并发设计，不是 QPS
4. DeepSeek **无 embedding API** → 嵌入走本地 BGE-M3 容器（SiliconFlow 只留 rerank）

## 技术栈（版本线）

- 后端：Python 3.11+ / FastAPI / uvicorn / sse-starlette；`langgraph==1.2.11` + `langchain==1.4.0` + `langgraph-checkpoint-sqlite==3.1.1`
- 前端：Next.js 15 + TypeScript + Tailwind CSS + shadcn/ui + Framer Motion + Recharts（雷达图）；设计规范参考 taste-skill
- 数据：**SQLite**（业务库 + checkpointer 两个文件）+ Qdrant 1.19（向量，原生稀疏 + 服务端 RRF）。**PG 迁移已取消**（部署上云一并取消，理由见 PRD §8.2 修订注；真要换时业务库只有七表、checkpointer 有官方 PG saver，替换面可控）
- RAG：本地 BGE-M3（1024d）+ bge-reranker-v2-m3（**按查询形态开关**：短查询 rerank、多漏点长查询直接走融合序——长查询实测净贡献为负）+ 每题一 doc 分块 + 混合检索（dense+sparse RRF）
- 可观测：Langfuse（云形态，无 key 即整体降级为零开销）；评估：**自研评估 harness**（评分一致性门禁，失败非零退出）+ RAGAS（离线检索指标）
- MCP：仅"题库查询"1 个 server（stdio only、只公共题），协议代码关在 `app/mcp_server/`（单测扫描钉死别的模块不许 import `mcp`）；A2A 不引入
- CI：GitHub Actions 三 job（后端 pytest / 前端 lint+vitest+build / 语料入库守卫 + 检查器自检）。**真比对与 smoke、评测不进 CI**（语料不进仓库、真 key、花钱、会被上游抖动误伤）
- 语音：**豆包流式 ASR**（`WS /api/asr` 中继，v3 二进制协议）+ **edge-tts 播报**（三档降级）。**引擎与模态解耦**——语音只是新增一条音频通道，图/状态机/落库/SSE 事件表一条未动。**两把火山 key 不通用**（方舟 Bearer ≠ 豆包语音 X-Api-Key）
- 视觉：**deepseek-flash 视觉**（代码截图先行）。图存磁盘 `data/uploads/`、state 只存 image_id；**图独立成附件消息、不改任何 prompt 模板**（无图路径逐字一致）

## 架构原则（面试要能讲出"为什么"）

- **LangGraph 管编排、LangChain 管组件**：流程有"向后的箭头"（循环/分支/中断恢复）→ LangGraph；直线管道 → LCEL
- **确定性逻辑用代码写死，语义任务交给 LLM**：阶段推进/轮数上限/追问上限/追问决策全部纯代码（可解释、可单测、UI 可回放）；LLM 只做出题/评分/追问文案
- **结构性多 Agent**：出题官/评分官/报告官为子图/角色节点，共享 InterviewState 黑板通信；不做 agent 自由对话
- 降级链：`deepseek-v4-pro → deepseek-flash → 预置题库兜底`（题库是天然降级路径）；上游不可用时另有完整的确定性兜底（见 SPEC §3）
- 一次面试 = Langfuse 一个 trace（session_id = 场次）；评分维度：技术深度/基础掌握/项目经验/沟通表达/问题解决

## 岗位与题库（已拍板）

- 岗位方向：**Agent/AI 工程师**；知识域与权重（规划报告 §5.3）：Agent 认知与架构 20% / 规划与推理范式 15% / Tool 与 Function Calling（含 MCP）15% / Memory 15% / RAG 20% / 工程化与可观测 15%
- 题库：个人题库 + 四源开源语料（JavaGuide / doocs / haizlin / tech-interview-handbook / InterviewGuide / WenQu 属白名单）= **1568 题（enabled 1096）**，结构化 JSON 携带 company/round/topic 元数据；难度按「L1 概念 / L2 原理与选型 / L3 底层与权衡」三档标注，12 个「难度 × 题量」组合全部可供
- 面试范围：技术面 + 行为面/HR 面（复用同一状态机，只换能力模型与题源）

## 语料合规（红线）

- 开源语料白名单：JavaGuide（Apache-2.0）、doocs 系列（CC-BY-SA-4.0）、haizlin/fe-interview（MIT）、tech-interview-handbook（MIT）、InterviewGuide（Apache-2.0）、WenQu（MIT）
- **禁用**：CS-Notes（NC）、小林 coding（无 license）、面试鸭题库、labuladong 等无许可仓库；不爬站
- **个人题库（docs/题库/）仅本地使用，默认不进 git**；每条语料带 source/license/url 元数据
- **红线覆盖派生文本，不只看原始文件**：个人题库的**题干/答案/关键点原文**经任何加工后（评测 golden、复核产物、报告、导出样例、日志摘录……）同样不进 git——原始文件被 gitignore 不等于它的内容安全。**判据不是「有没有 id」，是「文本里会不会出现题库原文或其片段」**：M12 实测踩过——以为「只提交 id 版产物」就安全，而检索 golden 的查询文本是报告漏点原文、漏点又正是题库关键点的片段（13 条漏点查询与个人题库关键点重合 94 处）。**提交前跑 `check_redline.py` 机械反查**（两侧归一成中文骨架后滑 12 字窗口比对，命中即非零退出），查得出来就不入库

## 开发纪律

- **验证命令跑通才算完成**，不口头声称成功；每阶段的验证标准见规划报告 §8
- TDD：追问决策、评分聚合、语料解析等确定性逻辑先写测试
- **单一来源**：凡是"两处算同一个东西"，抽成一份实现并用测试钉死——总分口径、检索 doc 文本、评测与生产共用的评分 prompt、决策与原因同源的 `explain_decision`。同一批数字两个来源，迟早不一致
- **机械判据优先**：能用断言证明的不靠肉眼——图内标识串证明"模型读到了图"、转写重合率证明"语音进了引擎"、CDP Network 域零请求证明"帧没上传"、像素采样证明暗色主题
- **诚实的边界**：没覆盖的写"未覆盖"，降级如实标注而不是静默掉质量；"缺数据"与"0 分"必须分得开
- 概率性断言与确定性断言长得一样：改了会影响某条断言前提的行为，回头看它是否还成立——**绿一次说明不了问题**
