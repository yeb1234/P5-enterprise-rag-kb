# webapp 演示切片镜像：登录 / 文档管理 / 流式问答 / 评测 / badcase / 内容安全。
# 只 COPY webapp 与它依赖的纯函数包；不 COPY backend/ demo/。
# 运行时需要连 PostgreSQL（SQLite 已移除），连接串由 WEBAPP_DATABASE_URL 注入。
# **例外**：`eval/config/` 必须进镜像 —— `/api/eval/objects` 直接读它做事实源
#   （避免页面写死对象清单与评测体系不一致）。漏了这行会让该端点 500。
#   只带 config/，不带 eval/objects、results、tests 等冻结物料。
# 密钥只从环境变量注入：本文件不含任何密钥值，也不读取 .env。
#
# 国内网络构建（可选）：
#   docker build -f webapp/Dockerfile -t kb-webapp:dev \
#     --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
#     --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple .
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} \
      "fastapi>=0.110" \
      "uvicorn>=0.29" \
      "python-multipart>=0.0.9" \
      "pypdf>=4.0" \
      "Pillow>=10.0" \
      "psycopg[binary]>=3.1"

# 中立基础设施模块：webapp/ 与 cache/ 共用同一套 PG 连接与占位符翻译
COPY pgkit.py ./pgkit.py

# 建表 DDL 的唯一事实源（容器启动时 db.init_db() 会执行它）
COPY scripts/pg_init.sql ./scripts/pg_init.sql

# 纯函数包（依赖方向 webapp/ -> parsing/ guardrails/ cache/ knowledge/）
COPY parsing/ ./parsing/
COPY guardrails/ ./guardrails/
COPY cache/ ./cache/
COPY knowledge/ ./knowledge/

# 演示切片本体（含静态前端与 out/ 事实源）
COPY webapp/ ./webapp/

# 评测注册表：`/api/eval/objects` 的运行期只读事实源（见文件头说明）
COPY eval/config/ ./eval/config/

# 运行期数据目录（SQLite 与上传）；挂 volume 可持久化
RUN mkdir -p /app/.data/webapp/uploads /app/webapp/out

EXPOSE 8765

# 首页返回 200 即视为就绪（webapp 无独立 health 路由）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8765/', timeout=4); exit(0 if r.status == 200 else 1)"

# 默认离线：不读 .env 的密钥值，也不发起真实模型调用
CMD ["python", "-m", "uvicorn", "webapp.main:app", "--host", "0.0.0.0", "--port", "8765"]
