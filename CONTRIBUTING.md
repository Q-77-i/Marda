# 参与 Marda 开发

> 个人项目，按小团队方式协作。开始前先读 [README](README.md) 的「快速开始」和 [CLAUDE.md](CLAUDE.md) 的工程规约；技术细节按需查 [SPEC](docs/spec/SPEC.md)。

## 本地跑起来

```bash
cp .env.example .env          # 填入自己的 key；测试不需要真实 key
cd backend && uv sync && uv run pytest -q
cd frontend && pnpm install && pnpm test
docker compose up -d --build  # http://localhost:8080
```

测试使用假 key / FakeLLM，不需要真实模型 API。**不要**在测试或 CI 里调用真实模型。P4 集成用例需要真 PostgreSQL / Redis，命令见 [docs/ops/P4-生产化.md §7](docs/ops/P4-生产化.md)。

## 分支

- `main`：唯一长期分支，也是可演示版本；受保护，功能改动走 PR。
- 功能分支：`feat/<短描述>` 或 `fix/<短描述>`，从 `main` 拉，PR 提 `main`。
- 一个 Issue = 一个分支 = 一个 PR；不要在一个 PR 里塞多个无关任务。

## Issue / PR

Issue 至少写清楚：

```text
背景：为什么做
范围：改哪些文件/模块
不做：明确边界
验收：命令 + 期望结果
依赖：前置 Issue
```

PR 描述用仓库模板；必须写测试命令和结果。单个 PR 尽量不超过约 400 行，大任务拆小。

## 必须遵守

- 不 force push、不改写已发布历史；不直接 push `main`（维护者的小改动除外）；
- 不提交 `.env`、真实密钥、个人题库原文、`docs/private/`；
- 提交前跑 `backend/.venv/bin/python data/scripts/check_redline.py`；
- 不改无关代码、不批量格式化；匹配现有风格；
- 改表结构 / API 契约 / 依赖前先在 Issue 里问；
- 开发/测试默认 FakeLLM；真实模型 smoke 可选，使用自己的 key、费用自理；不要提交 key，不要跑长时间压测；
- 不合并自己的 PR；CI 绿 + 所有者 review 后才合并。

## 完成标准

- 关联 Issue 的验收命令通过；
- 后端 `pytest`、前端 `lint + vitest + build` 绿；
- 新逻辑有测试；数据库变更带迁移 / 回滚说明；
- 没有密钥、题库原文、私有文档进入提交；
- PR 描述能让人从零复现验证。

## 项目角色与贡献

| 成员 | 角色 | 主要范围 | 粗略贡献 |
| --- | --- | --- | --- |
| Zhouq (Q-77-i) | 维护者 / 规划 | P1–P3、M13–M16、P5；P4 规划 / 验收 / 集成 / 发布；文档、评审、红线 | 全项目约 80–85%；P4 约 10–15% |
| RangerCao | P4 实现 | 多 worker、PG / Redis、checkpoint 压缩与保留、迁移 / 备份、P4 CI 集成 | P4 实现约 85–90%；全项目约 15–20% |

> 贡献度按阶段 / PR 范围估算，不按代码行数；依据 git 历史与 PR #20
> （27 commits / 60 files / +6691 / −507）。详细台账在内部记录中（不随仓库）。

## 文档公开与私有

- **公开**：README、PRD、SPEC、里程碑、`docs/ops/`、`docs/p5/` 的 README / 评估稿 / contracts、
  CONTRIBUTING。这些是给用户、面试官、合作者看的成果与契约。
- **私有**：`docs/private/` —— 阶段计划、基线、证据、贡献台账、个人笔记、题库原文；
  **不提交**，也不在公开文档里链接。
- 写文档前先判断：这是“对外成果 / 契约”，还是“内部过程”？过程文档不进公开仓库。

## 文档分工

README 是门面；需求看 [PRD](docs/PRD.md)，技术口径看 [SPEC](docs/spec/SPEC.md)，
生产化看 [P4-生产化](docs/ops/P4-生产化.md)，过程记录看 [里程碑](docs/里程碑.md)；
P5 业务背景看 [p5/README](docs/p5/README.md)，评估稿看 [P5-Java业务](docs/p5/P5-Java业务.md)，
契约看 [p5/contracts](docs/p5/contracts/)。改动涉及哪一层，就同步更新对应文档。
