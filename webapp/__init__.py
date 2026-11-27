"""webapp 包：可运行演示切片（FastAPI + PostgreSQL + 无构建静态前端）。

它是生产架构的“可演示替身”：与生产共用 PostgreSQL 真值层和 DDL，进程内信号量对应 Celery+Redis/RabbitMQ，
离线占位生成对应 DeepSeek 流式。契约与 next-version 方案保持一致，便于讲解取舍。
"""
