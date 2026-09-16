"""llm.py 真实 API smoke（手动跑，不进 pytest）。

单测已覆盖重试与校验逻辑，这里只验单测打不到的真实通路：
关 thinking 不 400、`json_schema` 模式被 DeepSeek 接受、返回内容合法。

用法：
  cd backend && uv run python scripts/smoke_llm.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from pydantic import BaseModel, Field  # noqa: E402

from app.llm import chat, chat_json  # noqa: E402

SYSTEM = "你是 Marda（码达）的 AI 面试官，正在面试一位 Agent/AI 工程师岗位的候选人。"


class ScoreSmoke(BaseModel):
    """模拟评分节点输出（T4 会用真的 ScoreItem）。"""

    technical_depth: int = Field(ge=1, le=5, description="技术深度，1-5 整数")
    covered_points: list[str] = Field(description="回答中覆盖到的技术要点")
    comment: str = Field(description="一句话点评")


async def main() -> None:
    print("— chat（文案通路）" + "—" * 20)
    started = time.perf_counter()
    text = await chat(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "请用一句话开场，说明这是一场 Agent 方向的技术面试。"},
        ],
        max_tokens=200,
    )
    print(f"[{time.perf_counter() - started:.2f}s] {text}\n")

    print("— chat_json（结构化通路）" + "—" * 20)
    started = time.perf_counter()
    score = await chat_json(
        [
            {"role": "system", "content": SYSTEM + "现在你是评分官，按给定 schema 评分。"},
            {
                "role": "user",
                "content": (
                    "题目：ReAct 范式的核心思想是什么？\n"
                    "参考答案要点：交替进行推理与行动；推理产生思考，行动调用工具；"
                    "观察结果回灌上下文再进入下一轮。\n\n"
                    "候选人回答：ReAct 就是让模型一边想一边用工具，"
                    "想完决定调哪个工具，拿到结果再想下一步，循环直到能回答。"
                ),
            },
        ],
        schema=ScoreSmoke,
    )
    print(f"[{time.perf_counter() - started:.2f}s] {score.model_dump_json(indent=2, ensure_ascii=False)}")


if __name__ == "__main__":
    asyncio.run(main())
