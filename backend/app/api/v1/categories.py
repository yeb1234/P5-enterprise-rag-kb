"""多级分类（物化路径）与标签。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from app.core.constants import Action
from app.core.deps import CurrentUser, DbSession, request_meta
from app.core.errors import BadRequest, Forbidden, NotFound
from app.db.repository import TenantRepository
from app.models.knowledge import Category, Document, Tag
from app.schemas.knowledge import CategoryCreate, CategoryNode, CategoryPatch, TagCreate, TagOut
from app.services.audit import record

router = APIRouter(tags=["分类与标签"])


def _tree(rows: list[Category]) -> list[CategoryNode]:
    nodes = {
        r.id: CategoryNode(id=r.id, name=r.name, parent_id=r.parent_id, level=r.level,
                           path_ids=[str(x) for x in r.path_ids], sort_order=r.sort_order)
        for r in rows
    }
    roots: list[CategoryNode] = []
    for r in rows:
        node = nodes[r.id]
        if r.parent_id and r.parent_id in nodes:
            nodes[r.parent_id].children.append(node)
        else:
            roots.append(node)
    return roots


@router.get("/categories/tree", response_model=list[CategoryNode], summary="分类树")
def category_tree(db: DbSession, user: CurrentUser, kb_id: uuid.UUID | None = None):
    q = TenantRepository(db, user.tenant_id).query(Category)
    if kb_id:
        q = q.filter(Category.kb_id == kb_id)
    return _tree(q.order_by(Category.sort_order, Category.name).all())


@router.post("/categories", response_model=CategoryNode, summary="新建分类")
def create_category(payload: CategoryCreate, request: Request, db: DbSession, user: CurrentUser):
    repo = TenantRepository(db, user.tenant_id)
    parent = repo.get(Category, payload.parent_id) if payload.parent_id else None
    path_ids = [*(str(x) for x in parent.path_ids), str(parent.id)] if parent else []
    obj = Category(
        tenant_id=user.tenant_id, kb_id=payload.kb_id, name=payload.name,
        parent_id=payload.parent_id, level=(parent.level + 1) if parent else 1,
        path_ids=path_ids, sort_order=payload.sort_order,
    )
    repo.add(obj)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.CREATE.value,
           resource_type="category", resource_id=str(obj.id), meta=request_meta(request))
    db.commit()
    return CategoryNode(id=obj.id, name=obj.name, parent_id=obj.parent_id, level=obj.level,
                        path_ids=[str(x) for x in obj.path_ids], sort_order=obj.sort_order)


@router.patch("/categories/{cat_id}", summary="重命名 / 移动分类")
def patch_category(cat_id: uuid.UUID, payload: CategoryPatch, db: DbSession, user: CurrentUser):
    repo = TenantRepository(db, user.tenant_id)
    obj = repo.get(Category, cat_id)
    if payload.parent_id == cat_id:
        raise BadRequest("不能把分类挂到自己下面")
    if payload.parent_id:
        parent = repo.get(Category, payload.parent_id)
        if str(obj.id) in [str(x) for x in parent.path_ids]:
            raise BadRequest("不能把分类挂到自己的子孙下面")
        obj.path_ids = [*(str(x) for x in parent.path_ids), str(parent.id)]
        obj.level = parent.level + 1
    obj.parent_id = payload.parent_id if payload.parent_id else obj.parent_id
    if payload.name:
        obj.name = payload.name
    if payload.sort_order is not None:
        obj.sort_order = payload.sort_order
    db.commit()
    return {"id": str(obj.id), "path_ids": [str(x) for x in obj.path_ids]}


@router.delete("/categories/{cat_id}", summary="删除分类")
def delete_category(cat_id: uuid.UUID, db: DbSession, user: CurrentUser):
    repo = TenantRepository(db, user.tenant_id)
    obj = repo.get(Category, cat_id)
    in_use = repo.query(Document).filter(Document.category_id == cat_id).count()
    if in_use:
        raise BadRequest(f"该分类下还有 {in_use} 篇文档，请先移动或删除文档")
    repo.query(Category).filter(Category.path_ids.contains([str(cat_id)])).delete(synchronize_session=False)
    db.delete(obj)
    db.commit()
    return {"ok": True}


@router.get("/tags", response_model=list[TagOut], summary="标签列表")
def list_tags(db: DbSession, user: CurrentUser):
    return [TagOut.model_validate(t) for t in
            TenantRepository(db, user.tenant_id).query(Tag).order_by(Tag.name).all()]


@router.post("/tags", response_model=TagOut, summary="新建标签")
def create_tag(payload: TagCreate, db: DbSession, user: CurrentUser):
    repo = TenantRepository(db, user.tenant_id)
    if repo.query(Tag).filter(Tag.name == payload.name).first():
        raise BadRequest("标签已存在")
    obj = Tag(tenant_id=user.tenant_id, name=payload.name, color=payload.color)
    repo.add(obj)
    db.commit()
    return TagOut.model_validate(obj)
