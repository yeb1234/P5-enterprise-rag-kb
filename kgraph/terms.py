"""离线实体抽取：不调 LLM，也能建出一张可检索的图（术语 + 共现关系）。

## 这是什么、不是什么

- **是**：术语共现图。「术语」是在 ≥2 段**不同文本**里反复出现的中文 2–4 字片段、
  或英文/数字标识；「共现」是两个术语出现在同一段文本里，次数是边权。
- **不是**：LLM 抽出来的语义实体关系。像「复位键 —[长按进入]→ 配网模式」这种
  **带类型**的关系要 `extractor=llm`（见 extract_llm.py），代价是每批一次模型调用。

默认走离线，是为了让开关一打开就有东西可查，而不是「打开了但图是空的」。
多跳检索在共现图上同样成立：问题里的术语 → 邻居术语 → 提到邻居的分块。

## 两个容易做错的地方

1. **DF 要按不同文本计**。演示库里同一份手册传过很多遍，若按分块数计词频，
   词频会被放大 N 倍、阈值全部失真。
2. **最长匹配剪枝**。「配网模式」出现几次、「配网」就出现几次，说明「配网」不是
   独立术语，只是更长术语的尾巴——DF 相同且互为子串时，丢短保长。
"""
from __future__ import annotations

import re
from collections import Counter

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]+")

# 虚词与过于泛的词。判据是「出现在任何文档里都不携带主题信息」，
# 不是「不重要」——领域词（配网、计费）一律不进这张表。
# 第二行起是实测补的：问「…出现在哪些分块」时，「出现 / 在哪 / 现在」被当成实体去查图，
# 带回大量噪声。这是演示起始词表，不是语言学结论；真要分词性得上分词器或模型。
STOP_ZH = frozenset("""
我们 你们 他们 它们 自己 这个 那个 这些 那些 一个 一些 可以 如果 然后 因为 所以
但是 而且 或者 以及 就是 不是 没有 进行 使用 通过 对于 关于 以下 以上 其中 其他
之后 之前 之间 时候 情况 方式 问题 注意 不要 建议 即可 已经 还有 这里 那里
什么 怎么 如何 为什么 哪些 是否 能够 需要 应该 可能 例如 比如 根据 按照
出现 在哪 现在 哪里 支持 提供 实现 包括 有关 相关 存在 属于 这种 那种 什么时候
""".split())
STOP_ASCII = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "will", "not",
    "http", "https", "com", "www", "true", "false", "null", "none",
})

_SIZES = (2, 3, 4)
_TWO_CJK = re.compile(r"[\u4e00-\u9fff]{2}")


def _is_two_cjk(name: str) -> bool:
    return _TWO_CJK.fullmatch(name) is not None


def _grams(text: str) -> set[str]:
    """一段文本里的候选术语：中文 2–4 字片段 + 英文/数字标识。

    中文按「连续汉字段」切再取 n-gram，标点、数字、空格天然是边界，不会跨句拼词。
    单个汉字不算术语——歧义太大（「灯」「网」）。
    """
    out: set[str] = set()
    for run in _CJK_RUN.findall(text):
        n = len(run)
        for size in _SIZES:
            if size > n:
                break
            for i in range(n - size + 1):
                g = run[i:i + size]
                if g not in STOP_ZH:
                    out.add(g)
    for tok in _ASCII_TOKEN.findall(text):
        if tok.lower() not in STOP_ASCII:
            out.add(tok)
    return out


def select_entities(chunks: list[dict], *, min_df: int = 2, max_df_ratio: float = 0.9,
                    max_entities: int = 4000) -> list[dict]:
    """选出图里的术语节点。`chunks` = [{chunk_id, text}]，按**不同文本**统计 DF。

    - `min_df=2`：只出现一次的片段无法证明它是术语。
    - `max_df_ratio`：几乎每段文本都有的词区分不了任何东西，丢掉。
    - `max_entities`：名额要**宽**。实测踩过的坑——名额设 300、按词频排序时，
      「文档 / 模型 / 数据」这类高频泛词把名额占满，只出现在 3 种文本里的
      「配网」被直接截掉，于是问「如何给设备配网」一个实体都命中不了。
      泛词的影响不该靠「截掉专门术语」来压制，而是靠最长匹配和边权归一化。
    """
    seen: set[str] = set()
    df: Counter[str] = Counter()
    for c in chunks:
        text = (c.get("text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        df.update(_grams(text))
    if not seen:
        return []
    n = len(seen)
    # 上限不能低于 min_df：小知识库里（比如只有两三份文档）一个术语出现在所有文本里是常态，
    # 不是「泛词」。上限按比例算出不到 min_df 时，等于让 min_df 和 max_df 互相打架，
    # 结果是一个词都选不出来、建出一张空图（单测在小语料上实测到）。
    upper = max(max_df_ratio * n, min_df)

    def wanted(name: str, count: int) -> bool:
        # 二字词的门槛更高（3 次）：跨词二元组是最主要的噪声来源——实测问「账号绑定后能做什么」
        # 命中了「定后」，它来自「绑定后」被切开，纯粹因为碰巧在两段文本里出现。
        # 三字以上的片段本身就是较强的证据，保持 min_df。小语料上再高也不能高过上限。
        need = 3 if (_is_two_cjk(name) and upper >= 3) else min_df
        return need <= count <= upper

    kept = {t: c for t, c in df.items() if wanted(t, c)}

    # 最长匹配剪枝：按长度从长到短处理，已保留的更长术语若「包含它且 DF 相同」就丢掉它。
    # 索引按「术语里出现过的字符」建，不能按前缀建——短词未必在长词的开头
    # （「绑定」在「账号绑定」的末尾），按前缀分桶会漏剪（单测实测到）。
    kept_names: list[str] = []
    by_char: dict[str, list[str]] = {}
    for t in sorted(kept, key=lambda x: (-len(x), x)):
        if any(t != u and t in u and kept[u] == kept[t] for u in by_char.get(t[0], ())):
            continue
        kept_names.append(t)
        for ch in set(t):
            by_char.setdefault(ch, []).append(t)

    kept_names.sort(key=lambda t: (-kept[t], -len(t), t))
    return [{"name": t, "df": kept[t]} for t in kept_names[:max_entities]]


def present(text: str, names: list[str], *, max_per_chunk: int = 64) -> list[str]:
    """一段文本里出现的**全部**术语，不做包含关系剪枝，长的在前，取前 N 个。

    专门给「提及」边用。实测踩过的坑：图里有「配网」这个术语，却查不到任何提到它的分块——
    因为每个含「配网」的分块里都同时有更长的「设备配网」或「完成配网」，按最长匹配
    一剪，「配网」的提及边全没了，成了孤立节点。召回要全，包含关系留给共现边去管。
    """
    low = (text or "").lower()
    found = [nm for nm in names if nm.lower() in low]
    found.sort(key=len, reverse=True)
    return found[:max_per_chunk]


def mentions(text: str, names: list[str], *, max_per_chunk: int = 16) -> list[str]:
    """一段文本里**最有代表性**的术语：最长匹配后的结果，专门给共现边用。

    「配网模式」在场时不再单独记「配网」，否则每个分块都会被短词刷满，
    共现边权被短词主导。上限 16 是为了不让一对多的分块产生成百上千条边。
    """
    return prune_subsumed(present(text, names, max_per_chunk=10 ** 6), max_per_chunk)


def prune_subsumed(found: list[str], limit: int = 10 ** 6) -> list[str]:
    """最长匹配：输入按长度降序的术语，被已保留的更长术语包含的短词丢掉。"""
    out: list[str] = []
    for nm in found:
        ln = nm.lower()
        if any(ln in o.lower() for o in out):
            continue
        out.append(nm)
    return out[:limit]


def co_mentions(mention_lists: list[list[str]]) -> Counter:
    """共现边权：同一分块内两两配对各记一次。无向，键统一按字典序排。"""
    co: Counter = Counter()
    for ms in mention_lists:
        for i in range(len(ms)):
            for j in range(i + 1, len(ms)):
                a, b = sorted((ms[i], ms[j]))
                co[(a, b)] += 1
    return co
