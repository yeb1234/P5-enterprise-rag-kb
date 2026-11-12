"""Celery 配置：Redis 同时作 broker 与 result backend。

取舍说明（相对设计文档里的 RabbitMQ）：
  · 用 Redis 做 broker 少一个中间件，学习期排障成本低，中小企业 50 人规模够用；
  · 代价是没有 RabbitMQ 的投递确认与死信队列语义，所以这里用
    acks_late + reject_on_worker_lost + visibility_timeout + PG 任务台账 + beat 兜底扫描
    共同补可靠性。切换到 RabbitMQ 不是只改 broker URL：还需重划 ack 时机、死信归属、
    beat 职责与可观测口径，见 docs/next-version-messaging-and-plugins.md。
"""
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "enterprise_kb",
    broker=settings.broker_url,
    backend=settings.result_url,
    include=["app.tasks.ingest", "app.tasks.maintenance"],
)

celery_app.conf.update(
    task_default_queue="default",
    task_routes={
        "app.tasks.ingest.parse_document": {"queue": "ingest"},
        "app.tasks.ingest.parse_richtext": {"queue": "ingest"},
        "app.tasks.ingest.chunk_document": {"queue": "ingest"},
        "app.tasks.ingest.embed_and_index": {"queue": "ingest"},
        "app.tasks.ingest.finalize_document": {"queue": "ingest"},
        "app.tasks.ingest.parse_page_range": {"queue": "ingest_heavy"},
        "app.tasks.ingest.merge_parsed": {"queue": "ingest_heavy"},
        "app.tasks.ingest.cleanup_document": {"queue": "maintenance"},
        "app.tasks.maintenance.*": {"queue": "maintenance"},
    },
    task_acks_late=True,               # 任务真正完成才 ack，worker 被杀可重投
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,      # 重型任务不预取，避免长任务堵住短任务
    worker_max_tasks_per_child=100,    # 防 pypdf / httpx 长跑内存泄漏
    task_soft_time_limit=settings.INGEST_SOFT_TIME_LIMIT,
    task_time_limit=settings.INGEST_HARD_TIME_LIMIT,
    result_expires=86400,
    broker_transport_options={
        # 必须 > 最长任务时限，否则任务还在跑就被 Redis 重新投递给别的 worker
        "visibility_timeout": settings.INGEST_HARD_TIME_LIMIT * 2,
        "fanout_prefix": True,
        "fanout_patterns": True,
    },
    beat_schedule={
        "rescue-stale-jobs": {
            "task": "app.tasks.maintenance.rescue_stale_jobs",
            "schedule": 300.0,
        },
        "cleanup-expired-uploads": {
            "task": "app.tasks.maintenance.cleanup_expired_uploads",
            "schedule": crontab(minute=30),
        },
        "refresh-daily-stats": {
            "task": "app.tasks.maintenance.refresh_stats",
            "schedule": crontab(hour=1, minute=0),
        },
        "reconcile-vectors": {
            "task": "app.tasks.maintenance.reconcile_vectors",
            "schedule": crontab(hour=2, minute=0),
        },
    },
    timezone="Asia/Shanghai",
)
