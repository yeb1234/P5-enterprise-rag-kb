# showcase 站点镜像（阿里云上线指导素材，可选）。
# 只 COPY showcase 与它依赖的纯函数包；不 COPY webapp/ backend/ eval/ demo/。
# 密钥只从环境变量注入：本文件不含任何密钥值，也不读取 .env。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 站点运行依赖（parsing/ 的可选增强 pypdf / Pillow 也在其中）
RUN pip install --no-cache-dir \
      "fastapi>=0.110" \
      "uvicorn>=0.29" \
      "python-multipart>=0.0.9" \
      "pypdf>=4.0" \
      "Pillow>=10.0"

# 只读纯函数包（依赖方向 showcase/ -> parsing/ guardrails/ cache/ knowledge/）
COPY parsing/ ./parsing/
COPY guardrails/ ./guardrails/
COPY cache/ ./cache/
COPY knowledge/ ./knowledge/

# 站点本体（含已落盘的真实样本；static/ 由前端成员并行提供）
COPY showcase/ ./showcase/

# 运行产物目录：.gitignore 已忽略 showcase/out/*，镜像内只需占位
RUN mkdir -p /app/showcase/out

EXPOSE 8767

# 健康检查只打 /api/health（A1 只回布尔，不回密钥值）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8767/api/health', timeout=4); exit(0 if r.status == 200 else 1)"

# 默认离线：站点本身恒定 offline=True；如需凭证由使用者 `-e` 注入，镜像不含值
CMD ["python", "-m", "uvicorn", "showcase.server:app", "--host", "0.0.0.0", "--port", "8767"]
