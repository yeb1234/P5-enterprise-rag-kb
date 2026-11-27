# P5 Knowledge MCP（只读）

P5 对外提供一个 HTTP JSON-RPC 2.0 的只读 MCP 入口，给 P10 项目管理工作台读取知识库。P5 仍然是语料、权限、切分和索引的唯一 owner，P10 不复制 P5 文档。

## 入口

```text
POST http://127.0.0.1:12416/api/v1/mcp
Content-Type: application/json
X-P5-MCP-Key: <MCP_SERVICE_KEY>
```

`12416` 是当前 `p5fe-api-1` 的宿主机端口，对应容器 `8000`；P10 后端从 `127.0.0.1` 调用。当前开发 compose 把端口发布到 `0.0.0.0`，只适合本机联调；如果不需要给局域网其他机器访问 P5 API，生产或共享环境应改为 `127.0.0.1:12416:8000`，或在前面加网关和网络策略。

## 配置

`backend/app/core/config.py`：

```dotenv
MCP_ENABLED=true
MCP_SERVICE_KEY=<共享密钥>
MCP_TENANT_ID=<P5 租户 id>
MCP_USER_ID=<P5 服务用户 id>
```

密钥只放在 `.env`，不写入代码、日志或文档。P5 容器不会在 `docker restart` 时重新读取 `env_file`；改完 `.env` 后要用 compose 重建 API：

```powershell
docker compose --project-directory "C:\FinalProjectYeb\P5企业级知识库项目" `
  -p p5fe `
  -f "C:\ProjectYeb\P7HarnessDesignE\P5Plus-RealEval\run\compose.eval.override.yml" `
  up -d --no-deps --force-recreate api
```

## 工具

### knowledge.search

包装 `app.services.search.hybrid_search`，保留 P5 的租户/部门/敏感度权限。

参数：`query`、`mode`（hybrid/keyword/semantic）、`top_k`、`kb_ids`、`category_ids`、`include_descendants`。

返回片段级 `hits`：`chunk_id / document_id / title / content / snippet / section_path / page_no / score / matched_by / source`。

### knowledge.text_structure

只读查询：

- 不传 `document_id`：返回 `knowledge_bases`、文档摘要、`documents_total`、`categories`。
- 传 `document_id`：返回该文档的 `chunks`，字段包含 `seq / section_path / block_type / source_level / page_no / token_count / content`。

## 协议

支持：

- `initialize`
- `tools/list`
- `tools/call`
- `notifications/initialized`（HTTP 202，无响应体）

其他方法返回 JSON-RPC `-32601`。

## 当前数据边界

当前 P5 库是 54 篇、54 个 chunk，多数文档 1 篇 1 个 L1 片段，`section_path` 为空，`categories` 为空。P10 页面按“知识库 → 文档 → 片段”展示，不假设存在章节树。

P10 侧契约、回退和轮换流程见 `../../P10ProjectManager/docs/P5_MCP_CONTRACT.md`。
