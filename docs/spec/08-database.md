> 本文是 SPEC 的 §8 正文；全局索引见 [../SPEC.md](../SPEC.md)。章节号冻结，不因拆分改变。

## 8. 数据库（题库 SQLite；业务库/检查点 PostgreSQL —— P4 已升级）

> **口径修订（P4 生产化，2026-10）**：本节标题原为「SQLite；PG 迁移已取消」。
> P4 已把**业务库**（users/interviews/answers/reports/resumes）与 **checkpointer**
> 迁到 PostgreSQL（两独立库），协调层用 Redis；**题库（questions/question_sources）仍在 SQLite**
> （离线构建、运行时只读的资产，不迁——见 [docs/P4-生产化.md](../P4-生产化.md)）。
> 下面的 DDL 是 **SQLite 形态**（题库部分）——**业务表在 PG 形态下有两处差异**：
> `answers.id` 是 `BIGSERIAL`（不是 `INTEGER PK AUTOINCREMENT`）、
> 占位符是 `%s`（不是 `?`）。业务表的语义与字段口径不变。

```sql
questions(id TEXT PK, question TEXT NOT NULL, answer TEXT NOT NULL,
  key_points JSON, follow_ups JSON, domain TEXT NOT NULL, topic TEXT NOT NULL,
  difficulty TEXT NOT NULL, company TEXT, round TEXT,
  source TEXT, status TEXT DEFAULT 'enabled', user_id TEXT)
  -- source = 答案主源（明细见 question_sources）
  -- user_id：NULL = 公共题；非 NULL = 私有上传，仅该用户可见 → **公共侧每一处查询都必须带
  --   `user_id IS NULL`**（题库浏览 / 分面 / 出题检索 / MCP 三条腿），私有题不进 Qdrant
question_sources(question_id TEXT, source TEXT, license TEXT, url TEXT,
  source_detail TEXT, imported_at TEXT, status TEXT DEFAULT 'enabled',
  PRIMARY KEY (question_id, source))                   -- 一题多源 = 多行（§8.1）
users(id TEXT PK, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL, created_at TEXT)
interviews(id TEXT PK, thread_id TEXT UNIQUE, user_id TEXT, position TEXT, interview_type TEXT,
  question_count INT, phase TEXT, difficulty TEXT, status TEXT, started_at TEXT, ended_at TEXT,
  resume_id TEXT)
  -- interview_type：tech/behavioral；老库补列后为 NULL，语义等同 tech，不做全表回填
  -- resume_id：本场使用的简历（NULL = 没传）；它同时是简历的**引用计数**
answers(id INTEGER PK AUTOINCREMENT, interview_id TEXT, question_id TEXT,
  domain TEXT, difficulty TEXT, candidate_answer TEXT, followup_count INT,
  skipped INT DEFAULT 0, score_json TEXT, created_at TEXT)
reports(id TEXT PK, interview_id TEXT, payload JSON, created_at TEXT)
resumes(id TEXT PK, user_id TEXT, filename TEXT, text TEXT, parsed JSON,
  content_hash TEXT, created_at TEXT)
  -- 简历原文 + 结构化抽取结果：接口只回结构化结果、不回原文，原文留库供重新解析
  -- content_hash = sha256(归一后文本)，(user_id, content_hash) 索引，同内容复用解析结果
```

面试过程以 **checkpointer state 为权威**，answers/reports 为落库产物（结束后一次写入）。

**账号与归属**：密码 `hashlib.scrypt`（n=2^14/r=8/p=1，存储串自描述 `scrypt$n$r$p$salt$digest`）；JWT HS256，7 天有效，密钥 `JWT_SECRET` 走 `.env`（长度下限 32，弱密钥启动即失败）。归属列是 `interviews.user_id`——**checkpointer 不需要隔离**（thread_id = 全局唯一 uuid），业务库才是归属权威；隔离实现 = 列表按 user_id 过滤 + 其余端点先校验归属（`service._require_owner`）。老库启动时轻量迁移补 `user_id` 列（`PRAGMA table_info` 探测），历史孤儿行（`user_id IS NULL`）由首个注册账号认领一次（不依赖「用户数为 0」判断，免并发竞态）。

**删除口径**：DELETE 物理删除——checkpointer 线程（`saver.adelete_thread`）与 interviews/answers/reports 三表一并清除，不做逻辑删除（逻辑删除的 `deleted_at` 过滤会污染所有查询）。**简历按引用计数清理**：删除场次时只在该简历已无任何场次引用时才一并删除。

### 8.1 多源扩充口径

`source` 列语义 = **答案主源**；来源明细进 `question_sources` 关联表（PK = (question_id, source)，一题多源 = 多行）。

- **license 按源记、不按题记**；业务表与向量层都不再持有来源字段
- **主源裁决**（`bank.rank`，`min` 取优）：主源优先级（`SOURCE_PRIORITY` 登记表，个人题库恒 0——人工整理，开源源答案再长也不顶替）> 能用（enabled）> 答案长 > 轮次可信；完全同分取先导入者（列表序稳定，不另记时间戳）
- **同题合并**：题干 md5 相同 → 同 question_id → 跨源合并成一条；`sources` 每源留**最优**那条（按 `bank.rank` 排序后取首个）——留「最优」而非「先出现」，出处才指向答案真正来自的那一篇
- **迁移**：`ingest.ensure_schema` 探测老库（questions 有 license/url 列）→ 建来源表并回填 → `DROP COLUMN` 两列；幂等可重跑
- **同步语义**：questions 按 id upsert + `DELETE NOT IN`；question_sources **整表重建**——它的同步规则比 questions 多一维（题还在、某来源没了也要删），逐行 diff 徒增复杂度
- 每个新源一个 adapter（复用 `bank.new_question`/`finalize_status`/`source_record`），把该源的分类体系归一化到统一 schema（topic→域、easy/medium/hard→L1/L2/L3、无轮次概念→NULL）；未知 topic 报错、人工补映射
- **对上层透明**：app 侧零改动（`fetch_by_ids` 只 select 固定列），Qdrant payload 不含 source 字段，检索命中后 join SQLite
