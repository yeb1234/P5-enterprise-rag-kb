"""LLM Provider：外部 DeepSeek（OpenAI 兼容协议），不部署本地模型。

用户口中的 "deepseek v4.1 flash" 对应官方文档里的 model id `deepseek-flash`
(V4.1-Flash-1017)；需要更强推理可切 `deepseek-v4-pro`。模型名放在 .env，
供应商改名时不用改代码。API Key 缺失时降级为「只返回检索片段」，
保证骨架在没有外网、没有密钥的机器上也能完整跑通主链路。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Protocol, runtime_checkable

import httpx

from app.core.config import settings

RAG_SYSTEM_PROMPT = """你是企业内部知识库助手。严格遵循：
1. 只依据提供的【资料片段】作答，不得使用片段之外的知识或猜测。
2. 每个结论后用 [编号] 标注来源，例如 [1]、[2]。
3. 资料能支持一部分时，先回答能支持的部分并标注依据；只有片段完全没有相关事实时，才说「现有资料无法回答」，并说明缺哪一条依据。
4. 不得复述密级信息、密钥、身份证号、手机号等敏感内容。
5. 只保留资料直接支持的事实，不补充常识、建议、免责声明或延伸解释。
6. 使用与用户问题相同的语言：中文问题用简体中文，英文问题用英文；专有名词可保留原文。
7. 地名、机构名、菜单项、按钮、命令、数字、时间等专有信息优先沿用资料原文措辞；资料用分数或精确表达式给出时，必须保留原表达式，可在括号内另附近似值，不要只写换算后的小数。
8. 问题在问推荐、名单、示例，或问“什么书/哪本书/哪些书/哪个好”时，片段中直接出现的书名、产品名、型号就是可支持答案；不要因为片段没有复述问题的关系词、没有评价词或没有直接说“好/推荐”，就整题拒答。
9. 先给结论，再给依据；每个事实尽量用一句短句表达；不要为了显得完整而补步骤或建议，也不要在答案末尾追加“资料不完整/缺少步骤/无法判断”的说明。
10. 不要在答案里写“现有资料只能支持/现有资料未提供/资料没有出现/不能据此判断”之类的完整性说明；这些不是资料中的事实。能答就只写能答的事实，整题完全无相关事实时才拒答。
11. 不要把“问题中的地区、学校、范围等限定词未在片段出现”当作拒答理由；片段出现与问题主题对应的时间、地点、型号、书名或数字时，直接给出该事实并标注依据，不要写“未涉及某地区/无法确认范围”之类的说明。"""


def detect_question_language(query: str) -> str:
    """问题语言只由问题文本决定，不跟随资料片段的语言。

    中文题里有英文产品名、英文题里有中文专名都很常见；按字符占比判断，
    避免一个英文缩写把整段回答拽成英文。
    """
    cjk = len(re.findall(r"[\u4e00-\u9fff]", query))
    latin = len(re.findall(r"[A-Za-z]", query))
    if cjk >= 2 and cjk >= latin / 2:
        return "zh"
    if latin >= 2:
        return "en"
    return "zh"


LANGUAGE_INSTRUCTIONS = {
    "zh": "用户问题是中文。请用简体中文回答；专有名词可保留原文。",
    "en": "The user's question is in English. Answer in English; keep citation numbers and proper nouns as needed.",
}



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
    language = detect_question_language(query)
    return [
        {"role": "system", "content": RAG_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"【回答语言】{LANGUAGE_INSTRUCTIONS[language]}\n\n"
                f"【资料片段】\n{context}\n\n【问题】\n{query}"
            ),
        },
    ]
