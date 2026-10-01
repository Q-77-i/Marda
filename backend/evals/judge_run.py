"""在离线评测里重复跑评分官（P1-M12 会话 2）：真 LLM 调用，K 次/条。

**测的就是生产 prompt**：消息由 `app.graph.nodes.judge.judge_messages` 构造
（judge 节点的同一入口），schema 走 `score_schema_for`——评测若自己拼一遍 prompt，
模板一改它就静默失配，量到的是旧口径。图节点的副作用（计数/难度/回写）不在评测范围，
故直接调 `llm.chat_json` 而不是跑 `judge_node`。

**温度**：`temperature` 显式传。生产臂用 `judge.JUDGE_TEMPERATURE`（0.3）；温度 0 只是
对照实验——「要不要改生产温度」是独立决策（影响评分/报告/PDF，需单独回归）。

**失败处理**：DeepSeek 结构化输出偶发非法 JSON（M8/M10 实测过）。单次调用重试一次
（与生产一致：生产是用户手动重试），仍失败则该运行记为 `{"failure": ...}`，
**不静默丢**——汇总里单列，条数不足的样本会被指标层排除并亮出来。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app import llm
from app.graph.nodes.judge import judge_messages
from app.graph.state import score_schema_for

from evals import judge_metrics

RUN_RETRY = 1  # 单次调用失败后的重试次数（生产等价语义：用户重发）
ARMS: tuple[str, ...] = ("real", "behavioral", "persona")
MIN_RUNS_PER_ITEM = 2  # 有效运行少于此的样本不进指标（单次运行量不出波动）


def score_to_record(score) -> dict:
    """评分模型 → 可 JSON 化的运行记录（指标层的输入形状，见 judge_metrics）。"""
    dims = {
        key: value
        for key, value in score.model_dump().items()
        # bool 是 int 的子类，必须显式排除，否则 error_flag 会混进维度表
        if isinstance(value, int) and not isinstance(value, bool)
    }
    return {
        "dims": dims,
        "covered": [str(p) for p in score.covered_key_points],
        "missed": [str(p) for p in score.missed_key_points],
        "error_flag": bool(score.error_flag),
    }


async def run_item(item: dict, *, runs: int, temperature: float) -> list[dict]:
    """一条样本跑 K 次（串行：并发在上层按样本粒度做，K 次之间保持独立与可追溯）。"""
    messages = judge_messages(
        question=item["question"],
        key_points=item["key_points"],
        answer=item["answer"],
        followup_log=(),  # golden 统一为「单轮回答」形态：见 SPEC §4.13 会话 2 口径
        interview_type=item["interview_type"],
    )
    schema = score_schema_for(item["interview_type"])
    out: list[dict] = []
    for _ in range(runs):
        last_error = ""
        for _attempt in range(RUN_RETRY + 1):
            try:
                score = await llm.chat_json(messages, schema=schema, temperature=temperature)
                out.append(score_to_record(score))
                break
            except Exception as exc:  # noqa: BLE001 — 失败要计数上报，不是此刻崩掉整轮
                last_error = f"{type(exc).__name__}: {exc}"
        else:
            out.append({"failure": last_error})
    return out


async def run_all(
    items: Sequence[dict], *, runs: int, temperature: float, concurrency: int = 6
) -> dict[str, list[dict]]:
    """全部样本 × K 次；返回 `{item_id: [run, ...]}`（含失败记录，由调用方统计）。"""
    sem = asyncio.Semaphore(concurrency)

    async def _one(item: dict) -> tuple[str, list[dict]]:
        async with sem:
            return item["id"], await run_item(item, runs=runs, temperature=temperature)

    pairs = await asyncio.gather(*[_one(item) for item in items])
    return dict(pairs)


def split_failures(runs_by_item: dict[str, list[dict]]) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """拆出可用运行与失败计数：`({id: [有效 run]}, {id: 失败次数})`。

    指标层拿到的必须全是有效运行；失败条数进报告（不静默）。
    """
    valid: dict[str, list[dict]] = {}
    failures: dict[str, int] = {}
    for iid, runs in runs_by_item.items():
        ok = [run for run in runs if "failure" not in run]
        valid[iid] = ok
        if len(ok) != len(runs):
            failures[iid] = len(runs) - len(ok)
    return valid, failures


def arm_report(items: Sequence[dict], runs_by_item: dict[str, list[dict]]) -> dict:
    """一次运行的完整指标：全量 + 按臂切片 + 三档单调性（run 与 gate 两个入口共用）。"""
    types = [t for t in ("tech", "behavioral") if any(item.get("interview_type") == t for item in items)]
    return {
        "overall": {
            "consistency": judge_metrics.consistency_report(items, runs_by_item),
            "accuracy": judge_metrics.accuracy_report(items, runs_by_item),
        },
        "by_arm": {
            arm: {
                "consistency": judge_metrics.consistency_report(items, runs_by_item, arm=arm),
                "accuracy": judge_metrics.accuracy_report(items, runs_by_item, arm=arm),
            }
            for arm in ARMS
            if any(item.get("arm") == arm for item in items)
        },
        # 维度表按类型分开：技术面五维与行为面五维是两套键，混在一张表里没法读
        "by_type": {
            itype: {
                "consistency": judge_metrics.consistency_report(items, runs_by_item, interview_type=itype),
                "accuracy": judge_metrics.accuracy_report(items, runs_by_item, interview_type=itype),
            }
            for itype in types
        },
        "monotonicity": judge_metrics.monotonicity_report(items, runs_by_item),
    }


async def run_and_aggregate(
    items: Sequence[dict], *, runs: int, temperature: float, concurrency: int = 6
) -> dict:
    """跑一条臂并出报告（含有效运行不足而掉出的样本清单与调用失败计数）。"""
    raw = await run_all(items, runs=runs, temperature=temperature, concurrency=concurrency)
    valid, failures = split_failures(raw)
    usable = [item for item in items if len(valid.get(item["id"], [])) >= MIN_RUNS_PER_ITEM]
    report = arm_report(usable, valid)
    report.update({
        "temperature": temperature,
        "n_items": len(usable),
        "dropped_items": [item["id"] for item in items if item not in usable],
        "failures": failures,
        # 原始运行结果一并落盘（指标代码改了可以据此复算，不必重新花钱调用）
        "runs_raw": {iid: runs for iid, runs in valid.items() if runs},
    })
    return report
