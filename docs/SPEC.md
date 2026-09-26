# SPEC：Marda 码达 — 技术规格（阶段 1 MVP）

> 版本 v1.0 ｜ 2026-09-15 ｜ 状态：已评审通过 ｜ 上游：docs/PRD.md（已评审通过）｜ 范围：阶段 1 demo 最小闭环

---

## 1. 范围与目标

实现 PRD §7 的 MVP：Agent/AI 工程师方向、单用户、文本面试全链路（五阶段状态机 + 追问决策 + 断线续面 + 报告），题库 ≥100 题结构化入库（个人题库 md/xmind 两格式解析，PDF 因合规否决），前端三个页面（仪表盘/面试/报告）。**全部 8 条验收标准（PRD §7）通过才算完成**（第 8 条 P95 首 token 在开发环境经 nginx 实测；部署环境实测随阶段 3 验收）。

阶段 1 明确不做：账号体系、混合检索（sparse/RRF）、reranker、私有题库、PDF 导出、Trace 回放、行为面、Langfuse 接入（预留接口）、MCP server、**服务器部署**（阶段 1 的部署形态 = Docker Compose 编排 + 本地一键起，部署方案随阶段 3 再定，与 PRD §8 里程碑一致）。

## 2. 工程结构

```
marda/
├── CLAUDE.md / .gitignore / .env(.example)
├── docs/             # 规划报告（个人留档）、PRD、SPEC
├── backend/
│   ├── pyproject.toml            # uv 管理，Python 3.11+
│   ├── app/
│   │   ├── main.py               # FastAPI 入口
│   │   ├── config.py             # env 读取（.env）
│   │   ├── llm.py                # DeepSeek 统一封装（openai SDK + base_url）
│   │   ├── observability.py      # Langfuse 接入（trace 上下文 / 无 key 降级，P1-M4）
│   │   ├── domain.py             # 知识域定义（配额 / 映射单一来源）
│   │   ├── api/                  # interviews.py（五端点，SSE 流）
│   │   ├── graph/                # state.py / graph.py / nodes/ / rules/
│   │   ├── agents/               # prompts.py / schemas.py（结构化输出 Pydantic）
│   │   ├── tools/                # question_search.py（RAG 检索工具）
│   │   ├── service.py            # 服务层（图单例 / 事件翻译 / 落库）
│   │   └── db.py                 # 业务库三表（interviews / answers / reports）
│   ├── scripts/                  # smoke_llm.py / smoke_graph.py / smoke_api.py
│   └── tests/                    # unit/ integration/ fixtures/
├── frontend/                     # Next.js 15 + TS + Tailwind + shadcn/ui + Recharts（pnpm）
│   ├── app/                      # page.tsx（仪表盘）/ interview/[id]/ report/[id]/
│   ├── components/               # chat / radar / report / …
│   └── lib/                      # api.ts / sse.ts / typewriter.ts / format.ts / constants.ts / chart-tokens.ts
├── data/
│   ├── scripts/                  # bootstrap.py / mapping.py / parse_md.py / parse_xmind.py / enrich.py / ingest.py
│   ├── parsed/                   # 解析产物（gitignore）
│   └── licenses/                 # 语料来源清单（入库）
├── docker/
│   └── nginx.conf                # 唯一入口：/api → api，其余 → web（本地与阶段 3 同构）
└── docker-compose.yml            # nginx + web + api + qdrant + embedding 一键起
```

- 后端依赖：fastapi、uvicorn、sse-starlette、langgraph==1.2.11、langchain==1.4.0、langgraph-checkpoint-sqlite==3.1.1、openai（SDK）、pydantic、pydantic-settings、tenacity、httpx、qdrant-client、pypdf、langfuse==4.9.1（可观测，P1-M4）、sqlite3（内置）
- 前端依赖：next@15、react、tailwindcss、shadcn/ui、framer-motion、recharts
- 阶段 1 存储：**SQLite 单文件**（业务库 + LangGraph checkpointer 两个文件），Qdrant 单容器（向量）；PG 阶段 2/3 引入
- 嵌入：**本地 BGE-M3 独立容器**（M3 起，`backend/embedding_service/`，torch 不进 api 镜像）；SiliconFlow 只留 rerank

## 3. LLM 集成（llm.py）

- 统一封装 `openai` SDK：`base_url="https://api.deepseek.com"`，`api_key` 从 .env 读。
- 模型：`deepseek-flash`（阶段 1 全部调用；v4-pro 阶段 2 用于报告）。
- **阶段 1 所有调用关 thinking**（`extra_body={"thinking": {"type": "disabled"}}`），规避坑位清单 1/2；阶段 2 再按节点开启。
- 两个函数：
  - `chat(messages, *, max_tokens, temperature) -> str`：文案类（开场/出题/追问/结束语）
  - `chat_json(messages, *, schema: type[BaseModel]) -> BaseModel`：结构化类（评分/提炼/报告），`response_format={"type":"json_object"}` + JSON Schema 注入 prompt + Pydantic 校验 + 失败重请求 1 次（非法 JSON 时）
- 重试：429/5xx tenacity 指数退避（阶段 1 只做重试，限流/熔断阶段 3）。
- 调用点全部走 `LLMError` 自定义异常 → API 层转 SSE error 事件。

## 4. 面试状态机（LangGraph）

### 4.1 State

```python
class Phase(str, Enum):
    INTRO="intro"; WARMUP="warmup"; TECH_BASE="tech_base"
    PROJECT="project"; CLOSING="closing"; FINISHED="finished"

class ScoreItem(BaseModel):
    technical_depth: int; fundamentals: int; project_experience: int
    communication: int; problem_solving: int            # 1-5 整数
    covered_key_points: list[str]; missed_key_points: list[str]
    error_flag: bool; comment: str

class QuestionRecord(BaseModel):
    question_id: str | None; text: str; domain: str; topic: str
    difficulty: str; key_points: list[str]
    follow_up_count: int = 0; clarify_used: int = 0; missing_used: int = 0
    followup_log: list[str] = []   # 评分节点需要追问记录（§4.5）
    answer: str | None = None      # 我的回答（含追问轮）：首答 + 「【追问补充】」标记追加（FR-25 复盘分段依据）
    score: ScoreItem | None = None
    skipped: bool = False; from_bank: bool = True
    question_type: str = "tech"    # 题型语义（tech/scenario 均计入问答轮次，编号见 §4.6；默认值兼容旧 checkpoint）

class InterviewState(BaseModel):
    interview_id: str; position: str
    question_count: int = 10   # 全场问答轮次（组成 = 技术 N−1 + 场景 1，见 domain.SCENARIO_COUNT）
    phase: Phase = Phase.INTRO
    current_question: QuestionRecord | None = None
    asked_ids: list[str] = []
    difficulty: str = "L1"
    consecutive_good: int = 0; consecutive_bad: int = 0
    candidate_profile: str = ""          # 自我介绍提炼
    answered_count: int = 0
    answered_questions: list[QuestionRecord] = []  # 报告聚合数据来源
    user_input: str = ""                 # resume 消息（route 分发依据）
    closing_question_count: int = 0      # 反问计数（PRD §4.1 上限 1-2）
    chat_history: list[dict] = []        # 完整对话流水（回放 + SSE 差分的单一来源，不截断）
    report: dict | None = None
    status: str = "running"              # running / finished
    trace_log: list[dict] = []           # 决策回放事件流（FR-21，P1-M4）：只增不改，见 §4.7
```

Checkpointer：`langgraph.checkpoint.sqlite.AsyncSqliteSaver`（独立 sqlite 文件），`thread_id = interview_id`。

### 4.2 图结构

```mermaid
flowchart TD
    S([入口]) --> ROUTE{route 纯代码}
    ROUTE -- 新会话 --> INTRO[开场节点 LLM] --> INTERRUPT([interrupt 等用户])
    ROUTE -- 自我介绍阶段 --> PROFILE[自我介绍提炼 LLM 结构化] --> ASK[出题节点]
    ROUTE -- 作答阶段 --> JUDGE[评分节点 LLM 关thinking 结构化]
    JUDGE --> FD{追问决策 纯代码}
    FD -- 追问 --> FU[追问节点 题库元数据直发或LLM] --> INTERRUPT
    FD -- 换题 --> ADV{轮数判断 纯代码}
    ADV -- 继续技术题 --> ASK
    ADV -- 进场景题 --> ASK
    ADV -- 收尾 --> CLOSING[反问邀请 LLM] --> INTERRUPT
    ROUTE -- 反问阶段 --> CANS[面试官作答 LLM] --> REPORT[报告生成 聚合纯代码+LLM]
    ROUTE -- 结束指令 --> REPORT
    ASK --> INTERRUPT
    REPORT --> E([end])
```

**节点职责与决策全在代码，LLM 只产出文案与评分。** 单 interrupt 点（`interrupt()` 等待用户输入），resume 时由 route 按 phase 分发。`recursion_limit` 显式设为 `question_count*6+10`，捕获 `GraphRecursionError`。

### 4.3 纯代码规则模块（TDD 核心，graph/rules/）

**follow_up.py**：**决策与原因同源**（P1-M4）——`explain_decision` 是唯一实现，返回 `(Decision, Reason)`；`decide_follow_up` 只是取决策的薄封装，回放展示的换题原因与当时的决策不可能漂移。

```python
class Reason(str, Enum):   # 决策原因（回放展示 / 报告口径）
    ERROR_FLAG; COVERAGE_LOW; DEEPEN_OK          # → 追问
    REMEDY_LIMIT; CLARIFY_LIMIT; MISSING_LIMIT; MISSING_ASKED; COVERAGE_OK   # → 换题
    # TOTAL_LIMIT 为 P0 遗留值（旧事件数据），不再产出

def remedy_budget(question_count): return max(3, ceil(question_count * 0.7))
    # 全场补救预算（P1-M4.5-R1）：5 题 4 次 / 10 题 7 次 / 15 题 11 次；单一来源（仿 end_quota）
def remedy_used_total(state): return sum(q.missing_used for q in state.answered_questions)
    # 补救已用量从已答题计数派生（含当前题），零独立 state 字段；澄清/深挖豁免，skipped 自然不计
def unasked_missed(score, asked): return [k for k in score.missed_key_points if k not in asked]
    # 同一 key_point 只追问一次（覆盖率跳变不触发重复追问）

def explain_decision(score, *, question_count, clarify_used, missing_used, deepen_used, remedy_used, asked_key_points, rules) -> tuple[Decision, Reason]:
    # 优先级（P1-M4.5-R1）：澄清（不占池）→ 深挖（达标，不占池）→ 遗漏（占池）→ 换题
    if score.error_flag and clarify_used < rules.clarify_limit:  return Decision.CLARIFY, Reason.ERROR_FLAG
    if score.error_flag:  return Decision.NEXT, Reason.CLARIFY_LIMIT      # 错误仍在 → 换题（不深挖）
    if score.coverage >= rules.coverage_threshold:
        if deepen_used < rules.deepen_limit:  return Decision.DEEPEN, Reason.DEEPEN_OK
        return Decision.NEXT, Reason.COVERAGE_OK
    unasked = unasked_missed(score, asked_key_points)
    if unasked and missing_used < rules.missing_limit:
        if remedy_used < remedy_budget(question_count):  return Decision.MISSING, Reason.COVERAGE_LOW
        return Decision.NEXT, Reason.REMEDY_LIMIT
    if missing_used >= rules.missing_limit:  return Decision.NEXT, Reason.MISSING_LIMIT
    return Decision.NEXT, Reason.MISSING_ASKED   # 有遗漏但遗漏点均已追问过

def decide_follow_up(...) -> Decision:   # 薄封装：decision, _ = explain_decision(...)
```

**追问密度口径（P1-M4.5-R1，实测 3 题 10 次追问后修订）**：单题上限 澄清 1 / 遗漏 2 / 深挖 1（单题最大 5 轮 = 首答 + 澄清 1 + 遗漏 2 + 深挖 1）；全场补救池 `remedy_budget` 管总量，澄清（error 纠错）与深挖（边界深挖）豁免——两者由单题上限约束、不占池。

**difficulty.py**：

```python
def update_difficulty(state) -> None:
    mean = score 五维均值
    if mean >= 4: good+1, bad=0
    elif mean <= 2: bad+1, good=0
    else: reset both
    good>=2 → difficulty 升一档（封顶 L3）并清零；bad>=2 → 降一档（保底 L1）并清零
```

**quota.py**：知识域配额（largest remainder 按权重 × 技术轮数 = 轮次 − SCENARIO_COUNT），例：10 轮 → 9 道技术题 → Agent 认知 2 / RAG 2 / 规划推理 2 / Tool-FC 1 / Memory 1 / 工程化 1。

**advance.py**：`answered_count+1`；技术轮答满（`answered_count >= question_count - SCENARIO_COUNT`）→ `phase=PROJECT`（场景题）；场景题完成 → `phase=CLOSING`；结束指令（用户主动结束按钮/「结束面试」）需 `answered_count >= end_quota(question_count)` 才允许，否则面试官礼貌拒绝并继续。**门槛单一来源**：`end_quota(question_count) = ceil(question_count × 0.6)`，判定（`meets_end_quota`）与回放展示（「还差 N 题」）同源，不各算一份。

### 4.4 出题节点

1. 纯代码算目标 domain（配额剩余最多的）+ difficulty；
2. 调 `search_questions` 工具（Qdrant：payload 过滤 domain/difficulty + 排除 asked_ids + 随机）→ 命中则用题库题（`from_bank=True`，`follow_ups` 元数据一并带出，深挖追问用）；
3. 未命中 → 放宽难度 ±1 再检索；仍未命中 → LLM 生成（`from_bank=False`，不入正式库）；
4. LLM 生成"面试官口吻"的提问文案（题库题：按 text 出题，禁止透露参考答案）。

**出题接上下文（P1-M4.5-A）**：`candidate_profile` 进口吻层模板与生成模板，允许结合候选人背景适度改写题干表述。**三条防漂移约束**：

1. **question_id 不变**：口吻层只产出面试官文案（`chat_history`），`state.current_question` 恒为原题记录——题库题的 question_id/key_points 原值保留，回放/评分/参考答案对齐不受改写影响；
2. **评分用原 key_points**：judge 的 key_points 恒取自 `QuestionRecord.key_points`（题库原值），不因口吻改写重新推导；
3. **prompt 显式禁改考察点**：口吻层模板写死「不得改变考察点、不得新增或删减考察要求」（生成模板同口径约束「考察方向与难度不变」）。

**深挖追问（P1-M4.5-B）**：followup 节点新增 DEEPEN 分支（决策见 §4.3）。文案来源按拍板分两路：题库题直接发 `follow_ups[deepen_used-1]` 元数据（**零 LLM 调用**，确定性可回放；D 的人味层统一处理衔接）；生成题/场景题（`from_bank=False`）由 LLM 经 `FOLLOWUP_DEEPEN_TEMPLATE` 从问答上下文现场生成。深挖统一生效不特判题型；观察点：C 之后项目深挖阶段自身即深挖，DEEPEN 在项目题上可能冗余，C 之后观察。

**遗漏追问去重（P1-M4.5-R1）**：`QuestionRecord.asked_key_points` 记录已追问过的 key_points，MISSING 只问未问过的漏点（`unasked_missed`），发出即写入 asked 集合——覆盖率跳变不再触发重复追问。

### 4.5 评分节点（结构化输出，关 thinking）

输入：题目（含 key_points）、**累计回答**（首答 + 全部追问补充，按 `【追问补充】` 分段标记，P1-M4.5-R1：先 `merge_answer` 再评分）、追问记录、rubric 定义。输出 `ScoreItem`。评分口径以**累计掌握程度**为准（补充后更扎实可提分，暴露理解偏差应降级）；覆盖率允许下降，反映真实掌握程度，不锁单调。

### 4.6 报告生成节点

- 纯代码聚合：五维均值、各 domain 均分、短板 = 均分最低的 2-3 个 domain、跳过标记。
- LLM 结构化输出：总评 + 逐题点评（每题一句话，引用追问过程）+ 学习建议。LLM 侧 schema：

```json
{ "total_comment": str, "per_question_comments": [{question_id, comment}], "study_advice": [{domain, advice}] }
```

- **逐题点评落库 payload 由后端组装**：LLM 的 `question_id` 是它自编的序号（prompt 未定义该字段含义），只取 `comment` 文本，元信息一律从 `state.answered_questions` 带出，条数恒等于已答题目数（LLM 少给时用评分官点评兜底）：

```json
{ "per_question_comments": [{ "index": int, "number": int|null, "question_id": str|null, "question_type": str, "domain": str, "text": str, "comment": str }] }
```

  场景题据此可识别（`domain="project"`、`question_id=null`），前端不再靠数组位置猜；`index` 为作答顺序（1 起）。
- **题型语义由后端定义**：`question_type` 为题型种类（tech/scenario），计入问答轮次的题型集合见 `app/domain.py COUNTED_QUESTION_TYPES`（单一来源）；`number` 为计入题型的按序编号（场景题计入轮次，编号为其轮次序号）。前端只消费不推断，未知题型显示原值；历史 payload（无新字段）前端按 domain/位置兜底。
- 报告落库（reports 表）+ state.status="finished"。

**阶段 2 复盘扩展（FR-25）**：`per_question_comments` 每项增 `candidate_answer`（我的回答，含追问轮）、`score`（五维）、`covered_key_points` / `missed_key_points`（评分官输出）、`reference_answer`（题库题 = 参考答案全文，按 question_id 取题库；场景题 question_id=null → null，前端不渲染——场景题无权威答案，硬编反而误导）。candidate_answer/score/关键点从 `state.answered_questions` 带出，组装口径与现有元信息一致；条数恒等于已答题目数不变。已结束场次的面试回放复用 `GET /api/interviews/{id}`（chat_history），只读模式为纯前端（隐藏输入框 + 状态标识）。

实现口径（T8 落地）：
- **追问轮回答为拼接串**：`state.answered_questions[].answer` = 首答 + `\n\n` + `【追问补充】` + 本轮回答（多轮依次追加）；标记常量 `graph/state.FOLLOWUP_ANSWER_MARKER`，前端同值副本在 `frontend/lib/constants.ts`（改文案需两边同改）。前端 `format.splitAnswerSegments` 按标记切段，标「首答 / 追问补充 N」；
- **`score` 进 payload 前必须转标量**：组装时显式取五维标量 dict（不塞 Pydantic 对象），否则报告接口 JSON 序列化会炸；
- **参考答案查询**：`tools/question_search.fetch_reference_answers(ids)`（复用 `_fetch_by_ids`，只含 enabled 题）；已归档/生成题自然缺席 → null，不编造参考。
- **报告生成走深度档**：`report` 节点 `chat_json(..., model=settings.deepseek_pro_model)`（SPEC §3 的 v4-pro 口径落地）。
- **`chat_history` 全量保留、不截断**（T8-R1）：它同时是回放数据源与 SSE delta 的差分依据（服务层按「本次长度 − 上次长度」取新增消息），从头部截断会让差分失效 → 面试官文案漏发、回放丢开场。面试官/LLM 的记忆来自结构化 state（`answered_questions` / `candidate_profile`），本字段不参与 prompt 组装；将来若要喂 LLM，在调用点按需切片。
- 历史 payload（T8 之前）无上述字段，前端按缺失兜底（复盘卡退化为「题干 + 点评」）。

## 4.7 决策回放与可观测（P1-M4 / FR-21）

**数据源 = checkpointer state 的 `trace_log`**（不另建表）：面试过程本来就以 state 为权威，回放跟着权威走，删除场次即随线程一起消失，不会留下孤立事件行。字段只增不改（`add_trace` 是唯一写入口），旧场次无此字段 → 空列表，前端按「该场次未记录决策」兜底。

事件形态 `{"type": str, "round": int|null, "detail": dict}`；`detail` **必须纯标量**（进 checkpoint 要能序列化，且前端直接渲染）：

| type | round | detail | 展示的决策要素 |
| --- | --- | --- | --- |
| ask | `answered_count+1` | domain / difficulty / question_type / from_bank / question_id / question / hits | 选了什么题、题库命中几个候选、是否降级生成 |
| judge | `answered_count` | answer（本轮回答原文）/ score（五维+关键点+error_flag）/ coverage / difficulty / difficulty_changed | 输入与评分输出、难度是否变档 |
| followup | `answered_count` | decision / reason / text | 追问决策与**原因**（§4.3 同源） |
| advance | `answered_count` | reason（换题原因）/ phase | 为什么换题、推进到哪一阶段 |
| end_refused | `answered_count+1` | answered_count / threshold | 主动结束被拒时的缺口（§4.3 `end_quota`） |
| report | null | answered_count / question_count / weaknesses | 收尾 |

`round` 语义 = 事件所属问答轮次（1 起，与报告 `number` 同义）：首次评分后即为该题序号，追问重评不变 → 同一题的 ask/judge/followup/advance 同号，UI 可按轮聚合。事件自带展示数据（题干、回答原文、五维），故 `/trace` 单次请求自包含，**未结束的场次同样可看**（实时决策视图）。

**接口**：`GET /api/interviews/{id}/trace` → `{interview_id, position, status, answered_count, question_count, events}`（鉴权与 404 口径同 §7）。前端回放页（会话 2）只消费不推断。

**Langfuse 接入口径（一次面试 = 一个 trace）**：

- **trace_id 由场次派生**（`client.create_trace_id(seed=interview_id)`）——面试是多轮 resume 的多个 HTTP 请求，派生 id 让它们落进同一个 trace，而不是散成 N 个；`session_id = interview_id`、`user_id = 账号` → 控制台可按场次/按人聚合成本；
- 每轮 `service._run` 整轮包在 `observability.turn_span()` 里（`propagate_attributes` + `start_as_current_observation`），LLM 调用经 `langfuse.openai` drop-in 自动成为 generation（带 usage）并挂在轮次 span 下；
- **无 key 时整体降级为零开销**：不 import langfuse、不构造客户端、不联网，本地与 CI 无需账号（`.env` 缺 `LANGFUSE_*` 即此路径）；
- 配置：`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL`（默认 `https://cloud.langfuse.com`），走 `.env` 不进仓库；上报由 SDK 异步批量完成，面试主链路不等待（上报失败的可观测性留阶段 3）。
- **成本金额的单位是人民币**：Langfuse 返回的数值就是人民币，控制台里的 `$` 是它硬编码的符号 → **前端一律显示 `¥` + 数值原样**（`0.0556` → `¥0.0556`），**禁止按汇率换算**。成本靠项目侧价格表（Settings → Models）匹配，配了才有值，为 0 属配置缺失而非链路故障。

**验证口径**：接线由单测离线固化（注入 `InMemorySpanExporter`：同场次多轮同 trace_id、generation 挂在轮次 span 下、无 key 时零开销）；**「按场次可查」由 smoke 读回核对**——`scripts/smoke_api.py` 按场次派生 trace_id 把观测从云端读回来，断言 session_id/user_id 归属、generation 归父、模型名（含报告走深度档）与 token/成本汇总，不靠抄 id 到控制台肉眼比对。

## 5. RAG

### 5.1 向量层（M3 会话 1 落地）

- 分块：**每题一 doc**（PRD §4.6 字段即 payload）；嵌入文本 = **题干 + 关键点**（M3 定：关键点是答案的要点提炼，M6 题库搜索按考点召回靠它；答案全文过长会稀释题干）。文本格式单一来源 = `app/tools/embedding.py::question_doc_text(question, key_points)`，ingest 与 hybrid_search 共用（文本漂移会让 rerank 打分对象与嵌入对象不一致）。
- 嵌入服务：本地 BGE-M3 **独立容器**（`backend/embedding_service/`，模型权重 build 时烤进镜像；SiliconFlow 嵌入退场，只留 rerank）。契约：
  - `POST /embed`，请求 `{"texts": [str, …]}`，1 ≤ 条数 ≤ 128（超限 413）
  - 响应 200：`{"dense": [[float,…], …], "sparse": [{token_id: weight}, …], "dim": int}`——dense L2 归一 1024d（cosine 与内积同口径）；sparse token_id 为 JSON key（字符串）、已剔零、按 token_id 升序，调用方转 int 升序后作 Qdrant SparseVector；dim = dense 实际维度，客户端据此校验——模型配置漂移（维度不一致）在客户端报错，而不是把错位向量写进库
  - 失败返回：503 模型未加载（启动窗口期）；非 200 客户端 raise_for_status 上抛
  - `GET /healthz` → `{"status": "ok", "model": …, "loaded": bool}`；推理 max_length 512 不截断（题库 doc 最长约 300 token）、inner batch 8、CPU 推理同步 def 走线程池（不堵 /healthz）
  - api 侧客户端 `app/tools/embedding.py`（`EMBEDDING_URL`，容器内 `http://embedding:8091`）：按 64 分批保序（EMBED_TIMEOUT 300s），响应条数/维度不符 → RuntimeError
- Qdrant collection `questions`：**命名双向量** `dense`（1024d cosine，L2 归一）+ `sparse`（BGE-M3 lexical weights，Qdrant 默认 modifier 不叠 IDF）；payload = {question_id, domain, topic, difficulty, round, company}。
- 建库脚本 ingest.py：parsed JSON → SQLite + Qdrant 双写；**Qdrant 侧每次 drop 重建**（命名双向量布局是 RRF prefetch 的硬前提，Qdrant 不支持无名字段在线改命名；出题检索走 payload 过滤、不碰向量，重建无停机影响），SQLite 侧按 question_id upsert + `DELETE NOT IN` 全量同步。
- 检索工具 `search_questions(domain, difficulty, exclude_ids, k=3)`：**出题场景不走向量**——无查询文本，dense/sparse 都没有输入。payload 过滤 + 随机取 k + SQLite join 完整题目。

### 5.2 混合检索（M3 会话 2 落地）

- `hybrid_search(query, *, k=5)`（`app/tools/hybrid_search.py`）：query 本地 embed → Qdrant Query API `prefetch`（dense + sparse 各 limit 30）→ `FusionQuery(Fusion.RRF)` 融合候选 30 → SQLite join（payload 只存过滤字段，题干与关键点在 SQLite，且 rerank 需要文档文本）→ rerank → top k。输出键同 search_questions（question_id/question/answer/key_points/follow_ups/domain/topic/difficulty/company/round），仅 enabled 题；join 后按候选序重排（SQLite IN 查询不保序）；空 query 报 ValueError。
- 候选 ≤1 时跳过 rerank；**rerank 失败直接抛**（降级/熔断阶段 3）。Rerank 文档 = 题干 + 关键点（与嵌入文本同一函数）。
- rerank 客户端（`app/tools/rerank.py`）：httpx 直调 `POST {siliconflow_base_url}/rerank`（Bearer 鉴权，超时 30s），请求 `{model: "BAAI/bge-reranker-v2-m3", query, documents, top_n, return_documents: false}`（top_n 为 None 时不传该字段）；响应 `results: [{index, relevance_score}]`（已按分降序）——客户端校验 index 在范围内且唯一、score 为有限数，否则 RuntimeError；空 documents 不发请求。
- 消费方：M6 题库搜索（FR-12 关键词搜索）/ M9 学习推荐（按短板域召回）；本会话无 API 暴露，**用户可见零变化**。

## 6. 语料解析与入库（data/scripts/）

### 6.1 md 解析（parse_md.py，主数据源）

- 层级规则：`#` = round（一面/二面/三面）；`##` = company；`###` = topic；`####` = 题目。
- 题目文本 = 标题去除编号前缀（正则 `^\d+\.\s*`，处理 "1. 1." 双重编号）；正文 = 参考答案。
- 输出 JSON：question / answer / topic / domain / difficulty / company / round / source="个人题库-牛客补充版" / license="personal" / url=本地路径。

### 6.2 映射表（config，随 SPEC 交付）

- **topic → domain**：Agent 认知与架构/规划与推理范式→`planning-reasoning`…（按 PRD 六大域映射，手撕算法→`algorithms`；映射表以 md 实际 topic 全集为准，未知 topic 报错不静默）
- **round → difficulty**：一面→L1，二面→L2，三面→L3；`algorithms` domain 默认 L2。

### 6.3 xmind 解析

- xmind：解 zip → content.json → 遍历主题树，按同样层级规则扁平化，复用 md 输出结构。

### 6.4 富化与质检（enrich.py）

- LLM 批量补齐 key_points / follow_ups（deepseek-flash，批处理 + 抽样人工质检 20 条）；
- 校验必填字段、去重（题目文本相似度 + 手动白名单）。

## 7. API 契约

**鉴权（FR-23）**：`/api/interviews/*` 全端点需登录，请求头 `Authorization: Bearer <token>`；未带/失效/过期统一 401。跨用户访问他人场次按「不存在」返回 404（不泄露存在性）。

| 方法/路径 | 请求 | 响应 |
| --- | --- | --- |
| POST /api/auth/register | `{username, password}`（username 3–32 位 `[A-Za-z0-9_]`，**统一小写存储**；password 6–72） | **201** `{token, username}`；重名（含大小写变体）409 |
| POST /api/auth/login | 同上 | `{token, username}`；账号不存在与密码错误同为 **401**（不泄露账号是否注册，文案一致） |
| GET /api/auth/me | — | `{id, username}`（前端刷新后校验 token 用） |
| POST /api/interviews | `{position, question_count}`（**2–20，默认 10**；question_count = 全场问答轮次，1 轮 = 0 技术 + 1 场景无意义） | SSE 流（首事件 meta 携带 interview_id；thread_id = interview_id）；创建后立即执行开场 |
| POST /api/interviews/{id}/messages | `{content}` | SSE 流（见事件表） |
| GET /api/interviews/{id} | — | 会话状态：phase / answered_count / question_count / 历史消息（供刷新恢复 UI） |
| GET /api/interviews/{id}/report | — | 报告 JSON（未结束 404） |
| GET /api/interviews/{id}/trace | — | 决策回放事件流 `{interview_id, position, status, answered_count, question_count, events}`（**未结束场次同样可查**；事件模型见 §4.7） |
| GET /api/interviews | — | 面试历史列表（倒序） |
| DELETE /api/interviews/{id} | — | **204**：物理删除（业务库三表 + checkpointer 线程，不可恢复；进行中的场次也允许）；不存在 404 |

**SSE 事件**（`sse-starlette` EventSourceResponse；POST 由前端 fetch 流解析）：

| event | data | 说明 |
| --- | --- | --- |
| meta | `{interview_id, phase, answered_count, question_count}` | 阶段/进度（创建流首事件携带 interview_id） |
| delta | `{text}` | 面试官消息（完整文案；打字机由前端客户端渲染） |
| question | `{index, question_id, domain, difficulty}` | 新题提示（只在新题时发一次：追问/评分重传同题不发；生成题无 question_id 不发） |
| done | `{interview_id, report_ready}` | 面试结束 |
| error | `{code, message, retryable}` | 流内错误（LLM 失败 / 步数超限） |

工程要求：`stream_mode=["updates"]`（llm.py 走裸 openai SDK，无 LangChain messages token 流可推；打字机效果由前端逐字渲染，阶段 2 若上真 token 流 delta 事件形状不变）；`X-Accel-Buffering: no`；15s 心跳注释（sse-starlette 内置 ping=15 实现）；async handler 全程 `astream` 不阻塞事件循环。

## 8. 数据库（SQLite，阶段 2 仍 SQLite，PG 迁移推阶段 3）

```sql
questions(id TEXT PK, question TEXT NOT NULL, answer TEXT NOT NULL,
  key_points JSON, follow_ups JSON, domain TEXT NOT NULL, topic TEXT NOT NULL,
  difficulty TEXT NOT NULL, company TEXT, round TEXT,
  source TEXT, license TEXT, url TEXT, status TEXT DEFAULT 'enabled')
users(id TEXT PK, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL, created_at TEXT)
interviews(id TEXT PK, thread_id TEXT UNIQUE, user_id TEXT, position TEXT, question_count INT,
  phase TEXT, difficulty TEXT, status TEXT, started_at TEXT, ended_at TEXT)
answers(id INTEGER PK AUTOINCREMENT, interview_id TEXT, question_id TEXT,
  domain TEXT, difficulty TEXT, candidate_answer TEXT, followup_count INT,
  skipped INT DEFAULT 0, score_json TEXT, created_at TEXT)
reports(id TEXT PK, interview_id TEXT, payload JSON, created_at TEXT)
```

面试过程以 **checkpointer state 为权威**，answers/reports 为落库产物（结束后一次写入）。

**账号与归属（FR-23）**：密码 `hashlib.scrypt`（n=2^14/r=8/p=1，存储串自描述 `scrypt$n$r$p$salt$digest`）；JWT HS256，7 天有效，密钥 `JWT_SECRET` 走 `.env`（长度下限 32，弱密钥启动即失败）。归属列是 `interviews.user_id`——**checkpointer 不需要隔离**（thread_id = 全局唯一 uuid），业务库才是归属权威；隔离实现 = 列表按 user_id 过滤 + 其余端点先校验归属（`service._require_owner`）。老库启动时轻量迁移补 `user_id` 列（`PRAGMA table_info` 探测，阶段 1 的库免手工处理），历史孤儿行（`user_id IS NULL`）由首个注册账号认领一次（`claim_orphan_interviews`，不依赖「用户数为 0」判断，免并发竞态）。

**删除口径**：DELETE /api/interviews/{id} 物理删除——checkpointer 线程（`saver.adelete_thread`）与 interviews/answers/reports 三表一并清除，不做逻辑删除（逻辑删除的 `deleted_at` 过滤会污染所有查询）。

### 8.1 多源扩充口径

阶段 1 单一数据源（个人题库），`source`/`license`/`url` 为单值。阶段 2 接入开源白名单语料（WenQu MIT 等）前，按以下路径扩展，不临时拍脑袋：

- `source` 列语义 = **答案主源**（provenance 的精简版）；接入第二个数据源时拆 `question_sources` 关联表（`question_id, source, license, url, source_detail, imported_at, status`），四个来源字段一并迁入，questions 表只保留主源外键
- **license 按源记、不按题记**；一题多源时主答案裁决：主源优先级 > 答案质量 > 导入时间，其余源记录保留；license 展示为集合
- 每个新源一个 adapter（复用 `_make_question`/`_finalize`），负责把该源的分类体系归一化到统一 schema（topic→域、easy/medium/hard→L1/L2/L3、无轮次概念→NULL）；未知 topic 沿用"报错、人工补映射"
- Qdrant payload 不含 source 字段，provenance 拆表对向量层透明（检索命中后 join SQLite）

## 9. 前端设计

- **仪表盘**：创建面试表单（方向固定 Agent/AI 工程师 + 题量 5/10/15 轮）+ 历史列表（进入报告，**每条带物理删除按钮**（确认弹窗后调 DELETE 接口））。
- **面试页**：聊天流（fetch POST + SSE 流解析，`lib/sse.ts`）、打字机渲染（客户端逐字动画，delta 事件为完整文案）、阶段/进度指示（"技术问答 7/10"）、主动结束按钮、刷新后用 GET /interviews/{id} 恢复 UI；已结束场次进入只读回放（阶段 2 FR-25：隐藏输入框、顶栏标「已结束」，复用同一恢复接口）。
- **报告页**：Recharts 雷达图（五维）、知识域条形图、逐题点评卡片、短板高亮、总评；逐题复盘卡（阶段 2 FR-25：我的回答 / 五维得分 / 关键点对比 / 题库题参考答案折叠展示，场景题仅关键点对比）。页头「决策回放」入口（P1-M4）。
- **决策回放页**（`/trace/[id]`，阶段 2 FR-21）：只读时间线，按 `round` 聚成逐轮卡片——出题信息（域/难度/题型/题库命中数）进卡片头，其余事件按发生顺序排在时间线上：评分（覆盖率/五维/漏掉的关键点/点评/回答原文折叠）、追问（决策+原因）、换题（原因+进入阶段）、结束被挽留（还差 N 题）；`round=null` 的收尾事件单列（完成题量+短板域）。**规则与原因由后端给，前端只映射文案、不重算决策**（重算就可能与当时不一致）；旧场次无事件流 → 空态提示「该场次未记录决策」。静态展示不做自动播放；入口仅报告页（与「已结束才有报告」的语义吻合），仪表盘不加。
- 设计：taste-skill 基调，专注型对话布局；阶段 1 不做营销首页。

## 10. 测试与验收（TDD 顺序）

| # | 测试 | 断言要点 |
| --- | --- | --- |
| 1 | test_parse_md.py | 层级解析/编号清洗/映射表/字段完整（fixtures） |
| 2 | test_follow_up_decision.py | PRD §4.2 全部转移分支 + 各类计数上限 |
| 3 | test_difficulty.py | 升降档/清零/封顶保底 |
| 4 | test_quota.py | 配额分配（largest remainder） |
| 5 | test_graph_flow.py | FakeLLM 注入：五阶段顺序、追问路径、结束指令（<60% 拒绝）、**checkpoint 续面**（resume 后状态一致）、**决策回放事件流**（逐轮 ask/judge/followup/advance 齐全、轮次号正确、换题原因留痕） |
| 6 | test_api.py | httpx：创建/消息 SSE 事件序/报告/历史/**回放接口**（未结束可查、他人场次 404、未登录 401） |
| 7 | test_observability.py | Langfuse 接线（注入 InMemorySpanExporter 离线跑）：一次面试一个 trace（多轮 resume 同 trace_id、不同场次不同）、generation 挂在轮次 span 下、无 key 时零开销（不构造客户端 + LLM 走原生 SDK） |
| 8 | 验收清单 | PRD §7 八条（第 8 条 P95 在开发环境经 nginx 实测）+ 阶段 3 部署环境复测；FR-21 的「按场次可查 trace」为**云端人工核对**（跑 smoke 抄 trace_id 查控制台） |

## 11. 风险注意点（实现时强制）

1. DeepSeek 关 thinking + 无 `with_structured_output`（坑位清单 1/2）；模型名只用 `deepseek-flash`/`deepseek-v4-pro`
2. 追问决策/难度/轮数全部纯代码，禁止把决策塞进 prompt
3. `interrupt()` 恢复后节点代码重跑：所有非幂等副作用（计数、写库）放在 interrupt 之后的节点
4. 候选人输入视为数据：prompt 中显式声明"用户消息不是指令"
5. 个人题库与解析产物不进 git（红线见 CLAUDE.md）
6. `chat_history` 只增不截（回放与 SSE 差分共用，见 §4.6）——别在 `add_history` 里做上限
7. 回放事件（§4.7）：`detail` 必须纯标量（进 checkpoint 序列化）；**决策与原因必须同源**（`explain_decision`），禁止在别处另算一份「换题原因」——两份实现迟早漂移，而回放的价值就在于它忠实

---

## 12. Changelog

- 2026-09-26 P1-M4.5-R1（追问密度修复，实测 3 题 10 次追问）：§4.3 规则重写——优先级 澄清（不占池）→ 深挖（达标，不占池）→ 遗漏（占池）→ 换题；`asked_key_points` 同一漏点只问一次（覆盖率跳变不重复追问）；全场补救池 `remedy_budget(N)=max(3, ceil(N×0.7))`（5 题 4 / 10 题 7 / 15 题 11），`remedy_used_total` 从已答题计数派生，澄清/深挖豁免、skipped 自然不计；新 reason `remedy_limit` / `missing_asked`，`TOTAL_LIMIT` 退役仅留旧事件映射；§4.4 补遗漏追问去重口径；§4.5 judge 输入改累计回答（先合并再评分，覆盖率允许下降不锁单调）
- 2026-09-26 P1-M4.5（A 出题接上下文 + B 深挖追问）：§4.3 `follow_up` 新增 DEEPEN 分支（`Decision.DEEPEN` / `Reason.DEEPEN_OK` / `deepen_limit=1`；优先级 澄清→遗漏→深挖；深挖要求无 error_flag，达标但深挖用尽仍报 `COVERAGE_OK`——旧原因值语义不漂移）；§4.4 出题接上下文（profile 进口吻层与生成模板 + 三条防漂移约束：question_id 不变 / 评分用原 key_points / prompt 禁改考察点）与深挖文案两路来源（题库元数据直发零 LLM / LLM 现场生成）；§4.1 `QuestionRecord` 补 `follow_ups`/`deepen_used`；§4.2 图注追问节点文案来源
- 2026-09-26 P1-M4 会话 2（FR-21 前端决策回放页）：§9 补决策回放页（`/trace/[id]` 逐轮时间线、只映射不重算、旧场次空态、入口仅报告页）与报告页入口；前端 `lib/trace.ts` 分组与取值守卫、`constants` 三类文案映射（事件/决策/原因，**原因标签不含阈值数字**，阈值只在后端 rules）。会话 2 收尾：评分小节补漏掉的关键点与评分官点评（`judgeEvidence` 守卫），`coverage_ok` 文案改「覆盖率达标」（原「关键点覆盖完整」与 70–100% 达标区间不符）；`lib/http.ts` 错误文案 CJK 守卫（框架英文兜底不端给用户）
- 2026-09-25 P1-M4 会话 1（FR-21 后端 + Langfuse 接入）：新增 §4.7（`trace_log` 事件模型 / `/trace` 接口 / Langfuse 接入口径与验证口径）；§4.1 补 `trace_log` 字段；§4.3 `follow_up` 改 `explain_decision` 决策与原因同源、`advance` 补 `end_quota` 门槛单一来源；§7 补回放接口；§2 补 `observability.py` 与 langfuse 依赖；§10/§11 补回放与可观测的测试与风险点
- 2026-09-24 P1-M3 会话 2（hybrid_search RRF + SiliconFlow rerank）：§5.1 补嵌入服务契约（请求/响应 schema、sparse 格式、批量上限、失败返回）；新增 §5.2 混合检索契约
- 2026-09-23 T8-R1（与 P1-M1 同批）：`chat_history` 取消 24 条截断（§4.1 字段语义 + §4.6 口径 + §11 风险点 6）——截断会让 SSE 长度差分失效、回放丢开场；补单测（state / sse）与整场 API 回归用例
- 2026-09-23 P1-M1（FR-25 复盘与回放）落地：§4.6 补实现口径（追问拼接标记、score 转标量、参考答案查询、报告走 v4-pro）、§4.1 answer 字段语义注释；前端复盘卡与只读回放（报告页 ↔ 面试页互链），历史 payload 兜底
- 2026-09-23 文档减负：§2 目录树对齐实际代码结构；PDF 解析因两份 PDF 合规否决、未实现（原 §6.3 已删）；实施顺序随 T1–T7b 全部完成而删除（原 §11 移除，风险注意点顺延为 §11）
- 2026-09-23 新增 FR-25 面试复盘与回放（阶段 2）：§4.6 复盘扩展口径、§9 复盘卡与只读回放
