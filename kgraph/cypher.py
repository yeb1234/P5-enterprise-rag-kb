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


# --- 存储层强制的命名空间静态检查 -------------------------------------------------
# 纯 schema/introspection：不读业务数据，无需 $kb。用**前缀**匹配（而非宽松子串），
# 避免把夹在中间的 `CALL db.` 当白名单放行。
_SCHEMA_CALL = re.compile(r"^(?:EXPLAIN\s+|PROFILE\s+)*CALL\s+(?:db|dbms)\.", re.I)
# schema DDL（建索引/约束）同样不读业务数据，也没有 $kb 的概念；ensure_indexes() 要走它。
_SCHEMA_DDL = re.compile(r"^(?:EXPLAIN\s+|PROFILE\s+)*(?:CREATE|DROP)\s+(?:INDEX|CONSTRAINT)\b", re.I)
# 可选的 EXPLAIN / PROFILE 前缀（explain() 的调用方可能已经拼好）。
_EXPLAIN_PREFIX = re.compile(r"^(?:(?:EXPLAIN|PROFILE)\s+)+", re.I)
# 带目标标签的节点模式：`(e:Entity {..})` / `(:Chunk)` 等。
# [^()]* 不跨越嵌套括号，避免把子查询里的 ) 误当成节点模式的结束。
_LABELLED_NODE = re.compile(
    r"\(\s*([A-Za-z_]\w*)?\s*:\s*(Entity|Chunk|Document)\b([^()]*)\)", re.I)
# 大括号里 kb 作为属性**键**（不是值里恰好出现 kb，如 {name: $kb} 不算）
_PROP_KB = re.compile(r"\{\s*[^}]*\bkb\s*:", re.I)
_PARAM_KB = re.compile(r"\$kb\b", re.I)


def assert_namespaced(cypher: str) -> None:
    """全项目唯一的执行入口检查：任何要落库的 Cypher 都必须带 P5 命名空间 $kb。

    这是**保守的静态检查，不是完整的 Cypher 解析器**。规则：

    1. 纯 schema/introspection 查询（`CALL db.` / `CALL dbms.` 开头）与 DDL
       （`CREATE/DROP INDEX|CONSTRAINT`）不读业务数据、也没有 $kb 概念，放行；
       可带可选的 EXPLAIN / PROFILE 前缀。
    2. 其余语句必须先出现 `$kb` 参数。
    3. 每个 `:(Entity|Chunk|Document)` 节点模式，要么大括号里带 `kb` 键
       （形如 `{kb: $kb}`），要么紧随其后有 `WHERE <alias>.kb = $kb`。

    不通过抛 ValueError，原因写明是哪一条。

    **局限**：正则覆盖不了 Cypher 的全部语法（子查询、模式理解、路径变量、反引号
    标识符等），复杂写法可能被误判——这里宁可拒绝也不放行（可在评审后调整检查逻辑，
    但不能靠放宽标准绕过）。白名单用**前缀**匹配把风险面收窄到只读的内省调用，
    但理论上若有人注册了名为 `db.*` 的自定义过程仍可能绕过；所以这一层之外还有
    Text2Cypher 关键词白名单、EXPLAIN 预检与只读会话三道防线，不是只靠它兜底。
    """
    q = (cypher or "").strip()
    if _SCHEMA_CALL.match(q) or _SCHEMA_DDL.match(q):
        return
    stripped = _EXPLAIN_PREFIX.sub("", q).strip()
    if not _PARAM_KB.search(stripped):
        raise ValueError("缺少命名空间 $kb（可能读到别的项目数据）")
    for m in _LABELLED_NODE.finditer(stripped):
        alias, label, detail = m.group(1), m.group(2), m.group(3)
        if _PROP_KB.search(detail):
            continue
        # 退路：大括号没带 kb 时，检查紧随节点模式之后是否有 WHERE <alias>.kb = $kb。
        # 必须带 alias 才引用得到该节点，且只匹配这个 alias——否则「上半句守规矩、
        # 下半句换个 alias 偷读别的项目」会蒙混过关。
        if alias:
            tail = stripped[m.end():m.end() + 120]
            if re.search(r"\bWHERE\s+" + re.escape(alias) + r"\.kb\s*=\s*\$kb\b", tail, re.I):
                continue
        who = f"节点模式 :{label}" + (f"（别名 {alias}）" if alias else "")
        raise ValueError(
            f"{who} 未带 kb 过滤：需在 {{}} 内写 {{kb: $kb}}，"
            f"或紧随其后写 WHERE {(alias or '<alias>')}.kb = $kb")


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
