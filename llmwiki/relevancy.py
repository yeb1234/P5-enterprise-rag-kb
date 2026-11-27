"""按业务场景筛信源：只把和场景有关的分块送去整理。

## 为什么需要这一步

实测：场景填的是「智能设备使用与故障排查」，提示词里也写了「与场景无关的分块直接忽略」，
模型仍然把知识库项目自己的设计文档整理成了 24 页——业务场景成了一个标签，没有约束力。

Wiki 的三道门禁里，「场景」和「信源」是一回事的两面：场景决定**哪些资料算信源**。
所以信源不能是「库里全部 ready 文档」，而应当是「和场景有关的那些文档」。

## 怎么判「有关」

场景文本切成和检索同一口径的词（中文二元组 + 英文/数字整词），再筛掉**没有区分度**的词：
几乎每段文本都有的词（「使用」）指不出任何东西。只保留在语料里出现得足够少的场景词，
分块含其中任意一个就算有关。

场景里一个有区分度的词都没有时，退回全量并写明原因——不假装筛过。
"""
from __future__ import annotations

from collections import Counter

from llmwiki.retriever import terms


def select_by_scenario(scenario: str, chunks: list[dict], *,
                        max_df_ratio: float = 0.2) -> dict:
    """返回 {chunks, mode, terms, kept, total}。

    `max_df_ratio`：场景词在超过这个比例的不同文本里出现，视为没有区分度
    （另要求至少出现 4 次，见函数内注释）。词频只按**不同文本**计（演示库里重复文档很多）。
    """
    seen: set[str] = set()
    df: Counter[str] = Counter()
    for c in chunks:
        text = (c.get("text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        df.update(terms(text))
    n = len(seen) or 1
    wanted = terms(scenario)

    def discriminating(term: str) -> bool:
        # 「没有区分度」要同时满足：至少出现 4 次、且占比超过 max_df_ratio。
        # 小语料下（库里只有两三种文本）一个场景词出现在其中两三种里是常态，不是泛词——
        # 只按比例判会把它筛掉，结果场景里一个强词都不剩（实测：单独跑测试时库里只有 3 种文本，
        # 占比 67% 的锚词被当成泛词，信源筛错）。
        count = df[term]
        return count > 0 and not (count >= 4 and count > max_df_ratio * n)

    strong = sorted(t for t in wanted if discriminating(t))
    if not strong:
        return {"chunks": list(chunks), "mode": "all", "terms": [],
                "kept": len(chunks), "total": len(chunks),
                "reason": "场景里的词在语料里没有命中"}

    kept = [c for c in chunks if strong and terms(c.get("text") or "") & set(strong)]
    if not kept:                                   # 理论上不会发生：强词至少来自某段文本
        return {"chunks": list(chunks), "mode": "all", "terms": strong,
                "kept": len(chunks), "total": len(chunks), "reason": "没有分块命中场景词"}
    return {"chunks": kept, "mode": "scenario", "terms": strong,
            "kept": len(kept), "total": len(chunks), "reason": None}
