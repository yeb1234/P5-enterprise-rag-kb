"""基线迁移：建表 + 全文检索触发器 + 中文模糊索引。

约定：0001 用 metadata.create_all 一次性生成与模型一致的基线（学习骨架少写 800 行
op.create_table），从 0002 起所有改动必须手写 / autogenerate 显式迁移，禁止再全量同步。

中文分词说明：
  · 默认用 PG 自带 'simple' 配置 + pg_trgm，英文/编号/型号检索准确，中文走 ilike 兜底；
  · 上生产装 zhparser 后，把下面函数里的 'simple' 改成 'zhcfg'，
    并给 search_vector 建 GIN 索引（索引已在模型里建好），业务代码零改动。
"""
from alembic import op

from app.db.base import Base
import app.models  # noqa: F401

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

DOC_TSV_FUNC = """
CREATE OR REPLACE FUNCTION kb_documents_tsv() RETURNS trigger AS $$
BEGIN
  NEW.search_vector := to_tsvector('simple',
      coalesce(NEW.title, '') || ' ' || coalesce(NEW.content_text, ''));
  RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""

CHUNK_TSV_FUNC = """
CREATE OR REPLACE FUNCTION kb_chunks_tsv() RETURNS trigger AS $$
BEGIN
  NEW.search_vector := to_tsvector('simple', coalesce(NEW.content, ''));
  RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    op.execute(DOC_TSV_FUNC)
    op.execute("""
        CREATE TRIGGER trg_documents_tsv
        BEFORE INSERT OR UPDATE OF title, content_text ON documents
        FOR EACH ROW EXECUTE FUNCTION kb_documents_tsv()
    """)
    op.execute(CHUNK_TSV_FUNC)
    op.execute("""
        CREATE TRIGGER trg_chunks_tsv
        BEFORE INSERT OR UPDATE OF content ON chunks
        FOR EACH ROW EXECUTE FUNCTION kb_chunks_tsv()
    """)
    # 回填（基线为空表，但重跑/导数据场景下保证向量不为空）
    op.execute("UPDATE documents SET title = title WHERE search_vector IS NULL")
    op.execute("UPDATE chunks SET content = content WHERE search_vector IS NULL")

    # 审计表按月分区属于阶段三：这里只补复合索引，分区改造另开迁移
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_metric_events_tenant_created
        ON metric_events (tenant_id, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_chunks_tsv ON chunks")
    op.execute("DROP TRIGGER IF EXISTS trg_documents_tsv ON documents")
    op.execute("DROP FUNCTION IF EXISTS kb_chunks_tsv()")
    op.execute("DROP FUNCTION IF EXISTS kb_documents_tsv()")
    Base.metadata.drop_all(bind=op.get_bind())
