"""认证：口令哈希（pbkdf2，标准库）与最小可用 JWT（HMAC-SHA256，标准库）。

说明：这是演示级实现，目的是“拿来即可跑”且便于设计讲解 token 结构；
生产环境应使用框架成熟的 JWT 库 + 强随机盐 + 密钥托管（如 Vault / KMS），
本项目生产架构见 docs/next-version-messaging-and-plugins.md 的鉴权红线。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid

# 演示密钥；生产必须由环境变量注入且禁止默认值。与 backend/app/core/config.py 的口径一致。
JWT_SECRET = os.environ.get(
    "WEBAPP_JWT_SECRET", "dev-only-webapp-secret-change-me-in-prod"
)
JWT_ALG = "HS256"
ACCESS_EXP_MIN = 60 * 12  # 12 小时，演示方便


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def hash_password(pw: str) -> tuple[str, str]:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 100_000)
    return salt, dk.hex()


def verify_password(pw: str, salt: str, expected: str) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 100_000)
    return hmac.compare_digest(dk.hex(), expected)


def make_token(user_id: str, role: str, exp_min: int = ACCESS_EXP_MIN) -> str:
    header = {"alg": JWT_ALG, "typ": "JWT"}
    iat = int(time.time())
    exp = iat + exp_min * 60
    payload = {"sub": user_id, "role": role, "iat": iat, "exp": exp}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64url(hmac.new(JWT_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"


def verify_token(token: str) -> dict | None:
    try:
        h, p, sig = token.split(".")
    except ValueError:
        return None
    expect = _b64url(
        hmac.new(JWT_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(_b64url_decode(p))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def new_id() -> str:
    return uuid.uuid4().hex


def _strip_brackets(addr: str) -> str:
    """把 "[2001:db8::1]" / "[2001:db8::1]:443" 规范成 "2001:db8::1"。"""
    if addr.startswith("["):
        end = addr.find("]")
        if end != -1:
            return addr[1:end].strip()
    return addr


def client_ip(host: str, forwarded_for: str | None, trusted: set[str]) -> str:
    """解析可信客户端 IP（纯函数，可单测；用于按 IP 限流，不可被伪造头绕过）。

    安全要点：
    - 只有直连对端 ``host`` 属于 ``trusted``（本机反代 / 云负载均衡回源地址）时，
      才采信 ``X-Forwarded-For`` 的**第一个**地址（最左 = 真实客户端）；
    - 否则一律用直连 ``host``，客户端自带伪造 XFF 无法换一个限流 key；
    - 多级代理取最左；空白 / 缺失回退 host；``[IPv6]`` / ``[IPv6]:port`` 剥成纯 IPv6；
    - host 缺失时返回 "unknown"，避免空串成为共享桶。
    """
    peer = (host or "").strip()
    if not peer or peer not in trusted or not forwarded_for:
        return peer or "unknown"
    first = forwarded_for.split(",", 1)[0].strip()
    if not first:
        return peer
    return _strip_brackets(first) or peer
