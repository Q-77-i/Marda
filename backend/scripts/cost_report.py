"""成本报告（P2-M10）：按场次把这场面试的成本从 Langfuse 读回来。

用法：cd backend && uv run python scripts/cost_report.py <interview_id>
      uv run python scripts/cost_report.py <interview_id> --json    # 机器可读

三个切面（都由 `observability.summarize_observations` 算，纯函数、单测覆盖）：

- **按模型**：flash / v4-pro 各花了多少 —— 一跑降级链，这里立刻看出报告是否走了深度档；
- **按环节**：出题 / 追问 / 评分 / 报告…（`llm.*(purpose=...)` 映射的 generation 名）；
- **按轮次**：挂在第几个 `interview-turn` span 下（开场/报告/收尾这类归「场外」）。

如实边界（都不静默）：未配 Langfuse key → 明确报错退出（成本只存在于云端）；
Langfuse 项目没配价格表 → 成本为 0，打印提示（不是代码的问题）；
**接入 P2-M10 之前的场次** generation 没有环节名 → 归「(未命名)」照常统计 token；
本地成本账本（不依赖 Langfuse 的 usage 落库）**未做**——见 SPEC §11 风险 16 的已知边界。

场次 id → trace id 是确定性派生（`create_trace_id(seed=interview_id)`，同一个函数），
所以本工具只要有场次 id 就能读回，不需要查库。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import observability  # noqa: E402


def _render(summary: dict, *, interview_id: str, trace_id: str) -> str:
    lines = [
        f"场次 {interview_id}",
        f"trace {trace_id}",
        f"观测：轮次 span {summary['turn_spans']} 个 / generation {summary['generations']} 个",
        "",
        f"总成本 ¥{summary['cost_total']:.4f} · token {summary['tokens']['total']}"
        f"（输入 {summary['tokens']['input']} / 输出 {summary['tokens']['output']}）",
    ]
    for title, rows in (("按模型", summary["by_model"]), ("按环节", summary["by_purpose"]),
                        ("按轮次", summary["by_turn"])):
        lines += ["", title]
        for row in rows:
            lines.append(
                f"  {row['label']:<24} 调用 {row['calls']:>3} · token {row['tokens']:>7} "
                f"· ¥{row['cost']:.4f}"
            )
        if not rows:
            lines.append("  （无）")
    if not summary["cost_priced"]:
        lines += [
            "",
            "⚠ 成本为 0：Langfuse 项目里没有这些模型的价格定义"
            "（Settings → Models 按 DeepSeek 官方价目加一条即可）；token 数照常可信",
        ]
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="按场次读回 Langfuse 成本（P2-M10）")
    parser.add_argument("interview_id", help="场次 id（面试 URL 里的那一段）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON（脚本消费）")
    args = parser.parse_args()

    if not observability.enabled():
        raise SystemExit(
            "未配置 Langfuse（.env 缺 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY）——\n"
            "成本只存在于云端 trace 里，没有 key 就读不回来。配好后重跑。"
        )

    client = observability.get_client()
    trace_id = client.create_trace_id(seed=args.interview_id)
    observability.flush()  # 若本进程刚跑过（如 smoke），把缓冲推上去
    try:
        observations = await observability.fetch_trace_observations(trace_id)
    except RuntimeError as exc:  # 云端一直看不到这条 trace（场次 id 打错 / 从没配 key 跑过）
        raise SystemExit(f"读不到 trace：{exc}\n（核对场次 id 是否正确、该场是否在本项目配了 Langfuse）")
    summary = observability.summarize_observations(observations)

    if args.json:
        print(json.dumps({"interview_id": args.interview_id, "trace_id": trace_id, **summary},
                         ensure_ascii=False, indent=2))
    else:
        print(_render(summary, interview_id=args.interview_id, trace_id=trace_id))


if __name__ == "__main__":
    asyncio.run(main())
