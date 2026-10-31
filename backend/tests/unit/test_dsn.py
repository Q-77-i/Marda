"""DSN 归一化的单元测试（app/dsn.py）。

**为什么要有单测**：这个函数改的是**生产 DSN**——
`DATABASE_URL` / `CHECKPOINT_DATABASE_URL` 默认就写着 `localhost`，
不改的话 Windows 上连接池必然 PoolTimeout（连接池不像裸连接那样回退 IPv4/IPv6）。

判据必须钉死：**只动 host**，用户名/密码/库名/参数里的 localhost 一律不动。
"""

from __future__ import annotations

import pytest

from app.dsn import normalize_dsn


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 标准形态：带用户名密码 + 端口
        (
            "postgresql://marda:marda@localhost:5432/marda",
            "postgresql://marda:marda@127.0.0.1:5432/marda",
        ),
        # 不带用户名密码
        ("postgresql://localhost:5432/marda", "postgresql://127.0.0.1:5432/marda"),
        # 不带端口
        ("postgresql://localhost/marda", "postgresql://127.0.0.1/marda"),
        # 带 query 参数：参数必须原样保留
        (
            "postgresql://u:p@localhost:5432/db?sslmode=require",
            "postgresql://u:p@127.0.0.1:5432/db?sslmode=require",
        ),
        # 已经是 IP：不动
        (
            "postgresql://u:p@127.0.0.1:5432/db",
            "postgresql://u:p@127.0.0.1:5432/db",
        ),
        # 其它主机名：不动（不能把真实主机名改成 IP）
        (
            "postgresql://u:p@db.example.com:5432/db",
            "postgresql://u:p@db.example.com:5432/db",
        ),
        # 容器内主机名（compose 里服务名）：不动！
        ("postgresql://marda:marda@postgres:5432/marda", "postgresql://marda:marda@postgres:5432/marda"),
        # 非 DSN：原样返回
        ("not-a-dsn", "not-a-dsn"),
        ("", ""),
    ],
)
def test_只把host的localhost换成IP(raw: str, expected: str) -> None:
    assert normalize_dsn(raw) == expected


def test_参数值里的localhost不动() -> None:
    """参数里的 localhost 是字面量（路径/选项），不能碰。"""
    raw = "postgresql://u:p@localhost:5432/db?options=-c%20search_path=localhost"
    out = normalize_dsn(raw)
    assert out.startswith("postgresql://u:p@127.0.0.1:5432/db?")
    assert "search_path=localhost" in out


def test_归一化幂等() -> None:
    """归一化后再归一化，结果不变（缓存键用归一化后的 DSN，不能来回变）。"""
    raw = "postgresql://marda:marda@localhost:5432/marda"
    once = normalize_dsn(raw)
    assert normalize_dsn(once) == once
