"""文档版本：不可变快照 + 行级 diff + 一键回滚。

编辑文档不是 UPDATE 一行，而是：
  生成新版本快照 → DocumentVersion 追加一条 → current_version +1 → 触发增量重索引。
版本内容不复用对象存储里的原文件（原文件会变），保证历史永远可还原。
"""
from __future__ import annotations

import difflib
import hashlib
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.core.errors import BadRequest, NotFound
from app.models.knowledge import Document, DocumentVersion
from app.schemas.common import DiffLine, DiffResult
from app.schemas.knowledge import VersionDetail, VersionOut

if TYPE_CHECKING:
    from app.core.deps import Principal


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot(
    db: Session, doc: Document, change_summary: str, created_by: uuid.UUID | None,
    content_text: str | None = None,
) -> DocumentVersion:
    """在外部事务里调用，只 flush 不 commit。"""
    text = doc.content_text if content_text is None else content_text
    version = DocumentVersion(
        tenant_id=doc.tenant_id, document_id=doc.id, version_no=doc.current_version,
        title=doc.title, content_text=text, content_uri=None,
        checksum=_checksum(text), change_summary=change_summary,
        diff_stats={"added": 0, "removed": 0}, created_by=created_by,
    )
    db.add(version)
    db.flush()
    return version


def list_versions(db: Session, tenant_id: uuid.UUID, doc_id: uuid.UUID) -> list[VersionOut]:
    rows = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.tenant_id == tenant_id, DocumentVersion.document_id == doc_id)
        .order_by(DocumentVersion.version_no.desc())
        .all()
    )
    return [VersionOut.model_validate(r) for r in rows]


def get_version(db: Session, tenant_id: uuid.UUID, doc_id: uuid.UUID, version_no: int) -> VersionDetail:
    row = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.tenant_id == tenant_id,
            DocumentVersion.document_id == doc_id,
            DocumentVersion.version_no == version_no,
        )
        .first()
    )
    if row is None:
        raise NotFound("版本不存在")
    return VersionDetail.model_validate(row)


def compare(
    db: Session, tenant_id: uuid.UUID, doc_id: uuid.UUID, from_v: int, to_v: int
) -> DiffResult:
    if from_v >= to_v:
        raise BadRequest("to_version 必须大于 from_version")
    a = get_version(db, tenant_id, doc_id, from_v).content_text.splitlines()
    b = get_version(db, tenant_id, doc_id, to_v).content_text.splitlines()

    lines: list[DiffLine] = []
    added = removed = 0
    old_no = new_no = 0
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, text in enumerate(a[i1:i2]):
                old_no += 1
                new_no += 1
                lines.append(DiffLine(type="equal", old_no=old_no, new_no=new_no, text=text))
        else:
            if tag in ("replace", "delete"):
                for text in a[i1:i2]:
                    old_no += 1
                    removed += 1
                    lines.append(DiffLine(type="removed", old_no=old_no, text=text))
            if tag in ("replace", "insert"):
                for text in b[j1:j2]:
                    new_no += 1
                    added += 1
                    lines.append(DiffLine(type="added", new_no=new_no, text=text))

    total = max(len(a) + len(b), 1)
    return DiffResult(
        from_version=from_v, to_version=to_v, added=added, removed=removed,
        changed_ratio=round((added + removed) / total, 4), lines=lines,
    )


def restore(
    db: Session, user: "Principal", doc: Document, version_no: int
) -> DocumentVersion:
    """回滚不是改历史，而是把旧内容作为「新版本」追加 —— 审计链不断。"""
    old = get_version(db, user.tenant_id, doc.id, version_no)
    doc.title = old.title
    doc.content_text = old.content_text
    doc.current_version += 1
    version = snapshot(db, doc, f"从 v{version_no} 回滚", user.id)
    version.diff_stats = {"restored_from": version_no}
    db.flush()
    return version
