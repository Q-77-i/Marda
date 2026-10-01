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
│   ├── scripts/                  # bootstrap / mapping / bank（共享层）/ parse_md / parse_xmind / parse_open / combine / enrich / ingest / apply_overrides / enable_behavioral
│   ├── curation/                 # 人工改判表（question_overrides.json）
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
    PROJECT="project"; BEHAVIORAL="behavioral"   # P1-M11：行为面场次的问答段（取代 PROJECT+TECH_BASE）
    CLOSING="closing"; FINISHED="finished"

class BaseScore(BaseModel):                      # P1-M11：两种评分的公共部分
    covered_key_points: list[str]; missed_key_points: list[str]
    error_flag: bool; comment: str               # coverage 为派生属性

class ScoreItem(BaseScore):                      # 技术面（默认）
    technical_depth: int; fundamentals: int; project_experience: int
    communication: int; problem_solving: int            # 1-5 整数

class BehavioralScoreItem(BaseScore):            # 行为面（P1-M11）：第 3 维与技术面同名同义（跨类型对照）
    communication: int; logic_structure: int; project_experience: int
    values_motivation: int; career_stability: int

class QuestionRecord(BaseModel):
    question_id: str | None; text: str; domain: str; topic: str
    difficulty: str; key_points: list[str]
    follow_up_count: int = 0; clarify_used: int = 0; missing_used: int = 0
    followup_log: list[str] = []   # 评分节点需要追问记录（§4.5）
    answer: str | None = None      # 我的回答（含追问轮）：首答 + 「【追问补充】」标记追加（FR-25 复盘分段依据）
    score: ScoreItem | BehavioralScoreItem | None = None   # 按字段集自动落到对应模型（两套维度的必填字段不重叠）
    skipped: bool = False; from_bank: bool = True
    question_type: str = "tech"    # 题型语义（tech/scenario/behavioral 均计入问答轮次，编号见 §4.6；默认值兼容旧 checkpoint）

class InterviewState(BaseModel):
    interview_id: str; position: str
    interview_type: str = "tech"   # P1-M11 FR-22：tech / behavioral（与 position 正交；默认值兼容旧 checkpoint）
    question_count: int = 10   # 全场问答轮次（P1-M4.6-C 组成 = 项目深挖 project_count(N) + 技术 N−project_count(N)，见 domain.project_count）
    phase: Phase = Phase.INTRO
    current_question: QuestionRecord | None = None
    asked_ids: list[str] = []
    difficulty: str = "L1"
    difficulty_locked: bool = False      # 固定难度场次（P1-M6 FR-14）：用户选了 L1/L2/L3 则全程不升降
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
    ADV -- 继续项目深挖 --> ASK
    ADV -- 进技术题 --> ASK
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
    REMEDY_LIMIT; CLARIFY_LIMIT; MISSING_LIMIT; MISSING_ASKED; COVERAGE_OK; DEEPEN_LIMIT  # → 换题
    # TOTAL_LIMIT 为 P0 遗留值（旧事件数据），不再产出；DEEPEN_LIMIT = 行为面 deepen-only 下深挖用尽（P1-M11）

def remedy_budget(question_count): return max(3, ceil(question_count * 0.7))
    # 全场补救预算（P1-M4.5-R1）：5 题 4 次 / 10 题 7 次 / 15 题 11 次；单一来源（仿 end_quota）
def remedy_used_total(state): return sum(q.missing_used for q in state.answered_questions)
    # 补救已用量从已答题计数派生（含当前题），零独立 state 字段；澄清/深挖豁免，skipped 自然不计
def unasked_missed(score, asked): return [k for k in score.missed_key_points if k not in asked]
    # 同一 key_point 只追问一次（覆盖率跳变不触发重复追问）

def explain_decision(score, *, question_count, clarify_used, missing_used, deepen_used, remedy_used, asked_key_points, rules, deepen_only=False) -> tuple[Decision, Reason]:
    # 优先级（P1-M4.5-R1）：澄清（不占池）→ 深挖（达标，不占池）→ 遗漏（占池）→ 换题
    if deepen_only:   # P1-M11 行为面：只深挖一次，其余分支全部不适用（见 §4.12）
        if deepen_used < rules.deepen_limit:  return Decision.DEEPEN, Reason.DEEPEN_OK
        return Decision.NEXT, Reason.DEEPEN_LIMIT
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
    if state.difficulty_locked: return    # 固定难度场次不升降（P1-M6 FR-14）
    mean = score 五维均值
    if mean >= 4: good+1, bad=0
    elif mean <= 2: bad+1, good=0
    else: reset both
    good>=2 → difficulty 升一档（封顶 L3）并清零；bad>=2 → 降一档（保底 L1）并清零
```

**difficulty_locked 的分工**：落库列 `interviews.difficulty` 存的是**用户的选择**（`adaptive`/`L1`/`L2`/`L3`，列表页据此回显），`state.difficulty` 存**当前档位**（自适应场次会升降）。`adaptive` 起步档 = `base_difficulty()` = L1——它同时是「自适应」的起点与 L1 固定场次的档位，所以容量校验里 adaptive 与 L1 的结论必须一致（smoke 用真实题库断言这一点）。

**capacity.py**（P1-M6 FR-14）：`check_capacity(question_count, difficulty, supply)` 比「配额 vs 直供」——`tech_quota(N)` 算出每域需要几题（与出题同一份实现，不另算一份），`supply` 是 `{难度: {域: enabled 题数}}`（`bank_query.difficulty_supply`），差集即不足明细 `{domain, required, available}`；`capacity_grid(counts, supply)` 把 4 难度 × N 题数摊平成前端要的网格。**只做展示判据、不拦创建**：直供不足 ≠ 出不了题——引擎有难度放宽（±1 档再检索）与 LLM 生成兜底，把它做成硬拦截会让用户在一个本可进行的场次前吃闭门羹。基准档取 `base_difficulty(difficulty)`：固定场次查该档，自适应查 L1（起步档）。

**quota.py**：知识域配额（largest remainder 按权重 × 技术轮数 = 轮次 − project_count），例：10 轮 → 3 项目深挖 + 7 道技术题 → Agent 认知 2 / RAG 1 / 规划推理 1 / Tool-FC 1 / Memory 1 / 工程化 1。

**同域成块（P1-M4.7-D）**：`pick_domain` 改粘性——当前域配额未尽就继续同域，用尽才切「剩余配额最多」的域，于是技术段是「域连问」而非跨域交错（跨域交错时题与题之间无逻辑链，体感像随机抽题，且「我们换个方向」这类衔接语跨域才成立）。计数必须取**已答题**（`Counter(q.domain for q in answered_questions)`），不能用 `remaining_quota`——它为当前题 +1，会把「最后一题」判成「配额已尽」而提前切域。**块序与域分布对同一 N 完全确定**（N=10：Agent×2 / RAG / 规划 / Tool / Memory / 工程化 各成一块），块内题目仍随机。**跨场次可比性不受影响**：`allocate_quota` / `remaining_quota` 语义未动，单测锁死「同域成块不改变域分布（== allocate_quota(N)）」与两档块序（10 题 / 15 题）。

**advance.py**：`answered_count+1`；阶段顺序（P1-M4.6-C）**项目深挖前置**——`phase=PROJECT` 答满 `project_count(question_count)` 道 → `phase=TECH_BASE`；技术轮答满（`answered_count >= question_count`）→ `phase=CLOSING`；结束指令（用户主动结束按钮/「结束面试」）需 `answered_count >= end_quota(question_count)` 才允许，否则面试官礼貌拒绝并继续。**门槛单一来源**：`end_quota(question_count) = ceil(question_count × 0.6)`，判定（`meets_end_quota`）与回放展示（「还差 N 题」）同源，不各算一份。

### 4.4 出题节点

1. 纯代码算目标 domain（配额剩余最多的域，粘性同域成块见 §4.3）+ difficulty；
2. 调 `search_questions` 工具（Qdrant：payload 过滤 domain/difficulty + 排除 asked_ids + 随机）→ 命中则用题库题（`from_bank=True`，`follow_ups` 元数据一并带出，深挖追问用）；
3. 未命中 → 放宽难度 ±1 再检索；仍未命中 → LLM 生成（`from_bank=False`，不入正式库）；
4. LLM 生成"面试官口吻"的提问文案（题库题：按 text 出题，禁止透露参考答案）。

**出题顺序（P1-M4.7-D）**：项目深挖题全部前置（M4.6-C），技术段按 §4.3 同域成块——`domain` 选定即连续出满该域配额的题，域用尽才换下一个。块序与域分布对同一 N 完全确定（可复现、可跨场次比较），**块内题目仍随机**（Qdrant 随机 + 排除 asked_ids）。旧行为是跨域交错，题与题之间无逻辑链，体感像随机抽题，且「我们换个方向」这类跨域衔接语无从谈起。

**项目深挖前置（P1-M4.6-C）**：首题（WARMUP 之后）与 `phase=PROJECT` 走 `_generate_scenario`（结合候选人项目经历定制）；`phase=TECH_BASE` 走题库/生成。`_generate_scenario` 按轮出题——轮次号进 prompt 供 LLM 换切入点（架构设计/难点攻坚/选型权衡）避免重复，难度随 `state.difficulty`（不再固定 L3）。项目题 `domain="project"` 不参与域统计的口径保留。图边不变：PROJECT/TECH_BASE 都走 judge，追问/评分/降级链通用。

**同场多道项目题的措辞去重（P1-M4.7 后续）**：出题官每轮都是**独立调用**、只拿得到轮次号，不喂前情时「换个切入点」等于掷骰子——三道题套同一个开头（真链路实测：题干原文已带「你在 Marda 码达面试引擎里做了一套……」这类背景复述，口吻层再改写也抹不掉）。修法两条同时在位：

1. **喂回已问题目原文**：`_asked_project_block(state)` 取 `answered_questions` 里的项目题（`domain=PROJECT_DOMAIN`）原文逐条塞进 `{asked}` 插槽——出题官只有看见措辞才谈得上换开头句式；一道都还没问时给 `ASKED_PROJECT_EMPTY` 明说「这是第一道」，不让模型自行脑补前情；
2. **两层模板同禁复述背景**：场景题模板写死「不复述候选人的项目背景（『你提到…』『你在…里做了…』一律不写）」——题前的衔接语（§4.8 人味层）已经交代过「结合你的项目」，题目再铺一句简历复述就是模板脸；口吻层模板同步加这条，避免改写阶段又把背景捡回来。

措辞是否真的不再雷同属生成质量，靠真链路 smoke 人眼验收；单测只钉**接线**（第二道起 prompt 必须带上第一道原文，见 `test_第二道项目题带前情_出题prompt含已问题目原文`）。

**出题接上下文（P1-M4.5-A）**：`candidate_profile` 进口吻层模板与生成模板，允许结合候选人背景适度改写题干表述。**三条防漂移约束**：

1. **question_id 不变**：口吻层只产出面试官文案（`chat_history`），`state.current_question` 恒为原题记录——题库题的 question_id/key_points 原值保留，回放/评分/参考答案对齐不受改写影响；
2. **评分用原 key_points**：judge 的 key_points 恒取自 `QuestionRecord.key_points`（题库原值），不因口吻改写重新推导；
3. **prompt 显式禁改考察点**：口吻层模板写死「不得改变考察点、不得新增或删减考察要求」（生成模板同口径约束「考察方向与难度不变」）。

**深挖追问（P1-M4.5-B）**：followup 节点新增 DEEPEN 分支（决策见 §4.3）。文案来源按拍板分两路：题库题直接发 `follow_ups[deepen_used-1]` 元数据（**零 LLM 调用**，确定性可回放；D 的人味层统一处理衔接）；生成题/项目深挖题（`from_bank=False`）由 LLM 经 `FOLLOWUP_DEEPEN_TEMPLATE` 从问答上下文现场生成。深挖统一生效不特判题型；观察点：C 之后项目深挖阶段自身即深挖，DEEPEN 在项目题上可能冗余，C 之后观察。

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

  项目深挖题据此可识别（`domain="project"`、`question_id=null`），前端不再靠数组位置猜；`index` 为作答顺序（1 起）。
- **题型语义由后端定义**：`question_type` 为题型种类（tech/scenario，值不变；展示标签「项目深挖」），计入问答轮次的题型集合见 `app/domain.py COUNTED_QUESTION_TYPES`（单一来源）；`number` 为计入题型的按序编号（项目深挖题计入轮次，编号为其轮次序号）。前端只消费不推断，未知题型显示原值；历史 payload（无新字段）前端按 domain/位置兜底。
- 报告落库（reports 表）+ state.status="finished"。

**阶段 2 复盘扩展（FR-25）**：`per_question_comments` 每项增 `candidate_answer`（我的回答，含追问轮）、`score`（五维）、`covered_key_points` / `missed_key_points`（评分官输出）、`reference_answer`（题库题 = 参考答案全文，按 question_id 取题库；项目深挖题 question_id=null → null，前端不渲染——项目深挖题无权威答案，硬编反而误导）。candidate_answer/score/关键点从 `state.answered_questions` 带出，组装口径与现有元信息一致；条数恒等于已答题目数不变。已结束场次的面试回放复用 `GET /api/interviews/{id}`（chat_history），只读模式为纯前端（隐藏输入框 + 状态标识）。

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

## 4.8 面试官人味层：衔接与收尾文案（P1-M4.7-D）

六类黏合点按「出现频率 × 文案长度」分两路生成——**高频短衔接零 LLM（模板 + 插槽，可单测可回放），低频长文才花调用**（每场固定 2 次）：

| 黏合点 | 落点 | 生成方式 |
| --- | --- | --- |
| 开场寒暄（含时长预告） | `intro` 节点 `INTRO_TEMPLATE` 插槽 | LLM（并入开场本身的调用，不额外花钱） |
| 阶段过渡 / 题间衔接 | `ask` 节点，与新题**同一条消息** | 模板（`graph/rules/transition.py`） |
| 答错缓冲 | `ask` 节点，**独立一条消息**先发 | 模板 |
| 重连语 | `GET /api/interviews/{id}?reconnect=true` 响应内附加 | 模板 |
| 结束陈词 | `report` 节点，报告生成后追加 | LLM（每场第 2 次调用） |

```python
MINUTES_PER_QUESTION = 3          # 开场时长插槽 = question_count × 3
class TransitionKind(str, Enum):  # 按「上一题 → 新题」判定，纯代码
    OPEN_PROJECT    # 首题（WARMUP → PROJECT）
    PROJECT_NEXT    # 项目段续题（换切入点）
    TO_TECH         # 项目段 → 技术段
    SAME_DOMAIN     # 技术段同域续问（同域成块后才出现）
    SWITCH_DOMAIN   # 技术段跨域换方向（文案含 {label} 域标签插槽）

transition_kind(state, new_question) -> TransitionKind   # prev 为 None → OPEN_PROJECT；
                                                         # prev 为 scenario → PROJECT_NEXT / TO_TECH
transition_line(state, new_question) -> str              # 变体按 answered_count 轮换，确定性
buffer_line(state) -> str | None                         # 仅上一题 error_flag 时非空
reconnect_line(state) -> str | None                      # 仅进行中且处于 PROJECT/TECH_BASE 且有当前题
domain_label(domain) -> str                              # DOMAIN_LABELS；project → 「项目深挖」
```

- **变体按 `answered_count % len(variants)` 轮换**：同类衔接语在同一场里不重样，且同 state 恒同文案（回放与单测可断言）。
- **衔接语与新题同一条消息**（`add_history` 一次写入）：**delta 数 == assistant 消息数**这条不变式不破，长场次不漏发。
- **答错缓冲独立成条**：缓冲回应的是上一题，prepend 到新题会读成「新题开头带着对上一题的评价」，时序错位——语义正确优先于「少发一条 delta」。缓冲文案**不含方向词**（不说「换个方向」），方向由紧随其后的衔接语表达，否则一句话说两遍。
- **域标签插值按中文排版补空格**：只在「汉字 ↔ 拉丁字母/数字」边界补（`看看 RAG 方面`），全角标点旁不补（`聊聊 Memory。`）——标签可能是纯中文、纯拉丁或中英混排，故取决于标签边界字符而非写死模板。
- **重连语不落 checkpoint**：`service.get_session` 仅在带 `reconnect=true` 时把问候拼进**本次响应**（附当前题干全文，断线回来不用往上滚），不写 state——连续刷新不堆叠，回放数据不受影响。上下文来源零新增 state：`phase` + `status` + `state.current_question` 就是「刚才聊到哪」；开场/反问阶段与已结束场次无「刚才那道题」→ 静默恢复。
- **文案不得含 FakeLLM 路由标记词**（`评分官`/`报告官`/`出题官`/`提炼`/`真诚收尾`）：单测锁死，否则测试环境降级到 fake 时会串路由。

**结束陈词红线（`CLOSING_REMARK_TEMPLATE`）**：模板**不接任何输入**（结构上拿不到报告文字），prompt 再写死四条禁止——① 不提分数/评级/名次；② 不点评知识域强弱、不引用总评与逐题点评；③ 不透露答案或关键点；④ 不承诺结果、不对录用表态、**不虚构后续流程**（真实链路实测出过「后续会有同事与你联系」——本产品不掌握任何真实招聘流程，说这句等于变相表态）。只讲感谢、陪伴感与「报告已生成、可在报告页查看」。真链路由 smoke 打印成品 + 红线自查（是否点短板域 / 复述总评）。**陈词失败不拖垮报告**：`llm.chat` 抛 `LLMError` 时记警告并跳过这一句，`report` / `status=finished` 照常落——陈词是装饰、报告是产物，装饰不能连坐产物（集成测试锁住）。

**验证口径**：衔接分类、变体轮换、空格排版、缓冲门控、重连门控由单测离线固化（21+ 项）；连读观感只能真链路看——`scripts/smoke_api.py` 打印逐轮衔接语、逐块难度曲线与结束陈词成品。

## 4.9 报告导出 PDF（P1-M8 / FR-18）

**路线：服务端 HTML+CSS 渲染**（`app/report_pdf.py` + `app/templates/report.html.j2`，jinja2 + weasyprint）。选它的理由是**验收可判**：产出的是真文本 PDF（可选中、可检索），「中文无乱码」于是能用 pypdf 读回断言（无 U+FFFD + 关键串逐条命中），而浏览器截图再拼 PDF 的路线文字不可选、长卡被拦腰切断、且无任何可断言的产物。代价是后端镜像要装 pango 与中文字体（见下）。

- **数据来源是报告 payload 本身**（§4.6），导出不重算任何分数：页面显示什么，PDF 就显示什么。旧 payload 缺字段（FR-25 之前）按缺失略过，不报错。
- **五维键与中文标签单一来源 = `graph/rules/aggregate.DIMENSION_LABELS`**（`FIVE_DIMS` 由它派生）；前端 `constants.DIMENSIONS` 是展示副本。雷达图顶点顺序即该表顺序。
- **雷达图 = 内联 SVG 手绘**（五轴五点，0–5 线性映射，越界截断）。两条打印引擎的硬约束，都踩过：
  1. **样式写 SVG 呈现属性，不写 CSS** —— `fill-opacity` 走 CSS 会被忽略（数据多边形糊成实心黑）；
  2. **画布与坐标系 1:1** —— 引擎对 `<text>` **不套用 viewBox 变换**（五边形按 viewBox 缩放、文字按原始坐标摆），靠 viewBox 留白给标签腾位置会被裁掉半截字；改为收紧半径把标签收进框内。
- **时间**：`created_at` 落库是 UTC，PDF 是给人看的文档，按**东八区**渲染（`format_created_at`），与页面的本地时间一致；解析不了就不显示时间，不影响导出。
- **渲染是纯 CPU 的确定性转换**，走 `asyncio.to_thread` 不阻塞事件循环；报告不可变故不落盘缓存（LLM 那份 60s 预算属于报告生成，不在这一步）。
- **模板 autoescape=True**：候选人回答与 LLM 文案都是不可信自由文本，直出 HTML 等于开了注入面；雷达 SVG 是自己拼的，模板里 `|safe` 放行。
- **字体必须钉死，不能落到本机字体**（`report_pdf.FONT_STACK` 是单一来源）：字体栈要在 `@page`（页边距框**不继承 body**）与 SVG `<text>` 上**各声明一次**，模板里用 `| safe`（autoescape 会把字体名引号转成 `&#39;`，CSS 不认 HTML 实体 → 静默退化到宋体）。**踩过的坑**：漏声明时那些文字会落到系统默认字体（macOS 上是苹方/宋体），而 **macOS 的 PDFKit 系（Safari / 预览 / Quick Look）渲染不了 weasyprint 嵌的苹方子集** → 整片缺字；Chrome 与 WPS 会回退到系统同名字体，**在开发机上完全看不出来**。回归由 `test_PDF只用随镜像分发的字体_不混进本机字体` 钉死——只有断言「PDF 里实际用到的字体名」拦得住，读文本、看渲染图、验嵌入与否都拦不住。
- **运行环境**：容器装 `libpango-1.0-0 / libpangoft2-1.0-0 / libharfbuzz-subset0 + fonts-noto-cjk`（缺字体就是一整页豆腐块）；macOS 宿主机需 `brew install pango` **和 `brew install --cask font-noto-sans-cjk-sc`**（后者缺失时字体栈会退到系统字体，导出物在 Safari/预览里缺字），且 Homebrew 的 glib 不在 dyld 默认搜索路径里——`report_pdf._ensure_native_libs()` 在 import weasyprint 之前补一条 `DYLD_FALLBACK_LIBRARY_PATH`（ctypes 的 macholib 每次 dlopen 现读 `os.environ`，所以运行时补也来得及）。
- **验收口径**：单测断言雷达几何 + PDF 读回（中文/参考答案/旧 payload 容缺/东八区时间）；集成测试断言内容类型、下载头、越权 404、内容与报告接口同源；smoke 在真链路取回 PDF 再读回核对。

## 4.10 学习推荐（P1-M9 / FR-20）

**数据源 = 报告 payload 本身**（§4.6）：`weaknesses` 给短板域，逐题 `missed_key_points` 给具体漏点。`tools/recommend.py` 每次请求现检索题库（**不重算分数、不落库**）——题库更新即新鲜，报告 payload 与 PDF 导出因此零回归。

- **查询文本 = 域中文标签 + 该域漏点关键词**（去重保序、最多 6 个；无漏点回退域名，因为 `hybrid_search` 收到空串会抛 ValueError）。漏点来自评分官输出，**零新增 LLM 调用**；用中文标签而非英文 key——题干与关键点都是中文，嵌入时 `agent-architecture` 这类 key 是噪点，域约束由 `filters={"domain": …}` 承担。
- **排除本场已问过的题**：复盘卡（§4.6）已给过它们的参考答案，推荐要给同域新材料。检索条数取 **`k + 本场该域已问数`**——最多只有这么多条会被过滤掉，故过滤后仍 ≥ k（题库够的话）；**比固定 margin 稳**，不依赖「题库比 margin 厚」的假设。生成题（`question_id` 为空）无从排除，也不进排除集。
- **多域并发检索**（`asyncio.gather`），结果保序 = 报告里短板域的展示顺序；**检索失败直接抛**（同 §5.2 的 rerank 口径），端点 500 透传、前端给错误态 + 重试，绝不静默给半份推荐。
- **空分组不静默隐藏**（用户看不到会以为系统漏了）：后端给 `status` 三态，前端给文案——`ok` 有卡片 / `exhausted` 命中的候选全是本场问过的（该域已无检索得到的新题）/ `empty` 该域一道题都没命中。
- **卡片带来源明细**（`bank_query.attach_sources`，主源首位）：复用 M5 的合规四要素。`question_search.fetch_by_ids` 为此多 select 一列 `source`——否则「（答案主源）」会标在按字典序排第一的来源上（**主源标签不能靠猜**）。
- **展示两处**：报告页「针对性练习推荐」卡（传 `showAdvice=false`——同页已有「学习建议」卡，同一批文案不复述）+ `/learn` 学习页（场次选择器）。从报告页跳转时用 `?interview=<id>` **承接来源场次**，默认选中它不是最近一场——否则用户点「查看全部推荐」会落到另一场的推荐上，路径断裂（判定纯函数在 `frontend/lib/learn.ts`）。

## 4.11 能力档案与曲线（P1-M10 / FR-19）

**数据源 = 已落库的报告 payload**（§4.6），与学习推荐同一口径：`tools/profile.py` 每次请求现读现算，**不重算分数、不落库、零 LLM 调用**——报告与 PDF 因此零回归，档案永远跟着报告走。

- **取数 = `reports ⋈ interviews` 按用户过滤，`started_at` 升序**（`db.list_reports`）。升序是刻意的：曲线从左到右 = 时间从早到晚，定序在 db 层，上层不重排。
- **以 reports 表为准，不按 `interviews.status` 过滤**（D5）：有报告才算数——`status='finished'` 但报告落库失败的边缘场次没有分数可画，混进来只会让曲线多一个空点。**代价**：这类场次在档案里不可见，用户会疑惑「我明明跑了那场」（列为已知待办，见 CLAUDE.md）。
- **总分口径单一来源 = `aggregate.overall_score`**（D1）：**五维等权均值**（不是加权）。报告页（`report.overall`）、PDF、档案曲线取同一个数——同一场面试在两个页面显示不同的总分，用户会以为系统算错了。报告 payload 新增 `overall` 字段；**FR-19 之前的 payload 没有它**，前端与 PDF 各自用同一函数现算兜底。
- **域有洞是常态而非异常**（D2）：一场只考部分域（`tech_quota` 按权重分配题量），没考的域在该场 `domain_scores` 里**根本没有键**。前端据此**断线**（`connectNulls={false}`），**不补零**——补零会凭空造出一个「该场该域得 0 分」的低谷，那是假信号。
- **短板变化**（`build_profile` 的 `weakness_changes`）= 逐场对**上一场**比，三态分开说：`new` 上场不是本场是 / `persistent` 两场都是 / `resolved` 上场是本场不是。三个列表按字典序（不随 payload 里 `weaknesses` 的排列漂）；**首场不产出条目**（无从比较），故条数恒为「场次数 − 1」。`resolved` 的口径是「本场不再是短板」——可能是真提升，也可能只是这场没考到该域，文案不替用户下结论。
- **概览** `summary`：场次数、平均总分、最高/最低场（并列取最早）、最近一场相对上一场的变化 `latest_delta`（单场为 `null`）。
- **响应形状 = `{sessions, summary, weakness_changes, excluded}`**，**不含图表序列**——曲线行是 Recharts 专用的展示整形，放前端 `lib/profile.ts`（纯函数 + vitest），后端重复算一遍等于同一批数字有两个来源。`excluded`（P1-M11）是未计入的场次计数（形状 `{"behavioral": N}`），供空档案/混排时说明白「为什么看不到那几场」。
- **行为面场次不计入档案（P1-M11 D4）**：**过滤字段 = 报告 payload 的 `interview_type`**（`payload.get("interview_type") or "tech"`，缺省视为技术面——FR-19 之前的老 payload 没有该字段，不能被误排除）。档案 = 技术能力档案：行为面的评分维度与知识域体系都不同，混入曲线会出现维度缺键造成的全 0 假点。排除掉的场次进 `excluded` 计数，前端据此渲染空态/提示（「行为面不计入技术能力档案」），不静默。
- **端点 `GET /api/profile` 无场次参数**：档案看的是「我的全部场次」，隔离由 user_id 过滤承担，因此**没有 404/越权面**（对照面试各端点的 owner 校验）；**没有场次时返回零态结构而不是 404**——「还没有数据」是正常状态。
- **前端三种形态**（判定在 `lib/profile.ts`）：空档案 → 文案 + 「开始第一场面试」CTA（**空态不只是告知，要给出路**）/ 只有一场 → 说明为什么画不出曲线 + 该场雷达等快照 + 「再开始一场」CTA / 多场 → 总分主曲线（点位可点进该场报告）+ 短板变化 + 知识域与五维的**小倍图**。选小倍图而不是多线图：5 条 / 6 条线缠在一张图里，交叉处用户分不清谁是谁，还得配图例与一套分类色板；各自成图则涨跌一眼可见，每张只用一个主色。横轴刻度同一天多场加序号（`MM-DD #2`）——只写日期会看起来是重复的点。
- **验收口径**：单测覆盖总分口径、域洞、三态、并列取最早、历史残缺 payload；集成测试覆盖零态、与报告 payload 逐字段对账、用户隔离、未结束场次不入选；smoke 在真链路取回档案与报告对账。

## 4.12 行为面 / HR 面（P1-M11 / FR-22）

**复用同一状态机与报告体系，只换能力模型与题源**。会话类型 `interview_type ∈ {tech, behavioral}` 与岗位 `position` **正交**（两种类型面向同一岗位，position 恒为「Agent/AI 工程师」）；一条新列贯穿 `interviews.interview_type`（DB）→ `state.interview_type` → 报告 payload `interview_type`。

- **流程（D1：单 BEHAVIORAL 段）**：`INTRO → WARMUP → BEHAVIORAL → CLOSING → 报告`。没有项目深挖段与技术分段——行为题池小（14 题）、题库里两类行为题（项目叙事 / HR 规划题）的元数据分不干净，随机混合与真实 HR 面一致。图结构一条边不动：`route` 把 `BEHAVIORAL` 与 `TECH_BASE/PROJECT` 同路分发到 `judge`；`advance.phase_after_answer(..., interview_type)` 答满 `question_count` → `CLOSING`。
- **题源**：`domain="behavioral"` 整池随机——`search_questions(difficulty=None)` = **不限难度**（行为题的 L1-L3 是技术深度语义，挂行为题上没有意义；D3）。池子耗尽 → LLM 按同标准现场生成行为题兜底（`BEHAVIORAL_ASK_GENERATE_TEMPLATE`，不入库）。
- **难度（D3）**：创建表单对行为面**隐藏难度选择器**，出题不消费难度；**自适应机制保留不关**（少一个分支）——`state.difficulty` 照常升降，但只是死数据（报告与列表都不展示难度徽标）。
- **题量（D1）**：行为面上限 **10 题**（`BEHAVIORAL_MAX_QUESTIONS`，API 层 422 校验 + 表单只给 5/10）——14 题的池子在 15 题场会当场耗尽走 LLM 兜底。
- **评分（D2）**：`BehavioralScoreItem` 五维 1-5——沟通表达 / 逻辑结构 / **项目经验**（与技术面同名同义，两类型雷达图跨类型对照时语义一致）/ 价值观与动机 / 职业稳定性。**维度表单一来源在后端**：报告 payload 与回放响应（`/trace`，judge 事件是评分模型裸 dump）都带 `dims: [{key, label}]`，前端与 PDF 都消费它，不再各自硬编维度表（老 payload 无该字段 → 前端退回技术面常量）。
- **追问 = deepen-only（P1-M11 ①）**：行为题的 key_points 是**讲述结构**（「用 STAR 说清情境与任务」）而非知识点——按覆盖率追问「补漏」语义不成立，澄清（当场对质矛盾点）也不做。`explain_decision(..., deepen_only=True)`：单题至多一次深挖（问细节：情境 / 个人动作 / 可验证结果 / 复盘），用过即换题（`Reason.DEEPEN_LIMIT`）；`error_flag` 仍由评分官产出，但只用于报告展示。判据按**题型**（`question_type == "behavioral"`）而非会话类型——将来若混排也成立。
- **聚合**：`aggregate_scores(..., interview_type)` 行为面走行为面维度表，`domain_scores` 恒为空（整场一个域），`weaknesses` 改为**最弱的评分维度**（同一「最低 2 个、并列第三也带」规则，`_weakest`）。总分仍走 `overall_score`（加 `dims` 参数，默认技术面五维——M10 D1 的单一来源不变）。
- **报告与 PDF**：报告页按 `dims` 渲染雷达与逐题得分；行为面**不渲染知识域卡与「针对性练习推荐」卡**（D5——推荐检索的是六大技术域，行为面没有可推的域；接口也直接返回空分组，不做无效检索）。PDF 模板按 `has_domains` 收起右栏（维度全宽展示），短板标签为「短板维度」；**导出按钮照常**（D 补充④）。
- **题库启用（D6）**：`data/scripts/enable_behavioral.py` 把**有实质答案**的行为题翻成 enabled 并补进 Qdrant（幂等；复用 `finalize_status` 与 ingest 的点构造，管线入库域已含行为面，下一次整链重跑结果一致）。**验收口径：脚本最后从 Qdrant 读回该域的点逐点核对**（只翻 status 不算数）。私有题库**不开放行为面域**（D7，`ENABLED_DOMAINS` 不含它）。
- **出题池隔离（验收②）**：行为面进 `ASKABLE_DOMAINS`（**可出题但不属于技术配额**——集合内域都能出题，但只有 `DOMAIN_WEIGHTS` 的键参与配额分配）；`pick_domain` 只在权重表分配，技术面永远抽不到 behavioral（单测 + 集成反查双保险）。
- **不做**：混合模式（两类型各自验证完再议）、行为面私有题、行为面学习推荐与档案（均按 D4/D5 排除）。


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
- **筛选下推到两路 prefetch（P1-M6）**：`filters` 接受 `domain/difficulty/company/round`，构造 `Filter` 后**每一路 prefetch 都要挂**。参数名是 `query_filter`（q/client 里写 `filter` 直接抛 `Unknown arguments: ['filter']`，探测时踩到过）；挂在顶层 `query_points` 是错的——`limit` 是 prefetch 级的，两路会先各取满 30 条全集候选再融合，顶层的过滤只能筛掉融合结果，无关域的候选把名额吃光，命中数少得莫名其妙。无筛选时不构造空 `Filter`（空 Filter 与 None 在 Qdrant 里语义不同，别赌等价）。单测钉死三条：两路都挂、空值维度不生成条件、无筛选时 `prefetch[i].filter is None`。
- 候选 ≤1 时跳过 rerank；**rerank 失败直接抛**（降级/熔断阶段 3）。Rerank 文档 = 题干 + 关键点（与嵌入文本同一函数）。
- rerank 客户端（`app/tools/rerank.py`）：httpx 直调 `POST {siliconflow_base_url}/rerank`（Bearer 鉴权，超时 30s），请求 `{model: "BAAI/bge-reranker-v2-m3", query, documents, top_n, return_documents: false}`（top_n 为 None 时不传该字段）；响应 `results: [{index, relevance_score}]`（已按分降序）——客户端校验 index 在范围内且唯一、score 为有限数，否则 RuntimeError；空 documents 不发请求。
- 消费方：M6 题库搜索（FR-12 关键词搜索）/ M9 学习推荐（按短板域召回）；本会话无 API 暴露，**用户可见零变化**。

## 6. 语料解析与入库（data/scripts/）

### 6.1 md 解析（parse_md.py，主数据源）

- 层级规则：`#` = round（一面/二面/三面）；`##` = company；`###` = topic；`####` = 题目。
- 题目文本 = 标题去除编号前缀（正则 `^\d+\.\s*`，处理 "1. 1." 双重编号）；正文 = 参考答案。
- 输出 JSON：question / answer / topic / domain / difficulty / company / round / `source`="个人题库-牛客补充版"（**答案主源**）/ `sources=[{source, license, url, source_detail}]`（来源明细，合规四要素；license 按源记）。
- 同题合并（题干 md5 相同 → 同 question_id）跨源生效，主源裁决 `bank.rank`（`min` 取优）：**主源优先级 > 能用 > 答案长 > 轮次可信**，完全同分时取先导入者（列表序稳定）；合并后 `sources` 每源留一条、按优先级排序，留的是该源里**最优**那条记录（占位存根不会把正本的 URL 挤掉）。

### 6.2 映射表（config，随 SPEC 交付）

- **topic → domain**：Agent 认知与架构/规划与推理范式→`planning-reasoning`…（按 PRD 六大域映射，手撕算法→`algorithms`；映射表以 md 实际 topic 全集为准，未知 topic 报错不静默）
- **round → difficulty**：一面→L1，二面→L2，三面→L3；`algorithms` domain 默认 L2。

### 6.3 xmind 解析

- xmind：解 zip → content.json → 遍历主题树，按同样层级规则扁平化，复用 md 输出结构。

### 6.4 富化与质检（enrich.py）

- LLM 批量补齐 key_points / follow_ups（deepseek-flash，批处理 + 抽样人工质检 20 条）；
- 校验必填字段、去重（题目文本相似度 + 手动白名单，见 §6.6）。

### 6.5 开源语料适配（parse_open.py，四源）

只采**明确 licensed 且题干与答案同在仓库内**（可溯源、非抓取）的源，清单见 [data/licenses/语料来源清单.md](../data/licenses/语料来源清单.md)。每源一个 adapter，共用 `bank` 共享层的 `new_question`/`finalize_status`/`source_record`，归一化到统一 schema（topic→域、难度→L1/L2/L3、无公司轮次概念→NULL）后与个人题库进同一套合并。

| 源 | 形态 | 明细行 | 解析要点 |
| --- | --- | --- | --- |
| ai-agents-from-zero | `### Qn-m.` 题 + 正文 | 89 | 唯一带**真实难度标注**的源（基础/中等/较难 → L1/L2/L3）；正文止于「常见追问」 |
| FAQ_Of_LLM_Interview | 题单+编号答案段 / 编号问题行+围栏答案 | 71 | 两种形态：题单与答案段**按编号配对**，配不上不成题；`text` 围栏剥壳、带语言围栏保留 |
| ai-agent-interview-guide | `**Q：**` + 答案标记 | 261 | 四种标记 `**A：**` / `**A**：` / `**标准答案 A：**` / `**标准答案（A）**`；「追问应对」起截断 |
| llm-interview-guide | `**Q：**` 内联问答（106 页） | 809 | topic 取 H1；图片链接转文本 |

- **未采的逐项进报告，不静默**：无答案段的题单条目、清单外文件（面经题单/关键词解析、CV 与工具用法笔记、参数手册）、站点页、`## 追问链` 与 `## Qn：` 体、速记与真题清单——超出「题干答案成对」口径的一律不采
- **未知章节/未知 topic 报错不静默**（同 §6.2）
- **占位答案不算答案**：`finalize_status` 按**答案的实质字符数**判定（剥掉代码围栏行与首尾空白后 < 5 字 → draft）。源里的 `答案：xx`、空代码块这类空壳因此不会以 enabled 身份去富化、进向量库——否则它会占着配额却给不出任何参考答案
- 四源内部同题干合并 16 组（1246 → 1229）；`company`/`round` 无此概念时为 `NULL`（**不是空串**，空串入库存的是 `''`）

### 6.6 合并入库（combine.py）

个人题库（`questions_enriched.json`）+ 开源语料（`questions_open.json`）→ `questions_combined.json`（`ingest.py` 的输入）：

- **个人题库在前**：完全同分时先导入者优先（与入库口径一致）；个人题库 `SOURCE_PRIORITY` 恒 0，开源答案再长也不顶替主源
- **自带零回归校验**：个人题库 12 个字段逐字比对，只允许新增 `sources` 明细行；不过则非零退出——**不允许带病入库**（开源语料解析改动后重跑，个人题库产物逐字节一致）
- **近似重复只报不并**：相似度 ≥0.9 的候选对打印清单（含个人×开源对），人工登记白名单后才合（§8.1）
- **人工改判表**（`data/curation/question_overrides.json`）：逐题修正 `domain` / `status`，`combine` 每次运行都应用（P1-M9 起，见 §4.10 的叙事题污染）。key 是 `question_id` —— **题干的内容哈希**，题干改一个字条目就失效，故未命中的条目会列进运行报告（`bank.apply_overrides` 返回未命中集，不静默）；每条必须写 `reason`，缺了直接报错。改判在零回归校验**之后**执行（改判就是要动 `domain`/`status`，不是回归）。**已落库的库**用 `data/scripts/apply_overrides.py` 补齐（幂等，改判成 draft 的从 Qdrant 撤点、仍在 enabled 但换域的改 payload 的 `domain`——它是检索过滤字段）——只为十来道题重建 1095 个向量点不值当。
- **入库 status 规则**（P1-M11 起）：`domain ∈ ASKABLE_DOMAINS ∪ ENABLED_DOMAINS` 且有实质答案 → enabled（行为面自 M11 起进可出题域，管线重跑与 `enable_behavioral.py` 的判定同源）；两个集合之外的域（cs-fundamentals）解析入库但置 draft。
- **启用行为面题库**（`data/scripts/enable_behavioral.py`，P1-M11）：把有实质答案的行为题翻成 enabled 并补进 Qdrant（幂等；写后从 Qdrant 读回逐点核对，只翻 status 不算数）。与 `apply_overrides.py` 同一模式：**就地补齐**，避免为十来道题全量重建向量库。

## 7. API 契约

**鉴权（FR-23）**：`/api/interviews/*`、`/api/bank/*` 与 `/api/profile` 全端点需登录，请求头 `Authorization: Bearer <token>`；未带/失效/过期统一 401。`/api/profile` 是**用户级、无路径参数**的端点（档案 = 我的全部场次），隔离由查询的 user_id 过滤承担，故只有 401 没有 404（同题库端点）。跨用户访问他人场次按「不存在」返回 404（不泄露存在性）；题库是公共资产、无归属隔离（私有题库留待 M7 另立维度），故题库端点只有 401 没有 404。

| 方法/路径 | 请求 | 响应 |
| --- | --- | --- |
| POST /api/auth/register | `{username, password}`（username 3–32 位 `[A-Za-z0-9_]`，**统一小写存储**；password 6–72） | **201** `{token, username}`；重名（含大小写变体）409 |
| POST /api/auth/login | 同上 | `{token, username}`；账号不存在与密码错误同为 **401**（不泄露账号是否注册，文案一致） |
| GET /api/auth/me | — | `{id, username}`（前端刷新后校验 token 用） |
| POST /api/interviews | `{position, question_count, difficulty, interview_type}`（**2–20，默认 10**；question_count = 全场问答轮次，1 轮 = 0 技术 + 1 场景无意义；difficulty ∈ `adaptive`/`L1`/`L2`/`L3`，默认 `adaptive`，非法值 422；**interview_type ∈ `tech`/`behavioral`，默认 `tech`，行为面 question_count > 10 → 422**） | SSE 流（首事件 meta 携带 interview_id；thread_id = interview_id）；创建后立即执行开场 |
| POST /api/interviews/{id}/messages | `{content}` | SSE 流（见事件表） |
| GET /api/interviews/{id} | 可选 `?reconnect=true` | 会话状态：phase / answered_count / question_count / 历史消息（供刷新恢复 UI）+ `stalled`；带 reconnect 时在 `chat_history` 末尾**附加**一句重连问候 + 当前题干（只随本次响应返回、不落库，§4.8） |
| GET /api/interviews/{id}/report | — | 报告 JSON（未结束 404） |
| GET /api/interviews/{id}/report.pdf | — | 报告 PDF（FR-18）：`application/pdf` + `attachment` 下载头（中文名走 RFC 5987 `filename*`，另给 ASCII 兜底名）；**未结束/不存在/越权同 404**（与报告端点同一判据）；每次现渲染不落盘缓存 |
| GET /api/interviews/{id}/recommendations | — | 学习推荐（FR-20）：`{interview_id, position, groups: [{domain, advice, status, cards}]}`，`status ∈ ok`/`exhausted`/`empty`（§4.10）；卡片含题干/答案/关键点/难度/厂商/面次 + `sources`（主源首位）。**与报告端点同一 404 判据**（未结束/不存在/越权），检索失败 500 透传；**行为面报告直接返回空 `groups`**（P1-M11 D5，不做无效检索） |
| GET /api/interviews/{id}/trace | — | 决策回放事件流 `{interview_id, position, dims, status, answered_count, question_count, events}`（**未结束场次同样可查**；事件模型见 §4.7）。`dims`（P1-M11）= 评分维度表 `[{key,label}]`：judge 事件的 detail 是评分模型裸 dump，键随会话类型变，标签由后端给（老场次无该字段 → 前端退回技术面五维常量） |
| GET /api/interviews | — | 面试历史列表（倒序） |
| GET /api/profile | — | 能力档案（FR-19）：`{sessions, summary, weakness_changes, excluded}`（§4.11）。**无路径参数**（用户级），隔离由 user_id 过滤承担；**没有场次时返回零态结构（不是 404）**——`session_count=0` 是正常状态，前端据此渲染空态 + 引导；**行为面场次不计入**（按报告 payload 的 `interview_type` 过滤，P1-M11 D4），只进 `excluded` 计数 |
| DELETE /api/interviews/{id} | — | **204**：物理删除（业务库三表 + checkpointer 线程，不可恢复；进行中的场次也允许）；不存在 404 |
| GET /api/bank/questions | `q`（关键词）/ `domain` / `difficulty`（`L1`\|`L2`\|`L3`）/ `company` / `round` / `page` / `page_size`（1–50，默认 10） | `{mode, total, page, page_size, items}`——`q` 非空走混合检索（`mode=search`，`total=null`：相关性排序不翻页，单页 `SEARCH_LIMIT=20`），否则走 SQL 浏览（`mode=browse`，有 `total` 可翻页）；每项含 `question_id`/题干/答案/关键点/追问/域/难度/厂商/面次 + `sources`（来源明细，**主源排首位**） |
| GET /api/bank/facets | — | `{domain, difficulty, company, round}` → `[{value, count}]`（仅 enabled；计数降序、同数按值升序，**难度例外：按档位 L1→L3**——有序维度按计数排会把 L2 顶到 L1 前面，而筛选项要的是档位序）。前端筛选项由此生成，不硬编候选值——扩语料后新厂商/面次自动出现 |
| GET /api/bank/capacity | `counts`（逗号分隔，默认 `5,10,15`；越界夹紧 2–20、去重升序） | `{options: [{difficulty, base, question_count, ok, shortfalls}]}`（FR-14；`shortfalls = [{domain, required, available}]`，基准档见 §4.3 capacity.py） |

**SSE 事件**（`sse-starlette` EventSourceResponse；POST 由前端 fetch 流解析）：

| event | data | 说明 |
| --- | --- | --- |
| meta | `{interview_id, phase, answered_count, question_count}` | 阶段/进度（创建流首事件携带 interview_id） |
| delta | `{text}` | 面试官消息（完整文案；打字机由前端客户端渲染） |
| question | `{index, question_id, domain, difficulty}` | 新题提示（只在新题时发一次：追问/评分重传同题不发；生成题无 question_id 不发） |
| done | `{interview_id, report_ready}` | 面试结束 |
| error | `{code, message, retryable}` | 流内错误（LLM 失败 / 步数超限）：**HTTP 仍是 200、场次仍有效**，图停在失败节点上——客户端「重试」= 重发同一文本，从该节点续跑（已入账的回答不重复计分；集成测试 `test_节点失败后重发同一文本_从断点续跑不重复计分` 钉死语义） |

**`stalled`（P1-M4.7 后续）**：`true` = 图卡在失败节点上（`next` 指向该节点且无中断载荷），区别于正常停在 `pause` 中断点（`next == ("pause",)` 且 tasks 带 interrupts）；已结束（`next` 为空）恒为 `false`。它是**服务端给的**判据，不是让前端从「末条消息是不是 assistant」这类外部特征反推——报告节点失败恰恰也表现为「末条是 assistant」，猜错就是面试永久卡死。

前端据此分两路（决策纯函数 `frontend/lib/recovery.ts`，vitest 覆盖）：卡住或回答没入账 → 重发（踢活失败节点 / 补发从未送达的回答）；**已跑完只是回复没传回来 → 只按服务端记录重建列表，绝不重发**（重发会被当成新一轮，同一份回答判两次）。

工程要求：`stream_mode=["updates"]`（llm.py 走裸 openai SDK，无 LangChain messages token 流可推；打字机效果由前端逐字渲染，阶段 2 若上真 token 流 delta 事件形状不变）；`X-Accel-Buffering: no`；15s 心跳注释（sse-starlette 内置 ping=15 实现）；async handler 全程 `astream` 不阻塞事件循环。

## 8. 数据库（SQLite，阶段 2 仍 SQLite，PG 迁移推阶段 3）

```sql
questions(id TEXT PK, question TEXT NOT NULL, answer TEXT NOT NULL,
  key_points JSON, follow_ups JSON, domain TEXT NOT NULL, topic TEXT NOT NULL,
  difficulty TEXT NOT NULL, company TEXT, round TEXT,
  source TEXT, status TEXT DEFAULT 'enabled')          -- source = 答案主源（明细见 question_sources）
question_sources(question_id TEXT, source TEXT, license TEXT, url TEXT,
  source_detail TEXT, imported_at TEXT, status TEXT DEFAULT 'enabled',
  PRIMARY KEY (question_id, source))                   -- 一题多源 = 多行（SPEC §8.1）
users(id TEXT PK, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL, created_at TEXT)
interviews(id TEXT PK, thread_id TEXT UNIQUE, user_id TEXT, position TEXT, interview_type TEXT,
  question_count INT, phase TEXT, difficulty TEXT, status TEXT, started_at TEXT, ended_at TEXT)
  -- interview_type（P1-M11）：tech/behavioral；老库补列后为 NULL，语义等同 tech，不做全表回填
answers(id INTEGER PK AUTOINCREMENT, interview_id TEXT, question_id TEXT,
  domain TEXT, difficulty TEXT, candidate_answer TEXT, followup_count INT,
  skipped INT DEFAULT 0, score_json TEXT, created_at TEXT)
reports(id TEXT PK, interview_id TEXT, payload JSON, created_at TEXT)
```

面试过程以 **checkpointer state 为权威**，answers/reports 为落库产物（结束后一次写入）。

**账号与归属（FR-23）**：密码 `hashlib.scrypt`（n=2^14/r=8/p=1，存储串自描述 `scrypt$n$r$p$salt$digest`）；JWT HS256，7 天有效，密钥 `JWT_SECRET` 走 `.env`（长度下限 32，弱密钥启动即失败）。归属列是 `interviews.user_id`——**checkpointer 不需要隔离**（thread_id = 全局唯一 uuid），业务库才是归属权威；隔离实现 = 列表按 user_id 过滤 + 其余端点先校验归属（`service._require_owner`）。老库启动时轻量迁移补 `user_id` 列（`PRAGMA table_info` 探测，阶段 1 的库免手工处理），历史孤儿行（`user_id IS NULL`）由首个注册账号认领一次（`claim_orphan_interviews`，不依赖「用户数为 0」判断，免并发竞态）。

**删除口径**：DELETE /api/interviews/{id} 物理删除——checkpointer 线程（`saver.adelete_thread`）与 interviews/answers/reports 三表一并清除，不做逻辑删除（逻辑删除的 `deleted_at` 过滤会污染所有查询）。

### 8.1 多源扩充口径（M5 会话 1 已落地拆表）

`source` 列语义 = **答案主源**；来源明细进 `question_sources` 关联表（PK = (question_id, source)，一题多源 = 多行）。

- **license 按源记、不按题记**；业务表与向量层都不再持有来源字段
- **主源裁决**（`bank.rank`，`min` 取优）：主源优先级（`SOURCE_PRIORITY` 登记表，个人题库恒 0——人工整理，开源源答案再长也不顶替）> 能用（enabled）> 答案长 > 轮次可信；完全同分取先导入者（列表序稳定，不另记时间戳）
- **同题合并**：题干 md5 相同 → 同 question_id → 跨源合并成一条；`sources` 每源留**最优**那条（按 `bank.rank` 排序后取首个）——留"最优"而非"先出现"，出处才指向答案真正来自的那一篇
- **迁移**：`ingest.ensure_schema` 探测老库（questions 有 license/url 列）→ 建来源表并回填 → `DROP COLUMN` 两列；幂等可重跑（`source_detail` 老库从未落库，回填 NULL，重跑管道由 JSON 补上）
- **同步语义**：questions 按 id upsert + `DELETE NOT IN`；question_sources 整表重建——它的同步规则比 questions 多一维（题还在、某来源没了也要删），逐行 diff 徒增复杂度，千行量级毫秒级
- 每个新源一个 adapter（复用 `bank.new_question`/`finalize_status`/`source_record`），把该源的分类体系归一化到统一 schema（topic→域、easy/medium/hard→L1/L2/L3、无轮次概念→NULL）；未知 topic 沿用"报错、人工补映射"（四源 adapter 见 §6.5）
- **对上层透明**：app 侧零改动（`fetch_by_ids` 只 select 固定列），Qdrant payload 不含 source 字段，检索命中后 join SQLite

## 9. 前端设计

- **导航（P1-M6 定调）**：**顶栏 tab**（`components/main-nav.tsx`），不用侧边栏。理由是这一层的页面性质：顶层页面是 3–4 个**平级工具页**（仪表盘 / 题库 / 能力档案 / 学习推荐），没有层级也没有分区，侧边栏是为「多层级 + 常驻切换」设计的，在这个规模上只是白占一条纵深；而面试页与报告页是**沉浸式**（导航必须隐藏），顶栏只要不渲染 `MainNav` 就干净了，侧边栏还得额外处理布局位移。**没做的页面现在就占位**：`NAV_ITEMS` 里 `ready: false` 的项渲染成不可点的灰字（`aria-disabled` + 「即将上线」），**绝不发出会 404 的 `<a href>`**——占位是让 M7/M9/M10 往里塞时不必重排导航，不是提前给用户一个坏链接。
- **仪表盘**：创建面试表单（方向固定 Agent/AI 工程师 + 题量 5/10/15 轮 + **难度选择**（P1-M6 FR-14：自适应 / L1 / L2 / L3，四选二行网格 + 一行说明）+ 历史列表（进入报告，**每条带物理删除按钮**（确认弹窗后调 DELETE 接口），meta 行显示题量与难度）。
- **题库页**（`/bank`，P1-M6 FR-12）：顶部搜索框（关键词走混合检索，提交后与筛选叠加）+ 域 chips（值来自 `facets`，`aria-pressed` 表选中）+ 三个下拉（难度/厂商/面次，首项「全部」）+ 结果卡片（可展开：参考答案 / 关键点 / **来源合规四要素**，主源标注「答案主源」、`原文` 外链 `rel=noreferrer`）+ 分页。结果卡头按模式切换文案：「按相关性排序 · 最多 20 条」（search，无 total）vs「共 N 题 · 第 x/y 页」（browse）。**筛选/搜索任一项变更即回第一页**（否则在第 5 页改筛选会落到空页）——这条在 `lib/bank.ts` 的 `withFilter` 里，vitest 钉死。
- **容量校验的前端口径（FR-14）**：创建表单挂载时拉一次 `/api/bank/capacity`（题量选项 × 4 难度一次拿全，切换难度零网络）；**直供不足的题量禁用 + 明写缺在哪**（「15 题不可选 —— 题库直供不足：规划与推理范式（需 2 题，题库 1 题）」），不做静默禁用（禁了不说原因，用户只会以为页面坏了）。**拉取失败一律不禁用**（`.catch` → `capacity = null`）：服务端本就不拦创建，网络抖动绝不能让表单自己把用户锁死。
- **面试页**：聊天流（fetch POST + SSE 流解析，`lib/sse.ts`）、打字机渲染（客户端逐字动画，delta 事件为完整文案）、阶段/进度指示（"技术问答 7/10"）、主动结束按钮、刷新后用 GET /interviews/{id} 恢复 UI；已结束场次进入只读回放（阶段 2 FR-25：隐藏输入框、顶栏标「已结束」，复用同一恢复接口）。
- **报告页**：Recharts 雷达图（五维）、知识域条形图、逐题点评卡片、短板高亮、总评；逐题复盘卡（阶段 2 FR-25：我的回答 / 五维得分 / 关键点对比 / 题库题参考答案折叠展示，项目深挖题仅关键点对比）。页头「决策回放」入口（P1-M4）。
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
| 8 | test_capacity / test_bank_query / test_bank_api（P1-M6） | 容量：配额 vs 直供的差集、自适应基准 = L1、`ok` 与不足明细互斥；浏览：分页稳定（同序不重不漏）、分面只计 enabled、来源按主源排序；接口：四筛选 + 关键词双模式（browse 有 total / search 无）、页码越界、`counts` 解析（夹紧去重升序）、未登录 401 |
| 9 | 验收清单 | PRD §7 八条（第 8 条 P95 在开发环境经 nginx 实测）+ 阶段 3 部署环境复测；FR-21 的「按场次可查 trace」为**云端人工核对**（跑 smoke 抄 trace_id 查控制台） |

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

- 2026-10-01 P1-M11（行为面 / HR 面 FR-22）：新增 §4.12（会话类型与 position 正交、单 BEHAVIORAL 段流程、行为题整池检索（不限难度）、题量上限 10、行为面五维与第 3 维对齐、**deepen-only 追问**（key_points 是讲述结构不是知识点）、聚合（域为空、短板改维度）、报告/PDF/推荐/档案的四处分派、`ASKABLE_DOMAINS`「可出题但不属于技术配额」、`enable_behavioral.py` 的 Qdrant 逐点核对验收、不做清单）；§4.1 补 `Phase.BEHAVIORAL` / `BaseScore`+`BehavioralScoreItem` / `interview_type`；§4.3 补 `explain_decision(deepen_only=)` 与 `Reason.DEEPEN_LIMIT`；§4.11 补**行为面排除（过滤字段 = 报告 payload 的 `interview_type`）**与响应新增 `excluded`；§7 创建接口补 `interview_type`（行为面 >10 题 422）、推荐与档案契约同步；§8 interviews 补 `interview_type` 列（老库补列 NULL ≡ tech，不回填）。新增 `data/scripts/enable_behavioral.py`（幂等：`finalize_status` 同源判定 + ingest 同源点构造 + **写后从 Qdrant 读回逐点核对**；真库已执行：behavioral enabled 14 题 / Qdrant 1099 点）。测试 470 → 506 passed
- 2026-09-30 P1-M10（能力档案 FR-19）：新增 §4.11（数据源 = 报告 payload、取数 `reports ⋈ interviews` 升序、**以 reports 表为准不按 status 过滤**、总分口径单一来源 `aggregate.overall_score`、域洞断线不补零、短板三态 `new/persistent/resolved` 与首场不产出、响应不含图表序列的理由、端点无场次参数故无 404 面、前端三形态与小倍图选型）与 §7 的 `/api/profile` 契约；§7 鉴权范围加 `/api/profile`。**总分口径收敛（D1）**：报告 payload 新增 `overall`（五维等权均值），报告页 / PDF / 档案共用同一个数——此前报告页与 PDF 各写了一遍同一公式，档案会是第三处。新增 `app/tools/profile.py`（`build_profile` 纯函数）、`app/api/profile.py`、`db.list_reports`、`service.get_profile`；前端 `lib/profile.ts`（刻度 / 断点 / 变化文案纯逻辑，vitest）、`components/profile-charts.tsx`（总分主曲线 + 小倍图）、`components/profile-client.tsx`、`/profile` 页与导航转正（**五项导航全部就绪**）；`report-charts` 的 `ChartFrame`/`tooltipStyles` 改为导出复用（不复制一套图表外观）
- 2026-09-30 项目叙事题改判 + 入库护栏（M9 实测整改）：§6.6 新增**人工改判表**口径（`data/curation/question_overrides.json`：逐题 `domain`/`status` + 必填 `reason`，key 是题干内容哈希故**未命中必报**，改判在零回归校验之后执行，已落库的库用 `apply_overrides.py` 补齐）；`ingest.py` 的 `DEFAULT_IN` 改指**合并后**的富化产物（原指个人题库单源的旧产物——全量同步语义下裸跑一次会删掉 1229 道开源题），并新增**入库护栏**（题量相差 >50% 直接停，`--force` 越过）。新增 `bank.load_overrides/apply_overrides`、`data/scripts/apply_overrides.py`、`test_curation.py` 与护栏用例
- 2026-09-30 P1-M9（学习推荐 FR-20）：新增 §4.10（数据源 = 报告 payload、查询 = 域标签 + 漏点、排除已问题且条数取 `k + 已问数`、多域并发保序、`status` 三态不静默隐藏、卡片带来源四要素、两处展示与场次承接）与 §7 的 `/recommendations` 契约。新增 `app/tools/recommend.py`（`build_query_items` 纯函数 + `recommend_for_report` 编排，`searcher`/`db_path` 可注入）；`question_search.fetch_by_ids` 多 select 一列 `source`（答案主源——来源列表的首位标签不能靠字典序猜）。前端 `lib/learn.ts`（默认场次承接/空分组文案纯逻辑）+ `components/recommend-groups.tsx`（报告页与学习页共用，含展开交互）+ `/learn` 页与导航转正；报告页新增「针对性练习推荐」卡并带 `?interview=<id>` 跳学习页
- 2026-09-30 P1-M8（PDF 导出 FR-18）：新增 §4.9（路线选型与服务端渲染的理由、payload 单一来源、雷达 SVG 两条引擎约束、东八区时间、线程池与不落盘、autoescape、容器与 macOS 宿主的运行环境、验收口径）；§7 补 `/report.pdf` 契约（下载头 RFC 5987、404 同报告端点）；§2 目录树补 `report_pdf.py` 与 `templates/`。新增 `app/report_pdf.py`（`radar_svg` / `render_report_pdf` / `content_disposition`；`_ensure_native_libs` 解 macOS 宿主 pango 搜索路径）与 `app/templates/report.html.j2`；五维中文标签上移到 `aggregate.DIMENSION_LABELS`（`FIVE_DIMS` 由它派生，PDF 与前端展示同源）。前端 `lib/download.ts`（文件名纯逻辑）+ 报告页「导出 PDF」按钮。**顺带修掉一条 M7 遗留**：smoke_api 的三处题库对账按全表计数，M7 起接口只认公共题（`user_id IS NULL`），真库一有私有题就误报「分面计数与题库总数不符」——抽出 `_PUBLIC_ENABLED` 谓词统一带上。**浏览器验收报出字体事故并当日修复**（用户：「PDF 在浏览器打开乱码、WPS 正常」）：字体栈只写在 `body` 上，`@page` 页边距框与 SVG `<text>` 不继承它 → 雷达标签与页眉页脚落到系统字体（macOS 苹方/宋体），而 macOS PDFKit 系渲染不了 weasyprint 嵌的苹方子集 → 整片缺字；Chrome/WPS 回退到系统同名字体故看不出。修法见 §4.9（`FONT_STACK` 单一来源 + 三处声明 + 模板 `| safe`），回归测试 `test_PDF只用随镜像分发的字体_不混进本机字体` 反向验证过。**修复后用户在浏览器复验通过（2026-09-30），P1-M8 验收闭环**
- 2026-09-29 P1-M6（题库页 FR-12 + 容量校验 FR-14）：新增 `tools/bank_query.py`（浏览查询层：SQL 分页 + 四维分面计数 + 来源明细挂载 + 供给统计）与 `api/bank.py`（三端点：`/questions` 双模式、`/facets`、`/capacity`）；§4.3 新增 capacity.py 口径 + `difficulty_locked` 分工（**落库 `interviews.difficulty` 存用户的选择、`state.difficulty` 存当前档位**）；§5.2 补筛选下推两路 prefetch 与 `query_filter` 参数名；§7 补三端点契约 + 创建接口的 `difficulty` 入参 + 题库端点的 401/404 分工；§9 补导航 IA（**顶栏 tab 定调**、未做页面占位不发出 404 链接）、题库页与容量禁用口径（不足要写明缺在哪、拉取失败一律不禁用）；§10 补 M6 测试行。前端 `lib/bank.ts`（筛选/分页/容量纯逻辑 15 例）+ `/bank` 页 + `MainNav`。**验收反馈两处**（2026-09-29 用户）：① 下拉与 chips **不带计数**（数字塞进选项显脏，条数只在结果区给总数）；② 难度分面**按档位 L1→L3 排**而非计数序（有序维度，L2 计数最多也不该顶到 L1 前）。**真库事实**（只读核对）：enabled 1095 题（L1 154 / L2 873 / L3 68），12 个「难度 × 题量」组合里**只有 L3 × 15 直供不足**（规划与推理范式 需 2 有 1，L3 × 10 需 1 有 1 恰好通过）——禁用态在真库上真实可见，smoke 用真实题库断言这一点并逐条复核不足明细。322 → 350 passed；前端 vitest 89 → 104；smoke_api 补题库三端点与 L3 锁定场次（对账直查 SQL：分面计数、浏览总数、分页不重不漏、主源排首位、license 齐、不足明细）
- 2026-09-29 P1-M5 会话 2（开源语料扩充，四源接入）：新增 §6.5（`parse_open.py` 四源 adapter——形态/题量/解析要点、未采项逐项进报告、占位答案判据）与 §6.6（`combine.py` 合并 + 个人题库零回归校验 + 近似重复只报不并）；§1 目录树补 `bank`/`parse_open`/`combine`。四源 license 逐仓核对（均仓库自带 MIT）：ai-agents-from-zero 89 / FAQ_Of_LLM_Interview 71 / ai-agent-interview-guide 261 / llm-interview-guide 809 = 1230 条来源明细；题库 342 → 1571 题（enabled 1095），`question_sources` 1572 行，Qdrant 重建 1095 点（dense+sparse）。新增 `test_parse_open.py` 与 `bank` 共享层用例（实质答案判据、聚合缺省不为空串、括号答案标记），302 → 322 passed；smoke_graph + smoke_api 零回归
- 2026-09-28 P1-M5 会话 1（`question_sources` 拆表）：§8 的 questions 表去掉 license/url、新增 `question_sources`（PK `(question_id, source)`）；§8.1 由「接入新源时怎么扩」改写为已落地口径（`source_rank`/`_rank` 主源裁决、`merge_sources` 每源留最优、列探测迁移、来源表整表重建、对上层透明）；§6.1 解析产物补 `sources` 明细与主源字段语义。新增 `backend/tests/unit/test_ingest_sqlite.py`（老库迁移与幂等、多源明细、全量同步删除），`test_parse_md.py` 补主源裁决与同源多记录用例（302 passed）。老库迁移在副本上逐字段对账后落真库：342 题零回归，`source_detail`（261 条）为老 schema 从未落库、本次顺带补回
- 2026-09-27 三条小修（P1-M4.7 后续，均为真链路暴露）：① §4.4 项目深挖题措辞去重（喂回已问题目原文 + 两层模板同禁复述背景）；② §7 会话响应新增 `stalled` 与前端两路处置（重发 / 只重建列表），`error` 事件补「不能靠前端猜死活」的口径与 `engine_stalled` 的两态判据；③ 成本回读口径与 Langfuse 模型价目核对（代码无改动，见踩坑记录）
- 2026-09-27 错误路径小修（P1-M4.7 后续）：§7 的 `error` 事件补口语义——流内错误时 HTTP 仍是 200、场次仍有效、**图停在失败节点上**，故客户端「重试」= 重发同一文本从断点续跑（已入账的回答不重复计分）；前端此前只 `setError` 不记 `failedInput` → 横幅没有重试出口、用户只能手动重打发一条重复消息。新增集成测试钉死「重发不重复计分」这一承重语义
- 2026-09-27 P1-M4.7-D（面试官人味层 + 技术题同域成块）：新增 §4.8（六类黏合点分两路生成——高频短衔接走 `rules/transition.py` 模板零 LLM，低频长文开场白/结束陈词走 LLM 每场 2 次；衔接语与新题同条消息、**答错缓冲独立成条**（回应上一题，prepend 会时序错位）；域标签插值按中文排版补空格；结束陈词模板无输入 + 四条红线含**禁止虚构后续流程**（真链路实测踩到「后续会有同事联系你」）、陈词调用失败降级跳过**不连坐报告**；重连语走 `?reconnect=true` 只附响应不落库）；§4.3 quota 补同域成块粘性（`pick_domain` 当前域配额未尽即续问，计数取**已答题**——`remaining_quota` 为当前题 +1 会提前切域；`allocate_quota` 未动，块序与域分布对同一 N 确定 → 跨场次可比性不受影响）；§4.4 补出题顺序；§7 补 reconnect 参数；smoke 加 `SMOKE_QUESTION_COUNT`、重连核对、同域成块断言 + 块内难度曲线打印、结束陈词红线自查，并**补印此前被静默忽略的 SSE error 事件**（实测有一轮因此「答了没反应」看不出来）
- 2026-09-26 P1-M4.6（C 阶段重排，项目深挖前置）：§4.3 advance 重写——PROJECT 答满 `project_count(question_count)`（min(3, max(2, ceil(N/3)), N−1)，保底 1 道技术题）→ TECH_BASE，答满 question_count → CLOSING；quota 技术轮数改 `question_count − project_count`；§4.1/§4.4 补项目深挖前置口径（首题与 PROJECT 阶段走 `_generate_scenario`：按轮出题、难度随 `state.difficulty`、domain="project" 不参与域统计保留）；展示标签「场景题」→「项目深挖」（question_type 值 scenario 不变，phase 枚举不变）；图边一条不动
- 2026-09-26 P1-M4.5-R1（追问密度修复，实测 3 题 10 次追问）：§4.3 规则重写——优先级 澄清（不占池）→ 深挖（达标，不占池）→ 遗漏（占池）→ 换题；`asked_key_points` 同一漏点只问一次（覆盖率跳变不重复追问）；全场补救池 `remedy_budget(N)=max(3, ceil(N×0.7))`（5 题 4 / 10 题 7 / 15 题 11），`remedy_used_total` 从已答题计数派生，澄清/深挖豁免、skipped 自然不计；新 reason `remedy_limit` / `missing_asked`，`TOTAL_LIMIT` 退役仅留旧事件映射；§4.4 补遗漏追问去重口径；§4.5 judge 输入改累计回答（先合并再评分，覆盖率允许下降不锁单调）
- 2026-09-26 P1-M4.5（A 出题接上下文 + B 深挖追问）：§4.3 `follow_up` 新增 DEEPEN 分支（`Decision.DEEPEN` / `Reason.DEEPEN_OK` / `deepen_limit=1`；优先级 澄清→遗漏→深挖；深挖要求无 error_flag，达标但深挖用尽仍报 `COVERAGE_OK`——旧原因值语义不漂移）；§4.4 出题接上下文（profile 进口吻层与生成模板 + 三条防漂移约束：question_id 不变 / 评分用原 key_points / prompt 禁改考察点）与深挖文案两路来源（题库元数据直发零 LLM / LLM 现场生成）；§4.1 `QuestionRecord` 补 `follow_ups`/`deepen_used`；§4.2 图注追问节点文案来源
- 2026-09-26 P1-M4 会话 2（FR-21 前端决策回放页）：§9 补决策回放页（`/trace/[id]` 逐轮时间线、只映射不重算、旧场次空态、入口仅报告页）与报告页入口；前端 `lib/trace.ts` 分组与取值守卫、`constants` 三类文案映射（事件/决策/原因，**原因标签不含阈值数字**，阈值只在后端 rules）。会话 2 收尾：评分小节补漏掉的关键点与评分官点评（`judgeEvidence` 守卫），`coverage_ok` 文案改「覆盖率达标」（原「关键点覆盖完整」与 70–100% 达标区间不符）；`lib/http.ts` 错误文案 CJK 守卫（框架英文兜底不端给用户）
- 2026-09-25 P1-M4 会话 1（FR-21 后端 + Langfuse 接入）：新增 §4.7（`trace_log` 事件模型 / `/trace` 接口 / Langfuse 接入口径与验证口径）；§4.1 补 `trace_log` 字段；§4.3 `follow_up` 改 `explain_decision` 决策与原因同源、`advance` 补 `end_quota` 门槛单一来源；§7 补回放接口；§2 补 `observability.py` 与 langfuse 依赖；§10/§11 补回放与可观测的测试与风险点
- 2026-09-24 P1-M3 会话 2（hybrid_search RRF + SiliconFlow rerank）：§5.1 补嵌入服务契约（请求/响应 schema、sparse 格式、批量上限、失败返回）；新增 §5.2 混合检索契约
- 2026-09-23 T8-R1（与 P1-M1 同批）：`chat_history` 取消 24 条截断（§4.1 字段语义 + §4.6 口径 + §11 风险点 6）——截断会让 SSE 长度差分失效、回放丢开场；补单测（state / sse）与整场 API 回归用例
- 2026-09-23 P1-M1（FR-25 复盘与回放）落地：§4.6 补实现口径（追问拼接标记、score 转标量、参考答案查询、报告走 v4-pro）、§4.1 answer 字段语义注释；前端复盘卡与只读回放（报告页 ↔ 面试页互链），历史 payload 兜底
- 2026-09-23 文档减负：§2 目录树对齐实际代码结构；PDF 解析因两份 PDF 合规否决、未实现（原 §6.3 已删）；实施顺序随 T1–T7b 全部完成而删除（原 §11 移除，风险注意点顺延为 §11）
- 2026-09-23 新增 FR-25 面试复盘与回放（阶段 2）：§4.6 复盘扩展口径、§9 复盘卡与只读回放
