"""把 data/scripts 与 tests/fixtures 加入 sys.path，测试可直接 import 管道脚本与 fake 组件。"""

import sys
from pathlib import Path

import pytest

from app import llm, observability

TESTS_DIR = Path(__file__).resolve().parent
for extra in (TESTS_DIR.parents[1] / "data" / "scripts", TESTS_DIR / "fixtures"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


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
