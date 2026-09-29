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

- 2026-09-29：P1-M7 私有题库（FR-13）——`tools/private_parse.py`（模板解析：`【题目】/【答案】/【关键点】/【追问】/【主题】`，md 与 PDF **同一入口**；用标记而非 markdown `##`，因为 PDF 抽文会把 `#` 丢掉，一套规则服务两种格式）+ `tools/bank_private.py`（私有题 SQL 的**唯一出口**，`user_id` 必传无默认——漏传在调用处直接 TypeError，而不是静默查出别人的题）+ `api/bank_private.py` 三端点（上传 / 列表 / 编辑归档，**部分成功**语义：好题照常入库、坏题进 errors 逐条给原因，整份文件不因一条坏题回滚）+ `question_search` **混入式**并池（公共候选走 Qdrant payload 过滤、私有候选走 SQL，私有题**不进 Qdrant**，两者合成一个池再随机——按池中占比自然混入，不设权重或开关；`user_id` 为空时行为与接入前逐字一致）。私有题与公共题**同表**（`questions.user_id`：NULL = 公共），故公共侧三处全加谓词：browse / facets 加 `user_id IS NULL`，容量按 `(公共 OR 本人)` 计入（私有题参与出题，容量不算上就会误报不足）。`fetch_sources`/`attach_sources` **故意不加**——它按 id 查、而 id 只可能来自已被隔离的查询，且私有题列表要靠它挂出 `个人上传` 出处。**顺手拆雷**：`ingest.py` 的全量同步 `DELETE FROM questions WHERE id NOT IN (管道 ids)` 会把私有题连同来源明细一起删光（私有题永不在管道 JSON 里，任何一次管道重跑都中招）→ 两个 DELETE 都按公共行收窄，回归测试钉死。前端顶栏第三 tab `/bank/private`：上传卡（模板说明前置）→ 结果条给**三份明细**（导入/重复/失败，重复特意写明「未覆盖」——答案可能已被他手改过）→ 列表筛选 + **行内展开**编辑/归档恢复（与题库页展开看答案同一套交互，不引新 dialog 原语）；后端复用的 `enabled/draft` 枚举在前端读作「使用中/已归档」。**真库验收**（临时账号跑完即删、计数回基线零残留）：8 道私有题 → 重传全判重不覆盖 → 管理闭环 → 5 题锁 L3 场次，`planning-reasoning/L3` 那一格**真的抽中私有题**（该池公共题只有 1 道；N=5 的技术题域由配额确定 = agent-architecture / rag / planning-reasoning，故这一格可预期）。397 passed（M6 时 350），前端 vitest 121（104 → 121），smoke_api 新增私有题库 6 项断言（含真 Qdrant 上的「私有题泄漏进公共检索」反查）。**两条待办发现**：① `docs/题库/` 三个真实文件（5118 行 md + 两份 PDF）是 `#### N. 题干` 层级式，模板解析全出 **0 题**——要进私有库得再写个 md adapter（用户自己的语料是 M7 最想接的东西，这条要拍板）；② `algorithms` 在 `ENABLED_DOMAINS` 里但不在 `DOMAIN_WEIGHTS`，`pick_domain` 只在六域分配配额 → **算法题（公共的也一样）永远不会被问到**，上传表单却可选它
- 2026-09-29：P1-M6 题库页 + 容量校验（FR-12 / FR-14）——`tools/bank_query.py`（浏览查询层：SQL 分页 / 四维分面计数 / 来源明细挂载 / 供给统计，与出题检索分工明确）+ `api/bank.py` 三端点（`/questions` 关键词与浏览双模式、`/facets` 筛选项由数据生成不硬编、`/capacity` 直供能力 + 不足明细）+ `rules/capacity.py`（**复用 `tech_quota` 单一来源**算「配额 vs 直供」差集，基准档 = 自适应取 L1）。创建时可选难度（adaptive/L1/L2/L3，落库存**用户的选择**、`state.difficulty` 才是当前档位，固定档位 `difficulty_locked` 全程不升降）。**容量只做前端展示判据、绝不拦创建**——直供不足 ≠ 出不了题（引擎有难度放宽 + LLM 生成兜底），且拉取失败一律不禁用（网络抖动不能让表单把用户锁死）；不足要**写明缺在哪个域、差几题**。`hybrid_search` 补 `filters`：**过滤必须挂到每一路 prefetch**（参数名 `query_filter`，写 `filter` 抛 `Unknown arguments`；只挂顶层会让两路先取满全集候选再融合，召回被无关域吃光）。导航定调**顶栏 tab**（3–4 个平级工具页、无层级，侧边栏白占纵深；沉浸式页面不渲染导航即可），「能力档案」「学习推荐」现在就占位但**渲染成不可点的灰字、不发 404 链接**。真库只读核对：enabled 1095（L1 154/L2 873/L3 68），12 个「难度 × 题量」组合里**只有 L3 × 15 直供不足**（规划与推理范式 需 2 有 1）。322 → 350 passed，前端 vitest 89 → 104，smoke_api 新增题库三端点对账 + L3 锁定场次
- 2026-09-29：P1-M5 会话 2 开源语料扩充（四源接入）——新增 `bank.py` 共享层（`question_id` 生成 / 主源裁决 / 同题合并 / 来源四要素 / **实质答案判定**）、`parse_open.py` 四源 adapter、`combine.py` 合并个人题库与开源语料（**自带个人题库零回归校验，12 字段逐字比对，不过则非零退出**）。四源 license 逐仓核对均 MIT（仓库自带 LICENSE）：ai-agents-from-zero 89 / FAQ_Of_LLM_Interview 71 / ai-agent-interview-guide 261 / llm-interview-guide 809 = 1230 条来源明细；题库 342 → 1571 题（enabled 1095），Qdrant 重建 1095 点。**占位答案不算答案**：源里 `答案：xx`、空代码块这类空壳按答案实质字符数（剥代码围栏行与空白后 < 5 字）判 draft——否则会以 enabled 身份占配额、进向量库却给不出参考答案。近似重复（相似度 ≥0.9）**只报不并**，等人工白名单。302 → 322 passed，smoke_graph + smoke_api 零回归。落真库前先备份两份 SQLite（`.gitignore` 补 `*.sqlite3.bak*`——`.sqlite3` 匹配不到备份名，会把个人题库数据扫进仓库）
- 2026-09-28：P1-M5 会话 1 `question_sources` 拆表（多源 provenance 落地）——`questions` 只留 `source`（**答案主源**），合规四要素明细进新表 `question_sources`（PK `(question_id, source)`，**license 按源记、不按题记**）；主源裁决 `_rank`（`min` 取优）：主源优先级 > 能用 > 答案长 > 轮次可信，完全同分取先导入者（新增源在 `SOURCE_PRIORITY` 登记，个人题库恒 0）。同题合并时**每源留最优那条**——留"最优"而非"先出现"，出处才指向答案真正来自的那一篇。老库迁移靠列探测 + `DROP COLUMN`，幂等可重跑；`question_sources` 走整表重建（同步规则比 questions 多一维：题还在、某来源没了也要删）。真库迁移前先在副本上逐字段 diff 对账：342 题零回归、app 侧零改动（`fetch_by_ids` 只 select 固定列），并顺带补回了老 schema 从未落库的 `source_detail`（261 条）
- 2026-09-27：P1-M4.7 后续三条小修（均为真链路暴露，M4 节末待办清空）——① 项目深挖题措辞去重：出题官每轮独立调用、只拿到轮次号，不喂前情「换个切入点」等于掷骰子（三道题套同一个开头）→ `_asked_project_block` 把已问题目**原文**喂回 `{asked}` 插槽，场景题与口吻层两层模板同时禁「复述候选人项目背景」（题前衔接语已交代过背景）；② 前端「重试」判据改由服务端给：会话接口新增 `stalled`（`service.engine_stalled`，两态实测`pause/中断载荷` vs `失败节点/无载荷`），前端 `lib/recovery.ts` 据此两路处置——卡住或没入账→重发（重跑失败节点，resume 值被丢弃、不重复计分），**已跑完只是回复没传回来→只按服务端记录重建列表、绝不重发**（否则同一份回答判两次）；有未落地作答时新消息先补发旧的、新文本留在输入框；③ 成本回读：Langfuse 价格表 2026-09-25 配好后回读 `¥0.1428`（46 generation/35354 token），此前为 0 的是价格表生效前的旧场次（摄入时算价、不追溯）
- 2026-09-27：P1-M4.7-D 面试官人味层落地——高频短衔接零 LLM（`graph/rules/transition.py` 模板 + 插槽），开场白/结束陈词走 LLM；技术题同域成块（`pick_domain` 粘性，跨场次可比性由「域分布不变」单测锁死）；重连语走 `GET /interviews/{id}?reconnect=true`（只附响应不落库）。**M4 收官（含后续小修）**：流内 SSE `error` 事件补重试出口——error 在流内到达（HTTP 仍 200）故 `catch` 不触发，原先横幅没有「重试」；重试语义 = 重跑失败节点（图停在失败节点上，`resume` 值被丢弃），已入账的回答不重复计分，由集成测试钉死
- 2026-09-16：坑位 1 实测确认 `json_schema` 返回 400 → 结构化输出定型 `json_object` + schema 注入 prompt + Pydantic 校验
- 2026-09-17：T4 实测锁定版本线 `langgraph==1.2.11` / `langchain==1.4.0` / `langgraph-checkpoint-sqlite==3.1.1`
- 2026-09-23：文档减负——「目录结构（规划）」删除（实际结构见 README）；demo（阶段 1）标记完成
