"""面试服务层（SPEC §7）：单例图生命周期 + 会话流编排 + SSE 事件映射。

- 图/saver 由 app lifespan 管理（start/close），路由层保持薄；
- start_interview / send_message 返回事件异步生成器，api 层直接包 EventSourceResponse；
- 结束落库（answers/report/interviews 收尾）在流内完成（SPEC §8「结束后一次写入」）；
- 心跳 ping 交给 EventSourceResponse 自带机制（15s，SPEC §7），本层不重复实现。
"""

from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import Any, AsyncIterator

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from pydantic import BaseModel

from app import db, llm
from app.config import Settings
from app.graph.graph import build_graph, make_serde, run_config
from app.graph.state import InterviewState


class InterviewNotFoundError(Exception):
    """场次不存在（404）。"""


class InterviewFinishedError(Exception):
    """场次已结束仍尝试发消息（409）。"""


def _plain(value: Any) -> Any:
    """递归归一化为纯 Python 对象（流更新/checkpoint 反序列化会带回 Pydantic 模型与枚举）。"""
    if isinstance(value, BaseModel):
        return _plain(value.model_dump())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _event(name: str, data: dict) -> dict:
    """SSE 事件：data 预序列化为 JSON 字符串（sse-starlette 对 dict 只做 str() 编码）。"""
    return {"event": name, "data": json.dumps(data, ensure_ascii=False)}


def map_updates(
    chunk: dict, snapshot: dict, *, interview_id: str | None = None
) -> tuple[list[dict], dict]:
    """一次 astream updates chunk → (SSE 事件列表, 新 snapshot)（纯函数，SPEC §7 事件表）。

    chunk = {node: updates}，updates 是该节点本次返回的字段（全量值，无 reducer）；
    interview_id 传入时随 meta 事件携带（创建流首事件已带，恢复 UI 用）。
    """
    events: list[dict] = []
    for node, updates in chunk.items():
        if node == "__interrupt__":
            continue  # langgraph 1.x 暂停标记（interrupt 载荷），不是业务状态更新
        updates = _plain(updates)
        # delta：chat_history 新增的面试官消息（用户消息/旧消息不发）
        if "chat_history" in updates:
            old_len = len(snapshot.get("chat_history", []))
            for entry in updates["chat_history"][old_len:]:
                if entry.get("role") == "assistant":
                    events.append(_event("delta", {"text": entry["content"]}))
        # question：新题提示（生成题无 question_id 不发；追问/评分重传同题不发）
        question = updates.get("current_question")
        if question and question.get("question_id"):
            prev_id = (snapshot.get("current_question") or {}).get("question_id")
            if question["question_id"] != prev_id:
                events.append(_event("question", {
                    "index": snapshot.get("answered_count", 0) + 1,
                    "question_id": question["question_id"],
                    "domain": question["domain"],
                    "difficulty": question["difficulty"],
                }))
        # meta：阶段/进度变化（interview_id 由 service 注入）
        if {"phase", "answered_count", "question_count"} & updates.keys():
            merged = {**snapshot, **updates}
            data = {
                "phase": merged.get("phase"),
                "answered_count": merged.get("answered_count", 0),
                "question_count": merged.get("question_count", 0),
            }
            if interview_id is not None:
                data["interview_id"] = interview_id
            events.append(_event("meta", data))
        snapshot.update(updates)
    return events, snapshot


def _answers_row(record: Any) -> dict:
    """QuestionRecord（或 plain dict）→ answers 行（SPEC §8 列映射）。"""
    q = _plain(record)
    score = q.get("score")
    return {
        "question_id": q.get("question_id"),  # 生成题为 None
        "domain": q.get("domain", ""),
        "difficulty": q.get("difficulty", ""),
        "candidate_answer": q.get("answer"),
        "followup_count": q.get("follow_up_count", 0),
        "skipped": 1 if q.get("skipped") else 0,
        "score_json": json.dumps(score, ensure_ascii=False) if score else None,
    }


class Service:
    """单场服务：持有一个编译图 + saver 连接，业务库走同步 db（to_thread）。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._graph = None
        self._conn: aiosqlite.Connection | None = None

    async def start(self) -> None:
        """lifespan 启动：业务库 schema + checkpointer 连接 + 编译图。"""
        await asyncio.to_thread(db.ensure_schema, self._settings.db_path)
        self._conn = await aiosqlite.connect(str(self._settings.checkpoint_db_path))
        saver = AsyncSqliteSaver(self._conn, serde=make_serde())
        self._graph = build_graph(checkpointer=saver)

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()

    @property
    def _g(self):
        if self._graph is None:
            raise RuntimeError("service 未启动（lifespan）")
        return self._graph

    async def _current_values(self, interview_id: str) -> dict:
        """checkpoint 当前 state（plain dict）；无此线程时为空 dict。"""
        snapshot = await self._g.aget_state({"configurable": {"thread_id": interview_id}})
        return _plain(snapshot.values)

    async def check_can_send(self, interview_id: str) -> None:
        """发消息前置校验（route 层调用，保证 4xx 在流开始前返回）。"""
        values = await self._current_values(interview_id)
        if not values:
            raise InterviewNotFoundError(interview_id)
        if values.get("status") == "finished":
            raise InterviewFinishedError(interview_id)

    async def start_interview(
        self, interview_id: str, position: str, question_count: int
    ) -> AsyncIterator[dict]:
        """创建场次后立即执行开场（SPEC §7）：首事件 meta 携带 interview_id。"""
        state = InterviewState(
            interview_id=interview_id, position=position, question_count=question_count
        )
        config = run_config(interview_id, question_count)
        yield _event("meta", {
            "interview_id": interview_id, "phase": "intro",
            "answered_count": 0, "question_count": question_count,
        })
        async for event in self._run(state, config, state.model_dump()):
            yield event

    async def send_message(self, interview_id: str, content: str) -> AsyncIterator[dict]:
        """resume 图到下一 interrupt 或结束；防御性复查（route 已查过）。"""
        values = await self._current_values(interview_id)
        if not values:
            raise InterviewNotFoundError(interview_id)
        if values.get("status") == "finished":
            raise InterviewFinishedError(interview_id)
        config = run_config(interview_id, values["question_count"])
        async for event in self._run(Command(resume=content), config, values):
            yield event

    async def _run(self, input_value: Any, config: dict, initial_values: dict) -> AsyncIterator[dict]:
        """跑图至 interrupt/END，翻译 updates 为 SSE 事件；结束时落库并发 done。"""
        interview_id = config["configurable"]["thread_id"]
        snapshot = _plain(initial_values)
        try:
            # langgraph 1.2 单 stream_mode 时每次产出 (mode, {node: updates}) 二元组
            async for item in self._g.astream(input_value, config=config, stream_mode=["updates"]):
                _, chunk = item
                events, snapshot = map_updates(chunk, snapshot, interview_id=interview_id)
                for event in events:
                    yield event
            values = _plain((await self._g.aget_state(config)).values)
            if values.get("status") == "finished":
                await self._persist(interview_id, values)
                yield _event("done", {"interview_id": interview_id, "report_ready": True})
        except llm.LLMError as exc:
            yield _event("error", {
                "code": "llm_error", "message": str(exc), "retryable": exc.retryable,
            })
        except GraphRecursionError:
            yield _event("error", {
                "code": "recursion_error", "message": "面试流程超出步数上限，请稍后重试",
            })

    async def _persist(self, interview_id: str, values: dict) -> None:
        """结束后一次落库（SPEC §8）：answers + report + interviews 收尾。"""
        records = [_answers_row(q) for q in values.get("answered_questions") or []]
        await asyncio.to_thread(db.save_answers, self._settings.db_path, interview_id, records)
        await asyncio.to_thread(
            db.save_report, self._settings.db_path, interview_id, values.get("report") or {}
        )
        await asyncio.to_thread(db.finish_interview, self._settings.db_path, interview_id)

    async def get_session(self, interview_id: str) -> dict:
        """UI 恢复数据（SPEC §7）：checkpoint 为权威。"""
        values = await self._current_values(interview_id)
        if not values:
            raise InterviewNotFoundError(interview_id)
        return {
            "interview_id": interview_id,
            "position": values.get("position", ""),
            "phase": values.get("phase", "intro"),
            "status": values.get("status", "running"),
            "answered_count": values.get("answered_count", 0),
            "question_count": values.get("question_count", 0),
            "chat_history": values.get("chat_history", []),
            "report_ready": values.get("status") == "finished",
        }

    async def get_report(self, interview_id: str) -> dict | None:
        return await asyncio.to_thread(db.get_report, self._settings.db_path, interview_id)

    async def list_interviews(self, limit: int = 50) -> list[dict]:
        rows = await asyncio.to_thread(db.list_interviews, self._settings.db_path, limit)
        for row in rows:
            row["report_ready"] = row["status"] == "finished"
        return rows
