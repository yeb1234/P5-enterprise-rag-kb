"""Cypher 相关的纯函数：只读校验、从模型输出里取语句、手写模板、Text2Cypher 提示词。

## 为什么自己写的 Text2Cypher 必须带防护

Text2Cypher 的本质是「把自然语言变成一条可执行语句」，模型一旦写出
`MATCH (n) DETACH DELETE n`，共享的图库就没了。所以执行前过三道：

1. **关键词校验**：出现任何写操作关键词直接拒绝（第 3 位：CREATE / MERGE / DELETE / SET …）；
2. **EXPLAIN 预检**：交给 Neo4j 先编译一遍，语法错误、未知函数在这里暴露，不碰数据；
3. **只读会话**：真正执行时用 READ 会话，驱动层再拒一次写操作。

再加两条与业务相关的硬要求：
- 语句里必须出现 `$kb`——所有节点都带命名空间属性，不带上它就可能读到别的项目的数据；
- 结果必须有 `chunk_id` 列并带 `LIMIT`——没有 chunk_id 就回不到 PG 的分块，结果不可用。

（neo4j-graphrag 从 1.16 起也给 Text2CypherRetriever 加了 EXPLAIN 防护，思路一致。）
"""
from __future__ import annotations

import re

MAX_CYPHER_LEN = 2000
DEFAULT_LIMIT = 50

# 词边界匹配，避免 OFFSET / RESET 这类标识符被误伤。
_WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|CALL|FOREACH|USE|IMPORT|GRANT|"
    r"DENY|REVOKE|ALTER|ADMIN|TERMINATE|ENABLE|REALLOCATE|STOP|START|RENAME|SCHEDULE)\b")
_ALLOWED_HEAD = ("MATCH", "OPTIONAL MATCH", "WITH", "UNWIND", "RETURN")

SCHEMA_TEXT = """节点标签与属性（**所有节点都有 kb 属性，每个节点模式必须带 {kb: $kb}**）：
  (:Entity {kb, name, kind, df})          术语/实体，name 是名称
  (:Chunk {kb, chunk_id, doc_id, idx})    文档分块，**不存正文**
  (:Document {kb, doc_id, title})         文档
关系：
  (:Document)-[:HAS_CHUNK]->(:Chunk)
  (:Chunk)-[:MENTIONS]->(:Entity)
  (:Entity)-[:CO_MENTION {weight}]->(:Entity)     术语共现，weight 是共现次数（无向使用）
  (:Entity)-[:REL {kind}]->(:Entity)               带类型的语义关系（无向使用）
可用参数：
  $kb    命名空间字符串
  $names 问题中已命中的实体名数组
  $hops  允许的最大跳数（整数）"""

_FENCE = re.compile(r"```(?:cypher)?\s*(.*?)```", re.S)
_TRAILING_SEMI = re.compile(r";\s*$")
_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\s*$", re.I)


def extract_cypher(text: str) -> str:
    """从模型输出里取出 Cypher：优先 ``` 代码块，否则从第一条子句关键词开始截到结尾。"""
    text = (text or "").strip()
    m = _FENCE.search(text)
    if m:
        return _TRAILING_SEMI.sub("", m.group(1).strip()).strip()
    up = text.upper()
    pos = [up.find(k) for k in ("MATCH", "WITH", "UNWIND", "RETURN")]
    pos = [p for p in pos if p >= 0]
    if not pos:
        raise ValueError("模型输出里没有 Cypher 语句")
    return _TRAILING_SEMI.sub("", text[min(pos):].strip()).strip()


def validate_readonly(cypher: str) -> str:
    """只读校验，通过则返回规范化后的语句，否则抛 ValueError（原因写明）。"""
    q = _TRAILING_SEMI.sub("", (cypher or "").strip()).strip()
    if not q:
        raise ValueError("Cypher 为空")
    if len(q) > MAX_CYPHER_LEN:
        raise ValueError(f"Cypher 超长（>{MAX_CYPHER_LEN}）")
    if ";" in q:
        raise ValueError("只允许单条语句")
    up = q.upper()
    hit = _WRITE_KEYWORDS.search(up)
    if hit:
        raise ValueError(f"包含写操作关键词：{hit.group(1)}")
    if "$KB" not in up:
        raise ValueError("缺少命名空间过滤 $kb（可能读到别的项目的数据）")
    if not any(up.startswith(k) for k in _ALLOWED_HEAD):
        raise ValueError("必须以 MATCH / WITH / UNWIND / RETURN 开头")
    if not re.search(r"\bRETURN\b", up):
        raise ValueError("缺少 RETURN 子句")
    return q


def ensure_limit(cypher: str, limit: int = DEFAULT_LIMIT) -> str:
    """没有结尾 LIMIT 就补一个；已有的超过上限则压到上限。

    只处理「语句末尾的 LIMIT」。补上的 LIMIT 是否合法由随后的 EXPLAIN 判定——
    语句若不是以 RETURN 子句收尾，EXPLAIN 会直接报错，不需要在这里猜语法。
    """
    q = (cypher or "").strip().rstrip(";").strip()
    m = _TRAILING_LIMIT.search(q)
    if m:
        n = int(m.group(1))
        return q if n <= limit else q[:m.start(1)] + str(limit)
    return f"{q} LIMIT {limit}"


def templates(hops: int, *, typed: bool = True) -> list[tuple[str, str]]:
    """手写 Cypher 模板：一跳 + （hops≥2 时）多跳扩展。返回 [(名称, cypher)]。

    不走 LLM、完全确定，是「模板」模式，也是 LLM 模式失败后的兜底。

    一跳：问题命中的实体 ← 提到它的分块（1 条边）。
    多跳：命中实体 —(共现/语义关系)— … — 邻居实体 ← 提到邻居的分块，
    且该分块**没有**直接提到命中实体——否则它在一跳里已经出现过，不算多跳新增。

    跳数口径：`hops` = 从命中实体走到分块经过的**边数**。实体之间最多走 hops-1 条边，
    最后一条「邻居实体 ← 分块」也算一跳。所以 hops=2 是「隔一个邻居术语」，
    不是「隔两个」——实测第一版把 hops 直接当成实体间的边数，设 2 却走出了 3 跳。

    `typed=False` 时不带 `REL`：图里一条语义边都没有（默认离线抽取）却去匹配 `REL`，
    Neo4j 每次都会发「关系类型不存在」的警告，把日志刷脏。
    """
    if hops not in (1, 2, 3):
        raise ValueError(f"hops 只支持 1/2/3，收到 {hops!r}")
    rel = "CO_MENTION|REL" if typed else "CO_MENTION"
    # **必须用 `WHERE e.name IN $names`，不能写 `(:Entity {name: $names})`。**
    # 实测（Neo4j 5.26.34）：节点模式里的属性与列表参数比较是「等于这个列表」，恒不成立，
    # 整条查询静默返回 0 行。更坑的是把它用在 `NOT (...)` 里——排除条件变成空真，
    # 结果里会混进本该排除的行，而查询「不报错」。
    # 列表语义只能靠 IN 谓词表达。
    out = [(
        "hop1",
        "MATCH (e:Entity {kb: $kb}) WHERE e.name IN $names "
        "MATCH (e)<-[:MENTIONS]-(c:Chunk {kb: $kb}) "
        "RETURN c.chunk_id AS chunk_id, collect(DISTINCT e.name)[0..4] AS matched, "
        "[] AS path, 1 AS hop",
    )]
    if hops >= 2:
        out.append((
            f"hop{hops}",
            "MATCH (e:Entity {kb: $kb}) WHERE e.name IN $names "
            # 跳数上界是经过校验的整数、关系类型是固定白名单，直接格式化进语句；
            # 用户输入永远走参数、不拼进 Cypher。
            "MATCH p = (e)-[:%s*1..%d]-(e2:Entity {kb: $kb}) "
            "WHERE NOT e2.name IN $names "
            "WITH e2, p, reduce(w = 0.0, r IN relationships(p) | w + coalesce(r.weight, 1.0)) AS w "
            "ORDER BY w DESC "
            "LIMIT 60 "
            "MATCH (e2)<-[:MENTIONS]-(c:Chunk {kb: $kb}) "
            "WHERE NOT EXISTS { MATCH (c)-[:MENTIONS]->(m:Entity) "
            "WHERE m.kb = $kb AND m.name IN $names } "
            "RETURN c.chunk_id AS chunk_id, [n IN nodes(p) | n.name] AS path, length(p) + 1 AS hop"
            % (rel, hops - 1),
        ))
    return out


def build_prompt(question: str, hops: int = 2, examples: bool = True) -> str:
    """Text2Cypher 的提示词：schema + 规则 + 少样本。问句通过参数传入，不拼进 Cypher。"""
    few_shot = """
示例 1
问：哪些分块提到了问题里的实体？
MATCH (e:Entity {kb: $kb}) WHERE e.name IN $names
MATCH (e)<-[:MENTIONS]-(c:Chunk {kb: $kb})
RETURN c.chunk_id AS chunk_id LIMIT 50
示例 2
问：和问题里的实体有关联的术语，在哪些分块里被提到？
MATCH (e:Entity {kb: $kb}) WHERE e.name IN $names
MATCH p = (e)-[:CO_MENTION]-(e2:Entity {kb: $kb})
MATCH (e2)<-[:MENTIONS]-(c:Chunk {kb: $kb})
RETURN c.chunk_id AS chunk_id, [n IN nodes(p) | n.name] AS path LIMIT 50
""" if examples else ""
    return (
        "你在把一个检索问题翻译成一条 Neo4j Cypher 只读查询。\n\n"
        f"{SCHEMA_TEXT}\n\n"
        "硬性规则：\n"
        "1. 只输出一条语句，不要解释；\n"
        "2. 只读：不得出现 CREATE / MERGE / DELETE / SET / REMOVE / CALL 等任何写操作；\n"
        "3. 节点都带 {kb: $kb}；按实体名筛选用 `WHERE e.name IN $names`，"
        "**不要**写成 `{name: $names}`（属性与列表比较恒不成立）；\n"
        f"4. 实体名只能用参数 $names，跳数上限 $hops={hops}；\n"
        "5. 必须返回 chunk_id 列，并以 LIMIT 结尾。\n"
        f"{few_shot}\n"
        f"问：{question}\n"
        "Cypher："
    )
