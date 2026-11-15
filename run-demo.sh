#!/usr/bin/env bash
# 一键构建并启动两个演示页：
#   完整功能首页  http://127.0.0.1:8765   （登录 / 文档 / 流式问答 / 评测 / badcase / 内容安全）
#   解析工作台    http://127.0.0.1:8767   （左分析 / 右入库 JSON / 参数可调 / LLM 路由快照）
#
# 为什么不用 `docker compose up`：Docker 29 + Compose v5 的 bake 在含中文的路径下会报
#   failed to dial gRPC: header key "x-docker-expose-session-sharedkey" contains value with non-printable ASCII characters
# 这是构建器兼容问题，不是镜像本身的问题；因此本脚本用 `docker build` + `docker run` 绕开。
# 需要 compose 时用：COMPOSE_BAKE=false docker compose -f docker-compose.showcase.yml up -d
set -euo pipefail
cd "$(dirname "$0")"

BASE_IMAGE="${BASE_IMAGE:-docker.m.daocloud.io/library/python:3.11-slim}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

echo "==> 构建镜像（基础镜像 $BASE_IMAGE）"
docker build -f webapp/Dockerfile -t kb-webapp:dev \
  --build-arg BASE_IMAGE="$BASE_IMAGE" --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" . | tail -n 3
docker build -f showcase/Dockerfile -t kb-showcase:dev \
  --build-arg BASE_IMAGE="$BASE_IMAGE" --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" . | tail -n 3

echo "==> 重建容器"
docker rm -f kb-webapp kb-showcase >/dev/null 2>&1 || true

# SQLite 已移除：webapp 必须连真 PostgreSQL。
# 先确保基础设施起着：KB_PG_PORT=55432 … docker compose -f docker-compose.infra.yml up -d postgres
DB_URL="${WEBAPP_DATABASE_URL:-postgresql://kb:kb_demo_only@kb-pg:5432/kb}"
docker run -d --name kb-webapp \
  -p 127.0.0.1:8765:8765 \
  --network kb-infra_default \
  -e WEBAPP_DATABASE_URL="$DB_URL" \
  -e WEBAPP_LLM_PROVIDER=offline \
  -e WEBAPP_EMBEDDING_PROVIDER=offline \
  -e WEBAPP_RERANK_PROVIDER=off \
  -e PARSING_OFFLINE=1 \
  -v kb-webapp-data:/app/.data \
  --restart unless-stopped \
  kb-webapp:dev >/dev/null

docker run -d --name kb-showcase \
  -p 127.0.0.1:8767:8767 \
  -e PARSING_OFFLINE=1 \
  -e SHOWCASE_MAIN_SITE_URL=http://127.0.0.1:8765 \
  --restart unless-stopped \
  kb-showcase:dev >/dev/null

echo "==> 等待健康检查（webapp 需要 PG 就绪，失败先看 docker logs kb-webapp）"
for p in 8765 8767; do
  for i in $(seq 1 30); do
    code=$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://127.0.0.1:$p/" || true)
    [ "$code" = "200" ] && break
    sleep 1
  done
  echo "  :$p -> HTTP $code"
done

echo
echo "首页（完整功能）  http://127.0.0.1:8765"
echo "解析工作台        http://127.0.0.1:8767"
echo "停止：docker stop kb-webapp kb-showcase    日志：docker logs -f kb-showcase"
