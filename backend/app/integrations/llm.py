"""LLM Provider：外部 DeepSeek（OpenAI 兼容协议），不部署本地模型。

用户口中的 "deepseek v4.1 flash" 对应官方文档里的 model id `deepseek-flash`
(V4.1-Flash-1017)；需要更强推理可切 `deepseek-v4-pro`。模型名放在 .env，
供应商改名时不用改代码。API Key 缺失时降级为「只返回检索片段」，
保证骨架在没有外网、没有密钥的机器上也能完整跑通主链路。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Protocol, runtime_checkable

import httpx

from app.core.config import settings

RAG_SYSTEM_PROMPT = """你是企业内部知识库助手。严格遵循：
1. 只依据提供的【资料片段】作答，不得使用片段之外的知识或猜测。
2. 每个结论后用 [编号] 标注来源，例如 [1]、[2]。
3. 资料不足以回答时，明确说「现有资料无法回答」，并建议联系文档负责人。
4. 不得复述密级信息、密钥、身份证号、手机号等敏感内容。
5. 用简体中文，先给结论，再给依据。"""


@runtime_checkable
class LLMClient(Protocol):
    model_name: str

    def chat(self, messages: list[dict], temperature: float = 0.2) -> str: ...

    def chat_stream(self, messages: list[dict], temperature: float = 0.2) -> Iterator[str]: ...


class DeepSeekClient:
    def __init__(self):
        self.model_name = settings.DEEPSEEK_MODEL
        self.base_url = settings.DEEPSEEK_BASE_URL.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages: list[dict], temperature: float, stream: bool) -> dict:
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        # V4 系列默认开启 thinking；检索问答要的是低延迟，显式关闭
        if settings.DEEPSEEK_THINKING == "disabled":
            payload["thinking"] = {"type": "disabled"}
        return payload

    def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        with httpx.Client(timeout=settings.DEEPSEEK_TIMEOUT_SECONDS) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=self._payload(messages, temperature, False),
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    def chat_stream(self, messages: list[dict], temperature: float = 0.2) -> Iterator[str]:
        with httpx.Client(timeout=settings.DEEPSEEK_TIMEOUT_SECONDS) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=self._payload(messages, temperature, True),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        delta = json.loads(raw)["choices"][0].get("delta", {}).get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta


class ExtractiveFallbackLLM:
    """无密钥 / 外部服务不可用时：不编造答案，直接把召回片段按引用编号列出。"""

    model_name = "extractive-fallback"

    def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        return "".join(self.chat_stream(messages, temperature))

    def chat_stream(self, messages: list[dict], temperature: float = 0.2) -> Iterator[str]:
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        yield "[未配置 LLM 密钥，当前为抽取式降级回答]\n\n"
        for line in user.splitlines():
            if line.strip().startswith("["):
                yield line + "\n"


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        if settings.DEEPSEEK_API_KEY:
            _client = DeepSeekClient()
        elif settings.LLM_FALLBACK_TO_EXTRACTIVE:
            _client = ExtractiveFallbackLLM()
        else:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
    return _client


def build_rag_messages(query: str, citations: list[dict]) -> list[dict]:
    context = "\n\n".join(
        f"[{i + 1}] 文档《{c['document_title']}》第 {c.get('page_no') or '-'} 页\n{c['snippet']}"
        for i, c in enumerate(citations)
    )
    return [
        {"role": "system", "content": RAG_SYSTEM_PROMPT},
        {"role": "user", "content": f"【资料片段】\n{context}\n\n【问题】\n{query}"},
    ]
