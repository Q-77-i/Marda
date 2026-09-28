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

1. **结构化输出必须显式关 thinking，且只能用 `json_object`**：v4 思考模式不支持 `tool_choice:"required"`，`with_structured_output` 会炸 → 评分/报告等结构化节点关掉 thinking；`response_format={"type":"json_schema"}` 实测 400 不可用 → 结构化输出 = `json_object` + schema 注入 prompt + Pydantic 校验（已在 llm.py 固化）
2. 思考模式 + 工具调用必须回传上一轮 `reasoning_content`，否则 400；`langchain-deepseek` 1.1.0 未修 → 集成首选官方 `openai` SDK + `base_url=https://api.deepseek.com`，或本地 patch ChatDeepSeek 子类
3. 限流是**并发数**（flash 2500 / pro 500，账户级，可用 user_id 隔离）→ 令牌桶按并发设计，不是 QPS
4. DeepSeek **无 embedding API** → 嵌入走 SiliconFlow BGE-M3（demo）/ 本地 BGE-M3（落地）

## 技术栈（版本线）

- 后端：Python 3.11+ / FastAPI / uvicorn / sse-starlette；`langgraph==1.2.11` + `langchain==1.4.0` + `langgraph-checkpoint-sqlite==3.1.1`
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
- 阶段节奏：规划（✓）→ CLAUDE.md（✓）→ PRD（✓）→ SPEC（✓）→ demo（✓）→ 完善（阶段 2）→ 落地（阶段 3）→ 二期语音

## Changelog

- 2026-09-28：P1-M5 会话 1 `question_sources` 拆表（多源 provenance 落地）——`questions` 只留 `source`（**答案主源**），合规四要素明细进新表 `question_sources`（PK `(question_id, source)`，**license 按源记、不按题记**）；主源裁决 `_rank`（`min` 取优）：主源优先级 > 能用 > 答案长 > 轮次可信，完全同分取先导入者（新增源在 `SOURCE_PRIORITY` 登记，个人题库恒 0）。同题合并时**每源留最优那条**——留"最优"而非"先出现"，出处才指向答案真正来自的那一篇。老库迁移靠列探测 + `DROP COLUMN`，幂等可重跑；`question_sources` 走整表重建（同步规则比 questions 多一维：题还在、某来源没了也要删）。真库迁移前先在副本上逐字段 diff 对账：342 题零回归、app 侧零改动（`fetch_by_ids` 只 select 固定列），并顺带补回了老 schema 从未落库的 `source_detail`（261 条）
- 2026-09-27：P1-M4.7 后续三条小修（均为真链路暴露，M4 节末待办清空）——① 项目深挖题措辞去重：出题官每轮独立调用、只拿到轮次号，不喂前情「换个切入点」等于掷骰子（三道题套同一个开头）→ `_asked_project_block` 把已问题目**原文**喂回 `{asked}` 插槽，场景题与口吻层两层模板同时禁「复述候选人项目背景」（题前衔接语已交代过背景）；② 前端「重试」判据改由服务端给：会话接口新增 `stalled`（`service.engine_stalled`，两态实测`pause/中断载荷` vs `失败节点/无载荷`），前端 `lib/recovery.ts` 据此两路处置——卡住或没入账→重发（重跑失败节点，resume 值被丢弃、不重复计分），**已跑完只是回复没传回来→只按服务端记录重建列表、绝不重发**（否则同一份回答判两次）；有未落地作答时新消息先补发旧的、新文本留在输入框；③ 成本回读：Langfuse 价格表 2026-09-25 配好后回读 `¥0.1428`（46 generation/35354 token），此前为 0 的是价格表生效前的旧场次（摄入时算价、不追溯）
- 2026-09-27：P1-M4.7-D 面试官人味层落地——高频短衔接零 LLM（`graph/rules/transition.py` 模板 + 插槽），开场白/结束陈词走 LLM；技术题同域成块（`pick_domain` 粘性，跨场次可比性由「域分布不变」单测锁死）；重连语走 `GET /interviews/{id}?reconnect=true`（只附响应不落库）。**M4 收官（含后续小修）**：流内 SSE `error` 事件补重试出口——error 在流内到达（HTTP 仍 200）故 `catch` 不触发，原先横幅没有「重试」；重试语义 = 重跑失败节点（图停在失败节点上，`resume` 值被丢弃），已入账的回答不重复计分，由集成测试钉死
- 2026-09-16：坑位 1 实测确认 `json_schema` 返回 400 → 结构化输出定型 `json_object` + schema 注入 prompt + Pydantic 校验
- 2026-09-17：T4 实测锁定版本线 `langgraph==1.2.11` / `langchain==1.4.0` / `langgraph-checkpoint-sqlite==3.1.1`
- 2026-09-23：文档减负——「目录结构（规划）」删除（实际结构见 README）；demo（阶段 1）标记完成
