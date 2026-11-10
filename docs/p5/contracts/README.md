# P5 契约文件

> 2026-11-10 · J1 评审骨架。契约先于实现；J2 开始写 Java，J3 实现后以服务生成的 OpenAPI 为准。
> 设计说明见 [P5-Java业务](../P5-Java业务.md)；完整 J1 评审稿不随仓库。

## 文件

| 文件 | 说明 |
| --- | --- |
| [learning-api.yaml](learning-api.yaml) | 公开业务 API `/api/v1/learning/**` + 内部 API `/internal/v1/**`（OpenAPI 3.1） |
| [event-envelope.schema.json](event-envelope.schema.json) | Kafka 事件统一信封（JSON Schema 2020-12） |
| [interview-finalized.schema.json](interview-finalized.schema.json) | `interview.finalized` payload |
| [examples/interview-finalized.json](examples/interview-finalized.json) | 示例事件（虚构 id；无题库原文 / 个人数据） |

## 约定

- JSON 字段用 `snake_case`；时间 RFC3339 UTC；资源 id 为字符串。
- 公开 API 版本前缀 `/api/v1`；内部 API `/internal/v1`；事件 `version` 为整数。
- 向后兼容只加字段；删字段 / 改语义必须递增 `version` 或换 `type`。
- 错误信封：`{code, message, request_id, details?}`；HTTP status 承载语义。
- 创建类 POST 带 `Idempotency-Key`；计划创建用 `source_interview_id`。
- 可变资源 PATCH 必须带 `version`，冲突返回 409。
- 内部 API 只走内网；`X-Service-Token` + `X-User-Id`；不把用户 JWT 传给业务服务。
- **契约里不出现数据库表名**；数据所有权见 [P5-Java业务](../P5-Java业务.md) §2.3。

## 校验

当前没有引入 OpenAPI 语义校验器；J1 先做结构校验 + JSON Schema 校验：

```bash
backend/.venv/bin/python - <<'PY'
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

base = Path("docs/p5/contracts")
env = json.loads((base / "event-envelope.schema.json").read_text(encoding="utf-8"))
payload = json.loads((base / "interview-finalized.schema.json").read_text(encoding="utf-8"))
example = json.loads((base / "examples/interview-finalized.json").read_text(encoding="utf-8"))

Draft202012Validator.check_schema(env)
Draft202012Validator.check_schema(payload)
fc = FormatChecker()
assert not list(Draft202012Validator(env, format_checker=fc).iter_errors(example))
assert not list(Draft202012Validator(payload, format_checker=fc).iter_errors(example["payload"]))
print("schemas + example OK")
PY
```

OpenAPI 的 `$ref` 结构检查（无第三方依赖）：

```bash
backend/.venv/bin/python - <<'PY'
import yaml
from pathlib import Path
base = Path("docs/p5/contracts")
spec = yaml.safe_load((base / "learning-api.yaml").read_text(encoding="utf-8"))
refs = []
def walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                refs.append(v)
            walk(v)
    elif isinstance(node, list):
        for v in node:
            walk(v)
walk(spec)
for ref in refs:
    if ref.startswith("./"):
        assert (base / ref).exists(), ref
    elif ref.startswith("#/"):
        node = spec
        for part in ref[2:].split("/"):
            node = node[part]
    else:
        raise AssertionError(ref)
print("openapi refs OK", len(refs))
PY
```

J2 起把上面的检查接进 CI；语义校验（OpenAPI validator / schema diff）在 J2 引入。
