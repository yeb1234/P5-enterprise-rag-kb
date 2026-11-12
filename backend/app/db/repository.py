"""租户强过滤 Repository。

这套系统是「共享 Schema + tenant_id」逻辑隔离，最大风险是某条查询漏写 tenant_id 就越权。
把所有列表/详情查询收敛到这一层，业务代码拿不到「不过滤」的 Query。
"""
from __future__ import annotations

import uuid
from typing import Any, TypeVar

from sqlalchemy.orm import Query, Session

from app.core.errors import NotFound

ModelT = TypeVar("ModelT")


class TenantRepository:
    def __init__(self, db: Session, tenant_id: uuid.UUID):
        self.db = db
        self.tenant_id = tenant_id

    def query(self, model: type[ModelT], include_deleted: bool = False) -> Query:
        q = self.db.query(model).filter(model.tenant_id == self.tenant_id)  # type: ignore[attr-defined]
        if not include_deleted and hasattr(model, "is_deleted"):
            q = q.filter(model.is_deleted.is_(False))
        return q

    def get(self, model: type[ModelT], entity_id: uuid.UUID, include_deleted: bool = False) -> ModelT:
        obj = self.query(model, include_deleted).filter(model.id == entity_id).first()  # type: ignore[attr-defined]
        if obj is None:
            raise NotFound(f"{model.__name__} 不存在或无权访问")
        return obj

    def paginate(self, q: Query, page: int = 1, page_size: int = 20) -> tuple[list[Any], int]:
        total = q.order_by(None).count()
        items = q.offset((page - 1) * page_size).limit(min(page_size, 200)).all()
        return items, total

    def add(self, obj: Any) -> Any:
        """写入前统一补 tenant_id，防止新建对象漏填。"""
        if hasattr(obj, "tenant_id") and getattr(obj, "tenant_id", None) is None:
            obj.tenant_id = self.tenant_id
        self.db.add(obj)
        return obj
