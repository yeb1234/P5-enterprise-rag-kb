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
# 先确保基础设施起着：docker compose -f docker-compose.infra.yml up -d postgres redis
# compose 项目名是 p5-kb-infra，网络名随之为 p5-kb-infra_default；
# 用 KB_NETWORK 覆盖，别把前缀写死（换项目名时这里最容易漏改，现象是 pgkit 解析不到 kb-pg）。
DB_URL="${WEBAPP_DATABASE_URL:-postgresql://kb:kb_demo_only@kb-pg:5432/kb}"
NETWORK="${KB_NETWORK:-${KB_INFRA_NETWORK:-p5-kb-infra_default}}"
# 日志上限必须写在 `docker run` 上：`docker-compose.yml` 里的 x-logging 锚点
# 只对 compose 起的服务生效，这两个容器是 `docker run` 起的，实测
# `docker inspect` 的 LogConfig 是空的（json-file 默认**无上限**，会一直长到磁盘满）。
LOG_OPTS="--log-opt max-size=10m --log-opt max-file=3"
# 网络名跟 infra 项目名走：compose name 已从 kb-infra 改为 p5-kb-infra，
# 默认网络随之变成 p5-kb-infra_default（旧的 kb-infra_default 已不存在）。
# 容器名 kb-pg / kb-redis 是靠 alias 保留的，连接串不用改。
docker run -d --name kb-webapp \
  -p 127.0.0.1:8765:8765 \
  --network "$NETWORK" \
  $LOG_OPTS \
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
  $LOG_OPTS \
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
