"""账号安全原语（FR-23，SPEC §7）：密码哈希 + JWT 编解码。

- 哈希用 stdlib `hashlib.scrypt`（零新增重依赖；同源参数写入存储串，未来提参可平滑升级）；
- JWT 用 PyJWT，HS256 且算法白名单钉死（防算法混淆），强制校验 exp/sub；
- 纯函数、不含 IO 与配置——路由层负责读密钥与落库。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

# 参数取 OWASP 底线：约 16MB/次、~50-100ms 开销，demo 单机可接受。
# 生产（阶段 3 起）提到 2^17 —— 注意需同时传 maxmem（128*n*r ≈ 134MB，超 OpenSSL 默认 32MB 上限）。
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(days=7)  # 单 token，过期重登；不做 refresh（demo 不做静默续期）


def hash_password(password: str) -> str:
    """返回自描述串 `scrypt$n$r$p$salt_hex$digest_hex`（盐随机，同密码两次结果不同）。"""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比对；存储串畸形/算法不识别一律 False（不抛异常，避免探测差异）。"""
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
    except (ValueError, TypeError):  # 字段数不对/hex 非法/参数非数字
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def encode_token(user_id: str, secret: str, *, ttl: timedelta = TOKEN_TTL) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + ttl}, secret, algorithm=ALGORITHM
    )


def decode_token(token: str, secret: str) -> str | None:
    """校验签名与有效期，返回 user_id；任何不合法（含缺 exp/sub）返回 None。"""
    try:
        payload = jwt.decode(
            token, secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
        )
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
