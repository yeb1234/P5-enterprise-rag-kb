"""LLM / Embedding / Rerank 提供者（适配器 seam）。

- 离线实现是默认兜底，零依赖、零网络；
- 外部实现走 OpenAI 兼容端点（Sensenova 代理 DeepSeek V4.1 Flash）；
- 本地实现走 LM Studio（OpenAI 兼容 /v1），支持本地 LLM、Bge M3 嵌入；
- 选择由 `webapp.settings` 决定（可在「模型设置」页切换，即时生效），不碰检索/权限/评测逻辑。

边界说明：LM Studio 当前不暴露 /v1/rerank，Rerank 开启时会探测并「优雅跳过」（返回 None），
不假装用了；换支持 rerank 的服务（Cohere/Jina/自建）即可生效。
"""
from __future__ import annotations

import http.client
import json
import os
import random
import re
import time
from typing import Iterator
from urllib.parse import urlparse

from webapp import settings
from webapp.envfile import load_env

load_env()


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+|\s+|[^\s]", text)


def _conn_for(url: str, timeout: int):
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return http.client.HTTPSConnection(parsed.netloc, timeout=timeout), parsed
    return http.client.HTTPConnection(parsed.netloc, timeout=timeout), parsed


def _post_json(url: str, payload: dict, api_key: str = "", timeout: int = 60) -> tuple[int, dict | str]:
    """非流式 POST，返回 (status, dict|str)。用于 embedding / rerank / models / 测试。"""
    conn, parsed = _conn_for(url, timeout)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        conn.request("POST", parsed.path + ("?" + parsed.query if parsed.query else ""),
                     body=json.dumps(payload).encode("utf-8"), headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, json.loads(raw)
        except Exception:
            return resp.status, raw
    finally:
        conn.close()


def _get_json(url: str, api_key: str = "", timeout: int = 10) -> tuple[int, dict | str]:
    conn, parsed = _conn_for(url, timeout)
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        conn.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""), headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, json.loads(raw)
        except Exception:
            return resp.status, raw
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 429 细分：能重试 vs 不能重试
# --------------------------------------------------------------------------
def classify_429(raw: str) -> str:
    """把 429 细分为 'quota'（额度/墨点耗尽，不能重试）或 'rate'（频率超限，可退避重试）。

    依据 TokenPlan 文档：rate_limit_exceeded 可退避；quota_exceeded 不应自动重试。
    Anthropic 兼容接口只有 error.type=rate_limit_error，此时看正文里是否写了 quota。
    """
    code = ""
    try:
        obj = json.loads(raw)
        err = obj.get("error") if isinstance(obj.get("error"), dict) else {}
        code = str(err.get("code") or err.get("type") or obj.get("code") or "")
    except Exception:
        pass
    blob = (code + " " + (raw or "")).lower()
    return "quota" if "quota_exceeded" in blob or "quota" in blob and "insufficient" in blob else "rate"


def backoff_seconds(attempt: int, retry_after: float | None = None) -> float:
    """指数退避 1/2/4 秒 + 抖动；服务端给了 Retry-After 就听它的（上限 30s）。

    抖动是为了避免多个 worker 在限流恢复瞬间同时打过去（惊群）。
    """
    if retry_after is not None and retry_after >= 0:
        return min(retry_after, 30.0)
    return min(2 ** attempt, 8) + random.uniform(0, 0.5)


# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------
class LLMProvider:
    model_label = "base"

    def stream(self, question: str, retrieved: list[dict]) -> Iterator[str]:
        raise NotImplementedError

    def chat(self, question: str, retrieved: list[dict]) -> str:
        return "".join(self.stream(question, retrieved))

    def complete(self, prompt: str, system: str | None = None,
                 max_tokens: int = 256, temperature: float = 0.0) -> str:
        """非流式补全，用于 LLM-as-reranker 等内部调用。"""
        raise NotImplementedError


class OfflineLLMProvider(LLMProvider):
    """离线占位：把检索片段拼成答案后逐字流出，确定性、可断线重放。"""

    model_label = "offline-stub"

    def stream(self, question: str, retrieved: list[dict]) -> Iterator[str]:
        answer, _, _ = _offline_answer(question, retrieved)
        for tok in _tokenize(answer):
            yield tok

    def complete(self, prompt: str, system: str | None = None,
                 max_tokens: int = 256, temperature: float = 0.0) -> str:
        return ""  # 离线占位不具备重排能力


def _offline_answer(question: str, retrieved: list[dict]) -> tuple[str, bool, str | None]:
    if not retrieved:
        return (
            "抱歉，我在当前知识库中没有找到与您问题相关的内容。"
            "您可以尝试上传相关文档，或更换更具体的关键词。",
            True,
            "retrieval_empty",
        )
    parts = []
    for i, item in enumerate(retrieved, 1):
        snippet = item["text"].strip().replace("\n", " ")
        parts.append(f"【参考 {i} · {item['doc_title']}】{snippet}")
    answer = (
        f"根据已上传的知识库内容，针对“{question}”的回答如下：\n\n"
        + "\n\n".join(parts)
        + "\n\n（演示为离线占位生成，仅拼接检索片段；生产将替换为 DeepSeek 流式接口。）"
    )
    return answer, False, None


_THINK_OPEN = ("<mm:think>", " thinking")
_THINK_CLOSE = ("</mm:think>", "</think>")


def _find_any(text: str, tags: tuple[str, ...]) -> tuple[int, str]:
    """返回最早出现的标记位置与标记本身；都不在则 (-1, "")。"""
    best, hit = -1, ""
    for t in tags:
        i = text.find(t)
        if i >= 0 and (best < 0 or i < best):
            best, hit = i, t
    return best, hit


def _partial_marker_len(text: str, markers: tuple[str, ...]) -> int:
    """text 尾部有多少字符可能是某个标记的前缀（用于跨 chunk 的标记拼接）。"""
    best = 0
    for m in markers:
        for k in range(1, len(m)):
            if text.endswith(m[:k]):
                best = max(best, k)
    return best


def _filter_inline_think(chunks: Iterator[str]) -> Iterator[str]:
    """剥掉 content 里内联的思考块（minimax 的 <mm:think>、部分模型（含本地）的  thinking）。

    流式下标记可能被切在两个 chunk 之间：只在「尾部看起来像标记前缀」时暂留几个字符，
    正常文本几乎不延迟；思考内容整段丢弃。
    """
    buf = ""
    inside = False
    for chunk in chunks:
        if not chunk:
            continue
        buf += chunk
        while True:
            if inside:
                pos, tag = _find_any(buf, _THINK_CLOSE)
                if pos < 0:
                    n = _partial_marker_len(buf, _THINK_CLOSE)
                    buf = buf[len(buf) - n:] if n else ""
                    break
                buf = buf[pos + len(tag):]
                inside = False
                continue
            pos, tag = _find_any(buf, _THINK_OPEN)
            if pos < 0:
                n = _partial_marker_len(buf, _THINK_OPEN)
                emit = buf[: len(buf) - n] if n else buf
                if emit:
                    yield emit
                buf = buf[len(buf) - n:] if n else ""
                break
            if pos > 0:
                yield buf[:pos]
            buf = buf[pos + len(tag):]
            inside = True
    if not inside and buf:
        yield buf


def thinking_fields(*, disabled: bool) -> dict:
    """低思考档要注入的请求体字段，与 backend/app/integrations/llm.py:52 同一口径。

    settings.thinking_disabled() 决定「是否低思考」，本函数只决定「低思考发什么字段」，
    让流式 / 非流式 / 连接测试三条路径共用一份 payload 定义。
    诚实边界：Discovery 网关是否识别 thinking 字段尚未实测。OpenAI 兼容端点一般忽略
    未知字段；不识别时靠 _filter_inline_think() 剥掉内联思考块，并用首 token 延迟与
    reasoning_tokens 观测判断，不假装「已经关闭思考」。
    """
    return {"thinking": {"type": "disabled"}} if disabled else {}


class OpenAILikeLLMProvider(LLMProvider):
    """OpenAI 兼容端点（外部 Sensenova / Discovery 网关 / 本地 LM Studio）。

    流式走 SSE（data: {...}\\n\\n，以 data: [DONE] 结束）；标准库 http.client，零额外依赖。
    api_key 为空时不带 Authorization（LM Studio 默认不校验）。
    """

    def __init__(self, base_url: str, api_key: str, model: str,
                 thinking_disabled: bool = True):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.model_label = model
        self.thinking_disabled = thinking_disabled

    def _extras(self) -> dict:
        # 构造时已定档，不在这里重读全局配置（便于单测锁定，也避免流中途配置变化）
        return thinking_fields(disabled=self.thinking_disabled)

    def _messages(self, question: str, retrieved: list[dict]) -> list[dict]:
        ctx = []
        for i, item in enumerate(retrieved, 1):
            ctx.append(f"[参考 {i} · {item['doc_title']}]\n{item['text'].strip()}")
        system = (
            "你是企业知识库的问答助手。只能依据下面提供的【参考】内容回答，"
            "不得编造知识库之外的信息；若参考中没有答案，明确说明“知识库中未找到相关内容”，"
            "并建议用户补充文档。回答简洁、用中文，可适度引用参考编号。"
        )
        if ctx:
            user = f"用户问题：{question}\n\n【参考内容】\n" + "\n\n".join(ctx)
        else:
            user = f"用户问题：{question}\n（当前知识库无可用参考内容）"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def stream(self, question: str, retrieved: list[dict]) -> Iterator[str]:
        # 个别模型（如 minimax-m3）把思考过程以 <mm:think>…</mm:think> 内联在 content 里，
        # 必须剥掉，否则用户会看到一堆英文思考。
        yield from _filter_inline_think(self._stream_raw(question, retrieved))

    def _stream_raw(self, question: str, retrieved: list[dict]) -> Iterator[str]:
        messages = self._messages(question, retrieved)
        payload = {"model": self.model, "messages": messages, "stream": True,
                   "temperature": 0.3}
        payload.update(self._extras())  # 低思考档：显式关闭 V4 系列的 thinking
        body = json.dumps(payload).encode("utf-8")
        path = urlparse(self.base_url).path + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last_err = None
        for attempt in range(3):  # 429/5xx 退避重试（仅限「还没开始流式」的阶段）
            conn, parsed = _conn_for(self.base_url, 120)
            try:
                conn.request("POST", path, body=body, headers=headers)
                resp = conn.getresponse()
                if resp.status in (429, 500, 502, 503):
                    raw = resp.read().decode("utf-8", "replace")
                    retry_after = None
                    try:
                        ra = resp.getheader("Retry-After")
                        if ra:
                            retry_after = float(ra)
                    except Exception:
                        pass
                    if resp.status == 429 and classify_429(raw) == "quota":
                        # 额度/墨点耗尽：重试只会白烧时间，直接失败并给出可读原因
                        raise RuntimeError(
                            "quota_exceeded: 网关额度或墨点已用尽，不自动重试；"
                            "请稍后重试、换模型或换供应商"
                        )
                    wait = backoff_seconds(attempt, retry_after)
                    last_err = f"HTTP {resp.status}，第 {attempt + 1} 次退避 {wait:.1f}s"
                    time.sleep(wait)
                    continue
                if resp.status != 200:
                    data = resp.read().decode("utf-8", "replace")[:500]
                    raise RuntimeError(f"LLM HTTP {resp.status}: {data}")
                fp = getattr(resp, "fp", None)
                if fp is None:
                    lines = resp.read().decode("utf-8", "replace").splitlines()
                else:
                    lines = iter(fp.readline, b"")
                for raw in lines:
                    line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else raw.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue
                    # 有的网关（如 Intern-AI Discovery）会在结尾发 choices: [] 的纯 usage 块，
                    # 直接取 [0] 会 IndexError，把正常回答误判成 model_error。
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
                # 走到这里说明流已正常结束。注意：一旦开始 yield 就不再做重试，
                # 否则用户会看到重复内容（TokenPlan 文档明确要求）。
                return
            finally:
                conn.close()
        raise RuntimeError(f"LLM 调用失败（含限流重试）：{last_err}")

    def complete(self, prompt: str, system: str | None = None,
                 max_tokens: int = 256, temperature: float = 0.0) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "temperature": temperature, "max_tokens": max_tokens}
        payload.update(self._extras())
        status, data = _post_json(
            self.base_url + "/chat/completions", payload,
            self.api_key, timeout=90,
        )
        if status == 429 and classify_429(json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data)) == "quota":
            raise RuntimeError("quota_exceeded: 网关额度或墨点已用尽，不自动重试")
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(f"LLM complete HTTP {status}: {str(data)[:200]}")
        choices = data.get("choices") or [{}]
        msg = choices[0].get("message", {})
        return msg.get("content") or msg.get("reasoning_content") or ""


# --------------------------------------------------------------------------
# Embedding
# --------------------------------------------------------------------------
class EmbeddingProvider:
    name = "base"
    dimension = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class OfflineEmbeddingProvider(EmbeddingProvider):
    """离线占位：不产出真实向量，检索走词法（见 rag.retrieve）。"""

    name = "offline-lexical"
    dimension = 0


class OpenAILikeEmbeddingProvider(EmbeddingProvider):
    """OpenAI 兼容 /v1/embeddings：本地 LM Studio 与硅基流动 BAAI/bge-m3 共用。

    两者请求/响应结构完全一致，差别只在 base_url / model / 是否需要 Bearer key。
    保留 name 前缀（local: / external:）作为向量缓存命名空间，避免跨模型复用缓存。
    """

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 batch: int = 32, name: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.batch = batch
        self.name = name or f"embedding:{model}"
        self.dimension = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i:i + self.batch]
            status, data = _post_json(
                self.base_url + "/embeddings",
                {"model": self.model, "input": chunk, "encoding_format": "float"},
                self.api_key,
                timeout=120,
            )
            if status != 200 or not isinstance(data, dict) or "data" not in data:
                raise RuntimeError(f"embedding HTTP {status}: {str(data)[:200]}")
            items = sorted(data["data"], key=lambda x: x.get("index", 0))
            if len(items) != len(chunk):
                raise RuntimeError(f"embedding 返回条数不符：期望 {len(chunk)}，实得 {len(items)}")
            for it in items:
                vec = it.get("embedding") or []
                if not vec:
                    raise RuntimeError("embedding 返回空向量")
                # 支持 base64 形态（部分网关默认返回）：这里统一要求 float，显式声明了 encoding_format
                if not isinstance(vec[0], (int, float)):
                    raise RuntimeError("embedding 返回非数值向量（检查 encoding_format）")
                self.dimension = len(vec)
                out.append(vec)
        return out


# 向后兼容：旧代码/文档里用 LocalEmbeddingProvider 指代本地嵌入实现。
LocalEmbeddingProvider = OpenAILikeEmbeddingProvider


# --------------------------------------------------------------------------
# 提供者装配（读运行时设置）
# --------------------------------------------------------------------------
def get_llm(cfg: dict | None = None) -> LLMProvider:
    cfg = cfg or settings.get_all()
    kind = cfg.get("llm_provider", "offline")
    low_thinking = settings.thinking_disabled(cfg)
    if kind == "local":
        return OpenAILikeLLMProvider(
            cfg.get("local_llm_base_url", ""), "", cfg.get("local_llm_model", ""),
            thinking_disabled=low_thinking,
        )
    if kind == "sensenova":
        return OpenAILikeLLMProvider(
            os.environ.get("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1"),
            os.environ.get("SENSENOVA_API_KEY", ""),
            cfg.get("sensenova_model") or os.environ.get("SENSENOVA_MODEL", "deepseek-flash"),
            thinking_disabled=low_thinking,
        )
    if kind == "discovery":
        # Intern-AI Discovery 网关：同一 key 下多家模型（deepseek / glm / kimi / minimax / qwen / vision）
        return OpenAILikeLLMProvider(
            os.environ.get("DISCOVERY_BASE_URL", "https://discovery-api.intern-ai.org.cn/v1"),
            os.environ.get("DISCOVERY_API_KEY", ""),
            cfg.get("discovery_model") or os.environ.get("DISCOVERY_MODEL", "deepseek-v4.1-flash-1017"),
            thinking_disabled=low_thinking,
        )
    return OfflineLLMProvider()


def get_thinking_mode(cfg: dict | None = None) -> str:
    """给设置页 / SSE meta 展示的档位标签，不改动 get_model_label() 的返回值。"""
    return "低思考 · 快速" if settings.thinking_disabled(cfg) else "完整推理"


def get_model_label(cfg: dict | None = None) -> str:
    cfg = cfg or settings.get_all()
    kind = cfg.get("llm_provider", "offline")
    if kind == "local":
        return "local:" + cfg.get("local_llm_model", "")
    if kind == "sensenova":
        return cfg.get("sensenova_model") or os.environ.get("SENSENOVA_MODEL", "deepseek-flash")
    if kind == "discovery":
        return cfg.get("discovery_model") or os.environ.get("DISCOVERY_MODEL", "deepseek-v4.1-flash-1017")
    return "offline-stub"


def get_embedding(cfg: dict | None = None) -> EmbeddingProvider:
    eff = settings.effective_embedding(cfg)
    if eff["kind"] == "offline":
        return OfflineEmbeddingProvider()
    return OpenAILikeEmbeddingProvider(
        eff["base_url"], eff["model"], eff["api_key"], name=eff["name"]
    )


def get_embedding_label(cfg: dict | None = None) -> str:
    return settings.effective_embedding(cfg)["name"]


# --------------------------------------------------------------------------
# 探测 / 测试 / rerank
# --------------------------------------------------------------------------
def probe_models(base_url: str, api_key: str = "") -> list[str]:
    status, data = _get_json(base_url.rstrip("/") + "/models", api_key, timeout=8)
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"HTTP {status}: {str(data)[:160]}")
    return [m.get("id") for m in data.get("data", []) if m.get("id")]


def test_llm(base_url: str, model: str, api_key: str = "",
             thinking_disabled: bool = True) -> dict:
    """连接测试：给足 token 预算，避免推理模型把 max_tokens 全花在 thinking 上导致正文为空。"""
    t0 = time.time()
    payload = {"model": model, "messages": [{"role": "user", "content": "只回复两个字：你好"}],
               "stream": False, "max_tokens": 256}
    payload.update(thinking_fields(disabled=thinking_disabled))
    status, data = _post_json(
        base_url.rstrip("/") + "/chat/completions", payload,
        api_key,
        timeout=90,
    )
    ms = int((time.time() - t0) * 1000)
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"HTTP {status}: {str(data)[:200]}")
    choices = data.get("choices") or [{}]
    msg = choices[0].get("message", {})
    content = (msg.get("content") or "").strip()
    usage = data.get("usage") or {}
    reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    return {
        "ok": True,
        "latency_ms": ms,
        "content": (content or msg.get("reasoning_content") or "")[:80],
        "content_chars": len(content),
        "reasoning_tokens": reasoning_tokens,
        # 推理模型在 token 预算不足时可能只吐 thinking：如实标注，不让「可用」掩盖空正文
        "reasoning_only": bool(content == "" and (msg.get("reasoning_content") or "")),
        "thinking_mode": "低思考 · 快速" if thinking_disabled else "完整推理",
        # 只说明「请求里带了什么」，不断言网关一定生效（未实测）。
        "thinking_field_sent": "thinking=disabled" if thinking_disabled else "",
        # usage 里是否观察到推理 token：仅作证据，不等于网关确认支持该字段。
        "low_thinking_evidence": (
            "reasoning_tokens_present" if reasoning_tokens else
            "no_reasoning_tokens" if reasoning_tokens == 0 else "not_reported"
        ),
    }


def test_embedding(base_url: str, model: str, api_key: str = "") -> dict:
    t0 = time.time()
    status, data = _post_json(
        base_url.rstrip("/") + "/embeddings",
        {"model": model, "input": ["设备配网步骤"], "encoding_format": "float"},
        api_key, timeout=60,
    )
    ms = int((time.time() - t0) * 1000)
    if status != 200 or not isinstance(data, dict) or "data" not in data:
        raise RuntimeError(f"HTTP {status}: {str(data)[:200]}")
    vec = data["data"][0].get("embedding") or []
    return {"ok": True, "latency_ms": ms, "dim": len(vec)}


def _apply_rerank_items(items, n: int) -> list[float] | None:
    if not isinstance(items, list) or not items:
        return None
    scores = [0.0] * n
    seen = False
    for r in items:
        if not isinstance(r, dict):
            continue
        idx = r.get("index")
        sc = r.get("relevance_score", r.get("score"))
        if isinstance(idx, int) and 0 <= idx < n and sc is not None:
            scores[idx] = float(sc)
            seen = True
    return scores if seen else None


def rerank(base_url: str, model: str, query: str, documents: list[str], api_key: str = "") -> list[float] | None:
    """调用 /rerank，自动兼容两种形态；不可用返回 None（调用方优雅跳过）。

    - Cohere / Jina / SiliconFlow：{model, query, documents} → {results:[{index, relevance_score}]}
    - TEI（text-embeddings-inference）：{query, texts} → [{index, score}]
    """
    if not documents:
        return None
    base = base_url.rstrip("/")
    n = len(documents)
    try:  # Cohere-style
        status, data = _post_json(
            base + "/rerank",
            {"model": model, "query": query, "documents": documents},
            api_key, timeout=30,
        )
        if status == 200 and isinstance(data, dict) and "error" not in data:
            s = _apply_rerank_items(data.get("results") or data.get("data"), n)
            if s is not None:
                return s
    except Exception:
        pass
    try:  # TEI-style
        status, data = _post_json(
            base + "/rerank",
            {"query": query, "texts": documents, "raw_scores": False},
            api_key, timeout=30,
        )
        if status == 200 and isinstance(data, list):
            s = _apply_rerank_items(data, n)
            if s is not None:
                return s
    except Exception:
        pass
    return None


def _extract_json_array(text: str):
    if not text:
        return None
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def rerank_with_llm(question: str, documents: list[str], llm: LLMProvider) -> list[float] | None:
    """LLM-as-reranker：让当前 LLM 给候选打分（离线/失败返回 None，由调用方跳过）。"""
    if not documents or isinstance(llm, OfflineLLMProvider):
        return None
    listing = "\n".join(f"[{i}] {d.strip()[:280]}" for i, d in enumerate(documents))
    prompt = (
        f"问题：{question}\n\n候选文档：\n{listing}\n\n"
        "请评估每个候选与问题的相关性，给出 0-10 的分数（越高越相关）。"
        "只输出 JSON 数组，例如 [{\"index\":0,\"score\":8}]，不要输出多余文字。"
    )
    try:
        txt = llm.complete(prompt, system="你是检索重排器，只输出 JSON。",
                           max_tokens=400, temperature=0.0)
    except Exception:
        return None
    arr = _extract_json_array(txt)
    if not arr:
        return None
    scores = [0.0] * len(documents)
    seen = False
    for it in arr:
        if not isinstance(it, dict):
            continue
        try:
            i = int(it.get("index"))
            s = float(it.get("score"))
        except Exception:
            continue
        if 0 <= i < len(scores):
            scores[i] = s
            seen = True
    return scores if seen else None
