# 前端全面检查报告（两套前端）

> 检查对象：`webapp/static/`（零构建静态演示切片）+ `frontend/src/`（React + Vite 生产骨架）
> 检查日期：2026-11-14 ｜ 检查人：前端体验工程师 颜可心
> **本报告只产出问题清单，未修改任何源码、未运行任何真实外部 LLM 调用。**
>
> **行号口径**：以下行号是 2026-11-14 实读快照 —— `webapp/static/index.html` 648 行、`app.js` 1314 行、`i18n.js` 951 行、`style.css` 522 行、`theme.js` 66 行；`frontend/src/` 各文件行数见 §1.2。
> 检查期间有其它成员在并发提交（观测到 `app.js` 由 1300 → 1314 行、`index.html` 由 642 → 648 行，且「推理强度」控件与维度 `tenant→department_id` 改名已落地）。**因此每条同时给出函数名与可 grep 的锚点**；若后续文件继续变动，以函数名／锚点复核，不要死认行号。
>
> **口径**：定位是「开发框架骨架 + 可运行演示切片 + 面试讲解版」，A/B 档，不是上线交付版。报告按「已确认缺陷 / 改进建议 / 演示切片可接受的取舍」三类判定，第四类单独在 §5 列出。

---

## 0. 主理人复核与修复记录（2026-11-14，最终态优先）

> 本报告是**问题清单**，状态以检查时为准。下面记录主理人逐条复核后的处置，
> **本节结论优先级高于正文的状态标注**；正文保留不回改，用于追溯「发现 → 处置」。

### 0.1 两条致命缺陷：核实属实，本轮已修

| ID | 复核结论 | 修复落点 | 修复方式 |
|---|---|---|---|
| F-01 跨用户聊天记录串号 | **属实**。`app.js` `forceLogout()` 只清 token/user，不清 `S.conversationId` 与 `#chat-log`；`bootApp()` 只在 `!S.conversationId` 时 `newConversation()`，B 登录后会看到 A 的对话并续写到 A 的会话 | `webapp/static/app.js` `forceLogout()` | 身份登出时一并清空 `conversationId` / `msgCount` / `_modalData` / `evalTasks` / `_lastBusy`，清空 `#chat-log`、复位 `S.streaming` 与发送按钮 |
| F-02 流式 401 后问答永久锁死 | **属实**。`sendChat()` 的 401 分支 `forceLogout(); return;` 跳过收尾段的 `S.streaming = false`，重新登录后开头守卫永久拦截 | `webapp/static/app.js` `sendChat()` | 401 改为置 `authExpired=true` 后 `break`，在收尾前插入专门的复位分支（清光标、复位 `S.streaming`、解禁按钮、状态提示后 return），不再直接 return |

两条都会直接在「多用户 / 断线恢复」演示中暴露，属于本轮必须修的演示阻断项。修复后 `node --check` 通过、`webapp/selftest.py` 33/33 `DEMO_READY` 复跑通过。

### 0.2 三条被实查推翻的结论（未写入本报告的交付结论）

| 说法 | 复核结果 |
|---|---|
| `index.html` 有 6 个 `onclick` 函数未在 `app.js` 定义、触发 ReferenceError | **不成立**。40 个内联处理器目标函数全部有定义；`setLang` 定义在 `i18n.js` 并挂 `window`（`index.html:9` 先于 `app.js` 加载），其余「缺失」是正则把 DOM 方法名（`click` / `getElementById` / `preventDefault`）与关键字（`if`）误当函数 |
| `backend/Dockerfile` / `frontend/Dockerfile` 全缺、compose healthcheck 必然失败 | **不成立**。两个文件都在（406 B / 369 B），backend 镜像 `apt-get install curl libmagic1 libpq5`，`backend/app/main.py` 确有 `/health/live` |
| `frontend/src/` 只有骨架、`App.tsx` 导入不存在、build 会覆盖已有静态页 | **不成立**。`frontend/src/` 实有 16 个文件、9 个导入全在；`npm run build` 实跑通过（3505 modules / 6.83 s，仅 antd chunk 875 KB 警告）；`dist/index.html` 本来就是带 hash 资源名的 Vite 构建产物 |

### 0.3 其余条目的处置

- S-01（重复 `kb:langchange` 监听）、S-02（`S._modalData` 只增不删）、S-04（2.5 s 轮询无条件重写 `innerHTML`）等**性能与资源类**问题：确认为真实改进项，本轮因范围纪律留作后续，不夸大、不静默修。
- S-09（React 骨架 `tenant_id` / `tenant_name` / `tenant_slug` 与新口径冲突）：**确认属实且有意保留**。用户本轮口径是「单组织 + 多用户多部门，暂不实现多租户」，但 `backend/` 的数据模型 13 步清理方案仍是「方案已定、代码尚未改造」；前端单方面删字段会造成 422，已登记为跨端同批改造项。
- 报告里的 `?v=11` 表述：本轮静态资源已统一升 **`?v=12`**（`index.html` 四处），报告写作时点的快照正确，当前值以 v=12 为准。
- 两处代码注释失真（`db.py` 的 `rate_rules.dimension` 四维字面、`observability.py` PLUGINS 把部门列为限流维度）：**本轮已修**，最终限流为三维 `("user","endpoint","model")`。

**最终回归**：`pytest webapp/tests` **66 passed**（含本轮新增 15 项）、`webapp/selftest.py` 33/33、`npm run build` 通过、`git diff --exit-code -- eval/ demo/` 返回 0。详见 `docs/acceptance-report.md` §5.2。

---

## 1. 检查范围与方法

### 1.1 文件清单（全部实读）

| 组 | 文件 | 行数 |
|---|---|---|
| A · 静态演示切片 | `webapp/static/index.html` | 648 |
| | `webapp/static/app.js` | 1314 |
| | `webapp/static/style.css` | 522 |
| | `webapp/static/i18n.js` | 951 |
| | `webapp/static/theme.js` | 66 |
| B · React 骨架 | `frontend/src/App.tsx` | 37 |
| | `frontend/src/main.tsx` / `index.css` / `types.ts` | 26 / 53 / 91 |
| | `frontend/src/api/client.ts` / `api/endpoints.ts` | 54 / 80 |
| | `frontend/src/store/AuthContext.tsx` | 49 |
| | `frontend/src/hooks/useProgressStream.ts` | 117 |
| | `frontend/src/components/AppLayout.tsx` | 60 |
| | `frontend/src/pages/*.tsx`（7 个页面） | 63 / 149 / 231 / 176 / 141 / 93 / 77 |
| | `frontend/index.html` / `vite.config.ts` / `package.json` / `tsconfig.json` / `nginx.conf` / `Dockerfile` | 13 / 30 / 28 / 19 / 16 / 13 |
| 交叉核对 | `backend/app/core/errors.py`、`backend/app/api/v1/auth.py`、`webapp/routers_auth.py`、`webapp/observability.py`、`webapp/db.py`、`nginx/nginx.conf`、`docker-compose.yml` | — |

### 1.2 六个检查面

① 代码质量与规范（命名 / 目录 / 重复代码）② 组件设计与状态管理合理性 ③ 页面布局与样式 ④ 响应式适配 ⑤ 潜在性能问题 ⑥ 明显 bug 与边界情况、控制台报错与警告。

### 1.3 已执行的离线复核命令（结果附在括号里，全部未触发外部模型）

```
node --check webapp/static/app.js && node --check webapp/static/i18n.js && node --check webapp/static/theme.js
  → 三个文件全部 OK（语法无错误）

python 扫描 index.html：
  → 150 个 id，重复 id 0 个
  → data-view 11 个 / id="view-*" 11 个（一一对应）
  → "?v=11" 4 处：L8 theme.js / L9 i18n.js / L10 style.css / L646 app.js（全部一致）
  → 内联 onclick= 50 处；data-i18n= 1 处；data-i18n-html= 0 处
  → app.js 中 innerHTML 赋值 59 处

grep 计数：
  → i18n.js 中文原文 key 约 639 条，其中进入 SUB_RE 的 ≥3 字片段 543 条
  → frontend/src/index.css 中 @media / :focus-visible / prefers-reduced-motion 命中 0
  → webapp/static/style.css 中 @media 5 处（1100/1000/760/420/reduced-motion），:focus-visible 1 处
  → frontend/src 中 console.log/warn/error 命中 0，TODO/FIXME 命中 0
  → webapp/static 中 console.* 命中 0
  → frontend/ 下无 ESLint / Prettier / 测试配置文件；package.json 无 lint / test 脚本
```

---

## 2. 结论摘要

| 级别 | 条目数 | 含义 |
|---|---|---|
| **致命** | **2** | 功能永久失效或跨用户数据串号，演示/开发环境可直接复现 |
| **严重** | **12** | 明确逻辑错误、重复请求、响应式失效、与新架构口径冲突、控制台报错 |
| **一般** | **15** | 有证据的边界缺陷、性能损耗、健壮性缺口、措辞口径残留 |
| **建议** | **12** | 可维护性 / 一致性改进，不影响当前可运行性 |
| 合计 | **41** | |

### 六面覆盖矩阵

| 条目 | ①质量 | ②组件状态 | ③布局样式 | ④响应式 | ⑤性能 | ⑥bug/边界/控制台 |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| F-01 / F-02 致命 | | ● | | | | ● |
| S-01 … S-04 | | ● | | | ● | ● |
| S-05 … S-09 | | ● | | | | ● |
| S-10 … S-12 | ● | | | ● | ● | |
| G-01 … G-06 | | ● | | | ● | ● |
| G-07 … G-11 | ● | ● | | | | ● |
| G-12 … G-15 | ● | | ● | | | ● |
| R-01 … R-12 | ● | ● | ● | ● | ● | |

### 判定图例

- **[已确认缺陷]**：§3 / §4 中的「致命 / 严重 / 一般」全部有具体行号与代码证据，可在断点下复现。
- **[改进建议]**：「建议」级 + 部分标注为建议的一般项，属于工程化取舍优化，不是当前 bug。
- **[演示取舍]**：见 §5，单独列出并说明「什么条件下不再可接受」。

---

## 3. 致命（2 条）

### F-01 【致命｜②⑥｜已确认缺陷】登出／切换账号不重置会话状态 → 跨用户聊天记录串号

- **位置**：`webapp/static/app.js:117-124` `forceLogout()`；`app.js:145` `bootApp()` 的 `if (!S.conversationId) newConversation();`；`app.js:390-391` `newConversation()`；`app.js:5-20` `S` 全局状态。
- **现象与证据**：`forceLogout()` 只清 `S.token` / `S.user` / `kb_token` / `S._timer`，**没有清** `S.conversationId`、`S.msgCount`、`S.badcaseCount`、`S.settings`、`S._modalData`，也**没有清空** `#chat-log` 的 DOM。`bootApp()` 仅当 `!S.conversationId` 才调 `newConversation()`。用户 A 产生过会话后登出，用户 B 登录 → `S.conversationId` 仍是 A 的 → 不走 `newConversation()` → 聊天区仍显示 A 的问答气泡；B 下一次 `sendChat()`（`app.js:441` 把 `conversation_id: convId` 发往后端）会继续追加到 A 的会话上。
- **影响**：在「多用户多部门」定位下属于跨用户数据串号，面试演示切换账号即可复现，直接击穿「权限分离」的说服力。
- **改进建议**：`forceLogout()` 内显式重置 `S.conversationId = ''` / `S.msgCount = 0` / `S.badcaseCount = 0` / `S.settings = null` / `S._modalData = {}`，清空 `$('chat-log').innerHTML` 与 `#modal-root`；`bootApp()` 无条件调 `newConversation()`（或按 `S.user.id` 比对，用户变化时强制开新会话）。后端 `/api/chat/conversations` 与 `/api/chat/history` 应继续按登录态 principal 过滤，不信任前端传入的 `conversation_id`。
- **复核**：见 §6 手工步骤 M1。

### F-02 【致命｜②⑥｜已确认缺陷】流式过程中 401 提前 return，问答功能永久锁死

- **位置**：`webapp/static/app.js:417-443` `sendChat()`；对照 `app.js:519-524` 的正常收尾。
- **现象与证据**：`sendChat()` 在 L422 置 `S.streaming = true`、L423 置 `$('chat-send').disabled = true`；唯一复位在 L520 `S.streaming = false` 与 L521 `$('chat-send').disabled = false`。但 L443 是 `if (res.status === 401) { forceLogout('登录已过期，请重新登录'); return; }` —— **提前 return 跳过了整个收尾段**（L481-524：移除光标、降级提示、badcase 按钮、`S.streaming=false`、按钮解禁、状态栏、`loadConversations()`）。重新登录后 `S.streaming` 仍为 `true`，`sendChat()` L418 的守卫 `if (S.streaming) { toast('正在生成回答，请稍候'); return; }` 会永久拦截，**除非整页刷新否则再也不能提问**。
- **影响**：令牌过期是必然发生的状态，命中后核心问答功能静默失效，用户只看到一句 toast，没有任何可恢复提示。
- **改进建议**：把收尾段抽成 `finally` 块（或 `try/catch/finally` 的 `finally`），保证任何出口（401 / 抛错 / 正常结束）都执行 `S.streaming = false` + 按钮解禁 + 光标移除 + `setStatus`；401 分支还要额外 `S.conversationId = ''`。同理 `forceLogout()` 应兜底复位 `S.streaming`。
- **复核**：见 §6 手工步骤 M2。

---

## 4. 严重（12 条）

### S-01 【严重｜②⑤｜已确认缺陷】`kb:langchange` 双监听，评估测试页重复发请求

- **位置**：`app.js:201-223` `initAppearance()` 内在 L211 注册 `kb:langchange`；`app.js:1307` 又注册一次 `document.addEventListener('kb:langchange', () => { if (S.user && S.view === 'evaltest') loadEvalTest(); });`；`app.js:194` `refreshCurrentView()`；`app.js:175` `LOADERS.evaltest = loadEvalTest`。
- **现象**：`initAppearance` 的监听在 L218 调 `refreshCurrentView()` → 当 `S.view === 'evaltest'` 时走 `LOADERS.evaltest` → 调 `loadEvalTest()`；同一事件的第二个监听 L1307 **再调一次** `loadEvalTest()`。即每次切换语言，评估测试页的 API 被重复请求一遍（`loadEtConfig/loadEtRuns/loadEtCost/loadEtGov` 等按当前 tab 至少 1～2 个请求 ×2）。此外 L220 的 `loadConversations()` 无论当前在哪个视图都会发一次请求。
- **影响**：重复网络请求、重复 `innerHTML` 重建、EN 模式下重复触发 i18n MutationObserver；在限流演示场景下还会额外消耗配额。
- **建议**：删除 L1307 的独立监听，把「evaltest 需要重渲染」并入 L211 的唯一监听（用 `if (S.view === 'evaltest') loadEvalTest();` 且不要再走 `refreshCurrentView()` 的重复路径）；`loadConversations()` 只在 `S.view === 'chat'` 时调用。

### S-02 【严重｜②⑤｜已确认缺陷】`S._modalData` 只增不删，长会话内存泄漏

- **位置**：`app.js:42-47` `storeModalData()` / `takeModalData()`；调用点 `app.js:515`（每条助手消息）、`543`（每张来源卡片）、`584`（每条历史会话）、`634`（每次打开 badcase 弹窗）、`744`（每条 badcase 行）、`1140`（每条运行记录）。
- **现象**：`storeModalData()` 以随机 key 把对象塞进 `S._modalData`，只有用户点击对应卡片/按钮触发 `takeModalData()` 才 `delete`。弹窗通过「取消」（L643 / L772）或遮罩点击（L38）关闭时**不删除**；`renderSources()` / `loadConversations()` / `renderBadcases()` / `loadEtRuns()` 每次重渲染都重新生成一批 key，旧 key 永久残留。对象内含完整 `answer` 文本与来源全文。
- **影响**：多轮问答 + 反复切换视图/语言后内存单调增长；演示切片长时间挂机尤其明显。
- **建议**：`closeModal()` 里清理本次弹窗关联的 key（在 `openModal()` 时记录）；或改为 `Map` + 容量上限（如保留最近 100 条）；或改用 `dataset` / `WeakMap` + 事件委托，彻底去掉全局注册表。

### S-03 【严重｜②⑥｜已确认缺陷】EN 模式下用户数据被当作界面文案翻译

- **位置**：`i18n.js:755-759` `SKIP_SELECTORS`（仅含 `.bubble / .quoted / .bc-question / .bc-answer / .conv-q / .file-name / .sc-title / .sc-snippet / .tc-id / code / pre / script / style / [data-i18n-skip]`）；对照 `app.js:749`（`issue_desc` 放在 `<td class="small truncate">`）、`app.js:933`（审计 `x.target` / `x.detail` 放在 `<td class="small">`）、`app.js:1221`（评估日志 `x.detail`）、`app.js:1301-1302`（插件 `x.boundary` / `x.why`，由后端返回中文）。
- **现象**：`handleTextNode()`（L814）先 `skipped(node)`，未命中才走 `t(orig)`；`t()` L801 用 `SUB_RE`（543 个中文片段）做**子串替换**。上述容器不在跳过表内且含中文，切到 EN 后中文用户数据被逐片段替换成英文（例如「未给出具体章节」中的「具体」「章节」若命中字典即被改写），且不写 `data-i18n` 就无法可逆还原。
- **影响**：数据展示错误、语言切回 ZH 后可能不可逆（`ORIG_TEXT` 记录的是已被部分替换的文本）；直接违反既有「用户数据容器在 SKIP_SELECTORS」的硬约束意图。
- **建议**：给所有由后端返回的业务文本容器补 `data-i18n-skip`（该选择器已在跳过表内），或把 `td.small` / `.plugin-card p` 等纳入 `SKIP_SELECTORS`；动态生成的用户数据统一走 `data-i18n-skip`，只有界面文案才用 `data-i18n`。

### S-04 【严重｜②⑤｜已确认缺陷】2.5 秒轮询无条件重写 `innerHTML`，EN 模式下持续触发 i18n 重扫

- **位置**：`app.js:148` `setInterval(tick, 2500)` → `app.js:151` `tick()` → `app.js:246-259` `pollBusy()` → `app.js:260-286` `renderBanners()`；其中 L268 `box.innerHTML = html;` 与 L281 `$('busy-detail').innerHTML = ...` **无条件赋值**，即使内容一字未变。对照 `i18n.js:938` 的 `MutationObserver` 监听 `childList + subtree + characterData`。
- **现象**：空闲状态下每 2.5 秒仍销毁并重建 `#banners` 与 `#busy-detail` 的全部子节点。EN 模式下每次重建都会产生一批 `childList` 记录 → `onMutation()` 重新 TreeWalker 扫描 + 逐文本节点跑 `SUB_RE`。`pollBusy()` 在 L252 还会 `renderBanners(r)` 第二次调用同一次轮询路径上的渲染。
- **影响**：恒定 0.4 Hz 的无效 DOM 抖动与翻译扫描；长挂机时主线程被反复占用；`#banners` 内的焦点/选择态会被清空。
- **建议**：`renderBanners()` 先比对 `box.innerHTML !== html` 再写；`busy-detail` 同理；把「状态真正变化」作为刷新条件，或在 `pollBusy()` 里比较 `JSON.stringify(r)` 与 `S._lastBusy`。同时见 S-01 —— 语言切换时还会额外触发一轮。

### S-05 【严重｜②⑥｜已确认缺陷】React 进度流 `close` 事件丢弃最终快照

- **位置**：`frontend/src/hooks/useProgressStream.ts:29-34` `poll()`；`:44-56` `handleEvent()`。
- **现象**：`handleEvent('close')` 在 L53 先 `stopped = true`，L54 再 `poll()`；而 `poll()` 的 L32 是 `if (!stopped) setSnapshot(res.data)` —— 此时 `stopped` 已为 `true`，**最后一次权威快照永远不会被写入 state**。进度条会停在「close 前」的状态。
- **影响**：分片上传收尾时进度可能卡在 99% / 状态停留在 `running`，而页面 `finally` 又把 `connected` 置为 false，表现为「已断连、进度未完成」，与注释宣称的「进度真值在 PG、刷新不丢」相矛盾。
- **建议**：`close` 分支改为 `await poll(); stopped = true;`（或给 `poll()` 加 `force` 参数绕过 `stopped` 判断）；并在 `finally` 中统一 `setConnected(false)`。

### S-06 【严重｜②⑥｜已确认缺陷】SSE 正常结束时未启动降级轮询

- **位置**：`frontend/src/hooks/useProgressStream.ts:64-103` `start()`；L79-80 `while (!stopped) { ... if (done) break; }`；L98-99 `catch { if (!stopped) startPollingFallback(); }`；L100-102 `finally { setConnected(false); }`。
- **现象**：`startPollingFallback()` **只在 `catch` 里调用**。若服务端不发 `close` 事件而直接结束流（`reader.read()` 返回 `done=true`，L80 break），循环正常退出，既不抛错也不启动降级；最终 `connected=false`、`degraded=false`、无轮询 —— 进度静默冻结，用户无任何提示。这与文件头注释「三级容错：流失败自动降级成 2s 轮询」不符。
- **影响**：演示「断连恢复」时反而出现最坏的「静默假死」，属于项目明确禁止的「静默失败」。
- **建议**：在 `finally` 中判断 `if (!stopped && !closedByServer) startPollingFallback();`，或把 `done` 分支也归一到 `startPollingFallback()`；同时把 `degraded` 状态显式展示在 UI 上。

### S-07 【严重｜⑥｜已确认缺陷】`LoginPage` 在 render 阶段调用 `navigate()`

- **位置**：`frontend/src/pages/LoginPage.tsx:15` `if (user) { navigate('/', { replace: true }); return null; }`；配合 `frontend/src/main.tsx:11` `<React.StrictMode>`。
- **现象**：`useNavigate()` 返回的 `navigate` 在**渲染期间**被调用，属于 React 反模式；React 18 会输出 `Warning: Cannot update a component (Router) while rendering a different component (LoginPage)`，StrictMode 下双渲染会放大该问题。
- **影响**：控制台报错/警告（本任务明确的检查面⑥）；行为上可能出现一次多余的重定向。
- **建议**：改用 `import { Navigate } from 'react-router-dom'` 并返回 `<Navigate to="/" replace />`，或把跳转放进 `useEffect`。同时 `Protected` 里已有 `!user → <Navigate to="/login">`，两处逻辑应统一。

### S-08 【严重｜②⑥｜已确认缺陷】StrictMode + 刷新令牌轮换 → 开发环境会话恢复失败

- **位置**：`frontend/src/main.tsx:11` `<React.StrictMode>`；`frontend/src/store/AuthContext.tsx:20-25` `useEffect(() => { bootstrapSession()... }, [])`；`frontend/src/api/client.ts:44-52` `bootstrapSession()`；`backend/app/api/v1/auth.py:72-73` `@router.post("/refresh", summary="刷新令牌（轮换）")`。
- **现象**：React 18 开发模式 StrictMode 会**双调用** `useEffect`，因此首屏发起两次 `POST /api/v1/auth/refresh`。后端刷新是「轮换」语义（一次一换、旧 token 立即失效），第二次请求携带的是已被作废的旧 Cookie → 返回 401 → `bootstrapSession()` 的 `catch` 返回 `null` → `setUser(null)`，即使第一次刷新已成功，用户仍被判为未登录。
- **影响**：开发环境刷新页面后偶发「明明有会话却被踢回登录页」，排查成本高（表现为随机性）。
- **建议**：`AuthProvider` 内加 `useRef` 幂等锁（或 `let bootstrapped` 模块级标志），保证 `bootstrapSession()` 每个挂载周期只发一次；或让后端对同一 refresh token 在极短窗口内幂等；`bootstrapSession()` 应区分「无会话（401，正常）」与「网络错误」，不要把网络抖动也静默吞掉。

### S-09 【严重｜①⑥｜已确认缺陷】React 骨架仍暴露 `tenant`，与本次「多用户多部门」新口径冲突

- **位置**（前端侧）：`frontend/src/types.ts:3` `tenant_id: string`（必填）、`:9` `tenant_name?: string`；`frontend/src/components/AppLayout.tsx:45` 顶栏渲染 `{user?.tenant_name} / {user?.department_name || '未分配部门'}`；`frontend/src/api/endpoints.ts:8-9` `login: (email, password, tenant_slug = 'demo')` 把 `tenant_slug` 发往 `/auth/login`；`frontend/src/pages/LoginPage.tsx:56-57` 演示账号仍按「管理员/研发/访客」列出且未标注部门。
- **辅助证据（非前端，仅说明契约暂未同步）**：`backend/app/api/v1/auth.py:52` 仍以 `Tenant.slug == (payload.tenant_slug or "demo")` 查租户；`webapp/static/index.html:416`、`app.js:947`、`i18n.js:417,463` 仍写「多租户行级权限」；`webapp/README.md:101,229` 仍有「单库单租户 / 切片单租户」措辞。
- **现象**：用户新指令明确「系统为多用户多部门架构，暂不实现多租户；多租户仅作为未来研发方向写入文档」。但 React 骨架的 `User` 类型把 `tenant_id` 设为**必填**、顶栏把「租户名」当作主要组织标识展示、登录请求仍带 `tenant_slug`。静态切片侧 `et-cost-dim` 与 `dimKey` 的维度改名已由其它成员落地（`index.html:519` 已是 `department_id`、`app.js:1285` 已含 `department_id`），但组织标识类措辞未同步。
- **影响**：界面传达「按租户隔离」，与作品集讲解口径「单组织 + 部门树逻辑隔离」直接矛盾；新用户按 `department_id` 建模后 `tenant_id` 必填会让类型与接口双错位。
- **建议**：`types.ts` 删 `tenant_id` / `tenant_name`，改 `department_id: string | null` + `department_name?: string` + `department_path?: string`；`AppLayout.tsx:45` 改为「组织 / 部门路径」展示；`endpoints.ts:8-9` 去掉 `tenant_slug` 入参；`LoginPage` 账号表补部门列。措辞侧见 G-15。**注意：这是跨前后端口径变更，需经主理人中转与 backend-engineer 同步，前端单方面删字段会 422。**

### S-10 【严重｜⑤｜已确认缺陷】React 全部页面静态导入，首屏加载全量 antd + recharts

- **位置**：`frontend/src/App.tsx:1-11` 一次性 `import` 7 个页面 + `AppLayout`；`frontend/src/main.tsx:5` `import 'antd/dist/reset.css'`；`frontend/src/pages/DashboardPage.tsx:4-7` 全量引入 `recharts`；`frontend/vite.config.ts:19-26` `manualChunks` 只做分包未做懒加载。
- **现象**：`manualChunks` 把 `antd / charts / vendor` 拆成独立 chunk，但 7 个页面都是**静态 import**，因此这些 chunk 全在首屏依赖图内，登录页也要下载图表库。`package.json` 无 `build` 之外的产物体积检查。
- **影响**：首屏 JS 体积与解析时间无谓增大；`manualChunks` 只改善了缓存命中，没改善首屏载荷，容易在面试讲解时被问倒。
- **建议**：路由级 `React.lazy` + `<Suspense fallback>`，把 `DashboardPage`（recharts）与 `VersionDiffPage` 等低频页拆出去；`npm run build` 后记录各 chunk 体积并设阈值；`index.html` 加 `preconnect` / `modulepreload` 提示。

### S-11 【严重｜④③｜已确认缺陷】React 侧零媒体查询 + 侧栏折叠后无触发按钮

- **位置**：`frontend/src/index.css` **全文 53 行，`@media` 命中 0**；`frontend/src/components/AppLayout.tsx:27` `<Sider theme="dark" width={216} breakpoint="lg" collapsedWidth="0">`（未设 `collapsible`，也没有 `trigger`）；`frontend/src/pages/DashboardPage.tsx:95` 内联 `gridTemplateColumns: '1fr 1fr 1fr'`；`frontend/src/pages/VersionDiffPage.tsx:59` 内联 `gridTemplateColumns: '300px 1fr'`；`frontend/src/index.css:23` `.login-card { width: 420px; }` 无 `max-width: 100%`。
- **现象**：视口 <992px 时 `Sider` 收起到 0 宽，但因为没有 `collapsible` / `trigger`，**没有任何按钮能重新展开导航**，整个后台在平板/手机上不可操作。三列看板与 `300px 1fr` 差异页在窄屏被硬挤压；420px 固定宽登录卡在 375px 屏上横向溢出。
- **影响**：响应式适配基本失效；与 A 套静态前端（`style.css` 有 4 个宽度断点 + 760px 侧栏横滚）形成明显能力落差。
- **建议**：`Sider` 增加 `collapsible` 与可见 `trigger`，或窄屏改用 `Drawer`；把三列改成 `repeat(auto-fit, minmax(260px, 1fr))`；登录卡加 `width: min(420px, calc(100vw - 32px))`；在 `index.css` 补 `@media (max-width: 992px / 760px)` 两组断点。

### S-12 【严重｜②⑤｜已确认缺陷】文档页每次击键触发列表请求，且 `onSearch` 再触发一次

- **位置**：`frontend/src/pages/DocumentsPage.tsx:31-32` `filters` 为对象 state；`:48-53` `useEffect(() => { load(); }, [page, filters])`；`:92-94` `Input.Search` 的 `onChange={(e) => setFilters({...filters, q: e.target.value})}` 与 `onSearch={() => { setPage(1); load(); }}`。
- **现象**：`onChange` 每敲一个字符就生成新 `filters` 对象 → effect 立即 `load()` 发一次 `/documents`；按下回车/点搜索时 `onSearch` 又直接 `load()` 一次，同时 `setPage(1)` 可能再次触发 effect。没有防抖、没有请求取消、没有竞态保护（旧响应可覆盖新结果）。
- **影响**：输入 10 个字发 10+ 次请求；在演示限流（429）时会被自己的搜索框打爆；快速切换筛选时列表可能显示过期数据。
- **建议**：把 `q` 从 `filters` 中拆出为独立「草稿 state」，用 300ms `useDebounce` 后才合并进查询条件；`onSearch` 只负责立即提交，不去重调用 `load()`；`load` 用 `AbortController` 或请求序号丢弃过期响应；`useEffect` 依赖改用 `useMemo` 稳定后的查询串。

---

## 5. 一般（15 条）

### G-01 【一般｜②⑥｜已确认缺陷（含明确结论）】`MutationObserver` **不会**无限回调，但 `suspended` 对异步派发的记录无效

- **位置**：`i18n.js:768` `var suspended = false`；`:873-885` `applyI18n()`（`suspended = true` @877，`finally suspended = false` @884）；`:894-912` `onMutation()`（`:895` `if (suspended || curr === 'zh') return;`，`:896` `suspended = true`，`finally suspended = false` @911）；`:931-939` `boot()` / `obs.observe(document.body, {childList:true, subtree:true, characterData:true})`。
- **结论（明确回答检查项）**：**不存在无限回调**。三重收敛保证：① `onMutation` 在 `curr === 'zh'` 时直接 return；② 所有写入都是幂等比较后赋值 —— `handleTextNode` @820 `if (node.nodeValue !== next)`、`handleAttrs` @835 `if (el.getAttribute(a) !== next)`、`handleDataI18n` @849 `if (el.textContent !== next)`、`handleRich` @861 `if (el.innerHTML !== target)`；③ `ORIG_TEXT / ORIG_ATTR / ORIG_HTML` 三个 WeakMap 保存中文原文，二次扫描算出的 `next` 与当前值相同，不再写入。另：`handleAttrs` 的 `setAttribute` 属于 attribute 变更，观察配置里没有 `attributes: true`，本身不产生记录。
- **但有证据的缺陷**：MutationObserver 的回调在**微任务**里异步派发，而 `suspended` 是同步布尔量、`finally` 在回调返回前就复位为 `false`。因此引擎自身写入产生的记录仍会被下一次 `onMutation` 消费，**`suspended` 实际挡不住自触发，只是靠幂等性收敛**，每次 `applyI18n()` / 每次动态 `innerHTML` 都会多跑一轮完整扫描。与 S-04 的 2.5s 无条件重写叠加后，EN 模式下是持续的重复工作。
- **建议**：在 `onMutation` 结束前调用 `obs.takeRecords()` 丢弃自己造成的记录；或改为 `obs.disconnect()` → 写入 → `obs.observe(...)`；或给写入节点打 `data-i18n-own` 标记并在回调里跳过。同时把「不会无限循环」的原因写成注释，避免后人误改幂等比较。

### G-02 【一般｜⑤｜已确认缺陷】543 分支的大正则 + 全文档 TreeWalker

- **位置**：`i18n.js:775-785` `SUB_KEYS`（≥3 字、无 `{`、无 `<` 的 key，实测 543 条）拼成单个 `new RegExp(SUB_KEYS.map(escapeRe).join('|'), 'g')`；`:788-803` `t()` 在精确匹配失败后对每个文本节点跑 `raw.replace(SUB_RE, ...)`；`:873-885` `applyI18n(document)` 每次语言切换都 `createTreeWalker(root, SHOW_TEXT)` 全量扫描 + `handleAttrs` 的 `querySelectorAll('*')`。
- **影响**：EN 模式下每个动态文本节点都要过一次 543 分支正则；`setLang()` 与 `switchView()`（`app.js:179`）都会触发全量扫描。字典继续增长时线性变差。
- **建议**：把片段替换换成 Aho-Corasick 或按首字建 `Map` 分桶；`applyI18n` 支持传入脏节点集合而非 `document`；长句优先走精确 key，短片段只作为兜底。

### G-03 【一般｜④⑥｜已确认缺陷】EN 模式首屏闪中文（语言 FOUC）

- **位置**：`i18n.js:931-935` `boot()` 在 `DOMContentLoaded`（`:942`）才执行 `if (curr === 'en') applyI18n(document)`；`index.html:9` 把 `i18n.js` 放在 `<head>` 但是同步脚本、逻辑延迟到 DOMContentLoaded；`index.html:8` 的 `theme.js` 在 `:52-53` 解析期就写好 `data-theme` / `data-mode` + `kb-boot`，因此**主题无闪烁、语言有闪烁**。
- **影响**：保存过 `kb_lang=en` 的用户每次刷新都会先看到一帧中文。既有硬约束只解决了主题闪烁。
- **建议**：`i18n.js` 在 `<head>` 同步阶段先读 `localStorage.kb_lang` 并给 `<html>` 加 `data-lang`；配合 `html[data-lang="en"]` 的过渡抑制，或把首次 `applyI18n` 提前到 `DOMContentLoaded` 之前对静态 DOM 做一次预扫描。

### G-04 【一般｜⑥｜已确认缺陷】`api()` 缺 Content-Type 校验与网络错误友好化

- **位置**：`webapp/static/app.js:49-70` `api()`；`:82` `return res.json()`（无 try/catch、不检查 `Content-Type`）；`:54-63` 401 分支；`:64-68` 非 2xx 分支。
- **现象**：服务端返回 200 但 body 为空 / 是 HTML（如反向代理错误页）时，`res.json()` 抛 `SyntaxError: Unexpected token '<'`，调用方把这句英文原文 toast 给中文用户；`fetch` 网络失败抛出 `TypeError: Failed to fetch` 同样直出。401 分支 `!path.startsWith('/api/auth/')` 的判定正确（登录接口的 401 不会被误判为令牌过期），但 `forceLogout()` 后调用方只 catch 到 `'unauthorized'`，不清理局部加载态。
- **建议**：`api()` 统一包一层：先读 `res.headers.get('content-type')`，非 JSON 抛「服务返回了非预期内容（HTTP xxx）」；网络异常统一映射为「网络不可用，正在自动重连…」；把所有错误文案纳入 `i18n.js` 字典。

### G-05 【一般｜⑥｜已确认缺陷】导出下载立即 `revokeObjectURL` 且绕过统一鉴权

- **位置**：`webapp/static/app.js:786-795` `exportBadcases()`；`frontend/src/pages/DashboardPage.tsx:30-38` `exportCsv()`。
- **现象**：两处都是 `a.click()` 后**立刻** `URL.revokeObjectURL(url)`，部分浏览器（Firefox / 旧 Safari）会在下载真正开始前释放 blob；`<a>` 也没有 append 到 DOM；`exportBadcases` 用裸 `fetch` 绕过了 `api()`，令牌过期时 401 不会被 `forceLogout` 处理，反而把错误响应体当 CSV 下载；`DashboardPage.exportCsv` 用裸 `fetch` 携带 `Bearer`，绕过 axios 的刷新拦截器，也没有 `credentials: 'include'`。
- **建议**：`document.body.appendChild(a)` → `a.click()` → `setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 0)`（或 `requestAnimationFrame` 后）；导出统一走带 401 处理的请求封装；下载前检查 `Content-Disposition` / `Content-Type`。

### G-06 【一般｜②⑤⑥｜已确认缺陷】`SearchPage` SSE 解析缺坏帧保护 + 逐 token 重渲染

- **位置**：`frontend/src/pages/SearchPage.tsx:50-72` `doAnswer()`；`:67` `const parsed = JSON.parse(data);` **无 try/catch**（对照 `useProgressStream.ts:94` 有 `try { handleEvent(...) } catch { /* 忽略坏帧 */ }`）；`:68` `setAnswer((prev) => prev + parsed.text)`。
- **现象**：任意一个畸形 SSE 帧就会抛出 `JSON.parse` 异常，被 `:73` 的外层 `catch` 捕获后 `message.error(e.message)` 显示原始 JSON 报错，且**整条流被中断**、已收到的部分答案保留但不标记为「中断」。每个 token 触发一次 React 渲染（真实配置实测 70 个 chunk，见 `webapp/out/eval_report.json`）。另外 `doAnswer` 没有 `AbortController`，组件卸载后流仍在读。
- **建议**：与 `useProgressStream` 复用同一个 SSE 解析函数（见 R-01）；`JSON.parse` 包 try/catch 并跳过坏帧；用 `requestAnimationFrame` 或 ref 缓冲批量 `setAnswer`；`useEffect` 清理时 `abort()`。

### G-07 【一般｜②⑥｜已确认缺陷】上传进度用数组下标算完成数、同名文件互相覆盖

- **位置**：`frontend/src/pages/UploadCenterPage.tsx:69-79` `setLocal()` 以 `filename` 为 key；`:104-111` `pending.indexOf(idx)` 计算 `done_units` / `progress`；`:186-191` `beforeUpload` / `onRemove` 与 `fileList`（`uid: f.name`）。
- **现象**：`pool()` 以 4 并发执行，分片完成顺序与 `pending` 数组顺序不一致，而进度用 `pending.indexOf(idx)+1` 当作「已完成数」，导致进度条可能倒退或跳变。`setLocal` 与 `onRemove` 都按文件名索引，选择两个同名文件（不同目录/多次选择）时状态互相覆盖、删除一个会同时删掉两个；`uid: f.name` 也会产生重复 key。
- **建议**：用 `useRef` 的原子完成计数器（每次 `putChunk` 成功 `++`）算进度；以 `file.uid` 或 `file.lastModified + size + name` 作唯一 key；`fileList` 的 `uid` 用递增 id。

### G-08 【一般｜②⑥｜已确认缺陷】`TaskCenterPage` 双 effect + 轮询竞态

- **位置**：`frontend/src/pages/TaskCenterPage.tsx:18-27` `load()`；`:29` `useEffect(() => { load(); }, [page, state])`；`:30-33` 另一个 `useEffect` 同样依赖 `[page, state]` 并 `setInterval(load, 5000)`。
- **现象**：`load` 每次渲染重新创建但不在依赖数组里（ESLint `exhaustive-deps` 会告警）；两个 effect 职责重叠，筛选变化时机同；5 秒轮询的旧响应可能在用户手动刷新/切页之后返回，覆盖较新数据（`setRows` 无请求序号保护）；组件卸载后没有取消在途请求。
- **建议**：合并为一个 effect；`load` 用 `useCallback` 并纳入依赖；加 `AbortController` 或自增 `requestId` 丢弃过期响应；卸载时清理。

### G-09 【一般｜⑥｜已确认缺陷】统一错误契约的 `trace_id` 与 422 详情在前端被丢弃

- **位置**：`frontend/src/api/client.ts:38` `const message = error.response?.data?.message || error.message || '请求失败';`；对照 `backend/app/core/errors.py:54-62` 返回 `{ code, message, detail, trace_id }`，`:70-77` 500 也带 `trace_id`；FastAPI 原生 `RequestValidationError`(422) 返回 `{ detail: [...] }`，无 `message` 字段。
- **现象**：422 校验错误落到 `error.message`（`Request failed with status code 422`），用户看不到具体字段错误；统一错误契约里专门设计的 `trace_id` 前端完全不展示、不上报，日志无法与用户反馈对齐。注意：两套后端契约不同 —— `backend/` 用 `message`，`webapp/` 的 `HTTPException` 用 `detail`（`app.js:56/66` 读的是 `j.detail`），这是**按各自后端正确实现的**，不是缺陷，但极易在跨端复用时被误改。
- **建议**：`client.ts` 兼容 `{message, detail, code, trace_id}` 四种形态；把 `trace_id` 放进 toast / 错误面板并写入前端日志；在项目文档里明确「两套前端各连一套后端、错误体字段不同」。

### G-10 【一般｜⑥｜演示取舍（需标注）】React 登录页硬编码演示口令与 bootstrap 按钮

- **位置**：`frontend/src/pages/LoginPage.tsx:45` `initialValues={{ email: 'admin@demo.local', password: 'Admin@12345' }}`；`:56-57` 演示账号明文；`:28-37` `bootstrap()` + `:59` 「初始化演示数据」按钮调用 `POST /auth/bootstrap`；对照 `app.js:362-387` `?demo=admin` 分支与 `autoLoginAdmin()` 硬编码 `admin/admin123`（L378）。
- **判定**：**演示切片可接受的取舍，但必须显式标注**。依据：`backend/app/api/v1/auth.py:126-127` 有 `if settings.is_production: raise Forbidden("生产环境禁止调用")` 保护；`webapp/README.md:30-31` 明写「仅本地演示切片使用，生产环境必须移除该分支」。
- **风险**：React 骨架的登录页没有「仅演示」水印，容易被误认为是生产骨架的一部分；`?demo=admin` 分支在 `app.js:362` 有注释但没有任何运行时环境判断。
- **建议**：加 `import.meta.env.DEV` / `settings.is_demo` 条件渲染，登录页加「演示环境」角标；把口令移到 `.env.example`；README 账号表补部门列（见 S-09）。

### G-11 【一般｜①｜已确认缺陷】`tsconfig strict: false` + `any` 泛滥

- **位置**：`frontend/tsconfig.json:14` `"strict": false`；`frontend/src/types.ts:28,47,88` `Record<string, any>`；`frontend/src/api/endpoints.ts` 多处 `data: any`；`frontend/src/pages/DocumentsPage.tsx:31` `filters: any`；`SearchPage.tsx:13` `kbs: any[]`、`:49` `opt: any`；`UploadCenterPage.tsx:187,191` `as any`。
- **影响**：`types.ts` 定义了 15+ 个接口，但 `strict:false` + 大量 `any` 让类型约束在关键路径（筛选参数、API 载荷、antd fileList）上基本失效；`npm run build` 的 `tsc --noEmit` 检查力度有限。
- **建议**：分批开启 `strict`（先 `noImplicitAny` / `strictNullChecks`），把 `Record<string, any>` 换成 `Record<string, unknown>` 或具体联合类型；给 `api` 的 `data` 用泛型。骨架期可暂缓，但应作为已知债务登记。

### G-12 【一般｜④⑥｜已确认缺陷（有条件）】独立部署 `frontend` 镜像时 `/api` 无反代

- **位置**：`frontend/nginx.conf:8-10` 只有 `location / { try_files $uri $uri/ /index.html; }` 与 `location /assets/`；**没有 `location /api/` 的 `proxy_pass`**；`frontend/src/api/client.ts:3` `API_BASE` 默认 `'/api/v1'`；`frontend/Dockerfile:9-11` 把该 nginx.conf 打进独立镜像。
- **澄清（避免误报）**：`docker-compose.yml:193-202` 另有顶层 `nginx/nginx.conf`，其 `:18-33` 已配置 `/api/ -> api_upstream` 且 `proxy_buffering off` 支持 SSE。因此**在 compose 编排下功能正常**。
- **缺陷**：单独 `docker run` frontend 镜像、或用 `npm run preview`（`vite.config.ts:9-11` 的 8000 代理只在 dev 生效）时，所有 `/api/v1/*` 请求会命中 `try_files` 回退、返回 `index.html`（HTTP 200 HTML），`axios` 拿到字符串，`r.data.items` 为 `undefined` → 运行时报错且难以定位。
- **建议**：在 `frontend/nginx.conf` 增加 `location /api/ { proxy_pass http://api:8000; ... }`（可被顶层网关覆盖也无妨），或在 README 明确「frontend 镜像不可独立对外提供 API」；`vite preview` 补 `preview.proxy`。

### G-13 【一般｜⑤⑥｜改进建议（配置级）】Vite 开发代理对 SSE 无显式缓冲/超时配置

- **位置**：`frontend/vite.config.ts:9-11` 注释写「SSE 需要关闭代理缓冲，vite 默认支持流式转发」，但配置只有 `{ target: 'http://localhost:8000', changeOrigin: true }`，没有 `configure` / `timeout` / `proxyTimeout`。
- **影响**：`http-proxy` 默认行为不保证不缓冲长连接，本地开发调试 SSE 进度流/流式回答时可能出现事件被攒着不推；对比顶层 `nginx/nginx.conf:27-32` 已显式 `proxy_buffering off` + 3600s 超时，开发环境反而更弱。
- **建议**：在代理项加 `configure: (proxy) => { proxy.on('proxyRes', ...) }` 关闭缓冲、设置 `timeout/proxyTimeout`，或直接用 `VITE_API_BASE_URL` 指向后端端口绕过代理；把注释里的「默认支持」改成有依据的描述。

### G-14 【一般｜⑥｜已确认机制有效但脆弱】无 `value` 的 `<option>` 依赖 MutationObserver 时序固化提交值

- **位置**：`webapp/static/app.js:768` `openEditModal()` 生成 `<option ${v === r.verdict ? 'selected' : ''}>${v}</option>`（**无 value 属性**）；对照 `i18n.js:814-826` `handleTextNode()` 的 OPTION 固化逻辑（`p.dataset.i18nValue = 中文原文; p.value = ...`）；对照 `app.js:639` `openBadcaseModal()` 用的是显式 `value="错误"` 等。
- **结论**：机制**当前有效** —— ZH 模式下无 value 的 option 提交值天然等于中文文本；EN 模式下 `openModal()` 写 `innerHTML`（`app.js:38`）后 MutationObserver 在微任务里执行 `handleTextNode`，把中文原文固化进 `p.value`，因此 `$('ed-verdict').value` 仍提交中文，不破坏后端 `verdict` 判定。这符合既有硬约束。
- **风险**：固化发生在**异步微任务**里，且依赖「EN 模式 + MutationObserver 正常注册 + 容器未被 `skipped()` 命中」三个前提；一旦有人给弹窗容器加 `data-i18n-skip`、或提前读 `.value`、或改用 `applyI18n` 之外的方式插入，提交值就会变成英文，badcase 判定静默写错。两套弹窗（L626 显式 value / L768 隐式）写法也不一致。
- **建议**：**保留固化机制**（不要删），但 `openEditModal` 也显式写 `value="错误"` 等，让正确性不依赖观察者时序；把该约束写成回归用例（切 EN → 打开编辑弹窗 → 保存 → 断言提交值为中文）。

### G-15 【一般｜①⑥｜已确认缺陷】「多租户」措辞残留，与新口径「多租户为未来研发方向」不一致

- **位置**：`webapp/static/index.html:416` 「明确不做：…**多租户行级权限**…」；`app.js:947` 「逐行 / 逐段落 ACL 与**多租户行级权限**只预留架构，未实现」；`i18n.js:417` / `:463` 对应中英词条；`webapp/README.md:101` 「切片是单库**单租户**」、`:229` 「切片**单租户**」；`webapp/routers_evaltest.py:226` 「**单租户（default）**」、`:248` 「本切片是**单租户演示**」（后端渲染文本，前端原样展示）。
- **现象**：用户新指令要求「明确标注：多租户为未来研发方向，当前为多用户多部门」。现有「明确不做：多租户行级权限」把「未来方向」写成了「永久不做」；「单租户 default」属于已作废口径。注意：维度改名（`tenant→department_id`）已由并发提交落地（`index.html:519`、`app.js:1285`），本条只针对**组织标识类措辞**，不重复报已修项。
- **建议**：「明确不做」中该项改为「跨组织（多租户）隔离：本次不做，作为未来研发方向」并标注「当前为单组织 + 部门树逻辑隔离」；`app.js:947` / `i18n.js` 成对改；README 与后端规则集文案由对应负责人同步（经主理人中转）。

---

## 6. 建议（12 条，改进建议，非当前 bug）

| 编号 | 面 | 位置（精确） | 现象 | 改进建议 |
|---|---|---|---|---|
| R-01 | ①⑤ | `app.js:432-471`、`frontend/src/hooks/useProgressStream.ts:76-96`、`frontend/src/pages/SearchPage.tsx:50-72` | SSE 帧解析（`getReader` + `split('\n\n')` + `event:/data:`）**三处近乎复制**，健壮性却不一致（只有 `useProgressStream` 有坏帧 try/catch，`SearchPage` 没有；注释帧判断一处 `frame.startsWith(':')`、一处 `frame.trim().startsWith(':')`） | 抽 `parseSSE(stream, onEvent)` 公共函数（React 侧放 `src/lib/sse.ts`，静态侧放 `app.js` 内部函数或 `sse.js`）；统一支持 `\r\n\r\n` 分隔、`TextDecoder` 末尾 `decode()` 冲刷残留多字节 |
| R-02 | ① | `SearchPage.tsx:1` 与 `:6` 两次 `from 'react'`；`UploadCenterPage.tsx:1` 与 `:8` 同样；`DocumentsPage.tsx:3` 导入 `InputNumber` 但全文未使用（grep 仅 1 处命中） | 重复 import / 死导入；`tsconfig` 未开 `noUnusedLocals` 所以不报错 | 合并 import；删 `InputNumber`；开启 `noUnusedLocals` 并配 ESLint |
| R-03 | ① | `DocumentsPage.tsx:10` `statusColor`、`TaskCenterPage.tsx:6` `stateColor`、`UploadCenterPage.tsx:46` `statusColor`、`app.js:1282` `stBadge` | 状态→颜色映射 4 份，键名重叠但不一致（`succeeded/indexed/completed` 都映射到 green） | 抽 `src/constants/status.ts`（React）与 `app.js` 顶部常量表，统一枚举 |
| R-04 | ① | `frontend/package.json:6-9` 仅有 `dev/build/preview`；`frontend/` 下无 ESLint/Prettier/测试配置（已 grep 确认） | 无 lint、无 test、无格式化约束，`strict:false` 下问题只能靠人肉发现（S-07/S-08/G-11 均属此类） | 加 `lint`(`eslint src`)、`typecheck`(`tsc --noEmit`)、`test`(`vitest`) 脚本与最小配置；CI 里跑 `npm run build` |
| R-05 | ①② | `app.js:432` `const setStatus = (t) => { ... t.replace(...) }` | 参数名 `t` **遮蔽全局 i18n 函数 `t()`**，函数体内只当字符串用，暂时无害但极易误改（若以后在里面调 `t('...')` 会 `TypeError`） | 改名 `setStatus(html)`；全局搜索类似的 `t` 作变量名处（`app.js:622` / `:638` 的 `S.evalTasks.map(t => ...)`、`app.js:706` 的 `tasks.map(t => ...)` 同理） |
| R-06 | ① | `i18n.js:750-753` `EN_HTML`、`:860-869` `handleRich()`；`index.html` 中 `data-i18n-html` **命中 0 处** | 富文本整段替换机制与 `auth.hero` 词条为死代码（登录页 `<h1>` 实际靠普通文本节点 `t()` 翻译） | 要么删掉 `EN_HTML` / `handleRich` 减少引擎复杂度，要么补上 `data-i18n-html="auth.hero"` 并验证；当前状态容易被误认为「已实现富文本 i18n」 |
| R-07 | ①② | `index.html` 仅 `:567` 一处 `data-i18n`；`app.js` 中 `data-i18n=` 命中 5 处（`:323 / :516 / :539 / :548 / :1084`），而 `innerHTML` 赋值 59 处，其中 `:757` badcase 表头、`:847` `runSearch()`、`:878` `loadTasks()`、`:900` `loadEval()`、`:933` `loadAudit()`、`:950` `loadSystem()` 等的表格头与空态是硬编码中文 | 动态文案大量依赖 MutationObserver + `SUB_RE` 片段替换（见 S-03/G-02），而非既有约定的 `data-i18n="中文原文"` 可逆重设 | 新增视图/表格一律走 `app.js:1084-1085` 的 `etTh(key)` / `etEmpty(key)` 同款 `t(key)` + `data-i18n` 模式；把「动态文案必须加 data-i18n」写成 lint 规则 |
| R-08 | ④③ | `style.css:492-500`（760px 断点：`.nav-group{display:none}` / `.nav .nav-tag{display:none}` / `.side-foot{display:none}`） | 11 个导航项在窄屏变成**无分组标签**的横向滚动条，ADMIN/OBS/DEV 徽标隐藏后权限提示消失；侧栏底部「离线演示切片 · SQLite 真值」说明也消失 | 保留分组名（改为行内小标题）或加 `aria-label`；把「离线演示切片」说明移到顶栏或系统页，避免窄屏丢失诚实边界提示 |
| R-09 | ③ | `style.css:378` `.table-wrap { overflow:auto; max-height:520px }`；`app.js:744` badcase 表 9 列 | 窄屏横向滚动时表头不吸顶，长表滚到中部后列名不可见 | `thead th { position: sticky; top: 0; background: var(--surface-2); z-index:1 }`；或窄屏改卡片式列表 |
| R-10 | ① | `index.html:8,9,10,646` 四处 `?v=11`（**已确认全部一致，无缺陷**）；`webapp/selftest.py:368` 请求 `/app.js?v=8` | 测试里残留旧版本号；静态路由忽略 query 所以不影响判定，但会掩盖「升版漏改」的真实问题 | 把 `selftest.py:368` 同步为当前版本；把「改任一 JS/CSS → 四处 `?v` 一次性同升且 `style.css` 与 `app.js` 同号」写成检查脚本；`docs/CHANGELOG.md` 已有 v=11 记录（本次按纪律不动该文件） |
| R-11 | ③④ | `frontend/src/index.css` 全文无 `:focus-visible` / `prefers-reduced-motion` / 暗色；`frontend/src/main.tsx:14-20` 硬编码 `colorPrimary:'#2563eb'`；对照 `style.css:92,97-100` 两者齐备 | B 套前端缺无障碍与动效降级基线；两套前端设计系统不互通（A 套 6 主题色 × 明暗正交，B 套单一浅色） | B 套补 `:focus-visible` 与 `@media (prefers-reduced-motion:reduce)`；抽共享设计令牌（色彩/间距/圆角）文档；至少在 README 说明「两套前端视觉不统一是骨架期取舍」 |
| R-12 | ②⑥ | `frontend/src/App.tsx:13-18` `Protected`、`:20-37` 路由表 | 无 `ErrorBoundary`（任一页面抛错整站白屏）；路由只校验 `user` 不校验 `role`，`/tasks` `/versions` 等对普通用户可见但后端 403 | 加顶层 `ErrorBoundary` + 兜底页；加 `<RequireRole>` 包装或在菜单按角色过滤；与后端 `require_admin` 口径对齐 |

---

## 7. 演示切片可接受的取舍（明确列出，不是缺陷）

以下均已实查，**在「离线可跑、A/B 档、面试讲解」定位下可接受**，报告不要求现在改；但每条给出「何时不再可接受」的触发条件。

| # | 取舍 | 证据 | 可接受理由 | 何时必须改 |
|---|---|---|---|---|
| 1 | 零构建原生 JS、单文件 `app.js` 1314 行、全局 `S` 对象 | `app.js:5-20` | 项目定位「零构建静态页」便于双击/单端口演示与代码精读；引入框架会推翻既有契约 | 需要多人协作开发真实业务、或页面数继续增长（>20）时 |
| 2 | 50 处内联 `onclick=` | `index.html` 全文计数 | 无构建、无打包，事件绑定最简；`storeModalData()`（`app.js:42`）已规避把用户输入拼进属性 | 引入 CSP（`script-src` 禁止 unsafe-inline）时 |
| 3 | 59 处 `innerHTML` 拼接 | `app.js` 计数 | 已有 `esc()`（`app.js:24-25`）统一转义 + `data-i18n-skip` 约定；演示数据均为本地 SQLite | 接入不可信外部内容前必须改 DOM API / 加 DOMPurify（另见 S-03：跳过表覆盖不全需先补） |
| 4 | `?demo=admin` 自动登录 + 硬编码 `admin/admin123` | `app.js:362-387`（口令在 L378）；`webapp/README.md:30-31` 明写「生产环境必须移除」 | 一键看到含管理后台全貌，是演示切片的显式设计 | **任何非 127.0.0.1 的部署**（当前无运行时环境判断，建议尽快加 `is_demo` 守卫） |
| 5 | 2.5s 轮询替代 WebSocket / Redis pub/sub | `app.js:148`；`index.html:107` 已注明「进程内队列」 | 演示切片用进程内信号量 + SQLite 作替身，页面明确标注「不是 Celery + RabbitMQ」 | 上生产 / 需要 <1s 进度反馈时 |
| 6 | SQLite + 进程内队列替身 | `webapp/db.py`、`webapp/queue.py`；`index.html:404-409` 替身映射表 | 契约与生产骨架对齐，换掉替身即真链路；界面已诚实标注 | 讲成「已实现生产级 ACL / PG / Milvus」时（口径失真） |
| 7 | i18n 手写字典 951 行、中文原文即 key | `i18n.js:12-741` | 无构建约束下最简单可逆方案；`theme.js` 防闪烁、`kb-boot` 首帧禁过渡均已到位 | 字典超过千条 / 需要复数、日期本地化时（另见 G-02 性能） |
| 8 | B 套 React 骨架无 i18n / 无暗色 / `strict:false` | `frontend/src/index.css`、`tsconfig.json:14` | 定位「开发框架骨架」，A 套才承担演示职责；`README.md:29-30` 已声明 A/B 档之间 | 进入 B 档加固（真实 embedding / 越权用例集 / trace 全链路）时 |
| 9 | 两套前端并存、视觉与设计令牌不统一 | A 套 `style.css` vs B 套 `index.css` | 分别对应「演示切片」与「生产骨架」，`index.html:404-409` 有映射表说明 | 团队决定以 React 为唯一前端时 |
| 10 | `webapp` 中 `usage_events` 的部门维度为成本归因、不做 429 按部门隔离 | `webapp/observability.py:36-39` 注释与 `RATE_DIMENSIONS` | 与 C18 口径一致，避免把 Won't 说成已落地 | 产品明确要求按部门分级配额时 |

---

## 8. 可量化复核方式（全部离线，不运行真实外部 LLM）

### 8.1 命令级（已执行，结果见 §1.3）

```bash
# 语法
node --check webapp/static/app.js && node --check webapp/static/i18n.js && node --check webapp/static/theme.js

# 资源版本一致性（应输出 4 行且版本号相同）
grep -n '?v=' webapp/static/index.html

# 视图/加载器双向齐全（应各 11）
python -c "import re;h=open('webapp/static/index.html',encoding='utf-8').read();\
print('data-view',re.findall(r'data-view=\"([^\"]+)\"',h));\
print('sections',re.findall(r'id=\"view-([^\"]+)\"',h))"

# 重复 id（应为 {}）
python -c "import re,collections;h=open('webapp/static/index.html',encoding='utf-8').read();\
print({k:v for k,v in collections.Counter(re.findall(r'\bid=\"([^\"]+)\"',h)).items() if v>1})"

# 字典 / 大正则规模（当前约 639 / 543）
python -c "import re;s=open('webapp/static/i18n.js',encoding='utf-8').read();\
k=re.findall(r\"^\s*'([^']*[\u4e00-\u9fff][^']*)'\s*:\",s,re.M);\
print('keys',len(k),'SUB_RE',len([x for x in k if len(x)>=3 and '{' not in x and '<' not in x]))"

# 响应式基线差异
grep -c '@media' webapp/static/style.css        # 5
grep -c '@media' frontend/src/index.css          # 0
grep -c ':focus-visible' frontend/src/index.css  # 0

# 本地离线回归（不消耗外部额度；按既有报告口径）
#   python -m pytest webapp/tests -q -p no:cacheprovider      → 51 passed（隔离库 + 强制离线）
#   python webapp/selftest.py                                  → 需临时服务；用隔离库 + 三个 provider 环境变量强制 offline/off，勿用真实 key
#   python demo/selftest.py                                    → 16/16，run_id 不变
#   python -m unittest discover -s eval/tests -t .             → 14 OK
#   python -m unittest discover -s evalbench/tests -t .        → 16 OK
```

> **纪律**：以上 pytest/selftest 命令只作为「可量化复核方式」列出，本次检查**未执行**任何会触发真实外部 LLM 的调用；如需实跑，按 `docs/org-model-change-plan.md:22` 的隔离库 + 强制离线方式，并在跑完后还原 `webapp/out/selftest_report.json`。

### 8.2 断点 / 手工步骤（针对已确认缺陷）

| 步骤 | 复现方式 | 期望观察 |
|---|---|---|
| **M1**（F-01 跨用户串号） | 在 `app.js:117` `forceLogout` 与 `app.js:145` 下断点；用 A 账号发一条问答 → 退出 → 用 B 账号登录 | 断点处 `S.conversationId` 不为空、`#chat-log` 仍有 A 的气泡；B 看到 A 的对话 |
| **M2**（F-02 流式锁死） | 在 `app.js:443`（401 分支）与 `app.js:520`（`S.streaming=false`）下断点；让 `/api/chat/stream` 返回 401（可直接改 localStorage 里的 token 为非法值） | 命中 443、**不命中 520**；重新登录后点发送，L418 守卫直接 toast「正在生成回答，请稍候」 |
| **M3**（S-01 重复请求） | 切到「评估测试」页 → 打开 DevTools Network 过滤 `/api/evaltest/` → 顶栏切一次语言 | 同一接口成对出现两次；在 L218 与 L1307 下断点可看到两个监听依次命中 |
| **M4**（S-02 内存增长） | 控制台周期性打印 `Object.keys(S._modalData).length`；反复切视图 / 切语言 / 发问答 | 数值单调上升，`closeModal()` 后不回落 |
| **M5**（S-03 用户数据被翻译） | 造一条中文 `issue_desc`（如「具体章节未给出」）→ 切到 EN → 打开管理后台 badcase 表 | 该单元格中文被片段替换；`SKIP_SELECTORS` 未命中该 `td` |
| **M6**（G-01 Observer 收敛性） | 在 `i18n.js:894 onMutation` 与 `:820`（`node.nodeValue !== next`）下条件断点；切一次语言 | `onMutation` 会额外触发 1～2 轮，但 `nodeValue !== next` 很快恒为 false → **无无限循环**；同时可观察到 `suspended` 在回调派发时已是 false |
| **M7**（S-04 无效重渲染） | 在 `app.js:268` 下断点，页面静置不操作 | 每 2.5 秒必命中一次，即使 `html` 内容与上次完全相同 |
| **M8**（S-05/S-06 进度流） | 在 `useProgressStream.ts:52`（`close`）与 `:98`（`catch`）下断点；让 SSE 正常结束但不发 `close` 事件 | `close` 分支中 `poll()` 的 `if (!stopped)` 为 false；正常 EOF 不进 `catch`，`startPollingFallback()` 从未调用 |
| **M9**（S-08 StrictMode 双刷新） | DevTools Network 过滤 `/auth/refresh`，React 开发模式（`main.tsx:11` StrictMode）刷新页面 | 两条并发 refresh 请求，其中一条 401；`setUser(null)` 被调用 |
| **M10**（S-11 响应式） | DevTools 设备工具栏设 768×1024，打开 React 骨架 | 侧栏宽度 0、无展开按钮、导航不可达；看板三列被压扁 |
| **M11**（G-14 提交值） | 切 EN → 打开 badcase「处理」弹窗 → 在 `app.js:780` 下断点读取 `$('ed-verdict').value` → 保存 | 值为中文（`错误`/`部分正确`…），证明固化生效；再临时给弹窗容器加 `data-i18n-skip` 重测，观察值变成英文 → 印证时序脆弱性 |

### 8.3 修复后的回归判定

- `node --check` ×3 全过；`grep -n '?v='` 四处同号；`data-view` 与 `id="view-*"` 集合相等。
- F-01/F-02 用 M1/M2 断点验证不再命中；S-01 用 M3 验证单请求；S-04 用 M7 验证空闲时不再命中。
- 前端语法与构建：`cd frontend && npm run build`（`tsc --noEmit && vite build`）应通过；`npm run lint` 在 R-04 落地后应零错误。
- **禁止**：为验证而调用真实 DeepSeek / 硅基流动接口；`eval/`、`demo/`、`evalbench/`、`docs/CHANGELOG.md` 在本次检查中零改动。

---

## 9. 与进行中任务的关系 / 越界声明

- 本检查期间观测到其它成员的并发提交：`st-llm-thinking`（`index.html:561-567` + `app.js:964/1026` + `webapp/settings.py`）已完整接入 `loadSettings`/`saveSettings`，**未发现该控件漏接**（已专门核查，避免误报）；成本/限流维度 `tenant → department_id` 已落地到 `index.html:519`、`app.js:1285`、`webapp/observability.py:36-39`、`webapp/db.py:163`。因此本报告**不把已修项列为缺陷**，仅在 S-09 / G-15 指出尚未同步的「组织标识类措辞」。
- 本报告**不输出**后端实现、数据迁移 DDL、部署 CI/CD、完整测试用例清单；涉及后端契约的地方（`errors.py`、`auth.py`、`observability.py`）仅作为前端表现的证据引用。
- 本报告**未修改**任何源码；`eval/`、`demo/`、`evalbench/`、`docs/CHANGELOG.md` 零改动；未运行任何真实外部 LLM 调用。
- 需要跨成员同步的项：S-09（React `tenant` 字段）、G-15（README / `routers_evaltest.py` 措辞）、G-12（frontend nginx 反代）—— 均需经主理人中转，本报告只列事实与建议。
