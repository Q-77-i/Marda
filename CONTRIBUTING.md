# 参与 Marda 开发

> 个人项目，按小团队方式协作。开始前先读 [README](README.md) 的「快速开始」和 [CLAUDE.md](CLAUDE.md) 的工程规约；技术细节按需查 [SPEC](docs/SPEC.md)。

## 本地跑起来

```bash
cp .env.example .env          # 填入自己的 key；测试不需要真实 key
cd backend && uv sync && uv run pytest -q
cd frontend && pnpm install && pnpm test
docker compose up -d --build  # http://localhost:8080
```

测试使用假 key / FakeLLM，不需要真实模型 API。**不要**在测试或 CI 里调用真实模型。P4 集成用例需要真 PostgreSQL / Redis，命令见 [docs/P4-生产化.md §7](docs/P4-生产化.md)。

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

## 文档分工

README 是门面；需求看 [PRD](docs/PRD.md)，技术口径看 [SPEC](docs/SPEC.md)，生产化看 [P4-生产化](docs/P4-生产化.md)，过程记录看 [开发历程](docs/开发历程.md)。改动涉及哪一层，就同步更新对应文档。
