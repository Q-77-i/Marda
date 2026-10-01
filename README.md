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
| 嵌入 | **本地 BGE-M3**（1024d，独立容器） | 一次前向出稠密+稀疏，省掉独立 BM25；模型自持，重嵌不依赖外部服务 |
| 向量库 | **Qdrant** | 原生稀疏+服务端 RRF，阶段2 混合检索不用换库 |
| 后端 | FastAPI + uvicorn + sse-starlette | Python 生态 + SSE 原生支持 |
| 前端 | Next.js 15 + TS + Tailwind + shadcn/ui + Recharts | App Router + AI 生态组件最全 |
| 可观测 | **Langfuse**（云形态） | 一次面试一个 trace：LLM 调用/成本按场次可查，面试过程可解释 |
| 业务库 | SQLite（阶段 1）→ PostgreSQL | checkpointer 同步升级 |

**设计原则**：确定性逻辑（阶段推进、轮数上限、追问决策、配额）全部用代码写死——可解释、可单测、UI 可回放；LLM 只负责出题、评分、追问文案这类语义任务。

## 目录结构

```
backend/            FastAPI + LangGraph
  app/
    domain.py       知识域定义（单一来源：配额 / 映射 / 报告共用）
    graph/          状态机（state / graph / nodes / rules）
    agents/         角色节点与结构化输出 schema
    tools/          RAG 检索工具（出题检索 / 混合检索 / 嵌入 / rerank 客户端）+ 题库浏览查询 + 学习推荐 + 能力档案
    api/            路由（SSE 流）
    service.py      服务层（图单例 / 事件翻译 / 落库编排）
    observability.py Langfuse 接入（一次面试一个 trace / 无 key 降级零开销）
    db.py           业务库持久化（interviews / answers / reports）
  embedding_service/  本地 BGE-M3 嵌入服务（独立镜像，torch 不进 api）
  tests/
data/
  scripts/          语料管道：bank（共享层）/ parse_md / parse_xmind / parse_open / combine / enrich / ingest
  parsed/           解析产物（gitignore）
  licenses/         语料来源清单与许可
frontend/           Next.js 15（app 路由 / lib 纯逻辑 / components）
  lib/              sse 流解析 / typewriter 队列 / api 封装 / 展示格式化 / 题库筛选与容量判据 / 学习推荐场次判定 / 能力档案曲线整形
  components/       顶栏导航 / 面试页客户端 / 报告页客户端 / 题库客户端 / 学习推荐卡 / 能力档案 / 图表 / UI 基础件
docs/               PRD / SPEC（个人规划文档不进仓库）
```

## 快速开始

### 一键起（演示 / 验收，Docker）

```bash
cp .env.example .env          # 填 DEEPSEEK_API_KEY / SILICONFLOW_API_KEY / JWT_SECRET
docker compose up -d --build  # → http://localhost:8080
```

`nginx（唯一入口） → web（Next standalone）/ api（uvicorn）`，背靠 `qdrant`（向量）与 `embedding`（本地 BGE-M3）——共五个容器；业务库与 checkpointer 落宿主机 `data/`，容器重建不丢。首次构建要下载模型权重（数分钟）。换端口：`MARDA_PORT=9000 docker compose up -d`。

### 开发模式（热重载）

```bash
brew install pango && brew install --cask font-noto-sans-cjk-sc  # macOS：报告 PDF 导出所需（见「报告导出 PDF」）
docker compose up -d qdrant embedding           # 向量库 + 嵌入服务（回环 6333 / 8091）
cd backend && uv sync && uv run uvicorn app.main:app --reload    # http://127.0.0.1:8000/healthz
cd frontend && pnpm install && pnpm dev         # http://localhost:3000
```

密钥放仓库根目录 `.env`（见 `.env.example`，**不进仓库**）：

```
DEEPSEEK_API_KEY=
SILICONFLOW_API_KEY=
JWT_SECRET=        # 账号体系签名密钥，随机生成；长度不足 32 会启动即失败
                   # python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

## LLM 封装

所有 LLM 调用走 [backend/app/llm.py](backend/app/llm.py) 这唯一入口（文案 `chat` / 结构化 `chat_json`，显式关 thinking + json_object + Pydantic 校验）。DeepSeek 坑位清单见 [CLAUDE.md](CLAUDE.md)。

## 语料管道

```bash
backend/.venv/bin/python data/scripts/parse_md.py       # 题库 md → 结构化 JSON
backend/.venv/bin/python data/scripts/parse_xmind.py    # xmind 解析 + 与 md 交叉对账
backend/.venv/bin/python data/scripts/parse_open.py     # 四源开源语料 → JSON（未采项逐项进报告）
backend/.venv/bin/python data/scripts/combine.py        # 个人题库 + 开源语料合并（自带零回归校验）
backend/.venv/bin/python data/scripts/enrich.py         # LLM 补 key_points / follow_ups（可断点续跑）
backend/.venv/bin/python data/scripts/ingest.py         # → SQLite + Qdrant 双写（重嵌 dense+sparse，需 embedding 在跑）
# 入库护栏：题量与库内相差 >50% 直接停（公共题是全量同步语义，指错文件会整批删题；--force 越过）

# 只改判十来道题（归域/上下架）时不必重跑全链：改判表 → 已落库的库
backend/.venv/bin/python data/scripts/apply_overrides.py [--dry-run]
```

**人工改判表**（`data/curation/question_overrides.json`）：逐题修正 `domain`/`status`，`combine.py` 每次运行都会应用。
用于个别题目归错域的场合（如 P1-M9 把「请介绍你的 Agent 项目」这类**项目叙事题**从技术域摘进行为面——它们
占技术题配额，还会把叙事漏点灌进学习推荐的检索查询）。key 是 `question_id`，即**题干的内容哈希**——题干改一个字
条目就失效，所以每次运行都会把未命中的条目标出来；已落库的库用 `apply_overrides.py` 补齐（幂等，不动其余题）。

`bank.py` 是三个解析脚本的共享层（`question_id` 生成、主源裁决、同题合并、来源四要素、状态判定），单测直接打在它上面。
解析口径与四源形态见 [docs/SPEC.md](docs/SPEC.md) §6.5/§6.6。

**语料规模**：个人题库 342 题 + 四源开源语料 1229 题 = 1571 题（enabled 1085——10 道项目叙事题已按改判表转入行为面 draft，见上）。
开源语料以 **Agent 岗方向**为主（agent-architecture / engineering-observability / rag），
LLM 模型层理论的页落在未启用的 `cs-fundamentals` 域、以 draft 只进 SQLite（域启用时零重采成本）。

**语料合规**：入库语料必须有明确 license；无 license、非商用（NC）、来源不明的一律不入库。
一题可有多源，来源明细进 `question_sources` 表（`source`/`license`/`url`/`source_detail` 四要素，**license 按源记**），
`questions.source` 只存**答案主源**（主源裁决规则见 [docs/SPEC.md](docs/SPEC.md) §8.1）。
清单见 [data/licenses/语料来源清单.md](data/licenses/语料来源清单.md)。

## 面试状态机（LangGraph）

[backend/app/graph/](backend/app/graph/) 是面试引擎核心，五阶段（开场 → 自我介绍 → 项目深挖 → 技术问答 → 反问）全部由状态机驱动：

- **确定性规则全在代码**（[graph/rules/](backend/app/graph/rules/)）：追问决策、难度连击升降档、知识域配额、阶段推进、结束门槛，全部有单测钉死状态转移
- **LLM 只产出文案与评分**（[graph/nodes/](backend/app/graph/nodes/)）：出题（题库检索 → 难度放宽 → LLM 生成三级降级）、评分（五维 1-5 结构化）、追问文案、场景题（结合候选人项目经历定制）、报告
- **出题接上下文（P1-M4.5）**：候选人的自我介绍提炼（`candidate_profile`）进口吻层与出题模板——面试官可以结合你的项目背景适度改写题干（"把抽象概念换成你项目里的场景"），但三条防漂移约束写死在契约里：question_id 不变、评分恒用原题 key_points、prompt 显式禁改考察点
- **深挖追问（P1-M4.5）**：答得好不再直接换题——覆盖率达标（≥70%）且无错误时触发 DEEPEN 追问，往答题边界追（为什么这样选/边界条件/底层机制/权衡）；题库题的深挖文案直接来自 `follow_ups` 元数据（零 LLM 调用），生成题由 LLM 从问答上下文现场生成。**追问密度控制（R1）**：评分看累计回答（首答+全部追问补充，覆盖率反映真实掌握程度）；同一 key_point 只追问一次（覆盖率跳变不重复追问）；全场补救池 `max(3, ceil(N×0.7))`（5 题 4 次 / 10 题 7 次 / 15 题 11 次）管总量，澄清与深挖豁免——单题上限 澄清 1 / 遗漏 2 / 深挖 1，最大 5 轮
- **出题顺序（P1-M4.7）**：项目深挖题全部前置，技术段**同域成块**——一个域连问到底才换下一个（RAG 域连问，而不是 RAG → Tool → RAG 的交叉抽取），块序与域分布对同一题量完全确定（保跨场次可比），块内题目仍随机；有了「跨域换方向」这件事，「我们换个方向，聊聊 RAG」这样的过渡才成立
- **面试官人味层（P1-M4.7）**：六类黏合点——开场寒暄（含预计时长）、阶段过渡、题间衔接、答错缓冲、重连语、结束陈词。高频短衔接**零 LLM**（[graph/rules/transition.py](backend/app/graph/rules/transition.py) 模板 + 插槽 + 按轮次轮换变体，确定性可单测可回放），只有开场白与结束陈词走 LLM（每场 2 次）。答错缓冲**独立成条**（回应的是上一题，拼进新题会读成"新题带着对上一题的评价"）；刷新回来带 `?reconnect=true` 即附一句问候 + 当前题干（只进本次响应、不落库，连续刷新不堆叠）；结束陈词模板**不接任何输入**（结构上拿不到报告文字）+ 红线禁止提分数/点评域强弱/虚构后续流程
- **断线续面**：checkpointer（SQLite）以场次为粒度持久化，中断后 resume 状态一致（集成测试覆盖）。失败重发**由服务端给判据**：会话接口的 `stalled` 报出「引擎卡在失败节点上」，前端据此分两路——卡住或回答没入账 → 重发（重跑失败节点，原始回答已在 state 里、不重复计分）；已跑完只是回复没传回来 → 只按服务端记录重建列表、绝不重发（否则同一份回答判两次）。判据不让前端从「末条消息是不是 assistant」反推：报告节点失败也长这样，猜错就是面试永久卡死
- **决策回放（FR-21）**：每个节点把「输入 / 决策 / 原因 / 状态变化」追加进 state 的 `trace_log`，`GET /interviews/{id}/trace` 一次取回整场事件流——为什么追问（答错澄清 / 覆盖率低补遗漏 / 达标深挖）、为什么换题（全场补救额度用尽 / 漏点均已追问 / 单题上限）、难度何时变档，逐轮可查。决策与原因**同源**（`explain_decision` 是唯一实现），回放里的原因不是旁白，是当时真正生效的那一条

```bash
uv run pytest -q                                    # 后端 470 个测试
uv run python scripts/smoke_graph.py                # 真实 DeepSeek + Qdrant 跑一场短面试
```

## API（SSE 流式）

[backend/app/api/](backend/app/api/) 提供面试 REST API，契约定在 SPEC §7：SSE 流式（`meta` / `delta` / `question` / `done` / `error` 事件，首事件携带 `interview_id`）、会话恢复、报告与历史查询、场次物理删除；过程状态以 checkpoint 为权威，结束一次落库。

- **账号（FR-23）**：[app/api/auth.py](backend/app/api/auth.py) 提供注册 / 登录 / 当前用户，JWT 全端点鉴权（Bearer）。密码 scrypt 加盐哈希、用户名字母大小写不敏感；场次按 `user_id` 隔离，跨用户访问按「不存在」404（不泄露存在性），阶段 1 的历史场次由首个注册账号认领
- **鉴权细节**：401 与 404 的分工——未登录/失效 token 401；他人场次 404（与「场次不存在」不可区分）
- **`GET /interviews/{id}/trace`**：决策回放事件流（未结束的场次同样可查，做实时决策视图）
- **`GET /interviews/{id}/recommendations`（P1-M9 FR-20）**：学习推荐——读该场报告的短板域，现检索题库给资料卡（题干 + 答案 + 关键点 + 来源四要素）。**不重算分数、不落库**：报告的 `weaknesses` 给域、逐题 `missed_key_points` 给查询词（域标签 + 漏点，零新增 LLM 调用）；本场已问过的题被排除（复盘卡已给过它们的参考答案），检索条数按 `k + 已问数` 取以保证过滤后仍够 k 条；空分组给三态而不是静默隐藏（已练过 / 该域无题）。检索走 M3 的 `hybrid_search`，编排在 [app/tools/recommend.py](backend/app/tools/recommend.py)
- **`GET /profile`（P1-M10 FR-19）**：能力档案——该用户全部已落库报告聚合成多场得分曲线与短板变化（场次序列 / 概览 / 逐场短板三态）。**不重算分数、不落库、零 LLM 调用**：数据源就是报告 payload 本身，总分取报告里存的 `overall`（与报告页、PDF 同一个数）；**以 reports 表为准**（有报告才算数，`finished` 但报告缺失的场次不进曲线）；未考过的知识域在曲线里**断线而不是补零**。聚合在 [app/tools/profile.py](backend/app/tools/profile.py)
- **题库（P1-M6 FR-12/FR-14）**：[app/api/bank.py](backend/app/api/bank.py) 三端点——`/api/bank/questions`（关键词走混合检索、否则 SQL 浏览分页，每项带来源明细，主源排首位）、`/api/bank/facets`（四维取值 + 计数，前端的筛选项由它生成、不硬编）、`/api/bank/capacity`（难度 × 题量的题库直供能力 + 不足明细）。查询层在 [app/tools/bank_query.py](backend/app/tools/bank_query.py)，与出题检索分工：出题只要「过滤 + 随机 k 条」，浏览要总数、来源合规四要素与分面计数
- **创建时选难度（FR-14）**：`difficulty ∈ adaptive / L1 / L2 / L3`（默认 adaptive）——固定档位场次全程不升降（`state.difficulty_locked`），落库存的是**用户的选择**（列表页回显），`state.difficulty` 才是当前档位。容量校验只做前端展示判据、**不拦创建**：直供不足 ≠ 出不了题（引擎有难度放宽 + LLM 生成兜底）

**可观测（P1-M4）**：[app/observability.py](backend/app/observability.py) 把 Langfuse 接在轮次这一层——trace_id 由场次 id 派生，所以一场面试的多次 resume 落进同一个 trace（不是散成 N 个），`session_id` = 场次、`user_id` = 账号；LLM 调用经 `langfuse.openai` drop-in 自动成为带 usage 的 generation，token 成本按场次/按人可聚合。**没配 key 就整体降级为零开销**：不 import、不构造客户端、不联网，本地与 CI 无需账号。接线由单测离线钉死（注入内存导出器），「云端按场次可查」由 smoke 读回核对——按场次派生 trace_id 把观测拉回来，断言 session_id/user_id 归属、generation 归父、模型名（含报告走深度档）与 token/成本汇总，不靠抄 id 到控制台肉眼比对。**成本金额的单位是人民币**（Langfuse 的 `$` 是它硬编码的符号），前端显示一律 `¥` + 数值原样，不按汇率换算。

```bash
uv run python scripts/smoke_api.py                # 真实链路走 HTTP 跑一场短面试 + 落库验证
SMOKE_QUESTION_COUNT=10 uv run python scripts/smoke_api.py   # 长场次：看同域成块、块内难度曲线与结束陈词
```

## 前端（九页面 + 流式联调）

[frontend/](frontend/) 是 Next.js 15 App Router，九个页面：登录 `/login`、仪表盘 `/`（新建 + 历史）、题库 `/bank`、私有题库 `/bank/private`、能力档案 `/profile`、学习推荐 `/learn`、面试页 `/interview/[id]`、报告页 `/report/[id]`、决策回放页 `/trace/[id]`。请求走同源 `/api/*`（[next.config.ts](frontend/next.config.ts) rewrites → 后端），免 CORS 配置。

- **导航（P1-M6 定调）**：顶栏 tab（[components/main-nav.tsx](frontend/components/main-nav.tsx)），不用侧边栏——顶层是 3–4 个平级工具页、没有层级，侧边栏只是白占一条纵深；面试页/报告页是沉浸式，顶栏不渲染 `MainNav` 就干净了。「学习推荐」于 P1-M9、「能力档案」于 P1-M10 先后转正，**五项导航现已全部就绪**（占位机制保留：`ready: false` 的项渲染成不可点的灰字，不发出会 404 的链接）
- **题库页（P1-M6 FR-12）**：关键词搜索（混合检索，与筛选叠加）+ 域 chips + 难度/厂商/面次三下拉（候选值都来自 `facets`，不硬编，扩语料后新厂商自动出现；**选项里不带计数**——数字塞进下拉和 chips 显得脏，条数只在结果区给总数）+ 结果卡可展开看参考答案/关键点/**来源合规四要素**（主源标注、`原文` 外链 `rel=noreferrer`）+ 分页；结果卡头按模式切换「按相关性排序 · 最多 20 条」/「共 N 题 · 第 x/y 页」。筛选或搜索一变就回第一页（否则在第 5 页改筛选会落到空页）
- **容量校验（FR-14）**：创建表单挂载时一次拿全「题量 × 难度」的直供结论，**不足的题量禁用并写明缺在哪**（「15 题不可选 —— 题库直供不足：规划与推理范式（需 2 题，题库 1 题）」）；不做静默禁用（禁了不说原因，用户只会以为页面坏了），**拉取失败一律不禁用**（服务端本就不拦，网络抖动不能让表单把自己锁死）

- **登录与路由守卫（FR-23）**：[lib/session.ts](frontend/lib/session.ts) 管 token（localStorage 优先，隐私模式等环境自动降级 sessionStorage，两者都禁用则明确提示而非静默失败），[lib/http.ts](frontend/lib/http.ts) 统一注入 Bearer 与 401 处置；未登录访问受保护页由 [AuthGuard](frontend/components/auth-guard.tsx) 跳登录（判断完成前先渲染载入态，不闪受保护内容）。**401 默认直跳登录页，唯独面试页弹确认再跳**——答题答到一半被直接踢走体感太差；登录接口自身的 401/409 只当表单错误展示，绝不触发全局跳转

- **SSE 走 POST**：`EventSource` 只支持 GET，[lib/sse.ts](frontend/lib/sse.ts) 用 `fetch` + 手动分帧，兼容心跳注释与中文跨 chunk 截断
- **打字机在前端**：后端 `delta` 发完整文案，前端 [TypewriterQueue](frontend/lib/typewriter.ts) 逐字渲染（FIFO，前一题吐完才吐下一题；单测钉死顺序性）
- **报告图表**：Recharts 雷达图（五维 1-5）+ 横向条形图（短板域警示色**并附文字标注**，不靠颜色单独表意）；配色经调色板校验器明暗双模式检查
- **逐题复盘（FR-25）**：报告页每题一张复盘卡——我的回答（按 `【追问补充】` 标记分成「首答 / 追问补充 N」，不混成一大段）、五维得分、关键点覆盖对比（✓ 覆盖 / ✗ 遗漏）、题库题参考答案折叠展示（场景题无权威答案不渲染）；历史报告缺这些字段时退化为「题干 + 点评」
- **只读回放（FR-25）**：已结束场次进面试页即完整回放（复用会话恢复接口，隐藏输入框、顶栏换「查看报告」），报告页与回放页互链；结束当刻仍自动跳报告。完整是有前提的——对话历史在状态里全量保留、不截断（截断会让 SSE 差分失效、面试官文案漏发，长场次尤其明显）
- **报告导出 PDF（FR-18）**：报告页「导出 PDF」一键下载，内容是**服务端渲染的真文本 PDF**（可选中、可检索），封面统计 + 五维雷达 + 域得分与短板 + 总评 + 逐题复盘（含参考答案）+ 学习建议，与页面同源同文案，不重算任何分数。中文排版由 HTML/CSS 引擎（weasyprint）负责、雷达图是后端手绘的内联 SVG，所以「中文无乱码」能落成 pypdf 读回断言而不是靠眼看（详见 [SPEC §4.9](docs/SPEC.md)）。**改后端代码的 macOS 宿主机需先 `brew install pango && brew install --cask font-noto-sans-cjk-sc`**（容器镜像已自带 pango 与 Noto CJK；宿主缺字体时，导出的 PDF 会退到本机字体，在 Safari/预览里缺字）
- **学习推荐（P1-M9 FR-20）**：报告页「针对性练习推荐」卡 + 学习页 `/learn`，按短板域给该域的**新材料**——题干、答案全文、关键点、来源四要素都可行内展开（与题库页同一套交互）。推荐读的是该场报告的短板定位，所以每张卡都对着你的短板（不是泛泛推题）；本场已问过的不再出现，检索条数按「已问数」放宽以保证过滤后仍够数；某个域练完了就说「该域题目已全部练过」——空着不吭声会被当成系统漏了。从报告页跳过来时默认选中**来源场次**（`?interview=<id>`），不落到最近一场上
- **能力档案（P1-M10 FR-19）**：`/profile` 把多次面试连起来看——**总分曲线**（点可点进那一场的报告）、**短板变化**（逐场对上一场比：新出现 / 持续存在 / 已改善，三态分开说）、知识域与五维各一张迷你趋势图。一场只考部分知识域，**没考过的场次曲线断开而不是记 0 分**（补零会凭空造出一个低谷）；总分与报告页、PDF 是同一个数（后端单一来源，不各算一遍）。空档案给引导与「开始第一场面试」入口，只有一场时说清楚为什么还画不出曲线
- **决策回放（FR-21）**：`/trace/[id]` 把引擎当时的判断逐轮摊开——选了哪道题（域/难度/题型/题库命中几个候选还是降级生成）、评分多少（覆盖率/五维/回答原文折叠）、**为什么追问、为什么换题**（原因与决策同源，由后端记录，前端只映射文案不重算）；被挽留的结束请求、报告收尾单列。入口在报告页；更早的场次没有事件流，页面给空态而不是装作有数据
- **刷新恢复与错误路径**：刷新后从 checkpoint 重建消息列表，已结束场次进只读回放；网络失败、HTTP 4xx 与流内 `error` 事件（LLM 抖动等）都转中文文案 + 重试按钮，重试不重复插入消息——流内错误时图停在失败节点上，重发同一文本就是从断点续跑，已入账的回答不会重复计分
- **输入体验**：Enter 发送、Shift + Enter 换行，输入法"上屏回车"不误发送；输入框随内容长高，约 40% 视口高封顶后框内滚动
- **题量与记录**：题量 = 全场问答轮次（选 N 就是 N 轮，进度与报告自然一致）；历史记录带物理删除（确认弹窗）

```bash
cd frontend && pnpm test          # vitest：SSE 解析 + 打字机队列 + 展示格式化 + 登录态 + 决策回放/失败重发 + 题库筛选分页与容量判据 + 学习推荐场次判定 + 能力档案曲线整形（143 个）
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

阶段 1 demo 已完成（T1–T7b）；阶段 2（P1）进行中：**P1-M1 面试复盘与回放已完成**（逐题复盘卡 / 只读回放 / 报告走 v4-pro）；**P1-M2 账号体系已完成**（后端 JWT 鉴权 + 多用户隔离，前端登录注册页 + 路由守卫 + 401 处置）；**P1-M3 混合检索与 rerank 已完成**（本地 BGE-M3 双向量 + Qdrant RRF + SiliconFlow rerank：hybrid_search 三路链路与六大域相关性抽查通过，M6 题库搜索时对用户可见）；**P1-M4 已完成**（会话 1：决策回放事件流 + `/trace` 接口 + Langfuse 接入；会话 2：前端 `/trace/[id]` 逐轮回放页与报告页入口）；**P1-M4.5 已完成**（出题接上下文 + 深挖追问 + R1 追问密度修复）；**P1-M4.6 已完成**（阶段重排：项目深挖前置 + `project_count` 公式 + 标签统一）；**P1-M4.7 已完成**（面试官人味层：六类衔接语 + 结束陈词红线 + 技术题同域成块）；**M4 整体收官**（含流内 `error` 事件的重试出口小修，浏览器手点一次完整面试验收通过）；**P1-M5 会话 1 已完成**（`question_sources` 拆表：一题多源 provenance 落地 + 老库迁移，342 题全字段零回归）；**P1-M5 会话 2 已完成**（开源语料扩充：WenQu 登记表定位的四源 MIT 语料接入，342 → 1571 题 / enabled 1095，Qdrant 重建 1095 点，smoke_graph + smoke_api 零回归）；**P1-M6 已完成**（题库页 + 容量校验：`/api/bank/*` 三端点与 `/bank` 页、创建时可选难度（固定档位全程不升降）、题量按题库直供能力禁用并写明缺在哪；真库上唯一不可选的组合是 L3 × 15 题）；**P1-M7 已完成**（私有题库：模板 md/PDF 上传与确定性解析、部分成功语义、混入式出题（私有题与公共题同池随机、不进向量库）、`/bank/private` 管理与归档）；**P1-M8 已完成**（报告导出 PDF：服务端渲染真文本 PDF，雷达图为后端手绘 SVG，pypdf 读回断言「中文无乱码」）；**P1-M9 已完成**（学习推荐：报告短板域 → 漏点关键词驱动混合检索 → 资料卡（题干/答案/来源四要素），报告页与 `/learn` 双展示，本场已问过的不重复推荐；两场实测暴露「项目叙事题混在技术域」会污染推荐查询 → 已建**人工改判表**把 10 道这类题摘出技术域）。**P1-M10 已完成**（能力档案：`GET /api/profile` 把该用户全部已落库报告聚合成多场曲线与短板三态变化，总分口径收敛到 `aggregate.overall_score` 单一来源，报告页/PDF/档案显示同一个数；未考过的域断线不补零；`/profile` 页与导航转正——**五项导航全部就绪**）。后续 M11–M12 见 [docs/PRD.md](docs/PRD.md) §8.1。

## 文档

- [docs/PRD.md](docs/PRD.md) — 需求（FR-01~24、核心业务规则、验收标准）
- [docs/SPEC.md](docs/SPEC.md) — 技术规格（状态机 schema、API 契约、数据库、测试计划）
