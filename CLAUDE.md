# Marda 码达 · 项目工程规约

面向计算机学生的 Agent 智能面试学习平台（校招简历副项目）。核心：Agent 智能面试引擎（LangGraph 状态机：自主出题、动态追问、会话状态维护）+ RAG 作为 Agent 工具 + 能力评估/短板定位/学习推送。业务闭环：模拟面试 → 发现短板 → 针对性学习。

**P1/P2 已开发完成**（2026-10-05 收尾）；**多模态扩展 M13–M16 已完成**（2026-10-21：语音动效/右栏三 Tab、白板工作台与结构点评、代码工作台、ASR 表达复盘），落在 `exp/multimodal` 并经 PR 合回 `main`。本文档 = 工程规约与拍板口径的现行版本；配套文档分工：

| 文档 | 角色 |
| --- | --- |
| [README.md](README.md) | 门面：定位 / 演示 / 快速开始 / 技术栈 |
| [docs/README.md](docs/README.md) | 文档导航：公开 / 私有边界与各目录职责 |
| [docs/PRD.md](docs/PRD.md) | 需求：功能清单、业务规则、页面、验收标准 |
| [docs/spec/SPEC.md](docs/spec/SPEC.md) | 技术规格索引：正文按域拆分到 [docs/spec/](docs/spec/)；**章节号被代码注释引用 100+ 处，冻结** |
| [docs/ops/P4-生产化.md](docs/ops/P4-生产化.md) | P4 生产化：多 worker + PG + Redis、checkpoint 压缩与保留、迁移与回滚、已知边界 |
| [docs/p5/README.md](docs/p5/README.md) | P5 业务动机：面试诊断 → 学习执行的闭环、业务目标与成功标准 |
| [docs/p5/P5-Java业务.md](docs/p5/P5-Java业务.md) | P5：Java 业务融合与分布式工程演进（评估稿） |
| [docs/p5/contracts/](docs/p5/contracts/) | P5：OpenAPI 骨架 + 事件 JSON Schema |
| [docs/里程碑.md](docs/里程碑.md) | 里程碑索引：每个阶段做了什么、关键决策、验证证据（详细过程在本地 private） |
| 本文档 | 规约：协作方式、环境密钥、坑位清单、架构原则、语料红线 |

## 协作规则

- 只管本文件夹 /Users/zhouq/VibeCoding/Marda；同级姊妹项目不读、不动、不受影响
- **Git 提交与推送前必须向用户确认**。公开仓库：https://github.com/Q-77-i/Marda（SSH 已配置）
- **分支模型**：`main` 是唯一长期分支，也是可演示最终版；小改动直接提交 main（ruleset 已给 Repository admin 配 Always allow bypass）。多模态扩展在 `exp/multimodal` 上完成（M13–M16）后 PR 合回 main；`exp/microservices` 需要时再建，不提前建空分支。里程碑用 annotated tag：`m-p1` / `m-p2` / `m-p2-ui` / `m-p2-mm`；tag 只是快照，不替代开发分支。
- **Git 安全与 CI**：不 force-push main、不改写已发布历史；删分支前走 `mattskills:git-safety-guardrails`。CI 在 push main 与 PR 上都会跑，直推前本地跑 pytest / vitest / build。
- **gh 可用**：本机已装 `gh`（Q-77-i，repo scope），PR / Release 可用 CLI；提交与推送前仍须确认。
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

- 后端：Python 3.11+ / FastAPI / uvicorn / sse-starlette；`langgraph==1.2.11` + `langchain==1.4.0` + `langgraph-checkpoint-sqlite==3.1.1` + `langgraph-checkpoint-postgres==3.1.0`（P4）+ `psycopg[binary]` / `psycopg-pool` / `redis`
- 前端：Next.js 15 + TypeScript + Tailwind CSS + shadcn/ui + Framer Motion + Recharts（雷达图）+ **Monaco**（代码工作台，同源 `/monaco/vs` 静态资源、不走 CDN）；设计基调：克制配色、强排版、微动效
- Java（P5 J2+）：**JDK 21** + Spring Boot 3.3 + Spring Cloud Gateway + Maven；`services/gateway` 是 J2 骨架（JWT HS256、`/api/v1/learning/**` 路由、`X-User-Id` / `X-Service-Token` / trace 头注入、访问日志）；**虚拟线程**只用于网关访问日志的阻塞 I/O（`VirtualThreadConfig`），**不要**套到 Netty 请求路径；J3 的 Spring MVC business 用 `spring.threads.virtual.enabled=true` 跑阻塞 DB / HTTP
- 数据：**P4 起 = PostgreSQL 16**（业务库 + checkpointer **独立库**）+ **Redis 7**（会话锁 / per-user 限流 / 全局 LLM 槽）+ SQLite（**题库只读**，离线资产不迁）+ Qdrant 1.19（向量，原生稀疏 + 服务端 RRF）。默认 compose 是 P4；单机形态保留为 `docker-compose.legacy.yml` 与 SQLite 开关（`DB_BACKEND=sqlite` / `CHECKPOINT_BACKEND=sqlite` / `COORDINATION_BACKEND=memory`）。**题库不迁 PG**——它是离线构建、运行时只读的资产，迁它会把语料管道与检索 join 一起拖进来
- 业务库分派：**门面 `app/db.py` 按 target 类型分派**（`Path`→SQLite / DSN 字符串→PostgreSQL），两个实现（`db_sqlite.py` / `db_postgres.py`）**函数签名与语义必须一致**；路由层只传 `settings.business_db`，**不出现 SQL 方言分支**
- RAG：本地 BGE-M3（1024d）+ bge-reranker-v2-m3（**按查询形态开关**：短查询 rerank、多漏点长查询直接走融合序——长查询实测净贡献为负）+ 每题一 doc 分块 + 混合检索（dense+sparse RRF）
- 可观测：Langfuse（云形态，无 key 即整体降级为零开销）；评估：**自研评估 harness**（评分一致性门禁，失败非零退出）+ RAGAS（离线检索指标）
- MCP：仅"题库查询"1 个 server（stdio only、只公共题），协议代码关在 `app/mcp_server/`（单测扫描钉死别的模块不许 import `mcp`）；A2A 不引入
- CI：GitHub Actions **五 job**（后端 pytest / 前端 lint+vitest+build / **P4 集成（真 PG + 真 Redis）** / **Java 网关（Maven + Testcontainers Redis）** / 语料入库守卫 + 检查器自检）。**真比对与 smoke、评测不进 CI**（语料不进仓库、真 key、花钱、会被上游抖动误伤）
- 语音：**豆包流式 ASR**（`WS /api/asr` 中继，v3 二进制协议）+ **edge-tts 播报**（三档降级）。**引擎与模态解耦**——语音只是新增一条音频通道，图/状态机/落库/SSE 事件表一条未动。**两把火山 key 不通用**（方舟 Bearer ≠ 豆包语音 X-Api-Key）
- 视觉：**deepseek-flash 视觉**（代码截图先行）。图存磁盘 `data/uploads/`、state 只存 image_id；**图独立成附件消息、不改任何 prompt 模板**（无图路径逐字一致）
- 白板（M14/M14b）：Excalidraw 导出 PNG 走既有图片通道；白板结构点评规则**只加在带图的附件说明里**（`NOTE_JUDGE_IMAGE_RULE`），不动 `JUDGE_TEMPLATE`——纯文字评分 prompt 逐字不变。报告官可能为「答非所问」丢掉缺失项，`build_per_question_comments` 会从评分官 comment 抽原句**确定性补回**（只对有图题、只抽含缺失语义的句子）
- 代码工作台（M15）：**独立于面试状态机**——`/api/coding/questions/random|reviews|followups` 三个端点，随机算法题只出安全投影（不带 answer/key_points），点评是 flash 结构化输出（问题/具体反例/复杂度/一个追问），追问无状态；v1 不打分、不执行代码、不落库、不进报告。**题面用 `agents/coding_questions.py` 的自建目录补全**（题库很多题只有标题；git 只存题库 id 与我们自己的标题/描述/示例/约束）
- 表达复盘（M16）：前端只上报原始计数（时长/字数/长停顿/口头禅/段数），语速与场次汇总在 `graph/rules/speech.py` 单点派生；随消息 `speech` 键进 state，报告 payload 落 `expression` 与逐题 `speech`，报告页/PDF 同一份数据。**不做面部**；ASR 可能漏语气词、长停顿是 RMS 估算，报告里如实标注

## 架构原则（面试要能讲出"为什么"）

- **LangGraph 管编排、LangChain 管组件**：流程有"向后的箭头"（循环/分支/中断恢复）→ LangGraph；直线管道 → LCEL
- **确定性逻辑用代码写死，语义任务交给 LLM**：阶段推进/轮数上限/追问上限/追问决策全部纯代码（可解释、可单测、UI 可回放）；LLM 只做出题/评分/追问文案
- **结构性多 Agent**：出题官/评分官/报告官为子图/角色节点，共享 InterviewState 黑板通信；不做 agent 自由对话
- **侧工具不碰状态机**：白板/代码工作台只新增输入通道或独立 API，不往图里加节点、不改评分 schema；表达复盘只算不评（不进五维分数）
- 降级链：`deepseek-v4-pro → deepseek-flash → 预置题库兜底`（题库是天然降级路径）；上游不可用时另有完整的确定性兜底（见 SPEC §3）
- 一次面试 = Langfuse 一个 trace（session_id = 场次）；评分维度：技术深度/基础掌握/项目经验/沟通表达/问题解决

## 岗位与题库（已拍板）

- 岗位方向：**Agent/AI 工程师**；知识域与权重：Agent 认知与架构 20% / 规划与推理范式 15% / Tool 与 Function Calling（含 MCP）15% / Memory 15% / RAG 20% / 工程化与可观测 15%
- 题库：个人题库 + 四源开源语料（JavaGuide / doocs / haizlin / tech-interview-handbook / InterviewGuide / WenQu 属白名单）= **1576 题（公共 enabled 1096 + 私有 enabled 8；J0 实测 2026-11-10）**，结构化 JSON 携带 company/round/topic 元数据；难度按「L1 概念 / L2 原理与选型 / L3 底层与权衡」三档标注，12 个「难度 × 题量」组合全部可供
- 面试范围：技术面 + 行为面/HR 面（复用同一状态机，只换能力模型与题源）

## 语料合规（红线）

- 开源语料白名单：JavaGuide（Apache-2.0）、doocs 系列（CC-BY-SA-4.0）、haizlin/fe-interview（MIT）、tech-interview-handbook（MIT）、InterviewGuide（Apache-2.0）、WenQu（MIT）
- **禁用**：CS-Notes（NC）、小林 coding（无 license）、面试鸭题库、labuladong 等无许可仓库；不爬站
- **个人题库（docs/题库/）仅本地使用，默认不进 git**；每条语料带 source/license/url 元数据
- **红线覆盖派生文本，不只看原始文件**：个人题库的**题干/答案/关键点原文**经任何加工后（评测 golden、复核产物、报告、导出样例、日志摘录……）同样不进 git——原始文件被 gitignore 不等于它的内容安全。**判据不是「有没有 id」，是「文本里会不会出现题库原文或其片段」**：M12 实测踩过——以为「只提交 id 版产物」就安全，而检索 golden 的查询文本是报告漏点原文、漏点又正是题库关键点的片段（13 条漏点查询与个人题库关键点重合 94 处）。**提交前跑 `check_redline.py` 机械反查**（两侧归一成中文骨架后滑 12 字窗口比对，命中即非零退出），查得出来就不入库

## 多模态扩展坑位（M13–M16，写代码前必读）

1. **Monaco 必须同源**：`scripts/copy-monaco-assets.mjs` 把 `monaco-editor/min/vs` 复制到 `public/monaco/vs`（gitignore），编辑器模块里 `loader.config({ paths: { vs: "/monaco/vs" } })`；不要走 `@monaco-editor/react` 默认 CDN。ESLint 必须忽略 `public/monaco/**`（否则 2.5 万条告警）。**功能探针用 `pnpm dev`**：`next start` 与 `output:"standalone"` 组合会在编译/资产上出怪问题（Monaco 静态页能过，含动态 chunk 的页面会卡）
2. **语音计数必须是整数**：`SpeechStats` 是 Pydantic int，前端 `pause_ms` 来自浮点时间戳，必须 `Math.round`——实测漏一次就整条消息 422。`pause_ms ≤ duration_ms`、`pause_count×2000 ≤ pause_ms` 由 API 校验
3. **文字消息要清空 `current_speech`**：pause 节点每次覆盖（None），否则上一轮指标会重复计入下一题；judge 在 LLM 调用**之前**合并，评分降级也要留下指标
4. **表达指标只算原始数**：语速/汇总只在后端 `rules/speech.py` 派生；前端不再算一遍
5. **白板点评的验收是手工探针**：评分门禁的 golden 是纯文字样本，只能证明「文字评分不回归」，证明不了「模型读了架构图」。真实探针 = 上传一张缺组件/连线的图，查 trace 的 judge comment 是否点名缺失项
6. **FakeLLM 路由顺序**：「报告官」必须排在「评分官」之前——M14b 起报告 prompt 里会出现「评分官点评」，先匹配评分官会把报告调用误路由成评分 JSON
7. **Chrome 155 的 `--use-file-for-fake-audio-capture` 在本机实测无声**（headless/headful 的 RMS 都是 0）：浏览器侧要机械验证语音链路，改用页面内覆写 `getUserMedia` + 同源 WAV → `MediaStreamDestination`，再经真实 AudioWorklet → ASR；探针证据在 `docs/private/m16-verify/`
8. **刷新恢复与摄像头**：摄像头**不在页面加载时自动取流**——Safari 无手势 `getUserMedia` 会每次刷新弹权限；`marda.cameraMode` 只驱动「恢复摄像头」按钮提示。工作台面板写 URL `?panel=code|board`，代码快照与白板场景按场次存 localStorage（`parseSavedCodeWorkbench` / `parseWhiteboardScene`）；快照格式坏了整份丢弃、重新抽题，不炸工作台

## P4 生产化坑位（写代码前必读）

1. **压缩 serde 的 type 要带后缀**：checkpoint 的 type 存在库的 `type` 列里，写成 `msgpack+zlib` 之后**旧 `msgpack` 行必须照常可读**——不兼容等于老场次全废，比膨胀更糟。`JsonPlusSerializer.loads_typed` 收的是**元组** `(type, payload)`，不是一个字符串
2. **PG saver 的表结构与 SQLite 版不同**：`checkpoints.checkpoint` 是 **JSONB**（通道值另存 `checkpoint_blobs` BYTEA，按 version 索引），删快照时要连**没人引用的孤儿 blob** 一起清，但只删「没有任何现存快照引用」的——删多了会读不出状态
3. **`langgraph-checkpoint-postgres` 的版本要对齐**：`langgraph-checkpoint 4.2.0` 配 `3.1.0`（3.2.0 起要求 `>=4.3.0`，会把 `langgraph 1.2.11` 顶掉）
4. **Redis 的四种用途失败语义各不相同**：会话锁 **fail closed**（503，宁可拒绝也不能两 worker 同跑一场）、限流 **fail open**（放行，限流是保护不是正确性）、LLM 槽**回退进程内闸门**（上游保护不能因为 Redis 挂了就没了）、TTS 票据**回退进程内**（播报是可选能力，不阻断面试）
5. **Redis 模式下不要再叠进程内 Semaphore**：全局槽就是闸门，两层叠加等于「上限 × worker 数」，「全局上限」这个语义直接废掉。`UpstreamGuard` 因此多了一个 `acquire_breaker()`（只走断路器不占槽）
6. **会话锁的 TTL 要大于一轮最长耗时**（默认 600s）：worker 崩了靠 TTL 自动过期，不永久卡死一场面试；释放锁要用 **Lua 比对 token 再删**（A 的锁过期后 B 拿到锁，A 来释放不能把 B 的删了）
7. **锁要在路由层拿、在流的 finally 里放**：拿不到锁要能在**开流之前**返回 409（流开始后就只能发 SSE error 事件了）；开场是「先锁后写库」，否则会留下「有行没流程」的孤儿场次
8. **题库路径与业务库路径不是一回事**：`settings.db_path` 永远指向 SQLite（题库）；业务库走 `settings.business_db`。`bank_query` / `question_search` / `coding` / `mcp_server` **不许切 PG**
9. **weasyprint 是带原生依赖的**：模块级 `import weasyprint` 会让**整个 API 起不来**（缺 pango/cairo 时直接 OSError）。改成惰性导入——PDF 只是一个可选出口，不该拖垮面试主链路
10. **Windows 上 uvicorn 绕开 event loop policy**：uvicorn 的 `asyncio_loop_factory` 在 Windows 上直接 `return asyncio.ProactorEventLoop`（不读 policy），而 psycopg 的**异步**模式（官方 PG checkpointer）不支持它 → 报 “Psycopg cannot use the 'ProactorEventLoop' to run in async mode”。**切 policy 对 uvicorn 无效**，必须 `--loop app.graph.checkpointer:uvicorn_loop_factory`（compose 里已带上）。同理，脚本里要用 `asyncio.Runner(loop_factory=compatible_loop_factory())`——`asyncio.run()` **不接受 factory**
11. **连接池不像裸连接那样回退 IPv4/IPv6**：Windows 上 `localhost` 优先解析成 `::1`，而 PG 端口只听 IPv4；`psycopg.connect` 会回退，**`psycopg_pool` 不会** → 只试一次 `::1` 就报 `PoolTimeout`（看着像「PG 连不上」）。所有建池处先过 `app/dsn.py::normalize_dsn`（只动 host）
12. **PG 的 BIGSERIAL 不会因为显式插 id 而前进**：迁移脚本显式插原 id 保持一致 → 序列必须 `setval(pg_get_serial_sequence('answers','id'), max(id), true)`，否则迁移后新场次 finalize 第一次插 answers 就撞主键
13. **embedding 镜像删了 onnx/，但新版 huggingface_hub 会在 `HF_HUB_OFFLINE=1` 时把它当“快照不完整”拒绝启动**：`backend/embedding_service/app.py` 启动前只跳过 completeness 检查（服务只用 PyTorch 权重）；否则离线启动会报 `IncompleteSnapshotError`，联网又会卡在 hf-mirror
14. **宿主机 pg_dump 版本高于 PG server 时，备份在容器里 restore 不了**：PG18 的 `pg_dump` 生成 archive 1.16，PG16 的 `pg_restore` 报 `unsupported version (1.16)`；本机 compose 用 `PG_DUMP_MODE=docker`（容器内 pg_dump），恢复也用容器内 pg_restore
15. **TTS 票据必须跨 worker 共享**：`POST /api/tts/ticket` 签发票据、`GET /api/tts/stream/{ticket}` 读取；票据放模块级 dict 时，2 worker 下 A 签发、B 收到 GET 就 404（实测同一票据 404 → 200，重试落到 A 才成功）。票据走 `Coordinator`（Redis 模式共享、memory 模式进程内），60s 过期；不要退回进程内 dict

## P5-J4 缓存坑位（写代码前必读）

1. **缓存 fail open，锁 fail closed**：缓存 Redis 异常一律回源 DB（`X-Cache: BYPASS`）；会话锁仍 503。别把两者统一处理。
2. **缓存与锁共用 Redis，不设 LRU**：`allkeys-lru` / `volatile-lru` 可能淘汰会话锁；J4 保持 noeviction，内存压力留 J6 拆独立缓存实例。
3. **业务服务不把 Redis 放进 readiness / compose 启动依赖**：缓存挂时 business 仍要能启动并回源；`management.health.redis.enabled=false`。
4. **写后删要在提交之后**：Java 用 `afterCommit`，Python 在 finalize / retry / delete 后删；TTL 是最终边界。
5. **`X-Cache` 只是证据 / 调试**：`HIT | MISS | BYPASS`，不是前端契约；日志不打印完整 user_id。
6. **J4 不做 Sentinel**：留 J6；Sentinel 切换可能丢锁（异步复制），届时单独评估。

## P5-J5 消息队列坑位（写代码前必读）

1. **outbox 必须和 finalize 同事务**：写在事务外，崩溃就丢事件；`agent_outbox` 用场次确定性 `uuid5` + 唯一键，`retry_persist` / 重复 finalize 不会造第二条。
2. **Kafka 不进请求路径**：api 只写本地 outbox；relay 独立进程。Kafka 挂时 `/healthz` 仍是 200，outbox 积压，broker 恢复后补发。
3. **relay 调 `get_store` 必须把 min/max/timeout 三个参数给全**：漏一个就会去实例化 `Settings`，而 relay 镜像没有（也不该有）LLM 密钥 → 启动即 `ValidationError`。
4. **消费幂等要和业务写入同事务**：`processed_event` 先插再建计划，业务异常一起回滚；分开写会在崩溃窗口重复。
5. **毒消息不重试**：解析 / 校验抛 `NonRetryableEventException`，`@RetryableTopic(exclude=...)` 直送 DLT；否则在 retry topic 空转 5 次。
6. **行为面的 weaknesses 是维度名（带下划线）**，不符合事件 schema 的 `^[a-z][a-z0-9-]*$`：行为面发空 `weak_domains` 让消费者记审计；整场未评分**不发**事件（缺数据 ≠ 0 分）。
7. **Spring 的 retry topic 是每档一个**：`<event>.retry-1000/2000/4000/8000`；`SINGLE_TOPIC` 只在同间隔时合并。别在 `NewTopic` 里再预建一个 `.retry` 假 topic。
8. **web 角色不能起消费者**：`business.worker.enabled` 条件装配；否则 web 实例也进消费组，故障演练与容量口径失真。
9. **notification 行由生产方写、worker 只投递**：Kafka 挂时行仍在（pending）；worker 标 sent；失败在 `REQUIRES_NEW` 事务里记 failed，别被 deliver 的事务回滚掉。
10. **PG 的 JSONB `::text` 带空格**：outbox payload 断言要解析 JSON，不能做子串匹配。
11. **SQLite 不支持 `SKIP LOCKED`**：relay 只跑 PG；SQLite 形态只写 outbox 表，不跑 relay。
12. **dev RF=1 是持久性边界**：outbox 保证「DB 提交的事件不丢」，已发布消息在单 broker 上不承诺磁盘冗余；HA 到 J6。

## P5-J6 高可用坑位（写代码前必读）

1. **单文件 bind mount 会读到半截**：nginx 配置按目录挂（`./docker/nginx-ha:/etc/nginx/conf.d`）；单文件挂载时编辑器 truncate+write，容器可能读到截断版本（`nginx -t` 报 unexpected end of file）。
2. **nginx 被动摘流必须设短 `proxy_connect_timeout`**：默认 60s，死实例黑洞时客户端先超时，`proxy_next_upstream` 根本来不及；HA 配 1s。
3. **gateway 到 business 的多实例要自己接**：`lb://business` + SimpleDiscoveryClient 静态列表 + `HealthCheckServiceInstanceListSupplier`（2s）；不引注册中心；`BUSINESS_URL` 单 URL 只适合单实例。
4. **会话锁 TTL + 心跳缺一不可**：TTL 管崩溃释放（30s），心跳（10s）管长轮次；没有心跳时一轮比 TTL 长会被第二个请求抢锁；409 带 `Retry-After`。
5. **Sentinel 下的锁要 `WAIT 2`**：异步复制切换可能提升没收到锁的副本；取锁后等两个从库确认，不足 fail closed；缓存/限流仍 fail open。
6. **被 Sentinel 提升的副本重启会按容器 `--replicaof` 回退成从库**：运行期角色不写进 compose 命令行；演练中不要重启被提升的 master，生产用托管 Redis 或持久化配置。
7. **Python Sentinel 客户端首请求可能打到旧主/只读副本**：`ReadOnlyError` 会断开连接并在下一命令重连；首请求可能 503，重试恢复；不要把 fail open 的缓存错误当成锁问题。
8. **Kafka 3 broker 必须同一个 `CLUSTER_ID`**：不设则每个容器各自生成，组不成集群；topic 预建 6 分区 RF3，`min.insync.replicas=2`；消费者显式 `CooperativeStickyAssignor`。
9. **`docker kill` 是手动停止**：`restart: unless-stopped` 不会把它拉起来。演练后恢复实例用 **`docker compose up -d --no-deps <svc>`**，不要用 `docker compose start`——`start` 会按 `depends_on` 把已停的旧 `postgres-primary` 拉起来，变成双主。杀 worker 后到 rebalance 完成有 session timeout（约 50s），期间分区仍在旧成员名下。
10. **PG 副本初始化别依赖 `.sh` 执行位**：挂载脚本常是 644；`postgres` entrypoint 会按 `-x` 判断，非执行位可能直接 exec 失败。用 `.sql` 建角色 + 挂自定义 `pg_hba.conf`；副本用 `.pgpass` 保证重启后回连。
11. **PG 提升后应用要重指**：`PG_HOST=postgres-replica` 在 compose 插值；`--no-deps` 绕过已停止的 `postgres-primary` 依赖；旧主库保持停止，重启会变第二主库。
12. **FakeLLM 的 SSE 事件名在 `event:` 行**：不是 data JSON 里的字段；杀 Agent 要等 `delta_chunk`（逐片），不是聚合 `delta`（节点完成后才发）；否则 kill 永远晚一步。
13. **HA 数据必须独立**：`docker/volumes/ha/`（gitignore）；seed 用 dev 副本管道；证据只留计数/ID，报告/题库原文不进仓库。
14. **不要宣称 exactly-once / 多可用区**：至少一次 + 幂等；单机多实例；PG 手动提升 + 异步复制 RPO 不承诺 0。

## P5-J7 压测与调优坑位（写代码前必读）

1. **Redis 全局槽是 `LLM_SLOTS_FLASH` / `LLM_SLOTS_PRO`**：`RedisCoordinator` 用它们；`LLM_MAX_CONCURRENCY_FLASH` 只是本地闸门（Redis 模式只走断路器）。改错 env 会让槽位实验全部无效（16/32/64 实际都跑 16）。
2. **gateway 429 先看 body**：`网关繁忙` = bulkhead 拒绝；`请求过于频繁` = token bucket。bulkhead 默认 20 在单实例 500 RPS + 延迟尖刺下会误伤；HA 用 100（J7-T2，after 网关/写 3/3 零 429）。
3. **FakeLLM 的首 token 需要 `LLM_FAKE_CALL_DELAY_S`**：`LLM_FAKE_DELAY_S` 只作用于流式片间，首片立即返回；非流式 judge/report 也没有延迟。默认 0，J7 用 0.8s / 0.03s。
4. **同步首波会打满舱壁**：`constant-vus` 140 同时起会产生 429；写场景用 30s 爬坡，不能把测试同步性当成容量信号。
5. **k6 口径**：`http_req_failed` 含 warmup，summary export 的 `passes/fails` 语义反直觉；SLO 用自定义 `measure_requests` / `measure_errors`，分位用自定义 Trend + `--summary-trend-stats`（默认没有 p(99)）。
6. **Redis EVALSHA 慢日志 10–35ms + 同机调度抖动是真实存在的**：缓存超时按 fail open 回源；不要把压测中的 429/延迟尖刺直接归因于业务代码。
7. **Agent 64 槽下「10 题 42 秒」会撞 per-user 30 次/分钟限流**：槽位不是唯一瓶颈；50 并发 SLO 的可用最小槽位是 **32**，用户限流是下一层。
8. **HA 全量内存 7.57 GiB / 目标 8GB**：25 个容器（不含 k6 生成器）；再加副本 / 更大堆前先复测。
9. **notification 表没有 `sent_at`**：MQ 通知跳不能从 DB 精确重算；J7 只重测事件→计划（p95 0.95s），通知跳沿用 J5 的 4.92s；要独立测就先加 `sent_at`（schema 变更）。

## P5-J8 可观测与运维坑位（写代码前必读）

1. **compose 每个服务名一个独立镜像**：只 `build gateway-1` 时 `gateway-2` 仍是旧镜像（表现为 401/403/404 假象）。构建/重建要覆盖全部实例，或用 `docker tag` 把同一镜像复用到其余实例。
2. **构建上下文在 `docker compose build` 启动时快照**：与编辑并行时，后写入的配置不会进镜像（本次 20:06 的构建缺 20:09 的 application-ha.yml）。改完代码再构建，构建完核对容器 jar 内的配置。
3. **Spring Boot BOM 不管理 `logstash-logback-encoder`**：必须显式版本（7.4），否则 Maven 直接报 `version is missing`。
4. **FastAPI 0.141 / Starlette 1.6 下 `FastAPIInstrumentor` 静默不产生 HTTP span**（Jaeger 只有 psycopg span）；改用通用 `OpenTelemetryMiddleware`（`opentelemetry-instrumentation-asgi`），ASGI 协议稳定、不依赖框架内部 hook。
5. **Starlette 的 `add_middleware` 是 `insert(0)`，最后添加的才在最外层**：OTel middleware 必须在所有自定义中间件之后添加，span 才覆盖它们——否则日志里的 trace_id 只能靠随机 id。`build_middleware_stack` 用 `reversed()` 包装，方向别记反。
6. **gateway（WebFlux）的 WebFilter 里拿不到 span**：`Tracer.currentSpan()` / MDC / Reactor context 的 `Span.class` 都为空——server span 在 handler 层创建，访问日志的 WebFilter 在其外层。网关访问日志用 `request_id` 串联（与 business/api 一致），trace 看 Jaeger；不要为此再手写 traceparent（会与 OTel 生成两条 trace）。
7. **手写 `traceparent` 必须删干净**：gateway 的 `UserHeaderFilter` / `AccessLogFilter` 同时手写 traceparent 与 OTel 注入会让网关和下游落在两条不同 trace 上；B 阶段以 OTel 为准。
8. **Docker Desktop 的 cAdvisor 只有 cgroup `id` 标签，没有容器名 `name`**：看板用 `id=~"/docker/.+"` 汇总；不要写 `name=~"marda-ha-.*"`（永远空）。
9. **worker 的 web 端口是 8082**（business 是 8081）；Prometheus 的 business job 要分开列 `worker-1:8082`，否则 connection refused。
10. **Prometheus recording rule 在无 5xx 时是空而不是 0**：`sum(rate(5xx))` 没有序列，整个可用性表达式变空、看板空白；用 `(sum(rate(5xx)) or (sum(rate(all)) * 0))` 补 0。
11. **postgres_exporter 的自定义指标名 = query key + `_` + metric 名**：`marda_outbox` + `pending` = `marda_outbox_pending`；写错一边告警就是空的。
12. **HA 的 profile 文件不能有重复顶层键**：append 第二个 `spring:` 会让 SnakeYAML 抛 `DuplicateKeyException`，应用启动即失败；Kafka observation 与 Redis Sentinel 必须合并在同一个 `spring:` 块。
13. **观测栈配置按目录挂载**：单文件 bind mount 在编辑时可能被读到截断版本（Prometheus 报 YAML `unexpected end of stream`），与 J6 的 nginx 坑位同源。
14. **JSON 日志只在 `ha` profile 启用**：`logback-spring.xml` 无条件上 `LogstashEncoder` 会让 CI 的 Kafka 集成测试明显变慢（客户端 INFO 日志量 × 编码开销），Consumer 线程启动被拖过 20s 的 await 超时（实测 job 从 1 分钟涨到 5 分钟并失败）；测试/默认用 Spring Boot 默认 console，测试资源再把 Kafka 客户端日志降到 WARN。
15. **Micrometer 默认不导出 histogram bucket**：p95 依赖 `http_server_requests_seconds_bucket`，必须配 `management.metrics.distribution.percentiles-histogram."[http.server.requests]": true`；否则 p95 记录规则 / HighLatencyP95 / 看板永久为空（`promtool` 测试仍绿——它手工喂了 bucket，掩盖了生产缺失）。
16. **RedisDown 要监控 `redis_up` 而不是 `up{job="redis"}`**：`up` 只是 exporter 的抓取健康；exporter 活着但连不上 Redis 时仍为 1。
17. **RED / SLO 必须排除 `/actuator`**：空闲 HA 下探针占请求数 99%+，真实 5xx 被稀释到 1% 阈值以下，99.9% 可用性实际在统计探针。选择器加 `uri!~"/actuator/.*"`。
18. **Langfuse 必须用独立 TracerProvider**：复用全局 OTLP provider 时，它会把 prompt / completion（含图片 data URL）与 user.id / session.id 一并导出到 Jaeger——违反「user_id / 题库原文不进 trace」的口径。构造 `Langfuse(..., tracer_provider=TracerProvider())`。
19. **worker 也要 `SPRING_PROFILES_ACTIVE=ha`**：它是唯一真正跑 Kafka 的进程；没有 profile 时 JSON 日志 / OTel / Kafka observation 全不生效（表现为 worker 日志是文本、Jaeger 没有消费段）。
20. **tail sampling 的 `decision_wait` 要长于最长轮次**：5s 会在根 span / `interview.turn` 结束前裁决，长 trace 被拆分或丢弃；本项目用 **90s**（真实 DeepSeek + SSE 可到 60–120s）。
21. **`StructuredArguments` 只被 `LogstashEncoder` 渲染**：非 ha profile 的默认 console 只打印 `%m`，访问日志会只剩一个词 `access`。访问日志按 profile 分支（ha 用结构化参数，默认用 `{}` 占位符）。
22. **gateway 的访问日志 / request_id 要放在 Security 之前**（`Ordered.HIGHEST_PRECEDENCE`；`WebFilterChainProxy` 是 -100）：放 `LOWEST_PRECEDENCE` 时 401/403 请求完全不可见，`X-Request-Id` 也不会回写。

## 开发纪律

- **验证命令跑通才算完成**，不口头声称成功；每阶段的验证标准见 [PRD §7](docs/PRD.md) 与 [SPEC §10](docs/spec/10-testing.md)
- TDD：追问决策、评分聚合、语料解析等确定性逻辑先写测试
- **单一来源**：凡是"两处算同一个东西"，抽成一份实现并用测试钉死——总分口径、检索 doc 文本、评测与生产共用的评分 prompt、决策与原因同源的 `explain_decision`。同一批数字两个来源，迟早不一致
- **简历缓存先查后清**：同一用户同一份内容按 sha256 复用解析结果；缓存查询必须排在 `prune_unreferenced_resumes` 之前，否则刚存的行会被当孤儿清掉。输入框草稿只存浏览器 localStorage，不落服务端
- **机械判据优先**：能用断言证明的不靠肉眼——图内标识串证明"模型读到了图"、转写重合率证明"语音进了引擎"、CDP Network 域零请求证明"帧没上传"、像素采样证明暗色主题
- **诚实的边界**：没覆盖的写"未覆盖"，降级如实标注而不是静默掉质量；"缺数据"与"0 分"必须分得开
- 概率性断言与确定性断言长得一样：改了会影响某条断言前提的行为，回头看它是否还成立——**绿一次说明不了问题**
