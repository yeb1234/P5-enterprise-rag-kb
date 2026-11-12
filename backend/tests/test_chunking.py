"""分块与融合的纯函数用例。"""
from app.services.parser import PageText, split_into_chunks


def make_pages(n: int = 3) -> list[PageText]:
    return [
        PageText(
            page_no=i + 1,
            text="# 第 %d 章 概览\n\n%s\n\n| 项目 | 数值 |\n| --- | --- |\n| A | %d |\n"
                 % (i + 1, ("这是一段用于验证语义分块的正文内容。" * 30), i),
        )
        for i in range(n)
    ]


def test_split_keeps_page_and_section_metadata():
    specs = split_into_chunks(make_pages(), max_chars=300, overlap_chars=40)
    assert specs
    assert all(s.seq == i for i, s in enumerate(specs))
    assert all(s.page_no is not None for s in specs)
    assert any("第 1 章" in s.section_path for s in specs)
    assert any(s.block_type == "table" for s in specs)
    assert all(len(s.content) > 0 for s in specs)


def test_chunk_window_overlap_exists():
    specs = split_into_chunks(make_pages(1), max_chars=200, overlap_chars=40)
    assert len(specs) > 1
    # 重叠窗口：后一块开头是前一块结尾的子串
    assert specs[1].content.startswith(specs[0].content[-40:][:20]) or len(specs) == 1


def test_rrf_fusion_merges_and_ranks():
    from app.services.search import rrf_fuse

    keyword = [{"chunk_id": "a", "source": "keyword"}, {"chunk_id": "b", "source": "keyword"}]
    semantic = [{"chunk_id": "b", "source": "semantic"}, {"chunk_id": "c", "source": "semantic"}]
    fused = rrf_fuse([keyword, semantic])
    assert fused[0]["chunk_id"] == "b"           # 两路都命中，排最前
    assert set(fused[0]["matched_by"]) == {"keyword", "semantic"}
    assert {x["chunk_id"] for x in fused} == {"a", "b", "c"}


def test_stub_embedding_is_deterministic_and_normalized():
    from app.integrations.embedding import StubEmbeddingProvider

    provider = StubEmbeddingProvider(dim=64)
    a = provider.embed(["DPI 切换按键位于鼠标顶部"])[0]
    b = provider.embed(["DPI 切换按键位于鼠标顶部"])[0]
    c = provider.embed(["财务报销制度"])[0]
    assert a == b and len(a) == 64
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6
    assert a != c
