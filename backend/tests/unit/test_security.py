"""账号安全原语单测（FR-23，SPEC §7）：密码哈希（stdlib scrypt）+ JWT 编解码。

纯函数、零 IO；app/security.py 不 import db/config，便于单测与替换。
"""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

from app import security

SECRET = "test-secret-0123456789abcdef0123456789"  # ≥32 字节（config 下限）
OTHER_SECRET = "other-secret-0123456789abcdef0123456789"


def test_哈希往返_正确密码通过错误密码拒绝():
    stored = security.hash_password("secret123")
    assert security.verify_password("secret123", stored)
    assert not security.verify_password("secret124", stored)


def test_同一密码两次哈希不同_盐随机():
    assert security.hash_password("secret123") != security.hash_password("secret123")


def test_存储格式自描述():
    scheme, n, r, p, salt, digest = security.hash_password("secret123").split("$")
    assert scheme == "scrypt"
    assert (int(n), int(r), int(p)) == (2**14, 8, 1)
    assert len(bytes.fromhex(salt)) == 16
    assert len(bytes.fromhex(digest)) == 32


@pytest.mark.parametrize(
    "stored",
    ["", "plain", "scrypt$bad", "md5$16384$8$1$aa$bb", "scrypt$16384$8$1$zz$zz"],
)
def test_畸形存储串不抛异常只返回False(stored):
    assert security.verify_password("secret123", stored) is False


def test_token往返():
    assert security.decode_token(security.encode_token("u1", SECRET), SECRET) == "u1"


def test_token过期返回None():
    expired = security.encode_token("u1", SECRET, ttl=timedelta(seconds=-1))
    assert security.decode_token(expired, SECRET) is None


def test_缺exp的token拒绝():
    """手签一个不带 exp 的 token：永不过期是隐患，必须拒绝。"""
    token = jwt.encode({"sub": "u1"}, SECRET, algorithm="HS256")
    assert security.decode_token(token, SECRET) is None


def test_换密钥返回None():
    assert security.decode_token(security.encode_token("u1", SECRET), OTHER_SECRET) is None


def test_篡改返回None():
    token = security.encode_token("u1", SECRET)
    assert security.decode_token(token[:-2] + "xy", SECRET) is None


def test_垃圾token返回None():
    assert security.decode_token("not-a-token", SECRET) is None
