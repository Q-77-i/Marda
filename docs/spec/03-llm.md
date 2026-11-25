> 本文是 SPEC 的 §3 正文；全局索引见 [SPEC.md](SPEC.md)。章节号冻结，不因拆分改变。

## 3. LLM 集成（llm.py）

- 统一封装 `openai` SDK：`base_url="https://api.deepseek.com"`，`api_key` 从 .env 读。模型：`deepseek-flash`（主力，支持视觉）/ `deepseek-v4-pro`（报告等难题）。
- **所有调用关 thinking**（`extra_body={"thinking": {"type": "disabled"}}`），规避坑位清单 1/2；开思考模式是独立决策（结构化节点与流式展示都不兼容）。
- **两个函数**：
  - `chat(messages, *, max_tokens, temperature, model, on_delta, purpose) -> str`：文案类（开场 / 出题 / 追问 / 结束语）。**内部走流式**——`stream=True` + `stream_options={"include_usage": True}`（usage 随末帧单发，是 Langfuse 成本归因的硬前提）；逐块回调 `on_delta`（默认 None = 不回调，evals 与脚本不受影响）。
  - `chat_json(messages, *, schema: type[BaseModel], max_tokens=2048, temperature=0.3, model=None, purpose=None) -> BaseModel`：结构化类（评分 / 提炼 / 报告 / 简历 / 难度标注）。**非流式**——`json_object` + schema 注入 prompt + Pydantic 校验 + 失败重请求 1 次；结构化输出没有增量语义。
  - 两者共有的重试边界：空输出重请求 1 次**只在一个字都没吐时**——已吐字的重发会把同一段说两遍。
- 重试：429/5xx tenacity 指数退避。**流式的重试只覆盖建连段**：一旦开始吐字，重试就是重复输出，中途断流直接抛（用户侧「重试」= 重跑失败节点，§7）。
- `purpose` 参数 → Langfuse 的 `name`（**只在启用时传**：原生 openai SDK 会把 `name` 当未知参数塞进请求体、DeepSeek 侧 400）。10 个调用点给稳定值（opening / ask / followup / judge / report / profile / closing / extract / coding_review / coding_followup）。
- 调用点全部走 `LLMError` 自定义异常 → API 层转 SSE error 事件。
- **单一实现纪律**：展示类文案只有 `chat` 一条路径——流式与非流式若各写一套，关 thinking / 空输出重请求 / 错误映射三处会各自漂移。

### 3.1 上游保护与降级链（P2-M9）

三件套：**并发闸门 + 断路器 + 降级链**（原语在 `reliability.py`，接线在 `llm.py`，降级语义在节点层）。

- **并发闸门**：按模型各一个计数信号量 + 有界等待（默认 30s），超时抛 `UpstreamBusy` → 可重试 `LLMError`。DeepSeek 的限流维度是**并发数**不是 QPS（账户级 flash 2500 / pro 500），故形态是信号量而非速率桶；默认上限（flash 16 / pro 4）是自设的保守值，env 可调。**持有期 = 整条上游调用的生命周期**——流式响应体是长连接，只包住建连段等于没限流（单测钉死：分片到达时仍占着槽）。
- **断路器**（每模型一个）：连续失败达阈值（默认 5）→ 开路（冷却 30s 内快速失败、不碰上游）→ 半开只放 **1 个**探针 → 成功闭合 / 失败重新开路。**只统计「上游不可用」类失败**（连接/超时/429/5xx，即 `retryable=True`）——内容类失败（JSON 校验、空输出、400/401）是上游活着时出的错，计入会误开熔断，把正常场次打成降级。
- **降级链**（判定与内容都在节点，`graph/rules/degrade.py` 是唯一标记出口）：v4-pro → flash（报告官）→ **确定性兜底**——

| 节点 | 断 LLM 时的确定性兜底 |
| --- | --- |
| 开场 / 收尾三节点 / 反问作答 | 固定文案（模板插槽与 LLM 版同源：岗位/题量/时长/人称），续在同一气泡里 |
| 出题（题库题） | 直接发原题面（题面本就不需要 LLM）+ 代码衔接语 |
| 出题（生成题） | **内置兜底题**（`agents/fallbacks.py`，自写通用题、不进题库、不涉语料红线；自带 key_points，模型恢复后照常可评分） |
| 评分 | **不评分**：`score=None`（不造分数——能力评估宁可缺、不可假）；追问决策走 `Reason.DEGRADED` 直接换题，难度自适应自然停摆 |
| 自我介绍提炼 | 跳过（只影响后续出题的个性化） |
| 简历解析（API 层） | **不降级**：失败 502 + 明确出路（重试 / 改用粘贴文本）——读不懂就不假装读懂 |
| 报告文字 | 三段式：v4-pro → flash → 无文字；**分数与逐题记录是纯代码聚合，照常产出** |

- **降级 ≠ 错误**：`retryable=True` 的失败走降级（面试继续、发 `degraded` 事件、进 `degraded_reasons`）；`retryable=False`（内容类/配置类）仍抛 `LLMError` → SSE error → 用户重试（`degrade.reraise_if_content` 是这条分界，**重试能治好的不许静默降级**）。
- **不产假信号**：整场未评分时报告 payload **不落 `scores`/`overall`**（缺数据 ≠ 0 分，同 §4.11「缺场不补零」口径）——报告页/PDF 收起分数区、能力档案按 `excluded.degraded` 排除并说明；部分未评分时分数照常展示，只标注未评分题数。
- **如实交代三处**：SSE `degraded` 事件（实时横幅）+ `GET /interviews/{id}` 的 `degraded_reasons`（刷新/中途进入也有横幅）+ 报告 payload 的 `degraded` / `degraded_reasons` / `unscored_count`。
- **已知边界**：P4 起 LLM 并发槽由 **Redis 全局共享**（memory 模式回退进程内闸门）；**断路器状态仍按进程**，跨 worker 不共享；已有 per-user 限流（Redis）。**降级只覆盖 LLM**——Qdrant/嵌入/rerank 故障仍会卡出题（如实记入「已知不覆盖」）；不做降级场次的事后补评分。
