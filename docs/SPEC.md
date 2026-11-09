# SPEC：Marda 码达 — 技术规格

> 现行口径索引。正文按域拆分到 [`docs/spec/`](spec/)；**章节号冻结**，代码注释与文档中的 `SPEC §X` 按本表映射。
> 状态：阶段 1 demo 闭环、阶段 2 功能完善、多模态扩展、P4 生产化已完成；P5 评估稿见 [P5-Java业务](P5-Java业务.md)。
>
> 配套：[README](../README.md) · [PRD](PRD.md) · [P4-生产化](P4-生产化.md) · [P5-Java业务](P5-Java业务.md) · [里程碑](里程碑.md)

## 章节索引

| 章节 | 主题 | 正文 |
| --- | --- | --- |
| §1 | 范围与目标 | [spec/01-overview.md](spec/01-overview.md) |
| §2 | 工程结构 | [spec/02-structure.md](spec/02-structure.md) |
| §3 | LLM 集成（llm.py） | [spec/03-llm.md](spec/03-llm.md) |
| §4 | 面试状态机（LangGraph） | [spec/04-state-machine.md](spec/04-state-machine.md) |
| §5 | RAG | [spec/05-rag.md](spec/05-rag.md) |
| §6 | 语料解析与入库（data/scripts/） | [spec/06-corpus.md](spec/06-corpus.md) |
| §7 | API 契约 | [spec/07-api.md](spec/07-api.md) |
| §8 | 数据库（题库 SQLite；业务库/检查点 PostgreSQL —— P4 已升级） | [spec/08-database.md](spec/08-database.md) |
| §9 | 前端设计 | [spec/09-frontend.md](spec/09-frontend.md) |
| §10 | 测试与验收 | [spec/10-testing.md](spec/10-testing.md) |
| §11 | 风险注意点（实现时强制） | [spec/11-risks.md](spec/11-risks.md) |
| §12 | 改动索引 | [spec/12-changelog.md](spec/12-changelog.md) |

## 文档分工

- [PRD](PRD.md)：要什么、验收什么。
- 本文件 + `spec/`：当前系统的技术口径（怎么做）。
- [P4-生产化](P4-生产化.md) / [P5-Java业务](P5-Java业务.md)：阶段专题（生产化 / Java 演进评估），不是当前规格。
- [里程碑](里程碑.md)：每个阶段做了什么、关键决策与验证证据。

## 全局编号约定

- `SPEC §X` / `§X.Y` 指**全局章节号**，与文件位置无关；上表是唯一映射。
- 新增内容追加到对应域文件；**不重编号**。确需移动章节时，更新本索引并在原位置留一行重定向。
- 本文件只做索引与约定，不重复正文，避免两份口径。

## 变更记录

历史改动见 [spec/12-changelog.md](spec/12-changelog.md)。
