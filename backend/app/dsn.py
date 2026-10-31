"""DSN 归一化（业务库 / checkpointer 共用一份实现）。

**要解决的坑（Windows 专有，但生产环境同样会踩）**：
``localhost`` 在 Windows 上优先解析成 IPv6 ``::1``，而 PG 发布的端口通常只监听 IPv4。

- ``psycopg.connect``（裸连接）会依次尝试 A/AAAA 并**回退** → 一切正常；
- ``psycopg_pool.ConnectionPool`` / ``AsyncConnectionPool`` **不回退** ——
  只在 ``::1`` 上试一次就报
  ``PoolTimeout: couldn't get a connection after 10.00 sec``。

报出来的是「连不上 PG」，极具误导性（实测：同一个 DSN，裸连 OK、池必挂；
把 host 换成 ``127.0.0.1`` 池立刻正常）。这不是测试专用问题——生产形态的
``DATABASE_URL`` / ``CHECKPOINT_DATABASE_URL`` 默认就写着 ``localhost``。

所以：**凡是建池的地方，DSN 都要先过这里**。
"""

from __future__ import annotations


def normalize_dsn(dsn: str) -> str:
    """把 DSN 里的 ``localhost`` 换成 ``127.0.0.1``（**只动 host**）。

    - 只改 host 部分：用户名/密码/库名/参数里的 localhost 一律不动
      （参数值可能是路径或字面量，不能碰）；
    - 保留 scheme、userinfo、端口、query 参数；
    - 不是 DSN（没有 ``://``）就原样返回。

    >>> normalize_dsn("postgresql://u:p@localhost:5432/db")
    'postgresql://u:p@127.0.0.1:5432/db'
    >>> normalize_dsn("postgresql://localhost/db")
    'postgresql://127.0.0.1/db'
    >>> normalize_dsn("postgresql://u:p@db.example.com:5432/db")
    'postgresql://u:p@db.example.com:5432/db'
    """
    if "://" not in dsn:
        return dsn
    scheme, _, rest = dsn.partition("://")
    if not rest:
        return dsn
    # 有 '@' 才把它当 userinfo；否则整段就是 host 部分。
    # （坑：`rpartition` 找不到 '@' 时会把**整段**塞进第一个返回值，
    #   所以必须用 `at` 判断分隔符是否真的存在。）
    userinfo, at, hostpart = rest.rpartition("@")
    if not at:
        hostpart, userinfo = rest, ""
    # host 与「路径（库名）」之间用 '/' 分开：
    # `postgresql://localhost/marda` 的 host 是 `localhost`，路径是 `/marda`。
    # （不分的话 host 会变成 `localhost/marda`，替换就匹配不上。）
    host, slash, path = hostpart.partition("/")
    if host.startswith("localhost:"):
        host = "127.0.0.1:" + host[len("localhost:"):]
    elif host == "localhost":
        host = "127.0.0.1"
    rebuilt = f"{scheme}://"
    if userinfo:
        rebuilt += userinfo + "@"
    rebuilt += host + (slash + path if slash else "")
    return rebuilt
