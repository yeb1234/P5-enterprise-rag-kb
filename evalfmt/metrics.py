"""指标计算（纯函数，零 IO —— 所以可以被单测钉死）。

设计口径先说清楚：**这些是规则级指标，不是模型级评测。**

- 召回率是**文档级**：期望文档有没有出现在 top-k 里，不看分块序号对不对。
  文档级是保守估计——分块级会更低，所以报文档级不会高估。
- 幻觉率是**规则级 grounding**：抽答案里的事实型 token（型号 / 编号 / 错误码 /
  带单位数字），看它是否在召回证据里出现过。它抓不到「把 A 的参数安到 B 头上」
  这类**有证据但归属错**的幻觉，所以陷阱题单独一个指标。
- 没有语义级 judge、没有标注集之外的样本，所以数字只能内部比较，不能对外宣称行业水平。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------- 归一化

_WS = re.compile(r"\s+")

# 字形变体：同一个事实的两种写法，不统一就会被判成「没答对」。
# 实测踩过：期望 `320x210x95`，模型答 `320×210×95`（乘号），被算成答错 ——
# 这是指标缺陷不是模型错误，所以归一化必须覆盖常见字形，而不是放宽到模糊匹配。
_GLYPH = str.maketrans({
    "×": "x", "✕": "x", "✖": "x",
    "－": "-", "–": "-", "—": "-",
    "％": "%", "．": ".", "～": "~", "　": " ",
})


def norm(text: str) -> str:
    """归一化：折叠空白、去零宽、统一大小写与全角括号。

    只做「不影响语义」的处理。**不做**同义词替换或数字改写——
    那会让「24 个月」和「24月」都算命中，进而把幻觉也算成正确。
    """
    if not text:
        return ""
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("（", "(").replace("）", ")")
    text = text.translate(_GLYPH)
    return _WS.sub("", text).lower()


def contains(text: str, needle: str) -> bool:
    if not needle:
        return False
    return norm(needle) in norm(text)


# ---------------------------------------------------------------- 检索指标

def recall_at_k(rank: int | None, k: int) -> float:
    """rank 是「期望文档首次出现的名次（从 1 开始）」，None 表示 top-k 内没出现。"""
    return 1.0 if rank is not None and rank <= k else 0.0


def reciprocal_rank(rank: int | None) -> float:
    return 0.0 if rank is None else 1.0 / rank


@dataclass
class RetrievalScore:
    case_id: str
    doc: str
    rank: int | None
    top1_doc: str
    citation_hit: bool          # top1 分块是否含期望事实

    @property
    def hit(self) -> bool:
        return self.rank is not None


def aggregate_retrieval(scores: list[RetrievalScore]) -> dict:
    n = len(scores) or 1
    return {
        "n": len(scores),
        "recall@1": sum(recall_at_k(s.rank, 1) for s in scores) / n,
        "recall@3": sum(recall_at_k(s.rank, 3) for s in scores) / n,
        "recall@5": sum(recall_at_k(s.rank, 5) for s in scores) / n,
        "mrr": sum(reciprocal_rank(s.rank) for s in scores) / n,
        "citation_precision@1": sum(1.0 for s in scores if s.citation_hit) / n,
        "top1_doc_accuracy": sum(1.0 for s in scores if s.rank == 1) / n,
    }


# ---------------------------------------------------------------- 幻觉指标

# 事实型 token：型号、序列号、错误码、IP 等级、带单位数字。
# 只抽这几类，是因为它们在答案里最容易「看着像真的」却被编出来。
_FACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z]{2,3}-\d{3,6}\b"),              # KX-2400 / SN-88301 / MX-9900
    re.compile(r"\bE\d{4}\b"),                          # E4711
    re.compile(r"\bIP\d{2}\b"),                         # IP54
    re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),  # IP 地址
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:W|kW|V|A|mm|cm|kg|g|ms|s|℃|°C)\b"),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:个月|小时|分钟|天|次|%|度)"),
    re.compile(r"\b\d{4,6}\b"),                         # 端口 18443 / 数量 74119
)


def extract_fact_tokens(answer: str) -> list[str]:
    """抽出答案里的事实型 token，去重保序。"""
    seen: dict[str, None] = {}
    for pat in _FACT_PATTERNS:
        for m in pat.finditer(answer or ""):
            token = m.group(0).strip()
            key = norm(token)
            if key not in seen:
                seen[key] = None
    # 用归一化后的 key 反查原文形态意义不大，直接返回原 token
    out: list[str] = []
    keys: set[str] = set()
    for pat in _FACT_PATTERNS:
        for m in pat.finditer(answer or ""):
            token = m.group(0).strip()
            k = norm(token)
            if k not in keys:
                keys.add(k)
                out.append(token)
    return out


def hallucination(answer: str, evidence: str,
                  question: str = "") -> tuple[float | None, list[str], int]:
    """返回 (幻觉率, 无证据支持的 token 列表, token 总数)。

    token 总数为 0 时返回 None —— 「没做事实断言」不等于「零幻觉」，
    把它算成 0 会系统性拉低幻觉率。

    `question` 里出现过的 token 不算幻觉：复述用户给的实体（MX-9900）
    是必要澄清，不是编造。第一版没排除，导致陷阱题被记成 40% 幻觉 ——
    幻觉的定义是「说出无依据的事实」，不是「提到了一个库里没有的名字」。
    """
    tokens = extract_fact_tokens(answer)
    if not tokens:
        return None, [], 0
    asked = {norm(t) for t in extract_fact_tokens(question)}
    unsupported = [t for t in tokens
                   if norm(t) not in asked and not contains(evidence, t)]
    return len(unsupported) / len(tokens), unsupported, len(tokens)


# 拒答用语：模型承认没找到，而不是硬答
_REFUSAL = re.compile(
    r"未找到|没有找到|未提及|未包含|不包含|无法(?:确定|确认|回答|提供)|"
    r"资料(?:中|里)?(?:没有|未)|知识库(?:中|里)?(?:没有|未)|暂无|抱歉"
)


def refused(answer: str) -> bool:
    return bool(_REFUSAL.search(answer or ""))


def trap_violated(answer: str, fact: str) -> bool:
    """陷阱题判定：把不属于该实体的事实套上去了，且没有任何「无资料」的说明。

    只看「答案里出现了 fact」还不够——正确答案也可能引述「本机型保修 24 个月，
    但 MX-9900 无资料」。所以要求同时**没有**拒答/澄清语气才算违规。
    """
    if not contains(answer, fact):
        return False
    return not refused(answer)


# ---------------------------------------------------------------- 汇总

@dataclass
class AnswerScore:
    case_id: str
    kind: str
    answer: str
    fact_hit: bool
    hallucination_rate: float | None
    unsupported: list[str] = field(default_factory=list)
    fact_tokens: int = 0
    refused: bool = False
    trap_violation: bool = False
    latency_s: float = 0.0


def aggregate_answers(scores: list[AnswerScore]) -> dict:
    factual = [s for s in scores if s.kind == "factual"]
    negative = [s for s in scores if s.kind == "negative"]
    trap = [s for s in scores if s.kind == "trap"]

    graded = [s for s in scores if s.hallucination_rate is not None]
    hall = (sum(s.hallucination_rate for s in graded) / len(graded)) if graded else None
    tokens_total = sum(s.fact_tokens for s in graded)
    tokens_bad = sum(len(s.unsupported) for s in graded)

    return {
        "n": len(scores),
        "answer_accuracy": (
            sum(1.0 for s in factual if s.fact_hit) / len(factual) if factual else None
        ),
        "hallucination_rate": hall,
        "hallucination_tokens": [tokens_bad, tokens_total],
        "hallucination_coverage": len(graded) / len(scores) if scores else 0.0,
        "refusal_accuracy": (
            sum(1.0 for s in negative if s.refused and not s.unsupported) / len(negative)
            if negative else None
        ),
        "trap_violation_rate": (
            sum(1.0 for s in trap if s.trap_violation) / len(trap) if trap else None
        ),
        "avg_latency_s": (
            sum(s.latency_s for s in scores) / len(scores) if scores else 0.0
        ),
    }


def format_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"
