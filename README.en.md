# P5 — Enterprise Knowledge Base & RAG Q&A

<p align="center">
  <a href="README.md">中文</a> · <a href="README.en.md">English</a> · <a href="docs/architecture-review.md">Architecture</a> · <a href="docs/README.md">Docs index</a>
</p>

**A multi-department enterprise knowledge base: document ingestion → parsing and chunking → retrieval and reranking → grounded answers with citations. Permissions, audit, graceful degradation and evaluation stay on the main path.**

![version](https://img.shields.io/badge/version-v1.4.0-2563EB?style=flat-square)
![tests](https://img.shields.io/badge/tests-482%20passed-16A34A?style=flat-square)
![python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=flat-square&logo=redis&logoColor=white)

<p align="center">
  <img src="docs/assets/webapp-dashboard.png" width="920" alt="P5 webapp dashboard">
</p>
<p align="center"><sub>The webapp dashboard: documents, Q&A, tasks, evaluation, audit and system boundaries in one place (real local run, demo data).</sub></p>

## Overview

P5 is a runnable enterprise knowledge base / RAG system. It covers the full loop from document upload to answer citation tracing: upload, parsing, chunking, permission filtering, hybrid retrieval, reranking, generation, citations, audit, cache recovery, evaluation and an external tool endpoint. Each part has code, database tables and tests behind it.

Two runtime shapes are provided:

- **Single-process webapp slice** — `webapp/` runs the complete UI in one process, intended for quick demos and end-to-end debugging.
- **Full service stack** — `frontend/` + `nginx/` + `backend/` + Celery + PostgreSQL/Redis/MinIO, matching a multi-replica, asynchronous production layout.

Positioning: a **runnable enterprise knowledge-base prototype and engineering base**, not a turnkey production product. Implemented features, demo-grade features and explicit gaps are separated in this README and under `docs/`.

## What is implemented

| Capability | Where it lives | Key constraint |
|---|---|---|
| Document ingestion | Chunked upload, resumable transfer, batch aggregation, instant deduplication | Fixed 8 MiB request size; large files never enter API memory; task state is persisted in PostgreSQL |
| Parsing and chunking | `parsing/` three-level strategy (in-house / paid OCR / multimodal fallback) | Per-page quality gate escalates gradually; budget gate caps the most expensive path |
| Retrieval | Vector retrieval + optional graph / LLM Wiki routes, RRF fusion, reranking | Vector route is on by default; a failed optional route is absent, not fatal |
| Q&A | SSE streaming, citations back to page/paragraph, explicit refusal on empty retrieval | No fabrication; degradation is visible and recorded |
| Knowledge governance | Freshness decay, effective/expiry windows, fact-conflict detection and arbitration | Policies are switchable; ranking flips and arbitration are observable |
| Content safety | Rule-based PII/sensitive-data detection at ingest, model input, output and log export | Audit records keep category and count, not raw text |
| Cache and recovery | L1 in-memory LRU + L2 PostgreSQL, 13-segment cache keys, 7 startup checks | Version bump drives lazy invalidation; TTL is not an access-control mechanism |
| Organization and permissions | JWT refresh rotation, RBAC, department tree, explicit ACL grants, append-only audit | Permission decisions converge on a single `decide()` function |
| Evaluation | `eval/` offline contracts + `evalbench/` live-API workbench + public benchmarks | Frozen areas stay diff-free; incomparable results are marked `NOT_COMPARABLE` |
| External interface | Read-only MCP gateway (JSON-RPC 2.0) | P5 remains responsible for ingestion, permissions and index maintenance |

## Architecture

<p align="center">
  <img src="docs/assets/architecture.svg" width="100%" alt="P5 architecture">
</p>

```text
Access      React admin (:8080)   webapp demo slice (:8765)   showcase parser (:8767)
                 │                        │                        │
Application FastAPI services · backend :8000 / webapp single process / Celery workers
                 │
Data        PostgreSQL 16 + pgvector   Redis 7   MinIO   Neo4j (optional)   Milvus (optional)
                 │
Models      DeepSeek generation   BGE-M3 embedding   BGE-reranker
```

Five design constraints:

1. **PostgreSQL is the source of truth for state.** Redis is only a real-time channel. Write text before vectors; delete vectors before soft-deleting a document.
2. **Permissions first.** `permission.decide()` is the only authority; filtering happens before retrieval and is re-checked after recall.
3. **One DDL source.** `scripts/pg_init.sql` owns schema creation; business code does not create tables.
4. **Failures must be visible.** Parsing, retrieval, model and cache degradation all write a status and reason; exceptions are not swallowed.
5. **Optional capabilities do not pollute the default path.** Neo4j and Milvus only start under their compose profiles; graph and Wiki routes are off by default.

## Retrieval pipeline

<p align="center">
  <img src="docs/assets/retrieval-pipeline.svg" width="100%" alt="Three-route retrieval and fusion">
</p>

Only the vector route is on by default. Set `WEBAPP_RAG_ROUTE_GRAPH` / `WEBAPP_RAG_ROUTE_WIKI` to enable the other two routes.

- **Vector route** — a lexical gate only decides whether there is a relevant signal; hits are ranked by cosine similarity in pgvector. Lightweight candidates are fetched first, then text is loaded by id, so the full corpus is never pulled into the process.
- **Graph route** — `kgraph/` builds a term co-occurrence graph. Neo4j stores relationships, not raw text. Multi-hop results come back as clues and are re-checked in PostgreSQL by `chunk_id`. Text2Cypher passes a keyword allowlist, an `EXPLAIN` precheck and a read-only session; failures fall back to a template and record `fallback_reason`.
- **Wiki route** — `llmwiki/` passes scenario/source/freshness gates, filters sources by scenario, and writes Markdown pages with claim-level evidence. A source fingerprint change invalidates the corresponding conclusions.
- **Fusion** — results hit by multiple routes rank first; within a group, RRF (Σ 1/(60+rank)) is used. Cosine scores, hop decay and page-match scores are not added across different scales.
- **Reranking** — cross-encoder or LLM reranking. When top-1 leads top-2 by ≥ 0.05, reranking is skipped adaptively; failures degrade gracefully and leave a trace.

## Quick start

### Run the webapp only

```bash
# 1) Start infrastructure (PostgreSQL required; Redis optional)
docker compose -f docker-compose.infra.yml up -d postgres redis

# 2) Configure
cp .env.example .env
#    At minimum, check WEBAPP_DATABASE_URL. If local port 5432 is occupied,
#    change KB_PG_PORT and update the port in .env as well.

# 3) Start the service (Python 3.11+)
pip install "fastapi>=0.115" "uvicorn>=0.30" "psycopg[binary]>=3.2" \
            "SQLAlchemy>=2.0,<2.1" "pypdf>=5.0" "Pillow>=10.0" "python-multipart>=0.0.9"
python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` and sign in with `admin / admin123`. The login page can initialize demo data on first use. `./run-demo.sh` starts both the webapp (8765) and the parser workbench (8767).

### Run the full stack

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec api alembic upgrade head
# http://localhost:8080
# admin@demo.local / Admin@12345
```

### Optional components

```bash
docker compose -f docker-compose.infra.yml --profile milvus up -d   # comparison-only Milvus
docker compose --profile parsing up -d --build parser                # standalone parser service
docker compose -f docker-compose.infra.yml --profile graphrag up -d neo4j
# Enable routes in .env: WEBAPP_RAG_ROUTE_GRAPH=on, WEBAPP_RAG_ROUTE_WIKI=on
# Wiki also needs WEBAPP_WIKI_SCENARIO=<scenario>; it stays off without one.
```

## Repository layout

```text
backend/            FastAPI service: core / db / models / schemas / api / services / integrations / tasks / agent
frontend/           React admin UI (Vite + AntD)
webapp/             Single-process full slice: routes, retrieval, providers, cache, evaluation, static pages
parsing/            Document parsing: page metadata, quality gate, budget gate, multimodal fallback
knowledge/          Knowledge governance: freshness, fact-conflict detection and arbitration
kgraph/             Graph retrieval: term co-occurrence graph, multi-hop queries, Text2Cypher guards
llmwiki/            LLM Wiki: three gates, pages and claim-level evidence chains
guardrails/         Content safety: PII rules, checksum validation, four-egress redaction
cache/              Cache and recovery: L1/L2, 13-segment keys, startup checks
eval/               Offline evaluation contracts (frozen objects, metrics, incomparability marker)
evalbench/          Live-API evaluation workbench: prompts / params / models, iterative convergence
evalfmt/            Evaluation result formatting and comparison
showcase/           Parser workbench (standalone page + contract tests)
nginx/              Reverse proxy, SSE buffering off, security headers
scripts/            DDL, initialization and operations scripts
docs/               Design, review, delivery and operations documents
docs/assets/        Architecture diagrams, retrieval diagram and UI screenshots
demo/               Frozen demo scenario
```

## Tests

The full suite requires PostgreSQL (SQLite has been removed). Each test process uses an isolated schema `kb_test_<uuid8>` and drops it afterwards, so the business database is untouched.

```bash
export WEBAPP_DATABASE_URL=postgresql://kb:kb_demo_only@127.0.0.1:5432/kb

for d in webapp/tests backend/tests knowledge/tests cache/tests guardrails/tests \
         parsing/tests showcase/tests evalbench/tests evalfmt/tests eval/tests \
         kgraph/tests llmwiki/tests; do
  python -m pytest "$d" -q
done
```

**Measured on 2026-11-27: 482 passed / 0 failed.** The breakdown by suite is in the Chinese README and in `docs/acceptance-report.md`.

## Evaluation

Public retrieval benchmark (`evalbench/`, nDCG@10 on a 100k-passage corpus):

| Configuration | nDCG@10 |
|---|---:|
| BM25 baseline | 16.29% |
| bge-m3 vector | 33.93% |
| bge-m3 + reranking | **36.09%** |

The 2026-11-27 dual-pool review used 200 queries on 3k and 12k document pools with real external embedding and reranking:

| Pool | nDCG@10 | Recall@5 | Mean latency | Rerank skip |
|---|---:|---:|---:|---:|
| 3,000 docs | 0.6747 | 0.7419 | 625 ms | 29.0% |
| 12,000 docs | 0.5547 | 0.6086 | 850 ms | 24.5% |

The converged defaults are `top_k=5`, `rerank_depth=20`, `rerank=external`, `margin=0.05`. Details and limitations: [`docs/retrieval-param-review-2026-11-27.md`](docs/retrieval-param-review-2026-11-27.md).

## Documentation

The full documentation map is [`docs/README.md`](docs/README.md). Most design documents are written in Chinese; the index gives the topic and status of each page. Common entry points:

- [`docs/architecture-review.md`](docs/architecture-review.md) — architecture boundaries and current scope
- [`docs/acceptance-report.md`](docs/acceptance-report.md) — final acceptance and test report
- [`docs/evaluation-plan.md`](docs/evaluation-plan.md) — evaluation plan
- [`docs/retrieval-param-convergence.md`](docs/retrieval-param-convergence.md) — historical parameter convergence
- [`docs/deployment-guide.md`](docs/deployment-guide.md) — deployment guide
- [`docs/operations.md`](docs/operations.md) — operations, security and high-availability boundaries
- [`docs/mcp.md`](docs/mcp.md) — read-only MCP gateway

## Known limitations

- **Single organization, multiple users and departments.** Multi-tenancy is a direction, not implemented. The `tenant_id` naming in `backend/` is technical debt; see `docs/org-model-change-plan.md`.
- **No department-level permission.** Departments are used for cost and rate-limit dimensions; authorization still relies on roles and explicit ACL grants.
- **No load or disaster-recovery drill.** There is no P95 first-token baseline, no primary/standby RTO/RPO exercise and no alert channel. The default deployment is single-host Compose, not high availability.
- **Parsing boundary.** pypdf is the main path; scanned documents, two-column layouts and complex tables do not have full OCR/multimodal fallback. Paid OCR is not connected; fields remain `__TBC__`.
- **Milvus is comparison-only.** The default path uses pgvector; Milvus is not in the default compose set.
- **Graph / Wiki are demo scale.** The graph is term co-occurrence (not a semantic KG); Wiki task state is in process memory and is lost on restart or multiple replicas. See `docs/kb-routes/01-design.md`.
- **Isolation strength.** Multi-user isolation relies on code discipline and schema conventions, not database constraints. A missing filter is an authorization risk; production needs a cross-tenant test set.
- **Demo credentials.** `admin/admin123` and `admin@demo.local/Admin@12345` are local-only; default keys and demo data must not be used in production.

## Version

Current **v1.4.0** (2026-11-27, merged): P5F retrieval performance / MCP / public benchmark work merged with the three-route retrieval line (vector / graph / LLM Wiki). Version coordinates and per-item changes are in `docs/CHANGELOG.md`; measured performance and parameter convergence are in `docs/retrieval-param-convergence.md`.
