/* i18n 引擎：中文原文即 key 的双语字典 + DOM 自动扫描 + 无刷新切换。
 * 设计取舍：
 * - zh 模式直接返回原文，零替换、零性能损耗，也不会改变任何既有数据/表单值；
 * - en 模式用 TreeWalker 扫描文本节点 + 占位符/属性，MutationObserver 覆盖动态 innerHTML；
 * - 「用户数据容器」在 SKIP_SELECTORS 中排除，避免把问题/答案/文件名当界面文案翻译；
 * - 无 value 的 <option> 会先把中文原文固化到 value，保证翻译后提交值仍是中文（不破坏后端判定）；
 * - 语言/主题/模式各自独立持久化，互不覆盖。
 */
(function () {
  'use strict';

  var EN = {
    /* ---- 品牌 / 登录 ---- */
    '企业级知识库': 'Knowledge Base',
    '企业级知识库系统': 'Enterprise Knowledge Base',
    '企业级知识库 · 可运行演示切片': 'Enterprise KB · Runnable Demo Slice',
    '从文档到问答，': 'From documents to answers,',
    '再到评测闭环。': 'and on to an evaluation loop.',
    '一个可运行、可讲解的开发切片：注册登录 → 文档上传管理 → 流式问答 → badcase 采集 → 管理员完成度与导出。':
      'A runnable, presentable dev slice: sign-up / sign-in → document upload → streaming Q&A → badcase capture → admin completion & export.',
    '文档上传与分析排队': 'Upload & Analysis Queue',
    '繁忙时显示队列位置，分析降级留痕，状态实时可见': 'Queue position while busy, degradation is logged, status visible in real time',
    '流式问答与断线恢复': 'Streaming Q&A & Reconnect',
    'SSE 逐 token 输出，断线自动续传，检索为空主动降级': 'SSE token streaming, auto resume, explicit degrade on empty retrieval',
    'badcase 采集闭环': 'Badcase Capture Loop',
    '一键标记关联评估任务，管理员按条件检索与导出': 'Tag to an eval task; admins filter and export by criteria',
    '管理员完成度看板': 'Admin Completion Board',
    '任务总数 / 已完成 / 待处理 / 完成率 / 状态一目了然': 'Targets / done / pending / rate / status at a glance',
    '欢迎回来': 'Welcome back',
    '创建账号': 'Create account',
    '登录以进入知识库工作台': 'Sign in to the workspace',
    '注册一个普通账号，体验问答与 badcase 采集': 'Register a standard account to try Q&A and badcase capture',
    '登录': 'Sign in',
    '注册账号': 'Sign up',
    '注册并登录': 'Sign up & sign in',
    '用户名': 'Username',
    '请输入用户名': 'Enter your username',
    '邮箱（可选）': 'Email (optional)',
    '密码': 'Password',
    '至少 6 位': 'At least 6 characters',
    '演示管理员账号：': 'Demo admin:',
    '（管理后台仅对 admin 可见）。': '(admin console visible to admins only).',
    '也可以直接注册一个普通账号体验问答与 badcase 采集。': 'Or register a standard account to try Q&A and badcase capture.',
    '请输入用户名和密码': 'Enter username and password',
    '认证失败': 'Authentication failed',
    '登录已过期，请重新登录': 'Session expired, please sign in again',
    '未登录或令牌缺失': 'Not signed in or token missing',
    '已退出登录': 'Signed out',
    '已自动登录演示管理员账号': 'Signed in as demo admin',

    /* ---- 导航 / 顶栏 ---- */
    '工作台': 'Workspace',
    '质量与治理': 'Quality & Governance',
    '系统': 'System',
    '工作台概览': 'Overview',
    '智能问答': 'Chat Q&A',
    '文档管理': 'Documents',
    '检索调试': 'Retrieval Debug',
    '任务中心': 'Task Center',
    '评测中心': 'Eval Center',
    '评估测试': 'Eval & Observability',
    '管理后台': 'Admin Console',
    '审计日志': 'Audit Log',
    '模型设置': 'Model Settings',
    '系统设置与边界': 'System & Boundaries',
    '退出': 'Sign out',
    '系统运行中': 'System running',
    '连接中断': 'Disconnected',
    '上传分析繁忙': 'Ingest busy',
    '离线演示切片 · SQLite 真值': 'Offline demo slice · SQLite source of truth',
    '占位 LLM / 进程内队列': 'Stub LLM / in-process queue',
    '空闲': 'Idle',
    'DEV': 'DEV', 'ADMIN': 'ADMIN',

    /* ---- 通用控件 ---- */
    '刷新': 'Refresh',
    '查询': 'Search',
    '重置': 'Reset',
    '关闭': 'Close',
    '取消': 'Cancel',
    '保存': 'Save',
    '保存记录': 'Save record',
    '处理': 'Handle',
    '删除': 'Delete',
    '上传': 'Upload',
    '发送': 'Send',
    '新建对话': 'New chat',
    '生成示例说明书': 'Generate sample doc',
    '下载 CSV（当前筛选）': 'Download CSV (current filter)',
    '下载 JSON（当前筛选）': 'Download JSON (current filter)',
    '全部任务': 'All tasks',
    '全部状态': 'All statuses',
    '全部来源': 'All sources',
    '全部动作': 'All actions',
    '待处理': 'Pending',
    '复核中': 'In review',
    '已解决': 'Resolved',
    '聊天': 'Chat',
    '问答': 'Q&A',
    '手工': 'Manual',
    '评测': 'Eval',
    '关键词': 'Keyword',
    '起始日期': 'Start date',
    '结束日期': 'End date',
    '关键字检索': 'Keyword',
    '问题 / 回答 / 问题说明': 'Question / answer / issue',
    '操作人': 'Actor',
    '精确匹配': 'Exact match',
    '动作': 'Action',
    '状态': 'Status',
    '来源': 'Source',
    '提供者': 'Provider',
    '提供者：': 'Provider:',
    '保存设置': 'Save settings',
    '保存后即时生效，无需重启。': 'Applies immediately, no restart needed.',
    '测试连接': 'Test connection',
    '测试中…': 'Testing…',
    '测试中': 'Testing',
    '当前生效': 'Effective configuration',
    '共 0 条记录': '0 records',
    '正在读取…': 'Loading…',
    '正常': 'Normal',
    '是': 'Yes', '否': 'No',

    /* ---- 文档管理 ---- */
    '上传说明书等文本，系统自动分块建索引；繁忙时会排队并实时反馈。':
      'Upload manuals and text; the system chunks and indexes them. Busy periods queue with live feedback.',
    '上传文档': 'Upload documents',
    '点击选择文件，或拖拽到此处': 'Click to choose files, or drop them here',
    '支持 txt / md / csv / json / py 等纯文本；其他格式会给出降级提示':
      'Plain text: txt / md / csv / json / py. Other formats degrade with an explicit notice.',
    '分析并发上限为 2，超出后自动排队 —— 可连续上传多个文件观察繁忙提示。':
      'Ingest concurrency is 2; extra files queue. Upload several files to see the busy banner.',
    '运行状态': 'Runtime status',
    '已上传文档': 'Documents uploaded',
    '已就绪': 'Ready',
    '分块总数': 'Total chunks',
    '处理失败 / 降级': 'Failed / degraded',
    '文档列表': 'Document list',
    '还没有文档，先上传一份或点击「生成示例说明书」。': 'No documents yet. Upload one or generate a sample.',
    '文件名': 'File', '大小': 'Size', '分块': 'Chunks', '上传时间': 'Uploaded', '操作': 'Actions',
    '块': ' chunks',
    '排队中': 'Queued', '分析中': 'Analyzing', '降级/失败': 'Failed / degraded',
    '已接收「{n}」，进入分析队列': 'Received "{n}", queued for analysis',
    '上传「{n}」失败：': 'Upload "{n}" failed: ',
    '示例说明书已生成并进入分析队列': 'Sample document generated and queued',
    '文档已删除': 'Document deleted',
    '系统空闲，上传文档将即时开始分析。': 'System idle; uploads will be analyzed immediately.',
    '上传分析繁忙，超出并发闸的请求已排队。': 'Ingest busy; requests beyond the concurrency gate are queued.',
    '系统空闲，可即时分析。': 'System idle, ready to analyze.',
    '当前 {a} 个任务正在分析，{q} 个排队。': 'Currently {a} analyzing, {q} queued.',
    '处理中：{n}': 'Processing: {n}',
    '排队中：{n}': 'Queued: {n}',
    '后端不可达': 'Backend unreachable',

    /* ---- 智能问答 ---- */
    '流式输出 · 检索为空自动降级 · 断线自动续传 · 一键标记 badcase':
      'Streaming · degrade on empty retrieval · auto resume · one-click badcase tagging',
    '输入问题，例如：如何给设备配网？': 'Ask a question, e.g. How do I configure the device network?',
    '就绪 · Enter 发送，Shift+Enter 换行': 'Ready · Enter to send, Shift+Enter for newline',
    '完成 · Enter 发送，Shift+Enter 换行': 'Done · Enter to send, Shift+Enter for newline',
    '连接异常结束': 'Connection ended unexpectedly',
    '正在检索知识库并流式生成…': 'Retrieving knowledge base and generating…',
    '连接中断，正在续传（第 {r} 次重试，已续 {n} 个 token）…':
      'Connection lost, resuming (retry {r}, {n} tokens resumed)…',
    '[连接中断，重试次数已用尽，请重新发送]': '[Connection lost, retries exhausted. Please resend.]',
    '正在生成回答，请稍候': 'Generating, please wait',
    '知识库助手': 'KB Assistant',
    '你好，我是知识库问答助手。请先到「文档管理」上传说明书，然后向我提问；回答会以流式逐字输出。<br/><br/>若知识库没有命中，我会明确告知，不会编造内容。':
      'Hi, I am the knowledge base assistant. Upload a manual in "Documents" first, then ask me a question; answers stream token by token.<br/><br/>If nothing is retrieved, I will say so explicitly instead of making things up.',
    '历史会话': 'Conversations',
    '（点击可追溯来源）': '(click to trace sources)',
    '还没有历史会话': 'No conversations yet',
    '本次会话': 'Current session',
    '会话 ID': 'Session ID',
    '消息数': 'Messages',
    '已标记 badcase': 'Badcases tagged',
    '连接状态': 'Connection',
    '流式传输中': 'Streaming',
    '尚未开始': 'Not started',
    '降级与恢复': 'Degradation & recovery',
    '知识库无命中 → 明确提示，不编造': 'No retrieval hit → explicit notice, no hallucination',
    '流中断 → 携带 resume_from 自动续传（最多 3 次）': 'Stream break → auto resume with resume_from (max 3)',
    '后端不可达 → 顶部断线横幅，恢复后自动刷新': 'Backend down → offline banner, auto refresh on recovery',
    '令牌失效 → 自动退回登录页': 'Token expired → return to sign-in',
    '试试这些问题': 'Try these questions',
    '如何给设备配网？': 'How do I configure the device network?',
    'badcase 谁能查看？': 'Who can view badcases?',
    '量子纠缠如何应用？（触发降级）': 'How is quantum entanglement applied? (triggers degrade)',
    '标记为 badcase': 'Tag as badcase',
    ' · 流式完成': ' · stream complete',
    '引用来源（{n} 条）· 点击查看原文': 'Sources ({n}) · click to view original',
    '无引用来源 · 本次回答未命中知识库（占位拒答或降级，不编造）':
      'No sources · nothing retrieved (placeholder refusal or degradation, no hallucination)',
    '未知文档': 'Unknown document',
    '分块 #': 'Chunk #', '分块': 'Chunk',
    '得分 ': 'Score ',
    '引用来源详情': 'Source detail',
    '来源文档': 'Source document',
    '分块序号': 'Chunk index',
    '相关度得分': 'Relevance score',
    '检索方式': 'Retrieval mode',
    '重排': 'Rerank',
    '已载入历史会话（含来源追溯）': 'Conversation loaded (with source trace)',
    '载入历史失败：': 'Failed to load history: ',
    '条 · 含来源': ' msgs · with sources',
    '条': ' msgs',
    '降级提示：模型服务暂时不可用或触发限流，已自动降级为本地拼接回答（仍基于检索片段）。稍后可重试以获取完整生成。':
      'Degraded: model unavailable or rate-limited; fell back to a locally assembled answer (still from retrieved chunks). Retry later for full generation.',
    '降级提示：当前模型额度（墨点）已用尽，未自动重试，本次为本地拼接回答。可在「模型设置」换模型或换供应商后重试。':
      'Degraded: model quota exhausted, no auto retry; this is a locally assembled answer. Switch model or provider in Model Settings.',
    '降级提示：流式生成中途中断，以上为已收到的部分内容。可重新提问以获取完整回答。':
      'Degraded: generation interrupted mid-stream; the above is partial. Ask again for a complete answer.',
    '降级提示：当前模型未返回正文（可能只输出了推理过程），已自动降级为本地拼接回答。可在「模型设置」换成非推理模型（如 qwen2.5-coder-7b-instruct）后重试。':
      'Degraded: model returned no body (reasoning only); fell back to a locally assembled answer. Switch to a non-reasoning model in Model Settings.',
    '降级提示：知识库未命中相关内容，本次为占位拒答话术（不编造）。可上传文档后重试。':
      'Degraded: nothing relevant retrieved; this is a placeholder refusal (no hallucination). Upload documents and retry.',
    '降级提示：本次回答已降级处理。': 'Degraded: this answer was downgraded.',

    /* ---- badcase / 管理后台 ---- */
    '评估任务完成度总览，以及 badcase 的筛选查看与按条件下载。':
      'Eval task completion overview, plus badcase filtering and filtered export.',
    '任务总数（目标样本）': 'Target samples',
    '全部评估任务合计': 'Across all eval tasks',
    '已完成': 'Completed',
    '已采集 badcase 数': 'Badcase records captured',
    '待处理': 'Pending',
    '目标 − 已完成': 'Target − completed',
    '整体完成率': 'Completion rate',
    '各评估任务完成度': 'Per-task completion',
    '暂无评估任务': 'No eval tasks',
    'badcase 筛选与下载': 'Badcase filter & export',
    '评估任务': 'Eval task',
    '判定': 'Verdict',
    '问题说明': 'Issue',
    '关联任务': 'Linked task',
    '时间': 'Time',
    '说明 / 时间': 'Note / time',
    '原始问题': 'Question',
    '模型回答': 'Model answer',
    '共 {n} 条记录（当前筛选条件）': '{n} records (current filter)',
    '当前筛选下没有 badcase 记录。': 'No badcase records match this filter.',
    '去「智能问答」里对一条回答点「标记为 badcase」即可产生数据。':
      'Go to Chat Q&A and click "Tag as badcase" on an answer to create data.',
    '完成度口径：一条关联该任务的 badcase 记录视为已评测一个样本；已完成 = 关联记录数，待处理 = 目标数 − 已完成。':
      'Definition: one badcase linked to a task counts as one evaluated sample; completed = linked records, pending = target − completed.',
    '标记为 badcase（弹窗）': 'Tag as badcase',
    '该记录会关联到指定评估任务，并进入管理员后台的完成度统计与导出。':
      'The record is linked to an eval task and feeds admin completion stats and export.',
    '原始问题：': 'Question: ',
    '模型回答：': 'Answer: ',
    '关联评估任务 *': 'Linked eval task *',
    '标注 / 判定结果': 'Verdict',
    '例如：引用缺失 / 检索未命中 / 回答超时 / 事实错误': 'e.g. missing citation / no retrieval hit / timeout / factual error',
    '错误': 'Wrong', '部分正确': 'Partially correct', '不确定': 'Uncertain', '正确': 'Correct',
    '问答标记': 'From chat', '手工录入': 'Manual', '评测批次': 'Eval batch',
    '问题：': 'Question: ', '回答：': 'Answer: ',
    '评估任务列表未加载，请刷新页面': 'Eval tasks not loaded, please refresh',
    '保存失败：': 'Save failed: ',
    'badcase 已保存并关联评估任务': 'Badcase saved and linked to eval task',
    '查询 badcase 失败：': 'Failed to query badcases: ',
    '读取完成度失败：': 'Failed to read completion: ',
    '记录已更新': 'Record updated',
    '处理 badcase': 'Handle badcase',
    '已按当前筛选下载 badcases.{n}': 'Downloaded badcases.{n} with current filter',
    '下载失败：': 'Download failed: ',
    '草稿': 'Draft', '进行中': 'Running', '已暂停': 'Paused', '已完成': 'Done',
    '任务总数': 'Target', '完成率': 'Rate',
    '未标注': 'Unlabeled',
    '共 {n} 条记录': '{n} records',

    /* ---- 概览 ---- */
    '一屏看全知识库状态；普通用户只看自己的数据，admin 额外看到全局完成度与成员数。':
      'Full KB status on one screen; standard users see only their own data, admins also see global completion and member count.',
    '文档总数': 'Documents',
    '已就绪 / 分块': 'Ready / chunks',
    '仅 ready 文档参与检索': 'Only ready documents are searchable',
    '问答会话': 'Conversations',
    '条消息': ' messages',
    '降级回答': 'Degraded answers',
    '检索为空，明确拒答': 'Empty retrieval, explicit refusal',
    'badcase 记录': 'Badcase records',
    '采集闭环数据量': 'Capture-loop volume',
    '分析队列': 'Analysis queue',
    '并发上限 2': 'Concurrency limit 2',
    '注册成员': 'Members',
    '仅 admin 可见': 'Admins only',
    '审计记录': 'Audit records',
    '只追加台账': 'Append-only ledger',
    '评测完成率': 'Eval completion',
    '样本': ' samples',
    '统计范围': 'Scope',
    '全局': 'Global',
    'admin 视图': 'admin view',
    '快捷入口': 'Shortcuts',
    '流式输出与 badcase 采集': 'Streaming & badcase capture',
    '上传、排队、状态': 'Upload, queue, status',
    '看命中与降级': 'Hits & degradation',
    '队列与恢复': 'Queue & recovery',
    '任务进度与对象': 'Tasks & objects',
    '系统边界': 'System boundaries',
    '替身映射与档次': 'Substitute map & tiers',
    '查看 / 导出': 'View / export',
    '按人回放': 'Replay by actor',
    '当前状态与数据口径': 'Current state & data scope',
    '当前账号': 'Account',
    '排队 / 分析中': 'Queued / analyzing',
    '失败 / 降级留痕': 'Failed / degraded',
    '这一页在讲什么': 'What this page shows',
    '全局（admin）': 'Global (admin)',
    '仅本人数据': 'Own data only',
    '每次刷新都从 SQLite 业务表实时聚合，不做预聚合日报；': 'Every refresh aggregates live from SQLite business tables; no pre-aggregated reports;',
    '权限分离': 'Permission isolation',
    '普通用户的统计强制带 owner_id / user_id / created_by 条件，不是把全局数字藏起来而是根本不查。':
      'Standard-user stats always carry owner_id / user_id / created_by predicates — global numbers are not hidden, they are never queried.',

    /* ---- 检索调试 ---- */
    '只召回、不生成，把「召回前过滤 → 命中打分 → 是否降级」摊开看。':
      'Retrieval only, no generation: exposes pre-filter → scoring → degradation decision.',
    '召回预览': 'Retrieval preview',
    '例如：如何给设备配网？': 'e.g. How do I configure the device network?',
    '输入关键词后回车；试试「量子纠缠」可看到降级判定。':
      'Type a keyword and press Enter; try "quantum entanglement" to see the degrade decision.',
    '命中分块（按重叠词数排序）': 'Hit chunks (sorted by term overlap)',
    '没有命中任何分块 —— 这正是 chat 页触发降级的条件。':
      'No chunks hit — this is exactly the condition that triggers degradation in Chat Q&A.',
    '面试讲法': 'How to present it',
    '分词（演示口径）': 'Tokens (demo)',
    '已索引 / 可召回分块': 'Indexed / eligible chunks',
    '命中 / top_k': 'Hits / top_k',
    '降级判定': 'Degrade decision',
    'retrieval_empty · 拒答不编造': 'retrieval_empty · refuse, no hallucination',
    '前置权限过滤': 'Pre-filter by permission',
    '重排 / 生成': 'Rerank / generation',
    '请输入检索词': 'Enter a search term',
    '正常生成': 'Normal generation',
    '文档': 'Document', '分块 / 重叠词数': 'Chunk / overlap', '片段': 'Snippet',

    /* ---- 任务中心 ---- */
    '上传分析队列、状态机与失败恢复；对应 docs/async-upload.md 的三层进度通道设计。':
      'Upload queue, state machine, and failure recovery; mirrors the 3-layer progress design in docs/async-upload.md.',
    '处理中': 'Processing',
    '排队中': 'Queued',
    '可被检索': 'Searchable',
    '失败 / 降级': 'Failed / degraded',
    '均留有具体原因': 'All with concrete reasons',
    '当前队列': 'Current queue',
    '处理中文件': 'Processing files',
    '排队中文件': 'Queued files',
    '队列排空后繁忙横幅自动消失；繁忙期间本页每 2.5 秒跟随系统状态刷新。':
      'The busy banner clears when the queue drains; this page refreshes with system state every 2.5s while busy.',
    '状态机（演示切片）': 'State machine (demo slice)',
    '已入队，等待并发闸': 'Queued, waiting for the concurrency gate',
    '提取 → 分块 → 落库': 'Extract → chunk → persist',
    '分块可被检索': 'Chunks are searchable',
    '格式不支持 / 文件丢失，留具体原因': 'Unsupported format / missing file, with a concrete reason',
    '生产还有 upload_sessions / batches / 页级 chord 四层进度与 SSE 权威快照；':
      'Production adds upload_sessions / batches / page-level chord (4 layers) and an authoritative SSE snapshot;',
    '这里用进程内信号量 + 轮询展示同一语义，': 'here an in-process semaphore + polling represents the same semantics,',
    'Celery + RabbitMQ': 'Celery + RabbitMQ',
    '最近文档状态': 'Recent document status',
    '暂无文档，去「文档管理」上传。': 'No documents yet; upload one in Documents.',
    '降级与恢复矩阵': 'Degradation & recovery matrix',
    '格式不支持 → error + 原因；繁忙 → 排队并显示位置；检索为空 → 拒答不编造；':
      'Unsupported format → error + reason; busy → queue with position; empty retrieval → refuse without hallucination;',
    '流中断 → resume_from 续传；后端不可达 → 红色横幅自动重连；令牌失效 → 退回登录。':
      'Stream break → resume_from; backend down → red banner with auto reconnect; token expired → back to sign-in.',
    '全部可见、全部留痕，不静默。': 'All visible, all logged, never silent.',

    /* ---- 评测中心 ---- */
    '评估任务进度 + 版本化对象清单；质量结论在占位档位下明确不可比。':
      'Eval task progress + versioned object registry; quality results are explicitly non-comparable in placeholder mode.',
    '档位 offline_placeholder · 质量结论': 'Profile offline_placeholder · quality verdict',
    '· comparable=false · 真实 API 字段仍 __TBC__': '· comparable=false · real API fields remain __TBC__',
    '版本化对象': 'Versioned objects',
    '注册表 ': 'Registry ',
    '版本策略': 'Version policy',
    '禁原地改': 'No in-place edits',
    '已产生 run 的对象必须升版本': 'Objects with runs require a version bump',
    '质量基线': 'Quality baseline',
    '未冻结': 'Not frozen',
    '占位结果不写基线': 'Placeholder results never enter the baseline',
    '仅 admin': 'Admin only',
    '采集仍可用': 'Capture still available',
    '版本化对象（实时读自 eval/config/registry.json）': 'Versioned objects (live from eval/config/registry.json)',
    '对象 ID': 'Object ID', '类型': 'Type', '版本': 'Version', '文件': 'File',
    '口径边界（别把 A 档说成 C 档）': 'Scope boundary (do not present tier A as tier C)',
    '上表是': 'The table above lists',
    '评测体系已落地的设计对象': 'design objects already landed in the eval system',
    '不是本切片的质量基线。': 'not a quality baseline for this slice.',
    '占位结果一律': 'Placeholder results are always',
    '质量结论': 'quality verdict',
    '禁止据此冻结阈值；': 'never freeze thresholds from them;',
    '所有真实 API 的地址、鉴权、模型名与配额仍是': 'all real API endpoints, auth, model names and quotas remain',
    '「评测 CI 闸门」只落地了 badcase 数据侧，闸门接 CI 仍未做。':
      'The "eval CI gate" only has the badcase data side; CI gating is not implemented.',

    /* ---- 审计 / 系统 ---- */
    '只追加台账，可按动作与操作人回放「谁在什么时候做了什么」。':
      'Append-only ledger; replay "who did what, when" by action and actor.',
    '当前筛选下没有审计记录。': 'No audit records match this filter.',
    '登录、上传、问答、导出都会写入台账。': 'Sign-in, upload, chat and export all write to the ledger.',
    '对应 README §4 的': 'Mirrors README §4',
    '与 C 档缺口「谁在什么时候查了什么」。': 'and the tier-C gap "who queried what, when".',
    '演示级只埋了登录/注册/上传/删除/问答/检索/badcase/导出八类动作，':
      'This demo logs eight action types (sign-in / register / upload / delete / chat / search / badcase / export),',
    '未接 trace_id、留存策略与脱敏检索': 'no trace_id, retention policy, or redacted search',
    '当前账号权限、运行状态，以及「做了什么 / 只设计 / 明确不做」的诚实边界。':
      'Account permissions, runtime state, and an honest boundary of built / designed / deliberately omitted.',
    '当前账号与权限': 'Account & permissions',
    '账号': 'Account', '角色': 'Role',
    '可见入口': 'Visible pages',
    '全部 10 项': 'All 10 pages',
    '7 项（无管理后台 / 审计日志 / 模型设置）': '7 pages (no admin console / audit log / model settings)',
    '生产骨架把权限判定收敛到 decide() 一个纯函数；切片里是 admin 门禁 + owner 过滤。':
      'The production skeleton funnels permission decisions into one pure decide(); this slice uses an admin gate + owner filter.',
    '逐行 / 逐段落 ACL 与多租户行级权限': 'Row / paragraph-level ACL and multi-tenant row-level permissions',
    '只预留架构，未实现': 'are architecture placeholders, not implemented',
    '实时运行状态': 'Live runtime',
    '连接': 'Connection',
    '分析队列': 'Analysis queue',
    '处理 ': 'Processing ',
    ' / 排队 ': ' / queued ',
    '真值存储': 'Source of truth',
    'SQLite（本地文件）': 'SQLite (local file)',
    '外部依赖': 'External dependencies',
    '可离线；可选外部 DeepSeek / 本地 LM Studio': 'Offline-capable; optional external DeepSeek / local LM Studio',
    '断线恢复': 'Reconnect',
    '2.5s 探测 + 自动刷新': '2.5s probe + auto refresh',
    '后端不可达，顶部横幅正在自动重连。': 'Backend unreachable; the top banner is reconnecting.',
    '成员与角色（仅 admin 可见数据）': 'Members & roles (admins only)',
    '成员列表仅管理员可见（接口同样 403）。': 'Member list is admin-only (the API also returns 403).',
    '文档数': 'Docs', 'badcase 数': 'Badcases', '注册时间': 'Registered',
    '组件替身映射：演示切片 vs 生产架构': 'Substitute map: demo slice vs production architecture',
    '能力': 'Capability',
    '本切片（真实可跑）': 'This slice (runnable)',
    '生产架构（设计）': 'Production (designed)',
    '状态真值': 'Source of truth',
    '任务投递': 'Task dispatch',
    '进程内信号量队列': 'In-process semaphore queue',
    '检索': 'Retrieval',
    '词法门禁 + 本地 Bge M3 向量排序（可切离线词法）': 'Lexical gate + local Bge M3 vector ranking (offline lexical optional)',
    'PG 全文 + Milvus 向量 + RRF + 权限双过滤': 'PG full-text + Milvus vectors + RRF + dual permission filter',
    '生成': 'Generation',
    '外部 DeepSeek / 本地 LM Studio / 离线占位（可切）': 'External DeepSeek / local LM Studio / offline stub (switchable)',
    '外部 DeepSeek SSE': 'External DeepSeek SSE',
    '实时进度': 'Live progress',
    '2.5s 轮询 + SSE 心跳': '2.5s polling + SSE heartbeat',
    'Redis pub/sub + SSE 权威快照': 'Redis pub/sub + authoritative SSE snapshot',
    '前端': 'Frontend',
    '无构建静态页': 'Build-free static page',
    'React + Vite': 'React + Vite',
    '交付档次与明确不做': 'Delivery tiers & deliberate omissions',
    'A 档（当前）': 'Tier A (current)',
    'B 档（面试期）': 'Tier B (interview phase)',
    'C 档（团队质量工作）': 'Tier C (team quality work)',
    '明确不做': 'Deliberately omitted',
    '离线可跑、闭环可演示、降级可见。': 'Runs offline, demoable loop, visible degradation.',
    '真实 embedding / 中文分词 /': 'Real embedding / Chinese tokenization /',
    'Rerank / 压测 / 越权用例 / trace 全链路。': 'Rerank / load tests / privilege-escalation cases / full trace.',
    '容灾演练、容量基线、审计留存合规、监控告警、灰度回滚。':
      'DR drills, capacity baselines, audit retention compliance, monitoring/alerting, canary rollback.',
    '真实 API、UI 框架、多租户行级权限、压测、质量基线、物理隔离。':
      'Real APIs, UI framework, multi-tenant row-level permissions, load tests, quality baselines, physical isolation.',

    /* ---- 模型设置 ---- */
    '切换 LLM / 向量 / 重排提供者：本地 LM Studio、外部 DeepSeek 或离线占位。保存即时生效，无需重启。':
      'Switch LLM / embedding / rerank providers: local LM Studio, external DeepSeek, or offline stub. Applies immediately.',
    'LLM 生成': 'LLM generation',
    '外部 DeepSeek（Intern-AI Discovery 网关）': 'External DeepSeek (Intern-AI Discovery gateway)',
    '外部 DeepSeek（Sensenova 代理）': 'External DeepSeek (Sensenova proxy)',
    '本地 LM Studio': 'Local LM Studio',
    '离线占位': 'Offline stub',
    '网关模型': 'Gateway model',
    '同一 key 下多模型可切（deepseek / glm / kimi / minimax / qwen / vision），密钥在':
      'Multiple models on one key (deepseek / glm / kimi / minimax / qwen / vision); key stored in',
    '本地端点': 'Local endpoint',
    '本地模型': 'Local model',
    '提示：Qwen3 系列是推理模型，正文可能为空；建议选非推理的 coder 模型。':
      'Tip: Qwen3 series are reasoning models and may return an empty body; prefer a non-reasoning coder model.',
    '外部模型：': 'External model: ',
    '；密钥在': '; key stored in',
    '，不落库、不下发前端。': ', never persisted or sent to the frontend.',
    '向量检索（Embedding）': 'Vector retrieval (Embedding)',
    '硅基流动 BAAI/bge-m3（云端）': 'SiliconFlow BAAI/bge-m3 (cloud)',
    '本地 Bge M3（LM Studio）': 'Local Bge M3 (LM Studio)',
    '离线词法': 'Offline lexical',
    '嵌入模型': 'Embedding model',
    '与重排共用': 'Shares the SiliconFlow key with rerank in',
    '里的硅基流动密钥（': ' (',
    '），不落库、不下发前端。向量按「provider:模型」隔离缓存。':
      '), never persisted or sent to the frontend. Vectors are cached per "provider:model".',
    '关闭': 'Off',
    '用当前 LLM 打分重排（LLM-as-reranker）': 'Score with current LLM (LLM-as-reranker)',
    '外部 rerank（硅基流动 / Jina / TEI）': 'External rerank (SiliconFlow / Jina / TEI)',
    '重排模型': 'Rerank model',
    '使用当前 LLM 对候选打分排序；会多一次模型调用（当前 LLM 为离线占位时自动跳过）。':
      'Ranks candidates with the current LLM; adds one model call (skipped when the LLM is the offline stub).',
    '密钥放': 'Key stored in',
    '的': ' in ',
    '说明：LM Studio 不暴露': 'Note: LM Studio does not expose',
    '；可选「LLM 打分重排」或外部 rerank（硅基流动免费、Cohere 风格格式兼容）。':
      '; use LLM-as-reranker or external rerank (SiliconFlow free tier, Cohere-style compatible).',
    '（未检测到本地模型）': '(no local models detected)',
    '已配置（嵌入 + 重排共用）': 'Configured (shared by embedding + rerank)',
    '未配置': 'Not configured',
    '硅基流动密钥：': 'SiliconFlow key: ',
    ' · 本地 LM Studio：': ' · Local LM Studio: ',
    '可达': 'Reachable', '不可达': 'Unreachable',
    '（{n} 个模型）': '({n} models)',
    '向量：': 'Embedding: ', ' · 重排：': ' · Rerank: ',
    'LLM：': 'LLM: ',
    '设置已保存，已即时生效': 'Settings saved and applied immediately',
    '测试失败：': 'Test failed: ',
    '测试连接': 'Test connection',
    '保存设置': 'Save settings',

    /* ---- 状态 / 提示 / 空状态 ---- */
    '请求失败': 'Request failed',
    '读取文档失败：': 'Failed to load documents: ',
    '服务连接已恢复，数据已刷新': 'Connection restored, data refreshed',
    '服务连接中断，正在自动重连…（每 2.5 秒尝试一次，恢复后自动刷新数据）':
      'Connection lost, reconnecting… (retries every 2.5s; data refreshes on recovery)',
    '繁忙 · 排队 {n}': 'Busy · {n} queued',
    '处理中 {a} · 排队 {q}': 'Processing {a} · Queued {q}',
    '仅管理员可访问该页面（接口同样返回 403）': 'Admins only (the API also returns 403)',
    '当前为离线词法检索，无需连接': 'Offline lexical retrieval, no connection needed',
    '本地嵌入可用，维度 {n}': 'Local embedding available, dimension {n}',
    '连接测试': 'Connection test',

    /* ---- 评估测试子页（新增） ---- */
    '评估配置': 'Eval configuration',
    '运行记录': 'Run history',
    '指标展示': 'Metrics',
    '日志追踪': 'Log trace',
    '成本与预算': 'Cost & budget',
    '限流配额': 'Rate limits',
    '插件与边界': 'Plugins & boundaries',
    '按项目 / 部门 / 调用维度统计 token 消耗与成本；超预算会留痕并拒绝新的付费调用。':
      'Token usage and cost by project / department / call dimension; budget overruns are logged and further billable calls are rejected.',
    '当前评估配置': 'Current eval configuration',
    '评估档位': 'Eval profile',
    '提示词版本': 'Prompt version',
    '参数组': 'Parameter set',
    '样本范围': 'Sample scope',
    '运行': 'Run',
    '运行一次评估': 'Run evaluation',
    '运行一次离线评估（零 API 调用）': 'Run an offline evaluation (zero API calls)',
    '最近运行': 'Recent runs',
    '时间': 'Time', '运行 ID': 'Run ID', '模型': 'Model', '样本': 'Samples', '状态': 'Status',
    '主指标': 'Primary metric', '耗时': 'Duration',
    '查看明细': 'Details',
    '暂无运行记录，点击「运行一次评估」生成。': 'No runs yet. Click "Run evaluation" to create one.',
    '按任务类别 / 维度': 'By task category / dimension',
    '任务类别': 'Task category', '维度': 'Dimension', '均值': 'Mean', '记录数': 'Records',
    '暂无指标数据': 'No metrics yet',
    '追踪日志': 'Trace log',
    '级别': 'Level', '事件': 'Event', '详情': 'Detail',
    '暂无日志': 'No logs',
    '全部': 'All',
    '按维度聚合': 'Group by',
    '项目': 'Project', '部门': 'Department', '调用类型': 'Call type', '模型': 'Model',
    '输入 token': 'Prompt tokens', '输出 token': 'Completion tokens', '总 token': 'Total tokens',
    '成本（估算）': 'Cost (estimated)', '调用次数': 'Calls',
    '单价表为演示估算值，非供应商报价；上线前须用账单校准。':
      'Prices are demo estimates, NOT vendor quotes; calibrate against real invoices before production.',
    '预算上限': 'Budget cap',
    '当前周期累计': 'Current period spend',
    '剩余额度': 'Remaining',
    '使用率': 'Usage',
    '超限处理': 'Over-limit policy',
    '告警并拒绝新的付费调用（留痕）': 'Alert and reject new billable calls (logged)',
    '保存预算': 'Save budget',
    '预算已更新': 'Budget updated',
    '预算超限：新的付费调用已被拒绝': 'Budget exceeded: new billable calls rejected',
    '暂无用量数据': 'No usage data',
    '配额维度': 'Dimension', '限额': 'Limit', '窗口': 'Window', '当前用量': 'Current', '命中次数': 'Hits',
    '用户': 'User', '接口': 'Endpoint', '模型调用': 'Model call',
    '超限反馈：HTTP 429 + Retry-After + 统一错误体 + 审计留痕':
      'Over-limit feedback: HTTP 429 + Retry-After + uniform error body + audit log',
    '插件能力': 'Plugin', '接入模块': 'Integration point', '状态': 'Status', '边界': 'Boundary', '面试考点': 'Why interviewers ask',
    '暂无配置': 'No configuration',
    '切换到英文': 'Switch to English',
    '切换到中文': '切换为中文',
    '主题色': 'Accent color',
    '外观模式': 'Appearance',
    '浅色': 'Light', '深色': 'Dark',
    '语言': 'Language',
    '外观': 'Appearance',
    '靛蓝': 'Indigo', '青碧': 'Teal', '紫罗兰': 'Violet', '琥珀': 'Amber', '玫红': 'Rose', '石墨': 'Graphite',
    '设置': 'Settings',
    '只读事实源：evalbench/ 资产 + eval_configs 表': 'Read-only source of truth: evalbench/ assets + eval_configs table',
    '对应生产骨架 README §4 的「看板 /stats/overview」。演示切片不预聚合日报，':
      'Mirrors the /stats/overview board in the production skeleton README §4. This demo slice has no pre-aggregated reports;',
    '每次刷新都从 SQLite 业务表实时算，所以上传、问答、标记 badcase 之后数字会立刻变。':
      'every refresh aggregates live from SQLite, so numbers change immediately after uploads, chats and badcase tags.',
    '生产链路是': 'The production pipeline is',
    '先按权限过滤，再重排，再进模型': 'filter by permission first, then rerank, then generate',
    '双重过滤，RRF 融合，P0 才加带降级开关的 Rerank。':
      'dual filtering, RRF fusion, and a Rerank with a degrade switch only at P0.',
    '本切片只有「ready 文档的分块」这一层前置条件，关键词重叠打分替身向量检索；':
      'this slice has only one precondition (chunks of ready documents) and uses keyword-overlap scoring as a stand-in for vector retrieval;',
    '未启用重排、未接模型': 'rerank disabled, no model connected',
    '，页面字段刻意与生产口径对齐，方便对照着讲。':
      ', fields are deliberately aligned with production semantics for side-by-side explanation.',
    'PostgreSQL：ingest_jobs / 审计台账': 'PostgreSQL: ingest_jobs / audit ledger',
    '管理员：可修改评估配置、预算与限流规则，并发起离线评估运行。':
      'Admin: you can change eval config, budget and rate-limit rules, and start offline evaluation runs.',
    '只读视图：用量与日志按本人范围过滤；配置与预算仅管理员可改。':
      'Read-only view: usage and logs are scoped to your own data; config and budget are admin-only.',
    '限流与插件': 'Rate limits & plugins',
    '元': 'CNY', '重排（Rerank）': 'Rerank', '或': 'or', '不是': 'not',
    '注册': 'Sign up', '导出': 'Export', '工作台 / 文档管理': 'Workspace / Documents',
    '语言 / Language': 'Language / 语言', '主题色 / Accent color': 'Accent color',
    '外观模式 / Appearance': 'Appearance',
    '中文': '中文', '浅色模式': 'Light mode', '深色模式': 'Dark mode',
    '切换到浅色': 'Switch to light', '切换到深色': 'Switch to dark',
    '外观模式：': 'Appearance: ',

    /* ---- 评估测试子页（静态） ---- */
    '评估配置、运行记录、指标展示、日志追踪，以及按项目 / 部门 / 调用维度统计 token 消耗与成本；支持预算上限与超限处理。':
      'Eval config, run history, metrics, log tracing, and token usage / cost by project, department and call dimension; with budget caps and over-limit handling.',
    '当前配置与口径': 'Active config & semantics',
    '离线档位结果': 'Offline-profile results are',
    '禁止写入质量基线；': 'never written to a quality baseline;',
    '档位在': 'the profile stays blocked until the blocking fields in',
    '的 blocking 字段解禁前保持阻断。': 'are resolved.',
    '本页配置只影响演示切片的评估运行，不改动': 'This config only affects demo-slice eval runs and does not modify',
    '冻结对象。': 'frozen objects.',
    '评估档位': 'Eval profile',
    'offline_placeholder（离线可控，comparable=false）': 'offline_placeholder (offline, comparable=false)',
    'real_api（真实 API，需契约解禁）': 'real_api (real API, contract gate required)',
    '提示词版本': 'Prompt version', '参数组': 'Parameter set', '样本范围': 'Sample scope',
    '保存配置': 'Save config', '运行一次评估': 'Run evaluation',
    '最近运行': 'Recent runs',
    '运行记录来自': 'Runs come from',
    '与本页面发起的离线运行；': 'and offline runs started here;',
    '与离线档位分别标注，不混算。': 'and offline profiles are labelled separately, never mixed.',
    '指标展示 · 按任务类别 / 维度': 'Metrics · by task category / dimension',
    '追踪日志': 'Trace log', '运行 ID': 'Run ID', '级别': 'Level',
    '预算上限与超限处理': 'Budget cap & over-limit handling',
    '使用率': 'Usage', '剩余额度': 'Remaining', '超限处理': 'Over-limit policy',
    '告警并拒绝新的付费调用（留痕）': 'Alert and reject new billable calls (logged)',
    '预算上限（元 / 周期）': 'Budget cap (CNY / period)',
    '超限策略': 'Over-limit policy',
    'warn（告警留痕，不拒绝）': 'warn (log an alert, do not reject)',
    'block（拒绝新的付费调用）': 'block (reject new billable calls)',
    '保存预算': 'Save budget',
    'Token 消耗与成本（按维度聚合）': 'Token usage & cost (by dimension)',
    '按维度聚合': 'Group by',
    '项目': 'Project', '部门': 'Department', '调用类型': 'Call type',
    '单价表为演示估算值，非供应商报价；上线前须用账单校准。':
      'Prices are demo estimates, NOT vendor quotes; calibrate against invoices before production.',
    '成本口径：输入 / 输出 token 分别计价，按调用逐条落台账后聚合。':
      'Cost model: prompt / completion tokens priced separately, aggregated from per-call ledger rows.',
    '限流配额（用户 / 接口 / 模型调用）': 'Rate limits (user / endpoint / model call)',
    '超限反馈：HTTP 429 + Retry-After + 统一错误体 + 审计留痕；':
      'Over-limit feedback: HTTP 429 + Retry-After + uniform error body + audit log;',
    '退避重试，': 'is retried with backoff,',
    '不重试。': 'is never retried.',
    '插件能力 · 接入模块与边界': 'Plugins · integration points & boundaries',
    '输入 token': 'Prompt tokens', '输出 token': 'Completion tokens',
    '总 token': 'Total tokens', '成本（估算）': 'Cost (est.)', '调用次数': 'Calls',
    '配额维度': 'Dimension', '限额': 'Limit', '窗口': 'Window', '当前用量': 'Current', '命中次数': 'Hits',
    '用户': 'User', '接口': 'Endpoint', '模型调用': 'Model call',
    '查看明细': 'Details', '查看': 'View',
    '仅本人数据': 'Own data only', '仅本人调用': 'Own calls only',
    '只读；预算配置仅 admin 可改': 'Read-only; budget config is admin-only',
    '本人 / 系统事件': 'Own / system events',
    '全局（admin）': 'Global (admin)',
    '暂无用量数据': 'No usage data', '暂无运行记录': 'No runs yet',
    '暂无指标数据': 'No metrics', '暂无日志': 'No logs', '暂无配置': 'No configuration',
    '暂无评估任务': 'No eval tasks',
    '已落地（演示级）': 'Landed (demo level)', '已落地': 'Landed',
    '架构预留': 'Architecture placeholder', '未做': 'Not implemented', '演示级': 'Demo level',
    '配置项已保存': 'Configuration saved', '配置保存失败：': 'Failed to save config: ',
    '评估已完成（离线，comparable=false）': 'Evaluation finished (offline, comparable=false)',
    '评估运行失败：': 'Evaluation failed: ', '预算已更新': 'Budget updated',
    '预算保存失败：': 'Failed to save budget: ',
    '运行 ID': 'Run ID', '样本': 'Samples', '主指标': 'Primary metric', '耗时': 'Duration',
    '任务类别': 'Task category', '均值': 'Mean', '记录数': 'Records',
    '事件': 'Event', '详情': 'Detail',
    '接入模块': 'Integration point', '边界': 'Boundary', '面试考点': 'Why interviewers ask',
    '插件能力': 'Plugin',

    /* ---- 插值型 / 状态（app.js 显式 t()） ---- */
    '共 {n} 条记录（当前筛选条件）': '{n} records (current filter)',
    '繁忙 · 排队 {n}': 'Busy · {n} queued',
    '处理中 {a} · 排队 {q}': 'Processing {a} · Queued {q}',
    '当前 {a} 个任务正在分析，{q} 个排队。': 'Currently {a} analyzing, {q} queued.',
    '处理中：{n}': 'Processing: {n}', '排队中：{n}': 'Queued: {n}',
    '正在检索知识库并流式生成…': 'Retrieving and generating…',
    '连接中断，正在续传（第 {r} 次重试，已续 {n} 个 token）…':
      'Connection lost, resuming (retry {r}, {n} tokens resumed)…',
    '引用来源（{n} 条）· 点击查看原文': 'Sources ({n}) · click to view original',
    '得分 {n}': 'Score {n}',
    '{n} 块': '{n} chunks',
    '{n} 条': '{n} msgs',
    '{n} 条 · 含来源': '{n} msgs · with sources',
    '已接收「{n}」，进入分析队列': 'Received "{n}", queued for analysis',
    '上传「{n}」失败：': 'Upload "{n}" failed: ',
    '已按当前筛选下载 badcases.{n}': 'Downloaded badcases.{n} with current filter',
    '（{n} 个模型）': '({n} models)',
    '{n} 条消息': '{n} messages',
    '{n} / {m} 样本': '{n} / {m} samples',
    '完成 · Enter 发送，Shift+Enter 换行': 'Done · Enter to send, Shift+Enter for newline',
    '就绪 · Enter 发送，Shift+Enter 换行': 'Ready · Enter to send, Shift+Enter for newline',
    '连接异常结束': 'Connection ended unexpectedly',
    '服务连接中断，正在自动重连…（每 2.5 秒尝试一次，恢复后自动刷新数据）':
      'Connection lost, reconnecting… (every 2.5s; data refreshes on recovery)',
    '服务连接已恢复，数据已刷新': 'Connection restored, data refreshed',
    '仅管理员可访问该页面（接口同样返回 403）': 'Admins only (API also returns 403)',
    '请输入检索词': 'Enter a search term',
    '正在生成回答，请稍候': 'Generating, please wait',
    '请输入用户名和密码': 'Enter username and password',
    '登录已过期，请重新登录': 'Session expired, please sign in again',
    '已退出登录': 'Signed out',
    '已自动登录演示管理员账号': 'Signed in as demo admin',
    '示例说明书已生成并进入分析队列': 'Sample document generated and queued',
    '文档已删除': 'Document deleted',
    'badcase 已保存并关联评估任务': 'Badcase saved and linked to eval task',
    '记录已更新': 'Record updated',
    '设置已保存，已即时生效': 'Settings saved and applied immediately',
    '已载入历史会话（含来源追溯）': 'Conversation loaded (with source trace)',
    '评估任务列表未加载，请刷新页面': 'Eval tasks not loaded, please refresh',
    '读取文档失败：': 'Failed to load documents: ',
    '读取完成度失败：': 'Failed to read completion: ',
    '查询 badcase 失败：': 'Failed to query badcases: ',
    '载入历史失败：': 'Failed to load history: ',
    '下载失败：': 'Download failed: ',
    '保存失败：': 'Save failed: ',
    '测试失败：': 'Test failed: ',
    '测试中…': 'Testing…',
    '你好，我是知识库问答助手。请先到「文档管理」上传说明书，然后向我提问；回答会以流式逐字输出。<br/><br/>若知识库没有命中，我会明确告知，不会编造内容。':
      'Hi, I am the KB assistant. Upload a manual in "Documents" first, then ask me a question; answers stream token by token.<br/><br/>If nothing is retrieved, I will say so explicitly instead of making things up.',
    '知识库助手': 'KB Assistant',
    '我': 'Me',
    '尚未开始': 'Not started',
    '全部 {n} 项': 'All {n} pages',
    '{n} 项（无管理后台 / 审计日志 / 模型设置）': '{n} pages (no admin / audit / model settings)',
    /* ---- 推理强度（DeepSeek V4.1 Flash 低思考快速档） ---- */
    '推理强度': 'Reasoning effort',
    '推理：': 'Reasoning: ',
    '低思考 · 快速（默认，关闭 thinking）': 'Low thinking · fast (default, thinking disabled)',
    '完整推理（慢，先输出思考过程）': 'Full reasoning (slow, emits thinking first)',
    'V4.1 系列默认会先输出思考过程；检索问答要低延迟，默认在请求里显式关闭。该字段经实测生效：推理 token 从 55 降到 0，耗时约减半；网关元数据里的布尔 reasoning 参数实测无效。':
      'V4.1 models emit reasoning first by default; retrieval Q&A needs low latency, so the request disables it explicitly. Verified on 2026-11-14: it cuts reasoning tokens from 55 to 0 and roughly halves latency; the boolean "reasoning" parameter listed in gateway metadata has no effect.',
    /* ---- 解析实验室（三级策略） ---- */
    '解析实验室': 'Parsing Lab',
    '质量与治理 / 解析实验室（三级策略）': 'Quality / Parsing Lab (3-tier strategies)',
    '三级解析策略（自研 / 收费 OCR / 多模态兜底）可切换；质量门禁决定逐页升级，预算闸门限制最贵链路占比，全过程留下可核对的轨迹。':
      'Switch between three parsing tiers (in-house / paid OCR / multimodal fallback); a quality gate drives per-page escalation, a budget gate caps the most expensive tier, and every step leaves an auditable trace.',
    '策略开关': 'Strategy switch',
    '解析策略': 'Parsing strategy',
    '保存策略': 'Save strategy',
    '自动档按质量门禁逐级升级：': 'Auto mode escalates tier by tier based on the quality gate:',
    '才会往贵的一级走，不是随机分流。': 'triggers a more expensive tier; this is not random splitting.',
    '预算闸门 · 90:10': 'Budget gate · 90:10',
    '网关视觉能力探测': 'Gateway vision capability probe',
    '三级策略可用性': 'Availability of the three tiers',
    '上传文件并解析（演示探测，不落业务库、不写向量）':
      'Upload and parse (demo probe; no business DB writes, no vector indexing)',
    '选择文件（支持 PDF / 图片 / Markdown / CSV / 纯文本，≤ 8 MiB）':
      'Choose a file (PDF / image / Markdown / CSV / plain text, up to 8 MiB)',
    '开始解析': 'Start parsing',
    '逐页解析轨迹': 'Per-page parsing trace',
    '尚未解析。': 'Not parsed yet.',
    '第一页结构化结果预览': 'Structured preview of page 1',
    '最贵链路占比上限': 'Cap on the most expensive tier',
    '单次任务最小名额': 'Minimum slots per job',
    '质量门禁置信度阈值': 'Quality-gate confidence threshold',
    'OCR 字符阈值': 'OCR character threshold',
    '可用模型数': 'Available models',
    '视觉模型': 'Vision model',
    '未接通': 'Not connected',
    '可用': 'Available',
    '请先选择文件': 'Please choose a file first',
    '自动（质量门禁逐级升级）': 'Auto (quality-gated escalation)',
    '仅自研 P0': 'In-house P0 only',
    '强制收费 OCR P1': 'Force paid OCR P1',
    '强制多模态 P2': 'Force multimodal P2',
    /* ---- 知识治理（新鲜度 + 事实冲突） ---- */
    '知识治理': 'Knowledge Governance',
    '质量与治理 / 知识治理（新鲜度与冲突检测）': 'Quality / Knowledge Governance (freshness & conflicts)',
    '入库新鲜度（时间衰减 + 适用性门控 + 过期处置）与事实冲突检测（claim 级比对 → 冲突图按簇裁决 → 结构化证据）。内置合成场景，不写业务库、不触发外部调用。':
      'Ingestion freshness (time decay + applicability gate + expiry policy) and fact-conflict detection (claim-level comparison, conflict-graph cluster resolution, structured evidence). Uses built-in synthetic samples: no DB writes, no external calls.',
    '新鲜度策略': 'Freshness policy',
    '启用时间衰减': 'Enable time decay',
    '半衰期（天）': 'Half-life (days)',
    '过期处置': 'Expiry action',
    '相关性混合权重 blend': 'Relevance blend weight',
    '保存策略': 'Save policy',
    '场景汇总': 'Scenario summary',
    '排序前后对照': 'Ranking before / after',
    '逐文档新鲜度报告': 'Per-document freshness report',
    '事实冲突与裁决': 'Fact conflicts and resolutions',
    '证据记录（EvidenceRecord）': 'Evidence record (EvidenceRecord)',
    '负向对照（检测器不乱报）': 'Negative controls (no false positives)',
    '场景原文（合成样本）': 'Scenario source text (synthetic)',
    '开启新鲜度': 'Freshness on',
    '关闭（仅关衰减）': 'Off (decay only disabled)',
    '当前策略：': 'Current policy: ',
    '阈值说明：': 'Threshold note: ',
    '需复核': 'Needs review',
    '可按规则': 'Rule-resolved',
    '并列转人工': 'Both shown / escalate',
    '候选文档 / stale / 已过期': 'Candidates / stale / expired',
    '未生效 / 被排除': 'Not yet effective / excluded',
    'stale 占比 / 告警线': 'Stale ratio / alert line',
    '年龄分布': 'Age distribution',
    'claim 数 / 冲突数 / conflict_signal': 'Claims / conflicts / conflict_signal',
    '选中 / 排除 claim': 'Selected / excluded claims',
    '未检测到冲突': 'No conflicts detected',
    '无冲突': 'No conflicts',
    /* ---- 内容安全 / PII 防护 ---- */
    '内容安全 / PII': 'Content Safety / PII',
    '内容安全 / PII 防护': 'Content Safety / PII Protection',
    '系统 / 内容安全与 PII 防护（四出口）': 'System / Content Safety & PII (four gates)',
    '同一套规则贯穿四个出口：入库前、进模型前、输出前、日志与导出前。检测走「格式 → 校验位 → 上下文 → 置信度」四级判定链，附防误报负向对照。全部离线可跑。':
      'One rule set across four gates: before ingest, before the model, before output, and before logs/exports. Detection uses a format-to-checksum-to-context-to-confidence chain, with false-positive controls. Fully offline.',
    '当前策略与四出口台账': 'Policy & four-gate hit ledger',
    '实时链路接入状态': 'Live integration status',
    '检测规则（格式 → 校验位 → 上下文 → 置信度）': 'Detection rules (format -> checksum -> context -> confidence)',
    '一键演示：正向命中 / 防误报 / 四出口链路 / 流式跨界': 'One-click demo: hits / false-positive controls / four gates / streaming boundary',
    '运行规则演示': 'Run rule demo',
    '任意文本四出口检测': 'Check any text across four gates',
    '待检测文本': 'Text to check',
    '检测': 'Check',
    '策略开关（admin）': 'Policy switch (admin)',
    '启用': 'Enabled',
    '开启（默认）': 'On (default)',
    '关闭（仅对照演示）': 'Off (comparison only)',
    '默认动作': 'Default action',
    'mask（部分遮蔽）': 'mask (partial)',
    'hash（带盐不可逆）': 'hash (salted, irreversible)',
    'remove（占位替换）': 'remove (placeholder)',
    'block（命中即拒）': 'block (reject on hit)',
    'token（可逆，默认不建议）': 'token (reversible, not recommended)',
    '最小置信度': 'Min confidence',
    '请输入待检测文本': 'Please enter text to check',
    '最小置信度必须在 0 到 1 之间': 'Min confidence must be between 0 and 1',
    '入库前': 'Before ingest',
    '进模型前': 'Before the model',
    '输出前': 'Before output',
    '日志与导出前': 'Before logs & exports',
    '规则版本 / 启用状态': 'Rule version / enabled',
    '开启': 'On',
    '关闭（对照演示）': 'Off (comparison)',
    '默认动作 / 最小置信度': 'Default action / min confidence',
    '拦截类别 / 可逆 token': 'Blocked kinds / reversible token',
    '无': 'None',
    '开启（有风险）': 'On (risky)',
    '哈希盐已配置': 'Hash salt configured',
    '否（演示占位盐）': 'No (demo placeholder salt)',
    '四个出口命中台账（真实链路）': 'Four-gate hit ledger (live pipeline)',
    '台账事件行 / 只存统计': 'Ledger rows / stats only',
    '类别': 'Kind',
    '名称': 'Name',
    '置信度': 'Confidence',
    '校验 / 门控': 'Validator / gating',
    '格式': 'Format',
    '上下文门控': 'Context-gated',
    '命中': 'Hits',
    '脱敏后': 'Redacted',
    '出口': 'Gate',
    '结果文本': 'Result text',
    '脱敏后文本': 'Redacted text',
    '规则版本 / 样本总命中': 'Rule version / total sample hits',
    '防误报负向对照': 'False-positive controls',
    '四出口链路（同一段文本依次穿过）': 'Four-gate chain (same text through each)',
    '流式逐 token 跨界验证': 'Streaming cross-chunk boundary check',
    '下发字符数 / 扣留边界 / 命中': 'Characters sent / boundaries held / hits',
    '原文是否泄漏': 'Raw identifier leaked',
    '是（异常）': 'Yes (abnormal)',
    '总命中（四出口合计）': 'Total hits (four gates)',
    '边界：规则级检测': 'Boundary: rule-level detection',
    /* ---- 缓存与恢复 ---- */
    '缓存与恢复': 'Cache & Recovery',
    '系统 / 缓存与恢复（重启稳定性）': 'System / Cache & Recovery (restart stability)',
    '双层缓存（L1 内存 LRU + L2 SQLite）、版本感知失效、缓存 key 逐段拆解，以及服务器重启后的启动自检与状态恢复报告。全部离线可跑。':
      'Two-tier cache (L1 in-memory LRU + L2 SQLite), version-aware invalidation, cache-key breakdown, and startup self-check / recovery report after a server restart. Fully offline.',
    '缓存策略与统计': 'Cache policy & stats',
    '缓存 key 逐段拆解': 'Cache key breakdown',
    '重启恢复报告': 'Restart recovery report',
    '检索结果缓存演示（先按权限过滤再写）': 'Retrieval cache demo (filter by permission before writing)',
    '演示查询': 'Demo query',
    '运行缓存演示': 'Run cache demo',
    '模拟重启': 'Simulate restart',
    '主动失效': 'Invalidate all',
    '未运行。': 'Not run yet.',
    '缓存策略开关（admin）': 'Cache policy (admin)',
    'L1 容量': 'L1 capacity',
    'L2 容量': 'L2 capacity',
    '正常 TTL（秒）': 'Normal TTL (seconds)',
    'L1 / L2 容量': 'L1 / L2 capacity',
    '正常 TTL / 负缓存 TTL': 'Normal TTL / negative TTL',
    '命中 / 未命中 / 命中率': 'Hits / misses / hit rate',
    'L1 / L2 命中': 'L1 / L2 hits',
    '写入 / 淘汰 / 过期清理': 'Writes / evictions / expired purged',
    '版本 data / grant / policy': 'Version data / grant / policy',
    'L2 持久化条目': 'Persistent L2 entries',
    '失效原因分布：': 'Invalidation reasons: ',
    '启动健康 / 启动次数': 'Startup health / boot count',
    '孤儿任务重投 / 幽灵向量清理': 'Orphan tasks requeued / ghost vectors purged',
    '过期缓存清理 / 容量淘汰': 'Expired purged / capacity evicted',
    '上次优雅关闭': 'Last graceful shutdown',
    '恢复的策略': 'Restored policies',
    '无（使用默认值）': 'None (defaults)',
    '检查项': 'Check',
    '分类': 'Category',
    '结论': 'Result',
    '可安全丢弃': 'Safe to discard',
    '可从真值重建': 'Rebuildable from truth',
    '必须持久化': 'Must persist',
    '首次查询': 'First lookup',
    '先过滤后写': 'Filter before write',
    '清 L1 后（模拟重启）': 'After clearing L1 (simulated restart)',
    'L2 命中，缓存可恢复': 'L2 hit, cache recoverable',
    '未命中': 'Miss',
    'data_version 变化后新 key': 'New key after data_version change',
    '未命中（懒失效生效）': 'Miss (lazy invalidation works)',
    '命中（异常）': 'Hit (unexpected)',
    'L1 已清空': 'L1 cleared',
    'L2 条目 / 自检健康': 'L2 entries / self-check healthy',
    '正常': 'OK',
    '异常': 'Degraded',
    '否（强停不写标记，真值层仍完好）': 'No (forced kill skips marker; truth layer intact)',
    '是': 'Yes',
    '否': 'No',
    '已主动清空': 'Purged',
  };

  /* 富文本整段替换：用 data-i18n-html 标记，保留标签结构 */
  var EN_HTML = {
    'auth.hero': '<h1>From documents to answers,<br />and on to an evaluation loop.</h1>',
  };

  var SKIP_SELECTORS = [
    '.bubble', '.quoted', '.bc-question', '.bc-answer', '.conv-q', '.file-name',
    '.sc-title', '.sc-snippet', '.tc-id', 'code', 'pre', 'script', 'style',
    '[data-i18n-skip]'
  ];

  var KEY_LANG = 'kb_lang';
  var curr = 'zh';
  var ORIG_TEXT = new WeakMap();
  var ORIG_ATTR = new WeakMap();
  var ORIG_HTML = new WeakMap();
  var TITLE_ORIG = null;
  var observers = [];
  var suspended = false;

  function getLang() { return curr; }

  function inject(s, vars) {
    if (!vars) return s;
    return String(s).replace(/\{(\w+)\}/g, function (m, k) {
      return Object.prototype.hasOwnProperty.call(vars, k) ? vars[k] : m;
    });
  }

  function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  // 富文本常被 <b>/<code>/<br> 切成多个文本节点：精确匹配失败时按长度降序做片段替换。
  // 只用 >=3 字且不含插值/标签的 key，避免单字误伤；正则只编译一次。
  var SUB_KEYS = Object.keys(EN).filter(function (k) {
    return k.length >= 3 && k.indexOf('{') < 0 && k.indexOf('<') < 0;
  }).sort(function (a, b) { return b.length - a.length; });
  var SUB_RE = new RegExp(SUB_KEYS.map(escapeRe).join('|'), 'g');

  /** 翻译单个字符串：zh 原样返回；en 精确查字典 → 片段替换 → 原文兜底。 */
  function t(s, vars) {
    var raw = String(s == null ? '' : s);
    if (curr === 'zh') return inject(raw, vars);
    var trimmed = raw.trim();
    var lead = raw.slice(0, raw.length - raw.trimStart().length);
    var trail = raw.slice(raw.trimEnd().length);
    var hit = Object.prototype.hasOwnProperty.call(EN, trimmed) ? EN[trimmed] : null;
    if (hit !== null) return lead + inject(hit, vars) + trail;
    if (vars) {
      var withVars = inject(raw, vars);
      if (Object.prototype.hasOwnProperty.call(EN, withVars.trim()))
        return lead + inject(EN[withVars.trim()], vars) + trail;
    }
    var sub = raw.replace(SUB_RE, function (m) { return EN[m]; });
    return sub !== raw ? sub : inject(raw, vars);
  }

  function skipped(node) {
    var el = node.nodeType === 1 ? node : node.parentElement;
    if (!el || !el.closest) return false;
    for (var i = 0; i < SKIP_SELECTORS.length; i++) {
      try { if (el.closest(SKIP_SELECTORS[i])) return true; } catch (_) { }
    }
    return false;
  }

  function handleTextNode(node) {
    if (!node || node.nodeType !== 3 || skipped(node)) return;
    var orig = ORIG_TEXT.has(node) ? ORIG_TEXT.get(node) : node.nodeValue;
    if (!/[\u4e00-\u9fff]/.test(orig) && curr === 'en') return;
    ORIG_TEXT.set(node, orig);
    var next = curr === 'zh' ? orig : t(orig);
    if (node.nodeValue !== next) node.nodeValue = next;
    // 无 value 的 option：把「翻译前的中文原文」固化到 value，避免翻译后提交值变化
    var p = node.parentElement;
    if (p && p.tagName === 'OPTION' && !p.hasAttribute('value')) {
      if (!p.dataset.i18nValue) p.dataset.i18nValue = String(orig).trim();
      p.value = p.dataset.i18nValue;
    }
  }

  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];
  function handleAttrs(root) {
    var list = [];
    if (root.nodeType === 1) list.push(root);
    if (root.querySelectorAll) list = list.concat(Array.prototype.slice.call(root.querySelectorAll('*')));
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      var saved = ORIG_ATTR.get(el);
      if (!saved) { saved = {}; ATTRS.forEach(function (a) { if (el.hasAttribute(a)) saved[a] = el.getAttribute(a); }); ORIG_ATTR.set(el, saved); }
      ATTRS.forEach(function (a) {
        if (!Object.prototype.hasOwnProperty.call(saved, a)) return;
        var next = curr === 'zh' ? saved[a] : t(saved[a]);
        if (el.getAttribute(a) !== next) el.setAttribute(a, next);
      });
    }
  }

  /** data-i18n="中文原文"：动态生成的界面文案用它标记，切换时可精确重设（可逆）。 */
  function handleDataI18n(root) {
    var list = [];
    if (root.nodeType === 1 && root.hasAttribute && root.hasAttribute('data-i18n')) list.push(root);
    if (root.querySelectorAll) list = list.concat(Array.prototype.slice.call(root.querySelectorAll('[data-i18n]')));
    list.forEach(function (el) {
      if (el.children.length) return;   // 含子元素的容器交给文本节点扫描，避免破坏结构
      var key = el.getAttribute('data-i18n');
      if (!key) return;
      var next = t(key);
      if (el.textContent !== next) el.textContent = next;
    });
  }

  function handleRich(root) {
    var list = root.querySelectorAll ? Array.prototype.slice.call(root.querySelectorAll('[data-i18n-html]')) : [];
    if (root.nodeType === 1 && root.hasAttribute && root.hasAttribute('data-i18n-html')) list.unshift(root);
    list.forEach(function (el) {
      if (!ORIG_HTML.has(el)) ORIG_HTML.set(el, el.innerHTML);
      var key = el.getAttribute('data-i18n-html');
      var html = curr === 'en' ? EN_HTML[key] : null;
      var target = html || ORIG_HTML.get(el);
      if (el.innerHTML !== target) el.innerHTML = target;
    });
  }

  /** 扫描并翻译 DOM 子树（幂等，可重复调用）。 */
  function applyI18n(root) {
    root = root || document;
    if (root.nodeType === 3) { handleTextNode(root); return; }
    if (!root.querySelectorAll && root.nodeType !== 9 && root.nodeType !== 1) return;
    suspended = true;
    try {
      handleRich(root);
      handleDataI18n(root);
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
      var nodes = [], n;
      while ((n = walker.nextNode())) nodes.push(n);
      nodes.forEach(handleTextNode);
      handleAttrs(root);
      if (TITLE_ORIG === null && document.title) TITLE_ORIG = document.title;
      if (TITLE_ORIG !== null) {
        var nt = curr === 'zh' ? TITLE_ORIG : t(TITLE_ORIG);
        if (document.title !== nt) document.title = nt;
      }
    } finally { suspended = false; }
  }

  function onMutation(records) {
    if (suspended || curr === 'zh') return;
    suspended = true;
    try {
      for (var i = 0; i < records.length; i++) {
        var rec = records[i];
        if (rec.type === 'childList') {
          for (var j = 0; j < rec.addedNodes.length; j++) {
            var nd = rec.addedNodes[j];
            if (nd.nodeType === 3) handleTextNode(nd);
            else if (nd.nodeType === 1) {
              if (skipped(nd)) continue;
              handleRich(nd);
              handleDataI18n(nd);
              var w = document.createTreeWalker(nd, NodeFilter.SHOW_TEXT, null, false), x;
              while ((x = w.nextNode())) handleTextNode(x);
              handleAttrs(nd);
            }
          }
        } else if (rec.type === 'characterData') {
          // 只处理界面文案节点；用户数据容器已由 skipped() 排除
          handleTextNode(rec.target);
        }
      }
    } finally { suspended = false; }
  }

  function setLang(lang) {
    lang = (lang === 'en') ? 'en' : 'zh';
    curr = lang;
    try { localStorage.setItem(KEY_LANG, lang); } catch (_) { }
    document.documentElement.setAttribute('lang', lang === 'en' ? 'en' : 'zh-CN');
    document.documentElement.setAttribute('data-lang', lang);
    applyI18n(document);
    try { document.dispatchEvent(new CustomEvent('kb:langchange', { detail: { lang: lang } })); } catch (_) { }
  }

  function boot() {
    try { curr = localStorage.getItem(KEY_LANG) === 'en' ? 'en' : 'zh'; } catch (_) { curr = 'zh'; }
    document.documentElement.setAttribute('lang', curr === 'en' ? 'en' : 'zh-CN');
    document.documentElement.setAttribute('data-lang', curr);
    if (curr === 'en') applyI18n(document);
    if (window.MutationObserver) {
      var obs = new MutationObserver(onMutation);
      obs.observe(document.body, { childList: true, subtree: true, characterData: true });
      observers.push(obs);
    }
  }

  window.t = t;
  window.getLang = getLang;
  window.setLang = setLang;
  window.applyI18n = applyI18n;
  window.I18N_DICT = EN;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
