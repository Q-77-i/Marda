"""把 data/scripts 与 tests/fixtures 加入 sys.path，测试可直接 import 管道脚本与 fake 组件。"""

import asyncio
import sys
from pathlib import Path

import pytest

from app import llm, observability

TESTS_DIR = Path(__file__).resolve().parent
for extra in (TESTS_DIR.parents[1] / "data" / "scripts", TESTS_DIR / "fixtures"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _force_selector_loop() -> None:
    """Windows 上把事件循环策略换成 Selector（Proactor 不支持 psycopg 的 async 模式）。

    **只在 Windows 生效**：Linux 生产环境的默认策略就是 Selector（Proactor 是 Windows 特有），
    所以这条只影响本机开发/测试，不影响部署。

    为什么要换：Windows 默认的 ProactorEventLoop 不支持 `add_reader` 这类接口，
    psycopg 的**异步**连接直接报
    "Psycopg cannot use the 'ProactorEventLoop' to run in async mode"。
    同步 psycopg 不受影响——所以业务库（同步）在 Windows 上能跑，
    而 **PG checkpointer（异步 saver）在 Windows 上必须换 Selector**。
    """
    if sys.platform != "win32":
        return
    policy = asyncio.get_event_loop_policy()
    if isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# 必须在任何事件循环被创建**之前**换策略（pytest-asyncio 按 policy 建 loop）
_force_selector_loop()


@pytest.fixture(autouse=True)
def _fresh_reliability():
    """清空上游保护器（P2-M9）：闸门/熔断是进程内单例，跨用例串味
    （上一个用例把熔断打开，下一个用例全被快速失败）。"""
    llm.reset_reliability()
    yield
    llm.reset_reliability()


@pytest.fixture(autouse=True)
def _hermetic_langfuse(monkeypatch):
    """测试默认与 Langfuse 断连：断在 `enabled()` 这一层。

    本机 .env 配了真 key 时，集成测试会真的建客户端、真的往云端上报（污染项目数据 + 多出
    网络耗时）。断 `enabled()` 而不是删环境变量：key 在 .env 文件里，pydantic-settings 的
    dotenv 兜在 os.environ 下面，删环境变量删不掉它。也只断这一层——不换 get_settings，
    免得连 `get_settings.cache_clear()` 这类既有用法一起拆掉。

    需要验证接线的用例（test_observability）自己再把 enabled / get_client 覆盖回来。
    """
    monkeypatch.setattr(observability, "enabled", lambda: False)
    yield
    # 别把上一个用例构造的客户端（可能是 drop-in 版）留给下一个用例。用例自己的
    # monkeypatch 此刻还没撤销，_get_client 可能已被换成 lambda，所以先探一下。
    if hasattr(llm._get_client, "cache_clear"):
        llm._get_client.cache_clear()
