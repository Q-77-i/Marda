> 本文是 SPEC 的 §7 正文；全局索引见 [SPEC.md](SPEC.md)。章节号冻结，不因拆分改变。

## 7. API 契约

**鉴权**：除注册 / 登录与 ASR 的 query token 外，所有端点需登录，请求头 `Authorization: Bearer <token>`；未带/失效/过期统一 401。跨用户访问他人场次按「不存在」返回 **404**（不泄露存在性）。题库是公共资产、无归属隔离，`/api/profile` 是**用户级、无路径参数**的端点（隔离由 user_id 过滤承担），故这两处只有 401 没有 404。

| 方法/路径 | 请求 | 响应 |
| --- | --- | --- |
| POST /api/auth/register | `{username, password}`（username 3–32 位 `[A-Za-z0-9_]`，**统一小写存储**；password 6–72） | **201** `{token, username}`；重名（含大小写变体）409 |
| POST /api/auth/login | 同上 | `{token, username}`；账号不存在与密码错误同为 **401**（文案一致，不泄露账号是否注册） |
| GET /api/auth/me | — | `{id, username}`（前端刷新后校验 token 用） |
| POST /api/interviews | `{position, question_count, difficulty, interview_type, resume_id?}`（**2–20，默认 10**；difficulty ∈ `adaptive`/`L1`/`L2`/`L3`，默认 `adaptive`，非法值 422；**interview_type ∈ `tech`/`behavioral`，默认 `tech`，行为面 question_count > 10 → 422**；**resume_id 可选**——先经 `POST /api/resumes` 解析，非本人/不存在 404） | SSE 流（首事件 meta 携带 interview_id；thread_id = interview_id）；创建后立即执行开场 |
| POST /api/interviews/{id}/messages | `{content, images?, speech?}`（images = 已上传的 image_id 列表，**≤3**；文字仍必填——图是回答的补充证据，不单独成答；**speech 可选** = M16 语音原始计数，`SpeechStats` 六个非负整数，`pause_ms ≤ duration_ms`、`pause_count×2000 ≤ pause_ms`，非法 422） | SSE 流（见事件表）；带图时评分/追问/生成题/收尾反问的 LLM 调用会收到图附件 |
| POST /api/interviews/{id}/images | multipart `file`（PNG/JPEG/WebP，**按 magic bytes 判定**不信任 Content-Type；≤8MB；每场 ≤30 张） | **201** `{image_id}`（服务端 uuid4 hex）；不存在/他人 404、已结束 409、非图片 400、超限 413 |
| GET /api/interviews/{id}/images/{image_id} | — | 图片字节（`nosniff` + `private` 缓存头）；他人/不存在/坏 id 一律 404；**已结束场次仍可取**（回放要显示图） |
| GET /api/interviews/{id} | 可选 `?reconnect=true` | 会话状态：phase / answered_count / question_count / 历史消息（供刷新恢复 UI）+ `stalled` + `degraded_reasons`（本场已发生的降级原因——SSE 同因只发一次，刷新/中途进入靠它补横幅）；带 reconnect 时在 `chat_history` 末尾**附加**一句重连问候 + 当前题干（只随本次响应返回、不落库，§4.8） |
| GET /api/interviews/{id}/report | — | 报告 JSON（未结束 404） |
| GET /api/interviews/{id}/report.pdf | — | 报告 PDF：`application/pdf` + `attachment` 下载头（中文名走 RFC 5987 `filename*`，另给 ASCII 兜底名）；**未结束/不存在/越权同 404**（与报告端点同一判据）；每次现渲染不落盘缓存 |
| GET /api/interviews/{id}/recommendations | — | 学习推荐：`{interview_id, position, groups: [{domain, advice, status, cards}]}`，`status ∈ ok`/`exhausted`/`empty`（§4.10）；卡片含题干/答案/关键点/难度/厂商/面次 + `sources`（主源首位）。**与报告端点同一 404 判据**，检索失败 500 透传；**行为面报告直接返回空 `groups`**（不做无效检索） |
| GET /api/interviews/{id}/trace | — | `{interview_id, position, dims, status, answered_count, question_count, events, nodes}`（**未结束场次同样可查**；事件模型见 §4.7）。`dims` = 评分维度表 `[{key,label}]`（judge 事件的 detail 是评分模型裸 dump，键随会话类型变，标签由后端给；老场次无该字段 → 前端退回技术面五维常量）。`nodes` = 节点时间线 `[{seq, node, node_label, round, duration_ms, writes}]` |
| GET /api/interviews | 可选 `status`（running/finished）、`type`（tech/behavioral；tech 含老场次 NULL）、`since`（YYYY-MM-DD，UTC 当日零点）、`page`（≥1）、`page_size`（1–50，默认 10） | 分页 + 过滤的历史列表（倒序）：`{items, total, page, page_size, counts:{finished, running}}`；**total 与 counts 基于过滤后全集**，不受分页影响；非法参数 422 |
| GET /api/profile | — | 能力档案：`{sessions, summary, weakness_changes, excluded}`（§4.11）。**无路径参数**；**没有场次时返回零态结构（不是 404）**；**行为面场次不计入**（按报告 payload 的 `interview_type` 过滤），只进 `excluded` 计数 |
| DELETE /api/interviews/{id} | — | **204**：物理删除（业务库三表 + checkpointer 线程，不可恢复；进行中的场次也允许）；不存在 404 |
| POST /api/interviews/{id}/retry-persist | — | 修复「图 finished、业务库未写」的半成品场次：只重跑 finalize（幂等），不重跑图；404（不存在/越权）、409（未结束/场次忙）、200 `{repaired}`；详见 [P4-生产化 §8](../ops/P4-生产化.md) |
| GET /api/bank/questions | `q`（关键词）/ `domain` / `difficulty` / `company` / `round` / `page` / `page_size`（1–50，默认 10） | `{mode, total, page, page_size, items}`——`q` 非空走混合检索（`mode=search`，`total=null`：相关性排序不翻页，单页 `SEARCH_LIMIT=20`），否则走 SQL 浏览（`mode=browse`，有 `total` 可翻页）；每项含题干/答案/关键点/追问/域/难度/厂商/面次 + `sources`（**主源排首位**） |
| GET /api/bank/facets | — | `{domain, difficulty, company, round}` → `[{value, count}]`（仅 enabled；计数降序、同数按值升序，**难度例外：按档位 L1→L3**——有序维度按计数排会把 L2 顶到 L1 前面，而筛选项要的是档位序）。前端筛选项由此生成，不硬编候选值 |
| GET /api/bank/capacity | `counts`（逗号分隔，默认 `5,10,15`；越界夹紧 2–20、去重升序） | `{options: [{difficulty, base, question_count, ok, shortfalls}]}`；`shortfalls = [{domain, required, available}]`（基准档见 §4.3） |
| POST /api/bank/private/upload | multipart `file`（.md/.txt/.pdf，≤2MB）+ 表单 `domain`（必须 ∈ `ENABLED_DOMAINS`——行为面与项目深挖域 **400**）+ `difficulty`（默认 L2） | **部分成功语义**：`{parsed, imported, duplicated[], errors[]}`——好题照常入库、坏题逐条给原因，整份文件不因一条坏题回滚；**重复题不覆盖**（答案可能已被他手改过），后缀/编码错 400、超限 413 |
| GET /api/bank/private/questions | `status`（enabled/draft）/ `domain` / `difficulty` / `page` / `page_size` | 仅本人私有题分页（`user_id = 本人`，默认含已归档），items 附来源明细 |
| PATCH /api/bank/private/questions/{id} | `{question?, answer?, key_points?, follow_ups?, topic?, domain?, difficulty?, status?}`（至少一项） | 编辑 / 归档恢复（归档 = `status: draft`）；**非本人 404**；`domain` 非法 422 且**域不变** |
| POST /api/resumes | multipart：`file`（.pdf/.md/.txt，≤2MB）**或** `text`（粘贴文本，≤5 万字符）二选一 | **201** `{resume_id, filename, projects[], skills[], chars}`——**只回结构化结果、不回原文**。二选一不满足 400、后缀/编码/空内容 400（文案指路粘贴框）、超限 413、模型不可用 502 |
| GET /api/coding/questions/random | — | 随机一道公共算法题：只回题干 / 主题 / 难度与题面（`description / examples / constraints`），**answer / key_points / follow_ups 不出 API**；没有可用题 404 |
| POST /api/coding/reviews | `{question_id, language, code}`（language ∈ python/java/cpp/javascript；code ≤8000） | `{question_id, language, review}`（问题 / 具体反例 / 复杂度 / 一个追问）；空代码 400、题不存在 404、LLM 不可用 503 |
| POST /api/coding/followups | `{question_id, language, code, review, message, history?}`（history ≤10 条） | `{answer}`；无状态，不落库、不进报告；空代码 400、题不存在 404、LLM 不可用 503 |
| POST /api/asr/correct | `{text, question?}`（text ≤4000，question ≤2000） | `{text}`：保守校对 ASR 转写；失败 502，前端保留原文、不打断面试 |
| POST /api/tts | `{text}`（1–4000 字，超长截断到 1000） | `audio/mpeg` 流（edge-tts 分片透传）；首块先取出来做错误映射 → 失败 502 + 中文文案；空白文本 422 |
| POST /api/tts/ticket | `{text}`（同上） | **201** `{ticket}`：签发 60s 播报票据；`<audio>` 不能带 Bearer，GET 时才真正合成；空白文本 422 |
| GET /api/tts/stream/{ticket} | — | `audio/mpeg` 流（`X-Accel-Buffering: no`、`Cache-Control: no-store`）；票据不存在 / 过期 404；合成失败 502 |
| WS /api/asr | 上行：二进制 = PCM 片段；文本 = `{"type":"stop"}` | 下行 JSON：`{type: partial\|final\|error, text, message?}`。**浏览器 WS 不能带请求头 → token 走 query**；失效一律 `close(4401)` |

**SSE 事件**（`sse-starlette` EventSourceResponse；POST 由前端 fetch 流解析）：

| event | data | 说明 |
| --- | --- | --- |
| meta | `{interview_id, phase, answered_count, question_count}` | 阶段/进度（创建流首事件携带 interview_id） |
| delta_start | `{}` | **一条面试官消息开始**：只作边界标记。**必须显式发**——同一节点内的分片全部先于该节点的 delta 到达，出题节点一次跑出两条消息（答错缓冲 + 题目）时，没有它就切不开谁是谁 |
| delta_chunk | `{text}` | **流式增量**：真 token 流的分片，追加到最近一条未结算的消息。1:1 透传不做碎块合并（实测中位 2 字/片、首字 0.47s） |
| delta | `{text}` | 面试官消息**终稿全文**（语义未变）：兼三职——前端用它对账（替换累积文本）、失败半截的清算依据、旧前端兼容 |
| question | `{index, question_id, domain, difficulty}` | 新题提示（只在新题时发一次：追问/评分重传同题不发；生成题无 question_id 不发） |
| done | `{interview_id, report_ready}` | 面试结束 |
| degraded | `{reason}` | **降级提示**：AI 上游不可用、引擎已切确定性兜底——**不是错误**，面试继续、输入不清空。同因去重只发一次；前端渲染警示横幅，不弹错误态 |
| error | `{code, message, retryable}` | 流内错误（**只有「重试能治好」的内容类失败 / 配置类 / 步数超限**——上游不可用类走 degraded）；**HTTP 仍是 200、场次仍有效**，图停在失败节点上——客户端「重试」= 重发同一文本，从该节点续跑（已入账的回答不重复计分）。**流式下多一条口径**：错误时未收敛到终稿的气泡按「从未落 checkpoint」丢弃，重试会重新流一遍 |
| ~~asr_partial / tts_chunk~~ | — | **未采用**（预留后决定不用）：ASR 需要双向（SSE 单向）、TTS 需要独立于面试流的生命周期 → 改走 `WS /api/asr` 与 `POST /api/tts`。名字留在表里做记录；前端分发层「未注册事件名静默丢弃」的机制不变 |

**`stalled`**：`true` = 图卡在失败节点上（`next` 指向该节点且无中断载荷），区别于正常停在 `pause` 中断点（`next == ("pause",)` 且 tasks 带 interrupts）；已结束（`next` 为空）恒为 `false`。它是**服务端给的**判据，不是让前端从「末条消息是不是 assistant」反推——报告节点失败恰恰也表现为「末条是 assistant」，猜错就是面试永久卡死。前端据此分两路（纯函数 `frontend/lib/recovery.ts`）：卡住或回答没入账 → 重发；**已跑完只是回复没传回来 → 只按服务端记录重建列表，绝不重发**（重发会被当成新一轮，同一份回答判两次）。

工程要求：`stream_mode=["updates", "custom"]`（节点内 `get_stream_writer()` 逐块外送走 custom 流——**状态机与图结构一条不动**，service 只把 custom 块映射成 SSE 事件名；节点在图外跑时 `get_stream_writer` 抛 `RuntimeError`，`graph/rules/stream.py` 统一吞掉退化为空操作）；`X-Accel-Buffering: no`；15s 心跳注释；async handler 全程 `astream` 不阻塞事件循环。

**流式不变量（测试钉死）**：发出的分片拼接 == 节点落 `chat_history` 的那条消息（节点侧只经 `stream.speak()/begin()` 出口，构造上不会漂）。**已知边界**：报告生成期间（`chat_json`，v4-pro）无输出——结构化不流式，等待体验由报告页承接。

### 7.1 语音通道（FR-24，引擎与模态解耦）

音频只是另一条**与面试引擎完全无关**的通道：录音 → 转写 → 文本照常走 `POST /interviews/{id}/messages`；面试官消息 → 播报。图 / 状态机 / 落库 / SSE 事件表**一条未动**。

- **ASR**：`WS /api/asr` 中继到火山豆包流式识别（**v3 二进制协议**：4 字节头 + gzip JSON / gzip PCM；实现 `tools/asr.py`，帧格式已在真服务端验证）。鉴权 `X-Api-Key` = **豆包语音控制台**签发的 key（`VOLCANO_SPEECH_API_KEY`）——**与方舟 Ark key 不通用**（探针实测三种失败形态：401 Invalid X-Api-Key / 403 requested resource not granted / plan 路径 acquire 失败），错误文案按状态码给「该去哪解决」。音频 PCM 16k/16bit/单声道、每 100ms 一片；`partial` 是**累计文本**（前端按替换落框，不叠加）。
- **TTS**：edge-tts（免费）；三档降级 = edge-tts → 浏览器 `speechSynthesis` → 纯文字。播报失败只提示一次，**不打断面试**；开麦即停播。**票据走协调层**（Redis 模式跨 worker 共享、memory 模式进程内，60s 过期）——P4 默认 2 worker，票据放模块级 dict 会「A 签发、B 收到 GET」直接 404。
- **系统音兜底（2026-11）**：降级时按「zh-CN 优先 → Enhanced / 自然音色 → 避开 Grandma / Grandpa 等搞怪音色」排序；面试间右栏「状态」可选具体音色（`marda.ttsVoice`，仅 edge-tts 失败时使用）。
- **前端**：`lib/voice.ts`（降采样/Int16/WS 地址/落框规则，纯逻辑 vitest）+ `lib/asr-client.ts` + `public/asr-worklet.js`（攒 2048 帧再送主线程）+ `lib/tts.ts`。**转写进输入框、发送前可编辑**（ASR 错字不能让评分官背锅）；开录时已有的草稿是底稿，转写整体替换其后那段。
- **红线**：音频**不落盘、不落库、不写日志**，内存转发即弃；转写文本与手打文字同等对待。
- **连接口径**：容器/生产走同源（nginx 加 `Upgrade`/`Connection` 头转发）；`next dev` 的 rewrites **不代理 WS upgrade**（已查实）→ 3000 端口一律直连后端 8000（`lib/voice.ts::asrUrl` 纯函数钉死）。

### 7.2 图片通道（FR-26）

候选人在面试中上传截图（代码截图先行），**出题与追问结合图内容**。与语音的「零改动通道」不同：图必须进 LLM 上下文，**节点内的消息构造有改动**；但图 / 状态机流转 / 落库结构 / SSE 事件表**仍一条不动**。

- **核心设计——图独立成一条消息附件，不改任何既有 prompt 模板**：带图时在消息列表尾部追加一条 user 消息（说明文字 + `image_url` data URI parts，`tools/images.attachment_message`）。无图调用的消息列表与接入前**逐字一致**（`judge_messages` 缺省路径有单测钉死）——「无图场次零回归」与「评分基线/评测门禁零漂移」由此**在构造上成立**。
- **哪些节点看图**：评分官 · 追问文案 · 自我介绍提炼 · 收尾反问——即**候选人消息的每一个 LLM 消费点**；**出题**只给「LLM 现场生成」路径带**上一题**的图，题库题不带（题面来自题库，带图是噪声）。每处取**该题最近 3 张**（token 可控；更早的图不再进上下文，不拒收）。
- **存储**：`data/uploads/{interview_id}/{image_id}.{ext}`（docker 走 `./data` 卷）；**state/checkpoint 只存 id**（图字节进 checkpoint 是序列化爆炸坑）；删除场次连带清理图片目录（best-effort）。`.gitignore` 含 `data/uploads/`。
- **视觉协议（探针实测）**：flash 走 OpenAI 式 `image_url` + base64 data URI；内容块**必须是 `{"type": ...}` 对象**（裸字符串 422）；与关 thinking / 流式 / `json_object` 全部兼容。计费按像素：900px 截图 ≈ 209 token、1568px ≈ 560 token；800px 会掉可读性 → **前端压缩目标 = 长边 ≤1568 的 JPEG**。
- **前端**：`lib/vision.ts`（校验/压缩尺寸纯逻辑）+ `lib/image-client.ts`（canvas 压缩、上传）；📎 选图 → 压缩 → 预览 → **发送时先传图拿 id 再发消息**（上传失败不发送、附件保留可重试）；重试复用同一批 id，**不重复落盘**。气泡与只读回放共用 `components/message-images.tsx`（鉴权 fetch → objectURL）。
- **风控与口径**：文件缺失/损坏 → 跳过该图 + warning、**退化为纯文字**（绝不因图丢文件拒答）；图是面试内容组成部分、**必须落盘**（与语音「音频不落盘」刻意不同，否则回放/复盘丢证据）；**报告页只显示逐题图片计数**，图本体在面试页回放看，PDF 不含图。
- **Langfuse**：drop-in 自动把 base64 图转成**媒体引用**（`@@@langfuseMedia:...@@@`），trace 零 base64、无需脱敏。含图调用成本 ≈ ¥0.0007/次。
- **评分官用图监控**：`scripts/eval_judge_vision.py`（手动跑，**推荐在容器内跑**——checkpoints 是 WAL，宿主机直读会 malformed）统计「judge 输出是否引用图内独有信息」的比率；**连续 3 场带图场次零引用 → 建议降级为「评分官不看图」**，由数据支撑、由人拍板。
- **白板结构点评（M14b）**：白板仍是图片通道（Excalidraw 导出 PNG），**不改评分 schema**。规则只加在**带图的附件说明**里（`NOTE_JUDGE_IMAGE_RULE`：若为架构图，指出缺失的 1-2 个关键组件/连线），`JUDGE_TEMPLATE` 逐字不动——纯文字评分 prompt 与评测门禁零漂移。报告官可能为「答非所问」丢掉缺失项：`build_per_question_comments` 从评分官 comment 抽含「架构图/白板 + 缺失语义」的原句，**确定性补成「白板点评：…」**（只对有图题；报告点评已含缺失语义则不重复）。验收是**手工探针**（评分门禁 golden 是纯文字样本，证明不了模型读了图）。

### 7.3 简历通道（FR-28）

**两段式（同图片通道）**：`POST /api/resumes` 先解析出 `resume_id`（创建页显示「已读到 N 段项目经历」——**解析错要当场看得见**），`POST /api/interviews` 带 `resume_id` 开跑。

- **文件与粘贴文本走同一端点、同一抽取管线**：`.pdf` 走 pypdf、`.md/.txt` 按 UTF-8/GBK 解码；粘贴文本是**扫描版 / 图片版简历的兜底**（抽不出文字时明确报错并指路，绝不静默退化成「没传简历」）。抽取走 flash + 关 thinking + `json_object`，schema = `summary / projects / skills`。
- **只回结构化结果、不回原文**：原文留在 `resumes.text` 供重新解析；响应无 `text` 字段（集成测试钉死）。
- **内容缓存（2026-11）**：`resumes.content_hash = sha256(归一后文本)`，按 `(user_id, content_hash)` 查；同一用户同一份内容直接复用上次解析结果，**不调 LLM、不新建行**。缓存查询必须排在 `prune_unreferenced_resumes` 之前（否则刚存的行会被当孤儿清掉）；不同内容 / 不同用户分别解析。启动时给旧行回填 hash。缓存跟随简历行生命周期（引用计数清理）：引用它的场次全部删除后缓存也消失，下次同份简历重新解析。
- **注入 = 预填 `candidate_profile`**（唯一候选人插槽，三处出题模板都在消费它）→ **出题侧零代码改动就变具体**；`resume_id` 一并进 state 与 `interviews` 行。
- **图内三处「有则追加、无则逐字一致」**（同款零回归模式；集成测试**逐字**钉死无简历路径）：`intro_node` 开场白追加「已看过简历」（只提一句、不复述）；`profile_node` 追加【已提交简历】块要求**合并**（简历为骨架、自我介绍补充与更新，冲突以当场所说为准）；`ask_node` 在口吻层与项目生成题上追加「**点名**其项目 / 技术点、几道项目题覆盖不同项目」。
- **归属与生命周期**：他人简历一律 404；简历是用户级资产、同一份可跑多场，**按引用计数清理**——删场次时无引用才销毁；解析了没建场次的孤儿在下一次解析时清掉（已知边界：多标签同时开着表单时，一边解析会清掉另一边尚未使用的简历）。
- **如实交代**（前端）：表单写明「简历会发送给模型解析；原文与解析结果会保留在服务端以便下次复用，只用于出题与评分；不解析也可以开始面试」。

### 7.4 MCP 通道：题库查询 server

把**题库查询**能力按 MCP 暴露给任意客户端（Claude Code / Claude Desktop），验证「RAG 与题库能力不只服务自家前端」。

```bash
claude mcp add marda-bank -- uv run --directory backend python -m app.mcp_server
```

- **协议版本锁定**：SDK `mcp>=2,<3`，协商出的规范修订 = **2026-07-28**（无状态核心：无 initialize 握手 / 无 session id）。**协议代码关在 `app/mcp_server/` 一个包里**（领域逻辑不重写：用的就是题库页那套 `bank_query` 与出题检索那套 `hybrid_search`）；源码扫描有单测钉死「其余 app 代码不得 import mcp」。
- **传输只做 stdio**：MCP 客户端在本机把它当子进程起（`python -m app.mcp_server`），题库不经过网络。**不做 HTTP 传输**——无鉴权的 HTTP 会把题库暴露给任何能连上的进程。
- **三个只读工具**：`search_questions`（混合检索，短查询保留 rerank）· `browse_questions`（SQL 浏览/分页）· `get_question`（单题 + 来源四要素）。出参是 Pydantic 模型（`output_schema` 随工具声明）。
- **只公共题**：一律走带 `user_id IS NULL` 的查询；私有题与归档题连 `get_question` 也不可探测（不区分「不存在 / 别人的私有题」，同 §7 的 404 口径）。
- **错误如实回报（探针实测的 SDK 行为）**：工具内未捕获的异常会被 SDK 换成「Error executing tool X」——原始信息只在服务端日志里、**模型看不到原因**。故工具边界统一转 `ToolError("人话原因 + 出路")`。
- **验证**：集成测试用 in-memory 客户端（真协议、无进程）；`smoke_mcp.py` 起 **stdio 子进程**对真库只读跑三工具。

### 7.5 代码工作台（M15，独立于状态机）

- **入口与定位**：面试间右栏「代码」Tab（窄屏顶栏按钮）；题库随机算法题 + Monaco + 结构化点评 + 追问。**不读写 interview、不进 checkpointer、不落库、不进报告**；代码不发给面试官。
- **题源**：`domain='algorithms'`、`status='enabled'`、公共题（22 道）。随机端点只 select 题干/主题/难度，**answer/key_points/follow_ups 永不出 API**；只有点评内部才取参考答案（`bank_query.get_coding_question_for_review`）。
- **题面补全（M15 补强）**：题库里很多算法题只有标题或答案，直接当机试题无法作答（实测「最小合法子串」只有五个字，且其答案实际是「最长有效括号」）。`agents/coding_questions.py` 提供**自建题面目录**（题库 id + 我们撰写的标题/描述/示例/约束；git 只存 id 与我们自己的文本），随机题优先从目录抽，目录外的题回退题库原文；`GET /questions/random` 响应带 `description / examples / constraints`，点评 prompt 也用同一份题面。
- **三个端点**：`GET /api/coding/questions/random`（没有算法题 → 404 中文）· `POST /api/coding/reviews`（`{question_id, language, code}`；语言 ∈ python/java/cpp/javascript，code ≤8000）· `POST /api/coding/followups`（带上次点评与最近 10 条历史，无状态）。LLM 失败 → 503 中文可重试。
- **点评**：flash + `chat_json(CodeReview)`，`max_tokens=3000`；`issues[]`（severity/line/reason/fix，可为空）、`edge_cases[]`（**至少一条具体输入/期望输出**）、`complexity`（时间/空间/是否正确）、`follow_up`（只问一个）。追问走 `chat`，`max_tokens=1200`。
- **前端**：Monaco 经 `next/dynamic(ssr:false)` 懒加载，走同源 `/monaco/vs`（`scripts/copy-monaco-assets.mjs` 从 node_modules 复制，gitignore、ESLint 忽略）；工作台复用白板的全屏覆盖层形态，首次打开后保持挂载；代码与点评只在内存，v1 不持久化。
- **成本归因**：`purpose=coding_review` / `coding_followup`（成本报告按环节分组；不在面试 turn_span 内，按场次读回不含这两类调用）。

### 7.6 表达复盘（M16，只算不评）

- **指标来源**：录音期间的 20Hz 原始 RMS + ASR final 转写；前端 `lib/expression.ts` 只产**原始计数**（`duration_ms / chars / pause_count / pause_ms / filler_count / segments`），随消息 `speech` 键上传；**语速（chars/min）与场次汇总只在后端 `graph/rules/speech.py` 派生**。
- **口径**：字数 = Unicode 字母/数字（不含空格/标点）；长停顿 = RMS 低于本段 p10×2.5（夹在 0.008–0.04）且持续 ≥2s 的**内部**静音段（去掉开头/结尾）；口头禅 = 转写中 `嗯/呃/额/那个/就是/然后` 的出现次数（ASR 可能漏掉）；每题时长 = 该题多轮语音消息时长之和，文字作答不计入。**时长在 stop 时冻结**（final 可能再等 8s），计数字段一律整数（后端 `SpeechStats` 是 int）。
- **报告**：`QuestionRecord.speech` 跨追问轮累加（judge 在 LLM 调用前合并，评分降级也保留）；报告 payload 逐题 `speech`（含 `chars_per_min`）+ 顶层 `expression`；报告页「表达复盘」卡独立于 `hasScores`，逐题复盘卡加一行语音指标；PDF 同一节 + 逐题一行。**不做面部、不进五维分数**。
