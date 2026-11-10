# Marda 文档导航

> 公开仓库只放“成果 / 契约 / 规约”；阶段计划、基线、证据、个人笔记放在 `docs/private/`（不随仓库）。
> 贡献分工见 [CONTRIBUTING](../CONTRIBUTING.md)。

## 公开文档

| 文档 | 读什么 |
| --- | --- |
| [PRD](PRD.md) | 产品需求：功能、业务规则、页面、验收标准 |
| [spec/SPEC.md](spec/SPEC.md) | 技术规格索引；正文按域拆分到 `spec/01..12`，章节号冻结 |
| [ops/P4-生产化.md](ops/P4-生产化.md) | 生产化：多 worker + PG + Redis、迁移、备份、已知边界 |
| [p5/README.md](p5/README.md) | P5 业务背景：为什么从面试诊断延伸到学习执行 |
| [p5/P5-Java业务.md](p5/P5-Java业务.md) | P5 评估稿：B / C 方案、边界、里程碑、SLO |
| [p5/contracts/](p5/contracts/) | P5 公开契约：OpenAPI 骨架 + 事件 JSON Schema |
| [里程碑](里程碑.md) | 每个阶段做了什么、关键决策与验证证据 |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | 本地开发、分支、Issue / PR、项目角色与贡献 |
| [assets/](assets/) | README / 文档引用的图片与架构图 |

## 私有文档（不随仓库）

阶段计划、基线、证据、贡献台账、个人笔记与题库原文放在 `docs/private/`，**不提交**，
也不在公开文档里链接。

## 目录结构

```text
docs/
  README.md          # 本文件
  PRD.md
  里程碑.md
  spec/              # SPEC 索引 + §1–§12 正文
  ops/               # 生产化 / 运维文档
  p5/                # P5 公开文档（README / 评估稿 / contracts）
  assets/            # 图片与架构图
  private/           # 本地私有，gitignore
  题库/               # 本地题库，gitignore
```
