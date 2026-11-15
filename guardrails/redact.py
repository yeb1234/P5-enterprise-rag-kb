"""脱敏执行器：四种动作 + 可逆 token 保险库 + SSE 流式输出闸。

四个动作：
- `mask`：保留首 3 尾 4，便于人工核对又不可还原；
- `hash`：带盐 HMAC，不可逆但跨请求稳定，适合统计去重；
- `token`：可逆占位符，**默认关闭**，开启后映射本身成为新的泄漏面；
- `block`：命中即拒绝（默认只对 API 密钥启用）。

SSE 逐 token 输出时，一个 18 位身份证可能被拆在多个 chunk 里。
`StreamRedactor` 保留尾部 HOLD 个字符不立即下发，等看到右边界后再扫描并脱敏，
避免「前 10 位已经发出去了」这种无法撤回的泄漏。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import threading
from typing import Iterable

from guardrails.contracts import (ACTION_BLOCK, ACTION_HASH, ACTION_MASK,
                                  ACTION_REMOVE, ACTION_TOKEN, PIIFinding,
                                  RedactionPolicy, RedactionResult)
from guardrails.detectors import scan

HOLD = 24   # 大于最长规则（19 位卡号 / 18 位身份证 + 边界余量）


class TokenVault:
    """可逆映射只放内存，默认关闭；生产必须换加密存储 + 独立权限 + 保留期。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._fwd: dict[str, str] = {}
        self._rev: dict[str, str] = {}
        self._seq = 0

    def put(self, kind: str, value: str) -> str:
        with self._lock:
            key = hmac.new(b"vault", value.encode(), hashlib.sha256).hexdigest()[:12]
            if key not in self._fwd:
                self._seq += 1
                token = f"T-{kind}-{self._seq:04d}"
                self._fwd[key] = token
                self._rev[token] = value
            return self._fwd[key]

    def resolve(self, token: str) -> str | None:
        with self._lock:
            return self._rev.get(token)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._rev)


VAULT = TokenVault()


def _mask(kind: str, value: str, p: RedactionPolicy) -> str:
    if kind == "EMAIL" and "@" in value:
        user, _, domain = value.partition("@")
        head = user[:1] if user else ""
        return f"{head}***@{domain}"
    h, t = p.mask_keep_head, p.mask_keep_tail
    if len(value) <= h + t + 1:
        return f"[已脱敏:{kind}]"
    return value[:h] + "*" * (len(value) - h - t) + value[-t:]


def _hash(value: str, p: RedactionPolicy) -> str:
    digest = hmac.new(p.hash_salt.encode(), value.encode(), hashlib.sha256).hexdigest()[:12]
    return f"[H:{digest}]"


def _replace(kind: str, value: str, p: RedactionPolicy) -> tuple[str, str]:
    action = p.action_for(kind)
    if action == ACTION_TOKEN and p.reversible_token:
        return VAULT.put(kind, value), action
    if action == ACTION_BLOCK:
        return f"[已拦截:{kind}]", action
    if action == ACTION_REMOVE:
        return f"[已移除:{kind}]", action
    if action == ACTION_HASH:
        return _hash(value, p), action
    return _mask(kind, value, p), ACTION_MASK


def redact_text(text: str, *, gate: str, policy: RedactionPolicy | None = None,
                findings: list[PIIFinding] | None = None) -> RedactionResult:
    """对一段文本做脱敏。四个出口共用这一份实现，保证规则一致。"""
    p = policy or RedactionPolicy()
    res = RedactionResult(gate=gate, text=text or "", original_len=len(text or ""))
    if not p.enabled or not text:
        res.redacted_len = len(text or "")
        return res
    hits = findings if findings is not None else scan(text)
    hits = [f for f in hits if f.confidence >= p.min_confidence]
    res.findings = hits
    if not hits:
        res.redacted_len = len(text)
        return res
    out, cursor = [], 0
    for f in hits:
        if f.start < cursor:      # 重叠（正常已由 scan 消解）
            continue
        if p.action_for(f.kind) == ACTION_BLOCK:
            res.blocked = True
            if f.kind not in res.blocked_kinds:
                res.blocked_kinds.append(f.kind)
        out.append(text[cursor:f.start])
        repl, action = _replace(f.kind, text[f.start:f.end], p)
        out.append(repl)
        res.actions[action] = res.actions.get(action, 0) + 1
        cursor = f.end
    out.append(text[cursor:])
    res.text = "".join(out)
    res.redacted_len = len(res.text)
    return res


class StreamRedactor:
    """SSE 逐 token 输出闸：尾部 HOLD 个字符不下发，且跨界的候选串整串扣住。

    只留尾部 HOLD 是不够的：一个 18 位身份证如果起点落在「可以下发」的区域、
    终点还在保留区，按字符切片会把前 10 位先发出去，发出去就撤不回来了。
    所以这里先找出横跨下发边界的连续标识符串（数字/字母/邮箱/工号常见分隔符），
    把下发边界收缩到它的起点，等它完整进入缓冲区后再扫描脱敏。
    """

    # 候选串：数字、字母，以及 API key / 邮箱 / MAC / 工号里常见的分隔符。
    # 中文与中文标点不在其中，天然充当边界。
    _CANDIDATE_RE = re.compile(r"[A-Za-z0-9_.@+\-:]+")

    def __init__(self, policy: RedactionPolicy | None = None):
        self.policy = policy or RedactionPolicy()
        self.buf = ""
        self.hits = 0
        self.held = 0          # 因跨边界被扣住的次数，便于观测
        self.blocked = False   # 流里出现过 block 类命中
        self.by_kind: dict[str, int] = {}

    def _accumulate(self, r: RedactionResult) -> None:
        self.hits += r.hit_count
        self.blocked = self.blocked or r.blocked
        for k, n in r.as_dict()["by_kind"].items():
            self.by_kind[k] = self.by_kind.get(k, 0) + n

    def summary(self) -> dict:
        return {"gate": "output", "hits": self.hits, "by_kind": dict(self.by_kind),
                "held_boundaries": self.held, "blocked": self.blocked}

    def push(self, token: str) -> str:
        self.buf += token
        if len(self.buf) <= HOLD:
            return ""
        boundary = len(self.buf) - HOLD
        min_conf = self.policy.min_confidence

        # 1) 跨界候选串整串扣住，绝不切一半下发
        for m in self._CANDIDATE_RE.finditer(self.buf):
            if m.start() < boundary < m.end():
                boundary = m.start()
                self.held += 1
                break
        if boundary <= 0:
            return ""

        # 2) 只处理完整落在 boundary 之前的命中；未闭合的一律继续等
        hits = [f for f in scan(self.buf[:boundary])
                if f.end <= boundary and f.confidence >= min_conf]
        prefix = self.buf[:boundary]
        if hits:
            r = redact_text(prefix, gate="output", policy=self.policy, findings=hits)
            self._accumulate(r)
            prefix = r.text
        self.buf = self.buf[boundary:]
        return prefix

    def flush(self) -> str:
        r = redact_text(self.buf, gate="output", policy=self.policy)
        self._accumulate(r)
        self.buf = ""
        return r.text


def redact_many(texts: Iterable[str], *, gate: str,
                policy: RedactionPolicy | None = None) -> list[str]:
    return [redact_text(t, gate=gate, policy=policy).text for t in texts]
