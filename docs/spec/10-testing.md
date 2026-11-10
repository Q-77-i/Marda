> 本文是 SPEC 的 §10 正文；全局索引见 [SPEC.md](SPEC.md)。章节号冻结，不因拆分改变。

## 10. 测试与验收

**分层**：`tests/unit/`（纯逻辑 + FakeLLM 注入跑整图，无密钥无网络）→ `tests/integration/`（httpx ASGITransport + 临时库）→ 冒烟脚本（真链路，手动跑）→ `evals/`（离线评测，显式命令，**不进 pytest**，见 §4.13）。

| 层 | 断言要点 |
| --- | --- |
| 规则（TDD 主战场） | `test_follow_up_decision`：全部转移分支 + 各类上限 + 补救池；`test_difficulty`：升降档 / 清零 / 封顶保底；`test_quota`：largest remainder；`test_capacity`：配额 vs 直供差集、自适应基准 = L1；`test_curation`：改判表未命中条目不静默 |
| 图与状态 | `test_graph_flow`（FakeLLM）：阶段顺序、追问路径、结束门槛（<60% 拒绝）、**checkpoint 续面**、回放事件流逐轮齐全；state 序列化边界（`json.dumps(payload)` 钉死标量降级） |
| API | `test_api`（httpx）：SSE 事件序、报告、历史、回放（未结束可查 / 他人场次 404 / 未登录 401）、**重发不重复计分**（图停在失败节点，resume = 重跑该节点） |
| 可观测 | `test_observability`（注入 InMemorySpanExporter 离线跑）：一场一 trace（多轮 resume 同 trace_id）、generation 归父、无 key 零开销 |
| 数据与语料 | `test_parse_md` / `test_parse_open` / `test_ingest_sqlite`：老库迁移幂等、多源明细、全量同步删除、**私有题不被管道重跑删除** |
| 前端（vitest） | 只覆盖 lib 纯逻辑：SSE 分帧、打字机 FIFO、恢复两路决策、题库筛选分页与容量判据、档案整形、文件名与展示格式化、代码工作台请求体、表达指标（阈值/停顿/多段累计/整数校验）、草稿存储（键/空值/异常降级） |
| 多模态扩展（M13–M16） | 白板附件规则与报告确定性补回；`/api/coding/*`（安全投影 / 422 / 503 / 追问）；`speech` 整数校验与报告汇总；PDF 表达复盘节。浏览器探针：Monaco 零 CDN + 18 个同源 `/monaco/vs` 请求；合成麦克风 → `/messages` 载荷含 `speech`（实测 `pause_count=1`、整数计数） |
| 简历缓存 | 同用户同内容命中（不调 LLM）/ 不同内容与用户分别解析 / 旧库 hash 回填 / PG 同口径 |
| 评测口径 | 指标纯函数（退化输入返回 0 而不抛错）、golden 校验（同组题干必须一致、维度键与类型绑定）、**评测消息与生产节点逐字一致** |
| 验收清单 | PRD §7 八条（第 8 条 P95 在开发环境经 nginx 实测）；FR-21「按场次可查 trace」= smoke 从云端读回核对，不靠肉眼看控制台 |

**跑法**：`cd backend && uv run pytest -q`（本机 986 passed / 20 skipped，未配 PG/Redis 时跳过集成用例）· `cd frontend && pnpm test`（353 passed）+ `pnpm lint && pnpm build`。

真链路 smoke 与离线评测需要真 key / Qdrant，手动跑：

```bash
cd backend
uv run python scripts/smoke_api.py            # 一场真面试：报告 / PDF / 回放 / 推荐 / 档案
uv run python scripts/smoke_e2e.py            # 组合场：语音进 / 语音出 / 截图 / 文字
uv run python scripts/smoke_degraded.py       # 降级链：假上游一律 503，面试仍走完并标注
uv run python scripts/smoke_mcp.py            # MCP stdio 子进程 + 三工具
uv run python scripts/eval_retrieval_run.py   # 检索基线（需 Qdrant + 嵌入容器）
uv run python scripts/eval_judge_gate.py      # 评分门禁（改评分 prompt 后必跑）
```

P4 集成（真 PG / Redis）的建库与环境变量见 [P4-生产化 §7](../ops/P4-生产化.md)。

**CI（`.github/workflows/ci.yml`）**：push main / PR 上跑**不需要密钥**的那一半——**四个 job**：后端 pytest · 前端 lint+vitest+build · **P4 集成（真 PostgreSQL + 真 Redis，用 Actions services 起）** · **语料红线（CI 可查部分）**。

- **必须有假密钥兜底**：`config.Settings` 的三个必填项（`deepseek_api_key` / `siliconflow_api_key` / `jwt_secret`）本地靠 `.env` 掩盖——CI 没有 `.env`，缺了它**单测层 84 个用例**直接 ValidationError（实测）。故 workflow 顶层给三个假值。
- **红线门禁只能在 CI 跑一半，且如实标注**：**真比对**需要比对语料（个人题库），而它按红线定义**不进仓库**——CI 里没有语料，跑不了，也不许假装跑得了。CI 跑的是**入库守卫 + 检查器自检**：① `git ls-files` 里不得出现语料/密钥类路径（含 `git check-ignore` 覆盖：任何已跟踪文件命中 `.gitignore` 即失败）；② 检查器的骨架/窗口/命中合并/语料取值逻辑用**合成语料**夹具单测（这个文件本身要进公开仓库，写真题即自我违规）。
- **不进 CI**：smoke（真 key / 真 Qdrant）· 评测与评分门禁（花钱，且门禁余量取自波动，会被上游抖动误伤）。**分支保护是仓库设置，一次性手动开**。
