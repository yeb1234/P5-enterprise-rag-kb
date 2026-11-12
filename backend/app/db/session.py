"""数据库连接生命周期。

FastAPI 用 yield 依赖：请求失败、抛异常、SSE 断开都会走 finally 归还连接。
Celery worker 用 session_scope()，长任务里每阶段提交一次，不长期持有事务。
"""
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,          # 拿连接前探活，PG 主备切换后不会一直用断掉的连接
    pool_recycle=1800,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    echo=settings.DEBUG and not settings.is_production,  # 生产强制不打印 SQL
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
