# 参与 Marda 开发

> 这是一个个人项目，但升级阶段会按小团队方式协作。请先读 `README.md`、`CLAUDE.md` 和 `docs/SPEC.md` 的相关章节。

## 本地跑起来

```bash
cp .env.example .env          # 填入自己的 key；测试不需要真实 key
cd backend && uv sync && uv run pytest -q
cd frontend && pnpm install && pnpm test
docker compose up -d --build  # http://localhost:8080
```

测试使用假 key / FakeLLM，不需要真实模型 API。**不要**在测试或 CI 里调用真实模型。

## 分支

- `main`：可演示版本，受保护；只通过 PR 合入。
- `p4-production`：升级集成分支；功能分支从这里拉，PR 也提这里。
- 功能分支：`p4/<issue>-短描述`，例如 `p4/e1-checkpoint-compression`。
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

PR 描述用仓库模板；必须写测试命令和结果。PR 不超过约 400 行，大任务拆小。

## 必须遵守

- 不直接 push `main`，不 force push，不改写已发布历史；
- 不提交 `.env`、真实密钥、个人题库原文、`docs/private/`；
- 提交前跑 `backend/.venv/bin/python data/scripts/check_redline.py`；
- 不改无关代码、不批量格式化；匹配现有风格；
- 改表结构 / API 契约 / 依赖前先在 Issue 里问；
- 开发/测试默认 FakeLLM；真实模型 smoke 可选，使用自己的 key、费用自理；不要提交 key，不要跑 20k/长时间压测；
- 不合并自己的 PR；CI 绿 + 所有者 review 后才合并。

## 完成标准

- 关联 Issue 的验收命令通过；
- 后端 `pytest`、前端 `lint + vitest + build` 绿；
- 新逻辑有测试；数据库变更带迁移/回滚说明；
- 没有密钥、题库原文、私有文档进入提交；
- PR 描述能让人从零复现验证。
