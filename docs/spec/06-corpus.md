> 本文是 SPEC 的 §6 正文；全局索引见 [../SPEC.md](../SPEC.md)。章节号冻结，不因拆分改变。

## 6. 语料解析与入库（data/scripts/）

管道顺序：`parse_md` / `parse_xmind` / `parse_open` → `combine` → `enrich` → `ingest`。产物在 `data/parsed/`，最终写 SQLite + Qdrant；个人题库与派生文本不进仓库（红线见 [CLAUDE.md](../../CLAUDE.md)）。命令在仓库根目录跑：

```bash
backend/.venv/bin/python data/scripts/parse_md.py      # 个人题库 md → 结构化 JSON
backend/.venv/bin/python data/scripts/parse_xmind.py   # xmind → JSON
backend/.venv/bin/python data/scripts/parse_open.py    # 开源语料 → JSON
backend/.venv/bin/python data/scripts/combine.py       # 合并（自带零回归校验，不过则退出）
backend/.venv/bin/python data/scripts/enrich.py        # LLM 补关键点 / 预置追问（可断点续跑）
backend/.venv/bin/python data/scripts/ingest.py        # → SQLite + Qdrant 双写（含入库护栏）
backend/.venv/bin/python data/scripts/check_redline.py # 提交前语料红线反查（--all 全量）
```

### 6.1 md 解析（parse_md.py，主数据源）

- 层级规则：`#` = round（一面/二面/三面）；`##` = company；`###` = topic；`####` = 题目。
- 题目文本 = 标题去除编号前缀（正则 `^\d+\.\s*`，处理 "1. 1." 双重编号）；正文 = 参考答案。
- 输出 JSON：question / answer / topic / domain / difficulty / company / round / `source`="个人题库-牛客补充版"（**答案主源**）/ `sources=[{source, license, url, source_detail}]`（合规四要素；license 按源记）。
- 同题合并（题干 md5 相同 → 同 question_id）跨源生效，主源裁决 `bank.rank`（`min` 取优）：**主源优先级 > 能用 > 答案长 > 轮次可信**，完全同分时取先导入者；合并后 `sources` 每源留一条、按优先级排序，留的是该源里**最优**那条（占位存根不会把正本的 URL 挤掉）。

### 6.2 映射表（config，随 SPEC 交付）

- **topic → domain**：按 PRD 六大域映射（手撕算法 → `algorithms`）；映射表以 md 实际 topic 全集为准，**未知 topic 报错不静默**。
- **round → difficulty**：一面→L1，二面→L2，三面→L3；`algorithms` 默认 L2。

### 6.3 xmind 解析

解 zip → content.json → 遍历主题树，按同样层级规则扁平化，复用 md 输出结构。

### 6.4 富化与质检（enrich.py）

- LLM 批量补齐 key_points / follow_ups（flash，批处理 + 抽样人工质检 20 条）；
- **按域选 prompt 分支**：`algorithms` → 思路/复杂度/边界三类要点；`behavioral` → key_points 写**应答要点与讲述结构**（不是技术知识点，评分官据此判覆盖，§4.12）；其余技术域用通用模板。
- 校验必填字段、去重（相似度 + 手动白名单，见 §6.6）。

### 6.5 开源语料适配（parse_open.py，四源）

只采**明确 licensed 且题干与答案同在仓库内**（可溯源、非抓取）的源，清单见 [data/licenses/语料来源清单.md](../../data/licenses/语料来源清单.md)。每源一个 adapter，共用 `bank` 共享层的 `new_question`/`finalize_status`/`source_record`，归一化到统一 schema 后与个人题库进同一套合并。

| 源 | 形态 | 明细行 | 解析要点 |
| --- | --- | --- | --- |
| ai-agents-from-zero | `### Qn-m.` 题 + 正文 | 89 | 唯一带**真实难度标注**的源（基础/中等/较难 → L1/L2/L3）；正文止于「常见追问」 |
| FAQ_Of_LLM_Interview | 题单+编号答案段 / 编号问题行+围栏答案 | 71 | 两种形态：题单与答案段**按编号配对**，配不上不成题；`text` 围栏剥壳、带语言围栏保留 |
| ai-agent-interview-guide | `**Q：**` + 答案标记 | 261 | 四种标记；「追问应对」起截断 |
| llm-interview-guide | `**Q：**` 内联问答（106 页） | 809 | topic 取 H1；图片链接转文本 |

- **未采的逐项进报告，不静默**：无答案段的题单条目、清单外文件、站点页、速记与真题清单——超出「题干答案成对」口径的一律不采。
- **未知章节/未知 topic 报错不静默**（同 §6.2）。
- **占位答案不算答案**：`finalize_status` 按**答案的实质字符数**判定（剥掉代码围栏行与首尾空白后 < 5 字 → draft）。源里的 `答案：xx`、空代码块这类空壳因此不会以 enabled 身份去富化、进向量库——否则会占着配额却给不出参考答案。
- 四源内部同题干合并 16 组（1246 → 1229）；`company`/`round` 无此概念时为 `NULL`（**不是空串**）。

### 6.6 合并入库（combine.py）

个人题库（`questions_enriched.json`）+ 开源语料（`questions_open.json`）→ `questions_combined.json`（`ingest.py` 的输入）：

- **个人题库在前**：完全同分时先导入者优先；个人题库 `SOURCE_PRIORITY` 恒 0，开源答案再长也不顶替主源。
- **自带零回归校验**：个人题库 12 个字段逐字比对，只允许新增 `sources` 明细行；不过则非零退出——**不允许带病入库**。
- **近似重复只报不并**：相似度 ≥0.9 的候选对打印清单，人工登记白名单后才合。
- **人工改判表**（`data/curation/question_overrides.json`）：逐题修正 `domain` / `status`，`combine` 每次运行都应用。key 是 **题干的内容哈希**——题干改一个字条目就失效，故未命中的条目会列进运行报告（不静默）；每条必须写 `reason`，缺了直接报错。改判在零回归校验**之后**执行。**已落库的库**用 `apply_overrides.py` 补齐（幂等，改判成 draft 的从 Qdrant 撤点、仍 enabled 但换域的改 payload 的 `domain`）——只为十来道题重建整个向量库不值当。8 道项目叙事题 `behavioral → project` 即此类（enabled→enabled 的纯域改判，向量文本不含域，**不重嵌、不补点**）。
- **入库 status 规则**：`domain ∈ ASKABLE_DOMAINS ∪ ENABLED_DOMAINS` 且有实质答案 → enabled；两个集合之外的域（cs-fundamentals）解析入库但置 draft。
- **近似重复白名单合并（P2-M1）**：白名单分两处应用、各管一段——`parse_md` 的清单管**个人库同源内部**，`combine` 的 `APPROVED_MERGE_PAIRS` 管**跨源**。实现同一份（`bank.merge_approved_pairs`），**匹配不到直接报错**（题干改字 → 哈希变，白名单不许静默失效）。已登记 3 对措辞级；假阳性对与跨域同题对**故意不并**，理由写在清单注释里。
- **难度重标注表（P2-M1，`data/curation/difficulty_annotations.json`）**：`{question_id: L1|L2|L3}`，由 `annotate_difficulty.py` 批量生成（flash + 与前端创建表单同源的三档语义）。
  - **为什么需要它**：四源里只有 `ai-agents-from-zero` 带真实难度标注，其余三源解析时一律落默认 L2（实测 686 题）——难度档因此几乎不区分（L2 一度占 80%），L3×15 组合直供不足。
  - **应用时机**：`combine` 在**零回归校验之后**应用（`difficulty` 在 `FROZEN_FIELDS` 里）；已落库的用 `apply_difficulty.py` 补齐（SQLite UPDATE + Qdrant `set_payload`；标注不改 doc 文本、不必重建向量库），幂等可重跑。
  - `from-zero` 的 89 道真实标注**不在表内**（保留原标注，并作为 rubric 校准集：完全一致 68.5% / 相邻 98.9%）。
- **启用行为面题库**（`enable_behavioral.py`）：把有实质答案的行为题翻成 enabled 并补进 Qdrant（幂等；写后从 Qdrant 读回逐点核对）。与 `apply_overrides.py` 同一模式：**就地补齐**，避免为十来道题全量重建向量库。
