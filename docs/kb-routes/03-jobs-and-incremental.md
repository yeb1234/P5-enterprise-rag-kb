# 后台任务落库与按变化文档增量重建

编制：2026-11-27 ｜ 状态：**已实现；演示切片，不是上线交付版**

本文只讲两件事：Wiki 构建的后台任务状态为什么从进程内存搬到 PostgreSQL，以及
「增量重建」到底按什么口径判定、什么时候一次模型调用都不发。三态边界见文末表格。

---

## 1. 任务状态为什么要落库

原来 `webapp/kb_routes.py` 用 `_jobs: dict` + `_job_locks` 记状态，够单进程演示用，但有两个
实打实的洞：

1. **进程重启即丢**：后台线程正在跑 Wiki 构建（十几批模型调用），进程一重启，这条
   「进行中」无声消失——状态页回到 idle，人以为没跑过，其实模型钱已经花了。
2. **多副本互相看不见**：副本 A 启动的任务，副本 B 的状态页查不到；用户刷新落到 B
   就以为任务没启动，重复点一次，两份模型调用同时烧。

现在状态落在 `scripts/pg_init.sql` 定义的 `kb_jobs` 表（DDL 唯一事实源仍然是那份文件，
`webapp/db.py` 里没有 CREATE TABLE）。表建成幂等：`CREATE TABLE IF NOT EXISTS` +
`CREATE INDEX IF NOT EXISTS`，索引单独建，和本仓其它表同风格。

- `worker_id = WORKER_ID`：模块导入时 `uuid.uuid4().hex` 生成一次，代表当前进程/副本。
- `start_job()`：先同步做前置检查，再 INSERT 一行 `running`（`started_at` / `updated_at`
  为 now）；后台线程完成后 UPDATE `status='done'` + `result_json`，失败则
  `status='failed'` + `error`。
- `job_state()`：`SELECT ... ORDER BY started_at DESC LIMIT 1`，取的是**库里最近一次**，
  不是进程里最后一次。`result_json` 是 jsonb，读出来可能是 dict 也可能是字符串，
  两种形态都解析；对外仍叫 `result`，老调用方不用改。
- `status()` 与 `job_state()` 读之前都跑 `_mark_interrupted()`（幂等，不需要 startup hook）。

### 1.1 同名任务跨进程并发怎么拦

靠 `ux_kb_jobs_running ON kb_jobs(name) WHERE status='running'` 这个**部分唯一索引**：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_kb_jobs_running ON kb_jobs(name) WHERE status = 'running';
```

只有 `status='running'` 的行进索引，所以同一名字的 running 行全库只能有一条；任务结束
改成 done/failed 后自动退出索引，下一次点击才能再启动。捕获唯一冲突（沿异常链找
`UniqueViolation`，兼容驱动外层的包装）转成 `RouteNotReady(f"{name} 正在进行中…")`，
路由层映射成 409。

这比原来的 `threading.Lock` 强在**跨进程也有效**：Lock 只在单进程里互斥，两个副本各持
一把锁，照样能把同一件事干两遍。

### 1.2 重启后 running → interrupted 的语义与边界

`_mark_interrupted()` 把 `status='running' AND worker_id <> WORKER_ID` 的行改成
`status='interrupted'`、`error='进程重启或换了副本，任务未完成'`、`finished_at=now`。

语义：**这条任务没有正常收尾，不要相信它的结果**。它不自动重试，也不复活成 running。
状态页会如实显示 interrupted + 原因，管理员自己决定要不要重新点一次。

边界必须说清楚，别当成分布式任务队列：

- 判定只是「按 worker_id 盲判」，**没有租约、心跳、抢锁**；
- 于是一个副本读状态时，另一个副本**正在跑**的同名任务也会被标 interrupted——这是
  演示切片接受的误判，不是 bug，但也不是「跨副本共享进度」；
- `progress` 列由任务自己的线程回写（每批一次），其它副本能读到，但**写入只发生在
  创建任务的那个进程**，没有跨副本的进度同步协议。

---

## 2. 增量重建：按变化文档重建，没变化就不花钱

### 2.1 判定口径

`wiki_build(mode)` 的 `mode` 取 `full`（默认，全删全建）或 `incremental`。
增量判定发生在**任何模型调用之前**，顺序很关键：

1. 取当前 `_ready_chunks()`，按业务场景过 `relevancy.select_by_scenario()`，得出本次
   **实际入选信源的文档** `source_doc_ids`；
2. 从 `_ready_docs()` 取这些文档的 `content_hash`（正文哈希由分块算，`documents` 表没有
   checksum 列），组成 `source_doc_hashes: {doc_id: content_hash}` 写进构建 meta；
3. 与上次 meta 里的 `source_doc_hashes` 逐 doc 比对：新增、内容变了、从信源里消失，
   三种都算变化。库外文档（没入选信源的）怎么改都不算。

场景变更也算「口径变了」：旧页面不能原样留着，退回全量重建。

### 2.2 省钱点：无变化零模型调用

`changed_doc_ids` 为空时**立即返回**，不调 `_precheck_wiki_build()`、更不调
`_llm_or_none()`：

```json
{"ok": true, "incremental_skipped": true, "reason": "信源无变化",
 "pages": <现有页数>, "claims": <现有结论数>, "gate": <当前门禁>}
```

这是整个改动里最值钱的一行——full 模式每次都要十几批模型调用，没变化还重跑就是纯烧钱。
测试里用 `monkeypatch` 让 `_llm_or_none()` 一被调用就抛异常，增量无变化路径仍然成功
skip，就是钉死这条约束。

### 2.3 有变化时只重建相关页面

1. 用 `_wiki_rows()` 读旧 claims（它已经把 jsonb 的 `evidence_json` 解析成 list，
   dict/字符串两种形态都兼容）。证据里记着 `doc_id`（构建时由 `builder` 写入），
   据此筛出 evidence 指向 `changed_doc_ids` 的旧页面；
2. 只删这批页面及其 claims，**其余页面和 claims 原样保留**（`built_at` 都不动）；
3. 只把属于变化文档的 ready 分块送进 `wiki_builder.build()`，仍然先过一遍
   `relevancy.select_by_scenario()`；新页面/claims 插入（page_id 撞保留页时先删后插）；
4. 重算信源指纹与 meta，`source_doc_hashes` 更新成当前值，写回 `runtime_state`。

接口侧：`POST /api/kb/wiki/build` 的请求体可选 `{"mode": "full" | "incremental"}`，
默认 `full`，不带请求体的老调用方行为不变；非法 mode 返回 400。

---

## 3. 三态表

| 状态 | 内容 |
|---|---|
| **已实现** | 任务状态落 `kb_jobs`（PG）；worker_id 区分进程；重启/换副本的 running 读时标记为 interrupted；同名任务靠部分唯一索引跨进程互斥；`progress` 回写 DB 列；按文档内容哈希的增量重建；信源无变化直接跳过、零模型调用；接口 `mode` 参数与 400 校验 |
| **只设计** | 真正的分布式任务队列（Celery / RQ）：带租约、心跳、失败重试与抢占；跨副本共享实时进度（当前只有创建任务的进程写 progress） |
| **未做** | 任务失败自动重试；构建产物版本回滚（回到上一次 wiki 快照）|

> 提醒：本文描述的是**演示切片**。任务落库解决了「重启丢状态、多副本看不见」，
> 但没有引入分布式协调，`interrupted` 的误判边界如上所述——不要拿它当生产调度器用。
