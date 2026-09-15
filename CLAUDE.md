# Marda 码达 · 项目工程规约

面向计算机学生的 Agent 智能面试学习平台（校招简历副项目）。核心：Agent 智能面试引擎（LangGraph 状态机：自主出题、动态追问、会话状态维护）+ RAG 作为 Agent 工具 + 能力评估/短板定位/学习推送。业务闭环：模拟面试 → 发现短板 → 针对性学习。

## 协作规则

- 只管本文件夹 /Users/zhouq/VibeCoding/Marda；同级姊妹项目不读、不动、不受影响
- **Git 提交与推送前必须向用户确认**（SSH 已配置，无需密钥）**GitHub**公开仓库已搭建：https://github.com/Q-77-i/Marda
- 架构级决策依据：docs/规划报告.md（已确认，2026-09-15）。开发流程：规划 → 本文件 → PRD → SPEC → demo → 落地

## 环境与密钥

- `DEEPSEEK_API_KEY` 从 /Users/zhouq/VibeCoding/Marda/.env 读取（开发时 `source` 使用）；**密钥不进仓库、不打印、不进对话上下文**
- 模型名（2026-07 换代后）：`deepseek-flash`（主力，支持视觉）/ `deepseek-v4-pro`（难题/报告，不支持视觉）。旧名 `deepseek-chat`/`deepseek-reasoner` 已下线，**禁用**

## DeepSeek 坑位清单（写代码前必读）

1. **结构化输出必须显式关 thinking**：v4 思考模式不支持 `tool_choice:"required"`，`with_structured_output` 会炸 → 评分/报告等结构化节点关掉 thinking
2. 思考模式 + 工具调用必须回传上一轮 `reasoning_content`，否则 400；`langchain-deepseek` 1.1.0 未修 → 集成首选官方 `openai` SDK + `base_url=https://api.deepseek.com`，或本地 patch ChatDeepSeek 子类
3. 限流是**并发数**（flash 2500 / pro 500，账户级，可用 user_id 隔离）→ 令牌桶按并发设计，不是 QPS
4. DeepSeek **无 embedding API** → 嵌入走 SiliconFlow BGE-M3（demo）/ 本地 BGE-M3（落地）

## 技术栈（版本线）

- 后端：Python 3.11+ / FastAPI / uvicorn / sse-starlette；`langgraph>=1.0,<2.0` + `langchain>=1.0,<2.0`
- 前端：Next.js 15 + TypeScript + Tailwind CSS + shadcn/ui + Framer Motion + Recharts（雷达图）；设计规范参考 taste-skill
- 数据：PostgreSQL（业务/面试记录/能力档案）+ Qdrant 1.19（向量，原生稀疏 + RRF）；checkpointer SQLite 起步 → PG
- RAG：BGE-M3（1024d）+ bge-reranker-v2-m3（必上）+ 每题一 doc 分块 + 混合检索（dense+sparse RRF）
- 可观测：Langfuse（demo 期云形态）；评估：DeepEval（CI 门禁）+ RAGAS（离线）
- MCP：仅"题库查询"1 个 server 作展示点（锁定 2026-07-28 后的规范版本，协议代码关在 adapter 后）；A2A 不引入
- 多模态二期：豆包流式 ASR/TTS 或 faster-whisper + edge-tts；**引擎与模态解耦**（语音只是音频通道）

## 架构原则（面试要能讲出"为什么"）

- **LangGraph 管编排、LangChain 管组件**：流程有"向后的箭头"（循环/分支/中断恢复）→ LangGraph；直线管道 → LCEL
- **确定性逻辑用代码写死，语义任务交给 LLM**：阶段推进/轮数上限/追问上限/追问决策全部纯代码（可解释、可单测、UI 可回放）；LLM 只做出题/评分/追问文案
- **结构性多 Agent**：出题官/评分官/报告官为子图/角色节点，共享 InterviewState 黑板通信；不做 agent 自由对话
- 降级链：`deepseek-v4-pro → deepseek-flash → 预置题库兜底`（题库是天然降级路径）
- 一次面试 = Langfuse 一个 trace（session_id = 场次）；评分维度：技术深度/基础掌握/项目经验/沟通表达/问题解决

## 岗位与题库（已拍板）

- 阶段 1 岗位方向：**Agent/AI 工程师**；知识域与权重（规划报告 §5.3）：Agent 认知与架构 20% / 规划与推理范式 15% / Tool 与 Function Calling（含 MCP）15% / Memory 15% / RAG 20% / 工程化与可观测 15%
- 种子题库优先解析个人题库（docs/题库/：md 5118 行层级规整、xmind zip、两份 PDF），结构化 JSON 携带 company/round/topic 元数据；WenQu（MIT）扩充
- 面试范围：技术面 + 行为面/HR 面（阶段 2 接入，复用状态机）

## 语料合规（红线）

- 开源语料白名单：JavaGuide（Apache-2.0）、doocs 系列（CC-BY-SA-4.0）、haizlin/fe-interview（MIT）、tech-interview-handbook（MIT）、InterviewGuide（Apache-2.0）、WenQu（MIT）
- **禁用**：CS-Notes（NC）、小林 coding（无 license）、面试鸭题库（不在仓库）、labuladong 等无许可仓库；不爬站
- **个人题库（docs/题库/）仅本地使用，默认不进 git**（如要提交先向用户确认）；每条语料带 source/license/url 元数据

## 开发纪律

- 每阶段有验证标准（规划报告 §8）：**验证命令跑通才算完成**，不口头声称成功
- TDD：追问决策、评分聚合、语料解析等确定性逻辑先写测试
- 阶段节奏：规划（✓）→ CLAUDE.md（✓）→ PRD（✓）→ SPEC（✓）→ demo（阶段 1）→ 完善（阶段 2）→ 落地（阶段 3）→ 二期语音

## 目录结构（规划）

```
backend/    FastAPI + LangGraph（api / graph 状态机 / agents 角色节点 / tools / rag / eval）
frontend/   Next.js（面试 / 题库 / 报告 / 雷达图 / Trace 回放）
data/       题库语料、三格式解析脚本、license 清单
docker/     compose（api / web / qdrant / pg / …）
docs/       规划报告等个人文档（开发时移走）
eval/       golden set、DeepEval 用例
```
