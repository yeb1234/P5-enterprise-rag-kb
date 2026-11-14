/* 企业级知识库 · 可运行演示切片 —— 全部前端逻辑（原生 JS，零构建）
 * 关键机制：Bearer 鉴权、SSE(fetch stream) 逐 token 渲染、断线续传、
 *           繁忙/离线横幅轮询、badcase 采集与按筛选导出。
 */
const S = {
  token: localStorage.getItem('kb_token') || '',
  user: null,
  view: 'docs',
  authMode: 'login',
  evalTasks: [],
  conversationId: '',
  msgCount: 0,
  badcaseCount: 0,
  offline: false,
  busy: false,
  needDocPoll: false,
  streaming: false,
  evalTab: 'config',
  _modalData: {},   // 弹窗参数注册表：避免把用户输入拼进 onclick 属性
};

/* ---------------- 基础工具 ---------------- */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtSize = (b) => b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(1) + ' MB';
const fmtTime = (s) => (s || '').replace('T', ' ').replace('Z', '');

function toast(text, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = text;
  $('toasts').appendChild(el);
  setTimeout(() => el.remove(), 3600);
}

function openModal(html) {
  $('modal-root').innerHTML = `<div class="modal-mask" onclick="if(event.target===this)closeModal()"><div class="card modal">${html}</div></div>`;
}
function closeModal() { $('modal-root').innerHTML = ''; }
/* 把含引号/换行的用户输入放进注册表，onclick 只传短 key，避免属性注入 */
function storeModalData(obj) {
  const k = 'k' + Math.random().toString(36).slice(2, 10);
  S._modalData[k] = obj;
  return k;
}
function takeModalData(k) { const d = S._modalData[k]; delete S._modalData[k]; return d; }

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (S.token) headers['Authorization'] = 'Bearer ' + S.token;
  if (opts.body && typeof opts.body === 'string') headers['Content-Type'] = 'application/json';
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (res.status === 401) {
    let detail = '认证失败';
    try { const j = await res.json(); detail = j.detail || detail; } catch (_) { }
    // 登录接口本身的 401 是「账号或密码错误」，不能误当成令牌过期
    if (!path.startsWith('/api/auth/')) {
      forceLogout('登录已过期，请重新登录');
      throw new Error('unauthorized');
    }
    throw new Error(detail);
  }
  if (res.status === 429) {
    // 统一 429 契约：区分可退避重试的 rate_limit_exceeded 与不重试的 quota_exceeded。
    // 后端 body 见 webapp/main.py 的 rate_limit_gate；这里不能只抛 detail，否则前端
    // 会把「额度耗尽」也提示成「稍后重试」，用户白等。
    let j = {};
    try { j = await res.json(); } catch (_) { }
    const ra = Number(res.headers.get('Retry-After') || j.retry_after || 0);
    const kind = j.kind || 'rate_limit_exceeded';
    const hint = kind === 'quota_exceeded'
      ? '额度已用尽，不会自动重试；请稍后重试或更换模型 / 供应商'
      : (ra > 0 ? `请 ${ra}s 后重试` : '请稍后重试');
    throw new Error(`${j.message || '请求被限流'}：${hint}${j.code ? `（${j.code}）` : ''}`);
  }
  if (!res.ok) {
    let detail = '请求失败 (' + res.status + ')';
    try { const j = await res.json(); detail = j.detail || detail; } catch (_) { }
    throw new Error(detail);
  }
  return res.json();
}

/* ---------------- 认证 ---------------- */
function switchAuth(mode) {
  S.authMode = mode;
  $('tab-login').classList.toggle('active', mode === 'login');
  $('tab-register').classList.toggle('active', mode === 'register');
  $('auth-title').textContent = mode === 'login' ? '欢迎回来' : '创建账号';
  $('auth-sub').textContent = mode === 'login' ? '登录以进入知识库工作台' : '注册一个普通账号，体验问答与 badcase 采集';
  $('auth-email-field').classList.toggle('hidden', mode === 'login');
  $('auth-submit').textContent = mode === 'login' ? '登录' : '注册并登录';
  $('auth-error').classList.add('hidden');
}

async function submitAuth() {
  const username = $('auth-username').value.trim();
  const password = $('auth-password').value;
  const email = $('auth-email').value.trim();
  if (!username || !password) { showAuthError('请输入用户名和密码'); return; }
  $('auth-submit').disabled = true;
  try {
    const body = S.authMode === 'login' ? { username, password } : { username, password, email: email || null };
    const r = await api('/api/auth/' + (S.authMode === 'login' ? 'login' : 'register'),
      { method: 'POST', body: JSON.stringify(body) });
    S.token = r.access_token;
    localStorage.setItem('kb_token', S.token);
    $('auth-error').classList.add('hidden');
    await bootApp();
  } catch (e) {
    if (e.message !== 'unauthorized') showAuthError(e.message);
  } finally { $('auth-submit').disabled = false; }
}
function showAuthError(msg) { $('auth-error').textContent = msg; $('auth-error').classList.remove('hidden'); }

function forceLogout(msg) {
  S.token = ''; S.user = null;
  // 会话态必须随身份一起清空：否则 A 登出后 B 登录仍看到 A 的对话，
  // 且下一次发问会追加到 A 的 conversation_id（跨用户串号）。
  // 流式状态也要复位，避免 A 生成中途被踢出后 B 永久无法发问。
  S.conversationId = '';
  S.msgCount = 0;
  S.streaming = false;
  S._modalData = {};
  S.evalTasks = [];
  S._lastBusy = null;
  localStorage.removeItem('kb_token');
  if (S._timer) { clearInterval(S._timer); S._timer = null; }
  const chatLog = $('chat-log'); if (chatLog) chatLog.innerHTML = '';
  const send = $('chat-send'); if (send) send.disabled = false;
  $('app-view').classList.add('hidden');
  $('auth-view').classList.remove('hidden');
  if (msg) toast(msg, 'warn');
}
function logout() { forceLogout('已退出登录'); }

async function bootApp() {
  if (!S.token) { $('auth-view').classList.remove('hidden'); return; }
  try {
    S.user = await api('/api/auth/me');
  } catch (_) { return; }
  $('auth-view').classList.add('hidden');
  $('app-view').classList.remove('hidden');
  $('user-name').textContent = S.user.username;
  $('user-avatar').textContent = S.user.username.slice(0, 1).toUpperCase();
  const isAdmin = S.user.role === 'admin';
  $('user-avatar').classList.toggle('admin', isAdmin);
  $('user-role').classList.toggle('hidden', !isAdmin);
  $('nav-admin').classList.toggle('hidden', !isAdmin);
  $('nav-audit').classList.toggle('hidden', !isAdmin);
  $('nav-settings').classList.toggle('hidden', !isAdmin);
  // 预取评估任务（chat 标记 badcase 与 admin 筛选共用）
  try { S.evalTasks = await api('/api/eval-tasks'); } catch (_) { S.evalTasks = []; }
  fillTaskSelect();
  if (!S.conversationId) newConversation();
  switchView('home');
  pollBusy();
  if (!S._timer) S._timer = setInterval(tick, 2500);
}

function tick() {
  if (!S.token) return;   // 已登出，停止轮询，避免 401 循环
  pollBusy();
  if (S.view === 'docs' && S.needDocPoll && !S.offline) refreshDocs(true);
  if (S.view === 'tasks' && S.busy && !S.offline) loadTasks();
}

/* ---------------- 视图切换 ---------------- */
const VIEW_META = {
  home:   ['工作台概览', '工作台 / 概览'],
  chat:   ['智能问答', '工作台 / 智能问答'],
  docs:   ['文档管理', '工作台 / 文档管理'],
  search: ['检索调试', '工作台 / 检索调试'],
  tasks:  ['任务中心', '质量与治理 / 任务中心'],
  eval:   ['评测中心', '质量与治理 / 评测中心'],
  evaltest: ['评估测试', '质量与治理 / 评估测试（可观测）'],
  parsing: ['解析实验室', '质量与治理 / 解析实验室（三级策略）'],
  knowledge: ['知识治理', '质量与治理 / 知识治理（新鲜度与冲突检测）'],
  admin:  ['管理后台', '质量与治理 / 管理后台（仅 admin）'],
  audit:  ['审计日志', '质量与治理 / 审计日志（仅 admin）'],
  settings: ['模型设置', '系统 / 模型设置（仅 admin）'],
  system: ['系统设置与边界', '系统 / 设置与边界'],
  runtime: ['缓存与恢复', '系统 / 缓存与恢复（重启稳定性）'],
  safety: ['内容安全 / PII 防护', '系统 / 内容安全与 PII 防护（四出口）'],
};
const ADMIN_VIEWS = ['admin', 'audit', 'settings'];
const VIEWS = Object.keys(VIEW_META);
/* 每个视图进入时的数据加载器；chat / search 由用户动作触发，不自动拉数据 */
const LOADERS = {
  home: loadOverview, docs: refreshDocs, tasks: loadTasks, eval: loadEval,
  admin: loadAdmin, audit: loadAudit, settings: loadSettings, system: loadSystem,
  evaltest: loadEvalTest, parsing: loadParsing, knowledge: loadKnowledge,
  runtime: loadRuntime,
  safety: loadSafety,
};
function switchView(view) {
  if (ADMIN_VIEWS.includes(view) && (!S.user || S.user.role !== 'admin')) {
    toast(t('仅管理员可访问该页面（接口同样返回 403）'), 'err'); return;
  }
  S.view = view;
  document.querySelectorAll('.nav button[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  VIEWS.forEach(v => $('view-' + v).classList.toggle('hidden', v !== view));
  $('page-title').textContent = t(VIEW_META[view][0]);
  $('page-crumb').textContent = t(VIEW_META[view][1]);
  const loader = LOADERS[view];
  if (loader) loader();
  if (view === 'chat') loadConversations();  // 进入问答页即加载历史会话列表
  if (window.applyI18n) applyI18n($('view-' + view));
}
function refreshCurrentView() { const l = LOADERS[S.view]; if (l) l(); }

/* ---------------- 外观（语言 / 主题色 / 明暗） ---------------- */
const THEME_COLORS = {
  indigo: '#6366f1', teal: '#14b8a6', violet: '#8b5cf6',
  amber: '#f59e0b', rose: '#f43f5e', graphite: '#64748b'
};
function initAppearance() {
  const box = $('kb-swatches');
  if (box) {
    box.innerHTML = Object.keys(THEME_COLORS).map(k => {
      const label = (window.KBTheme.labels[k] || {})[getLang()] || k;
      return `<button class="swatch" type="button" data-theme-key="${k}" style="--sw:${THEME_COLORS[k]}" title="${esc(label)}" aria-label="${esc(label)}" onclick="setTheme('${k}')"></button>`;
    }).join('');
  }
  renderAppearance();
  document.addEventListener('kb:themechange', renderAppearance);
  document.addEventListener('kb:langchange', () => {
    renderAppearance();
    // 静态 DOM 已由 i18n 引擎重扫；这里只重渲染由 JS 生成的动态内容
    if (S.user) {
      $('page-title').textContent = t(VIEW_META[S.view][0]);
      $('page-crumb').textContent = t(VIEW_META[S.view][1]);
      renderBanners(S._lastBusy || null);
      refreshCurrentView();
      updateConvStats();
      loadConversations();
    }
  });
}
function renderAppearance() {
  const lang = getLang();
  if ($('kb-lang')) $('kb-lang').value = lang;
  document.querySelectorAll('.swatch[data-theme-key]').forEach(b => {
    b.setAttribute('aria-pressed', b.dataset.themeKey === KBTheme.getTheme() ? 'true' : 'false');
    const l = (KBTheme.labels[b.dataset.themeKey] || {})[lang] || b.dataset.themeKey;
    b.setAttribute('title', l); b.setAttribute('aria-label', l);
  });
  const dark = KBTheme.getMode() === 'dark';
  const btn = $('kb-mode');
  if (btn) {
    btn.innerHTML = dark
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12.8A8.5 8.5 0 1 1 11.2 3a6.6 6.6 0 0 0 9.8 9.8z"/></svg>' + esc(t('深色'))
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19"/></svg>' + esc(t('浅色'));
    btn.setAttribute('aria-label', t(dark ? '切换到浅色' : '切换到深色'));
    btn.title = t(dark ? '切换到浅色' : '切换到深色');
  }
}
function setTheme(k) { if (window.KBTheme) KBTheme.setTheme(k); }
function toggleMode() { if (window.KBTheme) KBTheme.toggleMode(); }

/* ---------------- 繁忙 / 断线状态 ---------------- */
async function pollBusy() {
  try {
    const r = await api('/api/system/busy');
    const wasOffline = S.offline;
    S.offline = false; S.busy = r.busy;
    S._lastBusy = r;
    renderBanners(r);
    if (wasOffline) { toast(t('服务连接已恢复，数据已刷新'), 'ok'); refreshCurrentView(); }
  } catch (_) {
    S.offline = true;
    renderBanners(null);
  }
}

function renderBanners(r) {
  const box = $('banners');
  let html = '';
  if (S.offline) {
    html += `<div class="banner banner-offline"><span class="spin"></span>${esc(t('服务连接中断，正在自动重连…（每 2.5 秒尝试一次，恢复后自动刷新数据）'))}</div>`;
  } else if (r && r.busy) {
    html += `<div class="banner banner-busy"><span class="spin"></span>${esc(t('上传分析繁忙，超出并发闸的请求已排队。'))}<span class="queue-pill">${esc(t('处理中 {a} · 排队 {q}', { a: r.active_count, q: r.queue_count }))}</span></div>`;
  }
  box.innerHTML = html;
  // 侧边栏与顶栏状态
  const dot = $('sys-dot');
  dot.className = 'sys-dot' + (S.offline ? ' off' : (S.busy ? ' busy' : ''));
  $('sys-text').textContent = t(S.offline ? '连接中断' : (S.busy ? '上传分析繁忙' : '系统运行中'));
  const inline = $('busy-inline');
  inline.classList.toggle('hidden', !S.busy || S.offline);
  if (S.busy && !S.offline && r) {
    inline.className = 'badge badge-amber';
    inline.textContent = t('繁忙 · 排队 {n}', { n: r.queue_count });
  }
  // 文档页运行详情
  if (r) {
    $('busy-detail').innerHTML = S.offline ? `<span style="color:var(--danger)">${esc(t('后端不可达'))}</span>` :
      (r.busy
        ? `${esc(t('当前 {a} 个任务正在分析，{q} 个排队。', { a: r.active_count, q: r.queue_count }))}<br/>${esc(t('处理中：{n}', { n: (r.active || []).map(x => x.filename).join('、') || '—' }))}<br/>${esc(t('排队中：{n}', { n: (r.queued || []).map(x => x.filename).join('、') || '—' }))}`
        : esc(t('系统空闲，上传文档将即时开始分析。')));
  }
}

/* ---------------- 文档管理 ---------------- */
async function refreshDocs(silent) {
  try {
    const docs = await api('/api/docs');
    renderDocs(docs);
    S.needDocPoll = docs.some(d => d.status === 'queued' || d.status === 'analyzing');
    $('stat-docs').textContent = docs.length;
    $('stat-ready').textContent = docs.filter(d => d.status === 'ready').length;
    $('stat-chunks').textContent = docs.reduce((a, d) => a + (d.chunk_count || 0), 0);
    $('stat-error').textContent = docs.filter(d => d.status === 'error').length;
  } catch (e) { if (!silent && e.message !== 'unauthorized') toast('读取文档失败：' + e.message, 'err'); }
}

const DOC_BADGE = {
  queued: ['badge-amber', '排队中'], analyzing: ['badge-blue', '分析中'],
  ready: ['badge-green', '已就绪'], error: ['badge-red', '降级/失败'],
};
function renderDocs(docs) {
  if (!docs.length) {
    $('docs-table').innerHTML = `<div class="empty-state"><div class="big">▤</div>${esc(t('还没有文档，先上传一份或点击「生成示例说明书」。'))}</div>`;
    return;
  }
  const rows = docs.map(d => {
    const [cls, label] = DOC_BADGE[d.status] || ['badge-gray', d.status];
    let note = '';
    if (d.status === 'queued') note = `<div class="progress-mini"><i style="width:12%"></i></div>`;
    if (d.status === 'analyzing') note = `<div class="progress-mini"><i style="width:62%"></i></div>`;
    if (d.status === 'error') note = `<div class="small" style="color:var(--danger);max-width:340px">${esc(d.error_msg)}</div>`;
    return `<tr>
      <td><div class="file-cell"><span class="file-ico">${esc((d.filename.split('.').pop() || 'T').slice(0, 3).toUpperCase())}</span>
        <div class="file-name" title="${esc(d.filename)}">${esc(d.filename)}</div></div></td>
      <td class="small muted">${fmtSize(d.size_bytes)}</td>
      <td><span class="badge ${cls}">${label}</span>${note}</td>
      <td class="small">${esc(t('{n} 块', { n: d.chunk_count }))}</td>
      <td class="small muted">${esc(fmtTime(d.created_at))}</td>
      <td><button class="btn btn-sm btn-danger" data-i18n="删除" onclick="removeDoc('${esc(d.id)}')">${esc(t('删除'))}</button></td>
    </tr>`;
  }).join('');
  $('docs-table').innerHTML = `<table><thead><tr><th>${esc(t('文件名'))}</th><th>${esc(t('大小'))}</th><th>${esc(t('状态'))}</th><th>${esc(t('分块'))}</th><th>${esc(t('上传时间'))}</th><th>${esc(t('操作'))}</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function handleFiles(files) {
  if (!files || !files.length) return;
  for (const f of files) {
    const fd = new FormData(); fd.append('file', f);
    try {
      await api('/api/docs/upload', { method: 'POST', body: fd });
      toast(t('已接收「{n}」，进入分析队列', { n: f.name }), 'ok');
    } catch (e) { toast(t('上传「{n}」失败：', { n: f.name }) + e.message, 'err'); }
  }
  $('file-input').value = '';
  S.busy = true; pollBusy(); refreshDocs();
}
async function seedSample() {
  try {
    await api('/api/docs/seed-sample', { method: 'POST' });
    toast(t('示例说明书已生成并进入分析队列'), 'ok');
    pollBusy(); refreshDocs();
  } catch (e) { toast(e.message, 'err'); }
}
async function removeDoc(id) {
  try { await api('/api/docs/' + id, { method: 'DELETE' }); toast(t('文档已删除'), 'ok'); refreshDocs(); }
  catch (e) { toast(e.message, 'err'); }
}

/* 拖拽上传 */
document.addEventListener('DOMContentLoaded', () => {
  const z = $('drop-zone');
  if (z) {
    z.addEventListener('dragover', (e) => { e.preventDefault(); z.classList.add('drag'); });
    z.addEventListener('dragleave', () => z.classList.remove('drag'));
    z.addEventListener('drop', (e) => { e.preventDefault(); z.classList.remove('drag'); handleFiles(e.dataTransfer.files); });
  }
  $('auth-password').addEventListener('keydown', (e) => { if (e.key === 'Enter') submitAuth(); });
  // 演示快捷入口：?demo=admin 自动用内置演示管理员登录，一键看到含管理后台的全貌。
  // 仅用于本地离线演示切片；生产环境必须删掉该分支，绝不内置任何口令。
  if (new URLSearchParams(location.search).get('demo') === 'admin') {
    history.replaceState({}, '', location.pathname);
    localStorage.removeItem('kb_token');
    S.token = '';
    autoLoginAdmin();
    return;
  }
  if (S.token) bootApp();
});

async function autoLoginAdmin() {
  try {
    const r = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: 'admin', password: 'admin123' }),
    });
    S.token = r.access_token;
    localStorage.setItem('kb_token', S.token);
    await bootApp();
    toast('已自动登录演示管理员账号', 'ok');
  } catch (_) {
    $('auth-view').classList.remove('hidden');
  }
}

/* ---------------- Chat：流式 + 断线续传 ---------------- */
function newConversation() {
  S.conversationId = '';
  S.msgCount = 0; S.badcaseCount = 0;
  $('chat-log').innerHTML = `<div class="msg bot"><div class="m-avatar">KB</div><div class="msg-body">
    <div class="msg-name">${esc(t('知识库助手'))}</div><div class="bubble">${t('你好，我是知识库问答助手。请先到「文档管理」上传说明书，然后向我提问；回答会以流式逐字输出。<br/><br/>若知识库没有命中，我会明确告知，不会编造内容。')}</div></div></div>`;
  $('conv-id').textContent = t('尚未开始');
  updateConvStats();
}
function updateConvStats() {
  $('conv-msgs').textContent = S.msgCount;
  $('conv-bc').textContent = S.badcaseCount;
  $('conv-conn').textContent = S.offline ? '连接中断' : (S.streaming ? '流式传输中' : '正常');
}

function appendMsg(role, text) {
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
  wrap.innerHTML = `<div class="m-avatar">${role === 'user' ? esc(t('我')) : 'KB'}</div>
    <div class="msg-body"><div class="msg-name">${role === 'user' ? esc(S.user.username) : esc(t('知识库助手'))}</div>
    <div class="bubble"></div></div>`;
  const bubble = wrap.querySelector('.bubble');
  bubble.textContent = text || '';
  $('chat-log').appendChild(wrap);
  $('chat-log').scrollTop = $('chat-log').scrollHeight;
  return { wrap, bubble, body: wrap.querySelector('.msg-body') };
}

async function sendChat() {
  if (S.streaming) { toast('正在生成回答，请稍候', 'warn'); return; }
  const q = $('chat-q').value.trim();
  if (!q) return;
  $('chat-q').value = '';
  S.streaming = true;
  $('chat-send').disabled = true;
  appendMsg('user', q);
  S.msgCount++;
  const bot = appendMsg('bot', '');
  bot.bubble.innerHTML = '<span class="cursor-blink"></span>';
  updateConvStats();

  let resumeFrom = 0, retries = 0, finished = false, degrade = false, reason = '', convId = S.conversationId;
  let authExpired = false;
  let lastSources = [];
  const setStatus = (t) => { $('chat-status').innerHTML = t; $('conv-conn').textContent = t.replace(/<[^>]+>/g, ''); };

  while (retries <= 3 && !finished) {
    try {
      setStatus(retries === 0 ? esc(t('正在检索知识库并流式生成…'))
        : esc(t('连接中断，正在续传（第 {r} 次重试，已续 {n} 个 token）…', { r: retries, n: resumeFrom })));
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + S.token },
        body: JSON.stringify({ question: q, conversation_id: convId || null, resume_from: resumeFrom }),
      });
      if (res.status === 401) {
        forceLogout('登录已过期，请重新登录');
        authExpired = true;
        break;  // 不能直接 return：会跳过下面的流式状态复位
      }
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '', lastIndex = resumeFrom - 1, received = 0;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop();
        for (const part of parts) {
          if (!part.trim() || part.trim().startsWith(':')) continue;
          let ev = 'message', data = '';
          for (const line of part.split('\n')) {
            if (line.startsWith('event:')) ev = line.slice(6).trim();
            else if (line.startsWith('data:')) data += line.slice(5).trim();
          }
          if (!data) continue;
          const obj = JSON.parse(data);
          if (ev === 'meta') {
            convId = obj.conversation_id;
            S.conversationId = convId;
            $('conv-id').textContent = convId.slice(0, 8) + '…';
            // 流式过程中先渲染已命中的来源，让用户实时看到回答依据
            if (obj.sources && obj.sources.length) { lastSources = obj.sources; ensureSources(bot); renderSources(bot.sourcesEl, obj.sources); }
            if (obj.degraded) { degrade = true; if (obj.degrade_reason) reason = obj.degrade_reason; }
          } else if (ev === 'done') {
            finished = true;
            // 流式脱敏闸会扣住尾部若干字符等右边界确认；收尾时由 done.tail 补齐，
            // 这样「一个原始 token 一个包」的续传索引契约与内容安全两者都不破。
            if (obj.tail) bot.bubble.insertAdjacentText('beforeend', obj.tail);
            // done 一定带 sources（可能为空数组，对应检索未命中）
            if (obj.sources) { lastSources = obj.sources; ensureSources(bot); renderSources(bot.sourcesEl, obj.sources); }
            if (obj.degraded) { degrade = true; if (obj.degrade_reason) reason = obj.degrade_reason; }
          } else {
            if (received === 0 && bot.bubble.querySelector('.cursor-blink')) bot.bubble.innerHTML = '';
            bot.bubble.insertAdjacentText('beforeend', obj.token);
            lastIndex = obj.index;
            if (obj.degraded) { degrade = true; if (obj.degrade_reason) reason = obj.degrade_reason; }
            received++;
            $('chat-log').scrollTop = $('chat-log').scrollHeight;
          }
        }
      }
      resumeFrom = lastIndex + 1;
      if (!finished) { retries++; degrade = false; reason = ''; }
    } catch (e) {
      retries++;
      if (retries > 3) { bot.bubble.insertAdjacentText('beforeend', '\n\n' + t('[连接中断，重试次数已用尽，请重新发送]')); }
      else await new Promise(r => setTimeout(r, 600));
    }
  }

  // 401 提前退出时没有答案可展示，但必须在返回前复位流式状态；
  // 否则重新登录后 sendChat() 开头的 if (S.streaming) 守卫会永久拦截。
  if (authExpired) {
    const c = bot.bubble.querySelector('.cursor-blink'); if (c) c.remove();
    S.streaming = false;
    $('chat-send').disabled = false;
    setStatus(esc(t('登录已过期，请重新登录后重试')));
    return;
  }

  // 收尾
  const cursor = bot.bubble.querySelector('.cursor-blink');
  if (cursor) cursor.remove();
  const answer = bot.bubble.textContent;
  if (degrade) {
    const d = document.createElement('div');
    d.className = 'degrade-note';
    const DEGRADE_KEYS = {
      model_error: '降级提示：模型服务暂时不可用或触发限流，已自动降级为本地拼接回答（仍基于检索片段）。稍后可重试以获取完整生成。',
      quota_exceeded: '降级提示：当前模型额度（墨点）已用尽，未自动重试，本次为本地拼接回答。可在「模型设置」换模型或换供应商后重试。',
      stream_interrupted: '降级提示：流式生成中途中断，以上为已收到的部分内容。可重新提问以获取完整回答。',
      empty_model_output: '降级提示：当前模型未返回正文（可能只输出了推理过程），已自动降级为本地拼接回答。可在「模型设置」换成非推理模型（如 qwen2.5-coder-7b-instruct）后重试。',
      retrieval_empty: '降级提示：知识库未命中相关内容，本次为占位拒答话术（不编造）。可上传文档后重试。',
    };
    const dk = DEGRADE_KEYS[reason] || '降级提示：本次回答已降级处理。';
    d.setAttribute('data-i18n', dk);
    d.textContent = t(dk);
    bot.body.appendChild(d);
  }
  const tools = document.createElement('div');
  tools.className = 'msg-tools';
  const bcKey = storeModalData({ q, answer });
  tools.innerHTML = `<button class="btn btn-sm" data-i18n="标记为 badcase" onclick="openBadcaseFromKey('${bcKey}')">${esc(t('标记为 badcase'))}</button>
    <span class="muted small">${esc(fmtTime(new Date().toISOString().replace(/\.\d+Z$/, 'Z')))} ${esc(t(' · 流式完成'))}</span>`;
  bot.body.appendChild(tools);
  S.msgCount++;
  S.streaming = false;
  $('chat-send').disabled = false;
  setStatus(finished ? esc(t('完成 · Enter 发送，Shift+Enter 换行')) : esc(t('连接异常结束')));
  updateConvStats();
  loadConversations();  // 刷新左侧历史会话列表（含来源标记）
}

/* 确保 bot 消息体里有一个来源容器（紧跟在气泡之后） */
function ensureSources(bot) {
  if (!bot.sourcesEl) {
    bot.sourcesEl = document.createElement('div');
    bot.body.appendChild(bot.sourcesEl);
  }
  return bot.sourcesEl;
}

/* 渲染来源卡片（每条助手回答下方的「引用来源」面板） */
function renderSources(container, sources) {
  if (!sources || !sources.length) {
    container.innerHTML = `<div class="sources-empty" data-i18n="无引用来源 · 本次回答未命中知识库（占位拒答或降级，不编造）">${esc(t('无引用来源 · 本次回答未命中知识库（占位拒答或降级，不编造）'))}</div>`;
    return;
  }
  const cards = sources.map((s, i) => {
    const key = storeModalData(s);
    const score = (s.score == null ? '' : t('得分 {n}', { n: (typeof s.score === 'number' ? s.score.toFixed(3) : s.score) }));
    return `<div class="source-card" onclick="openSourceFromKey('${key}')">
      <div class="sc-head"><span class="sc-idx">${i + 1}</span><b class="sc-title">${esc(s.doc_title || t('未知文档'))}</b></div>
      <div class="sc-meta">
        <span class="badge badge-blue"><i style="font-style:normal" data-i18n="分块">${esc(t('分块'))}</i> #${esc(s.idx)}</span>
        ${score ? `<span class="badge badge-gray">${esc(score)}</span>` : ''}
        ${s.retrieval_mode ? `<span class="badge badge-purple">${esc(s.retrieval_mode)}</span>` : ''}
      </div>
      <div class="sc-snippet">${esc((s.snippet || '').replace(/\s+/g, ' ').trim())}…</div>
    </div>`;
  }).join('');
  container.innerHTML = `<div class="sources"><div class="sources-title">${esc(t('引用来源（{n} 条）· 点击查看原文', { n: sources.length }))}</div><div class="source-list">${cards}</div></div>`;
}

function openSourceFromKey(k) {
  const s = takeModalData(k);
  if (s) openSourceModal(s);
}
function openSourceModal(s) {
  openModal(`<h3>引用来源详情</h3>
    <div class="sub">${esc(s.doc_title || '未知文档')} · 分块 #${esc(s.idx)}</div>
    <div class="stack">
      <div class="kv-line"><span>来源文档</span><b>${esc(s.doc_title || '—')}</b></div>
      <div class="kv-line"><span>分块序号</span><b>#${esc(s.idx)}（chunk_id ${esc((s.chunk_id || '').slice(0, 8))}…）</b></div>
      <div class="kv-line"><span>相关度得分</span><b>${s.score == null ? '—' : (typeof s.score === 'number' ? s.score.toFixed(4) : s.score)}</b></div>
      <div class="kv-line"><span>检索方式</span><b>${esc(s.retrieval_mode || '—')}</b></div>
      <div class="kv-line"><span>重排</span><b>${esc(s.rerank || 'off')}</b></div>
      <div class="quoted" style="white-space:pre-wrap">${esc(s.text || s.snippet || '')}</div>
    </div>
    <div class="actions"><button class="btn btn-primary" onclick="closeModal()">关闭</button></div>`);
}

/* ---------------- 问答历史与会话追溯 ---------------- */
async function loadConversations() {
  const box = $('conv-list');
  if (!box) return;
  try {
    const list = await api('/api/chat/conversations');
    if (!list.length) { box.innerHTML = '<div class="muted small">还没有历史会话</div>'; return; }
    box.innerHTML = list.map(c => {
      const key = storeModalData(c);
      return `<div class="conv-item" onclick="openConversation('${key}')">
        <div class="conv-q">${esc((c.first_question || '').slice(0, 38))}${(c.first_question || '').length > 38 ? '…' : ''}</div>
        <div class="conv-sub small muted">${esc(fmtTime(c.last_at))} · ${esc(t(c.has_sources ? '{n} 条 · 含来源' : '{n} 条', { n: c.msg_count }))}</div>
      </div>`;
    }).join('');
  } catch (_) { /* 静默：历史列表不影响当前问答 */ }
}

function openConversation(key) {
  const c = takeModalData(key);
  if (c) loadHistory(c.conversation_id);
}

async function loadHistory(convId) {
  try {
    const r = await api('/api/chat/history?conversation_id=' + encodeURIComponent(convId));
    S.conversationId = convId;
    $('conv-id').textContent = convId.slice(0, 8) + '…';
    S.msgCount = 0; S.badcaseCount = 0;
    $('chat-log').innerHTML = '';
    for (const m of (r.messages || [])) {
      const node = appendMsg(m.role, m.content);
      if (m.role === 'assistant' && m.sources) {
        const sc = document.createElement('div');
        node.body.appendChild(sc);
        renderSources(sc, m.sources);
      }
      S.msgCount++;
    }
    updateConvStats();
    toast('已载入历史会话（含来源追溯）', 'ok');
  } catch (e) { toast('载入历史失败：' + e.message, 'err'); }
}
function fillTaskSelect() {
  const sel = $('f-task');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="">全部任务</option>' + S.evalTasks.map(t =>
    `<option value="${esc(t.id)}">${esc(t.name)} (${esc(t.id)})</option>`).join('');
  sel.value = cur;
}

function openBadcaseFromKey(k) {
  const d = takeModalData(k);
  if (d) openBadcaseModal(d.q, d.answer);
}

function openBadcaseModal(question, answer) {
  if (!S.evalTasks.length) { toast('评估任务列表未加载，请刷新页面', 'err'); return; }
  const submitKey = storeModalData({ question, answer });
  openModal(`<h3>标记为 badcase</h3><div class="sub">该记录会关联到指定评估任务，并进入管理员后台的完成度统计与导出。</div>
    <div class="stack">
      <div class="quoted"><b>原始问题：</b>${esc(question)}<br/><b>模型回答：</b>${esc((answer || '').slice(0, 220))}${(answer || '').length > 220 ? '…' : ''}</div>
      <div class="field"><label>关联评估任务 *</label><select id="bc-task" class="select">${S.evalTasks.map(t => `<option value="${esc(t.id)}">${esc(t.name)} (${esc(t.id)})</option>`).join('')}</select></div>
      <div class="field"><label>标注 / 判定结果</label><select id="bc-verdict" class="select"><option value="错误">错误</option><option value="部分正确">部分正确</option><option value="不确定">不确定</option><option value="正确">正确</option></select></div>
      <div class="field"><label>问题说明</label><textarea id="bc-issue" class="textarea" placeholder="例如：引用缺失 / 检索未命中 / 回答超时 / 事实错误"></textarea></div>
      <div class="field"><label>来源</label><select id="bc-source" class="select"><option value="chat">问答标记</option><option value="manual">手工录入</option><option value="eval">评测批次</option></select></div>
    </div>
    <div class="actions"><button class="btn" onclick="closeModal()">取消</button>
      <button class="btn btn-primary" id="bc-submit" onclick="submitBadcaseFromKey('${submitKey}')">保存记录</button></div>`);
}

async function submitBadcaseFromKey(k) {
  const d = takeModalData(k);
  if (d) await submitBadcase(d.question, d.answer);
}

async function submitBadcase(question, answer) {
  const body = {
    eval_task_id: $('bc-task').value,
    question, answer,
    verdict: $('bc-verdict').value,
    issue_desc: $('bc-issue').value.trim() || null,
    source: $('bc-source').value,
  };
  $('bc-submit').disabled = true;
  try {
    await api('/api/badcases', { method: 'POST', body: JSON.stringify(body) });
    closeModal();
    S.badcaseCount++; updateConvStats();
    toast('badcase 已保存并关联评估任务', 'ok');
    if (S.view === 'admin') loadAdmin();
  } catch (e) { toast('保存失败：' + e.message, 'err'); $('bc-submit').disabled = false; }
}

/* ---------------- Admin：完成度 + 筛选 + 导出 ---------------- */
function filterQuery() {
  const p = new URLSearchParams();
  const map = { 'f-task': 'eval_task_id', 'f-status': 'status', 'f-source': 'source', 'f-start': 'start', 'f-end': 'end', 'f-q': 'q' };
  for (const [id, key] of Object.entries(map)) {
    const v = $(id).value.trim();
    if (v) p.set(key, v);
  }
  return p.toString();
}

async function loadAdmin() {
  if (!S.user || S.user.role !== 'admin') return;
  try {
    S.evalTasks = await api('/api/eval-tasks');
    fillTaskSelect();
  } catch (_) { }
  try {
    const c = await api('/api/admin/completion');
    renderStats(c.overall);
    renderTaskCards(c.tasks);
  } catch (e) { toast('读取完成度失败：' + e.message, 'err'); }
  await loadBadcases();
}

const TASK_BADGE = { draft: ['badge-gray', '草稿'], running: ['badge-blue', '进行中'], paused: ['badge-amber', '已暂停'], done: ['badge-green', '已完成'] };
function renderStats(o) {
  const rate = (o.completion_rate * 100).toFixed(1) + '%';
  $('admin-stats').innerHTML = `
    <div class="card stat"><div class="label">任务总数（目标样本）</div><div class="value">${o.target_total}</div><div class="foot">全部评估任务合计</div></div>
    <div class="card stat"><div class="label">已完成</div><div class="value" style="color:var(--success)">${o.completed_total}</div><div class="foot">已采集 badcase 数</div></div>
    <div class="card stat"><div class="label">待处理</div><div class="value" style="color:var(--warn)">${o.pending_total}</div><div class="foot">目标 − 已完成</div></div>
    <div class="card stat"><div class="label">整体完成率</div><div class="value" style="color:var(--primary)">${rate}</div><div class="progress-mini"><i style="width:${Math.min(100, o.completion_rate * 100)}%"></i></div></div>`;
}
function renderTaskCards(tasks) {
  if (!tasks.length) { $('task-cards').innerHTML = '<div class="card padded muted">暂无评估任务</div>'; return; }
  $('task-cards').innerHTML = tasks.map(t => {
    const [cls, label] = TASK_BADGE[t.status] || ['badge-gray', t.status];
    const pct = Math.min(100, t.completion_rate * 100).toFixed(1);
    return `<div class="card task-card">
      <div class="tc-head"><b>${esc(t.name)}</b><span class="tc-id">${esc(t.eval_task_id)}</span><span class="badge ${cls}" style="margin-left:auto">${label}</span></div>
      <div class="small muted">${esc(t.description || '')}</div>
      <div class="tc-nums">
        <div><span>任务总数</span><b>${t.target_count}</b></div>
        <div><span>已完成</span><b style="color:var(--success)">${t.completed}</b></div>
        <div><span>待处理</span><b style="color:var(--warn)">${t.pending}</b></div>
        <div><span>完成率</span><b class="rate-num">${(t.completion_rate * 100).toFixed(1)}%</b></div>
      </div>
      <div class="progress-mini"><i style="width:${pct}%"></i></div>
    </div>`;
  }).join('');
}

async function loadBadcases() {
  try {
    const rows = await api('/api/badcases?' + filterQuery());
    renderBadcases(rows);
  } catch (e) { if (e.message !== 'unauthorized') toast('查询 badcase 失败：' + e.message, 'err'); }
}
function resetFilter() {
  ['f-task', 'f-status', 'f-source', 'f-start', 'f-end', 'f-q'].forEach(id => $(id).value = '');
  loadBadcases();
}

const BC_BADGE = { pending: ['badge-amber', '待处理'], in_review: ['badge-blue', '复核中'], resolved: ['badge-green', '已解决'] };
const SRC_LABEL = { chat: '问答', manual: '手工', eval: '评测' };
function renderBadcases(rows) {
  $('bc-count').textContent = t('共 {n} 条记录（当前筛选条件）', { n: rows.length });
  if (!rows.length) {
    $('bc-table').innerHTML = `<div class="empty-state"><div class="big">◫</div>当前筛选下没有 badcase 记录。<br/><span class="small">去「智能问答」里对一条回答点「标记为 badcase」即可产生数据。</span></div>`;
    return;
  }
  const body = rows.map(r => {
    const [cls, label] = BC_BADGE[r.status] || ['badge-gray', r.status];
    const editKey = storeModalData(r);
    return `<tr>
      <td class="bc-question truncate" title="${esc(r.question)}">${esc(r.question)}</td>
      <td class="bc-answer truncate" title="${esc(r.answer)}">${esc(r.answer || '—')}</td>
      <td><span class="badge badge-purple">${esc(r.verdict || '未标注')}</span></td>
      <td class="small truncate" style="max-width:150px" title="${esc(r.issue_desc)}">${esc(r.issue_desc || '—')}</td>
      <td class="small">${esc(SRC_LABEL[r.source] || r.source)}</td>
      <td><span class="badge ${cls}">${label}</span></td>
      <td class="small" style="font-family:ui-monospace,monospace">${esc(r.eval_task_id)}</td>
      <td class="small muted">${esc(fmtTime(r.created_at))}</td>
      <td><button class="btn btn-sm" onclick="openEditFromKey('${editKey}')">处理</button></td>
    </tr>`;
  }).join('');
  $('bc-table').innerHTML = `<table><thead><tr><th>原始问题</th><th>模型回答</th><th>判定</th><th>问题说明</th><th>来源</th><th>状态</th><th>关联任务</th><th>时间</th><th>操作</th></tr></thead><tbody>${body}</tbody></table>`;
}

function openEditFromKey(k) {
  const r = takeModalData(k);
  if (r) openEditModal(r);
}
function openEditModal(r) {
  openModal(`<h3>处理 badcase</h3><div class="sub">${esc(r.id)} · 关联 ${esc(r.eval_task_id)}</div>
    <div class="stack">
      <div class="quoted"><b>问题：</b>${esc(r.question)}<br/><b>回答：</b>${esc((r.answer || '').slice(0, 260))}</div>
      <div class="field"><label>判定结果</label><select id="ed-verdict" class="select">${['错误', '部分正确', '不确定', '正确'].map(v => `<option ${v === r.verdict ? 'selected' : ''}>${v}</option>`).join('')}</select></div>
      <div class="field"><label>状态</label><select id="ed-status" class="select">${[['pending', '待处理'], ['in_review', '复核中'], ['resolved', '已解决']].map(([v, l]) => `<option value="${v}" ${v === r.status ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      <div class="field"><label>问题说明</label><textarea id="ed-issue" class="textarea">${esc(r.issue_desc || '')}</textarea></div>
    </div>
    <div class="actions"><button class="btn" onclick="closeModal()">取消</button>
      <button class="btn btn-primary" id="ed-submit" onclick="submitEdit('${esc(r.id)}')">保存</button></div>`);
}
async function submitEdit(id) {
  $('ed-submit').disabled = true;
  try {
    await api('/api/badcases/' + id, {
      method: 'PATCH',
      body: JSON.stringify({ verdict: $('ed-verdict').value, status: $('ed-status').value, issue_desc: $('ed-issue').value.trim() }),
    });
    closeModal(); toast('记录已更新', 'ok'); loadAdmin();
  } catch (e) { toast(e.message, 'err'); $('ed-submit').disabled = false; }
}

async function exportBadcases(format) {
  try {
    const res = await fetch('/api/badcases/export?format=' + format + '&' + filterQuery(),
      { headers: { 'Authorization': 'Bearer ' + S.token } });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'badcases.' + format; a.click();
    URL.revokeObjectURL(url);
    toast(t('已按当前筛选下载 badcases.{n}', { n: format }), 'ok');
  } catch (e) { toast('下载失败：' + e.message, 'err'); }
}

/* ================= 新增分组导航的数据加载器 ================= */
const statCard = (label, value, foot, color) =>
  `<div class="card stat"><div class="label">${label}</div><div class="value"${color ? ' style="color:' + color + '"' : ''}>${value}</div><div class="foot">${foot}</div></div>`;
const safeToast = (e) => { if (e.message !== 'unauthorized') toast(e.message, 'err'); };

/* ---------- 工作台概览 ---------- */
async function loadOverview() {
  try {
    const o = await api('/api/overview');
    const cards = [
      statCard('文档总数', o.docs.total, o.scope),
      statCard('已就绪 / 分块', o.docs.ready + ' / ' + o.chunks, '仅 ready 文档参与检索'),
      statCard('问答会话', o.chat.conversations, o.chat.messages + ' 条消息'),
      statCard('降级回答', o.chat.degraded, '检索为空，明确拒答', 'var(--warn)'),
      statCard('badcase 记录', o.badcases, '采集闭环数据量'),
      statCard('分析队列', o.system.busy ? '处理 ' + o.system.active_count + ' / 排队 ' + o.system.queue_count : '空闲', '并发上限 2'),
    ];
    if (o.admin) cards.push(
      statCard('注册成员', o.admin.user_count, '仅 admin 可见'),
      statCard('审计记录', o.admin.audit_count, '只追加台账'),
      statCard('评测完成率', (o.admin.completion_rate * 100).toFixed(1) + '%', o.admin.eval_done + ' / ' + o.admin.eval_target + ' 样本'),
      statCard('统计范围', '全局', 'admin 视图'),
    );
    $('home-stats').innerHTML = cards.join('');
    const shortcuts = [
      ['chat', '智能问答', '流式输出与 badcase 采集'],
      ['docs', '文档管理', '上传、排队、状态'],
      ['search', '检索调试', '看命中与降级'],
      ['tasks', '任务中心', '队列与恢复'],
      ['eval', '评测中心', '任务进度与对象'],
      ['system', '系统边界', '替身映射与档次'],
    ];
    if (S.user.role === 'admin') shortcuts.splice(4, 0,
      ['admin', '管理后台', '查看 / 导出'], ['audit', '审计日志', '按人回放']);
    $('home-shortcuts').innerHTML = shortcuts.map(([v, t, d]) =>
      `<button class="shortcut" onclick="switchView('${v}')"><b>${t}</b><span>${d}</span></button>`).join('');
    $('home-scope').innerHTML = `
      <div class="kv-line"><span>当前账号</span><b>${esc(o.user.username)}（${esc(o.user.role)}）</b></div>
      <div class="kv-line"><span>统计范围</span><b>${esc(o.scope)}</b></div>
      <div class="kv-line"><span>排队 / 分析中</span><b>${o.docs.queued} / ${o.docs.analyzing}</b></div>
      <div class="kv-line"><span>失败 / 降级留痕</span><b>${o.docs.error}</b></div>
      <div class="note-box">每次刷新都从 SQLite 业务表实时聚合，不做预聚合日报；
        普通用户的统计强制带 owner_id / user_id / created_by 条件，不是把全局数字藏起来而是根本不查。</div>`;
  } catch (e) { safeToast(e); }
}

/* ---------- 检索调试 ---------- */
async function runSearch() {
  const q = $('sq').value.trim();
  if (!q) { toast('请输入检索词', 'warn'); return; }
  try {
    const r = await api('/api/search/preview?q=' + encodeURIComponent(q));
    $('search-meta').innerHTML = `
      <div class="kv-line"><span>分词（演示口径）</span><b>${esc((r.terms || []).join(' / ') || '—')}</b></div>
      <div class="kv-line"><span>已索引 / 可召回分块</span><b>${r.indexed_chunks} / ${r.eligible_chunks}</b></div>
      <div class="kv-line"><span>命中 / top_k</span><b>${r.hit_count} / ${r.top_k}</b></div>
      <div class="kv-line"><span>降级判定</span><b>${r.degraded ? '<span style="color:var(--warn)">retrieval_empty · 拒答不编造</span>' : '正常生成'}</b></div>
      <div class="kv-line"><span>前置权限过滤</span><b class="small">${esc(r.permission_filter)}</b></div>
      <div class="kv-line"><span>重排 / 生成</span><b class="small">${esc(r.rerank)} · ${esc(r.generator)}</b></div>`;
    if (!r.hits.length) {
      $('search-hits').innerHTML = '<div class="empty-state"><div class="big">◫</div>没有命中任何分块 —— 这正是 chat 页触发降级的条件。</div>';
      return;
    }
    $('search-hits').innerHTML = '<div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>文档</th><th>分块 / 重叠词数</th><th>片段</th></tr></thead><tbody>' +
      r.hits.map((h, i) => `<tr><td>${i + 1}</td><td>${esc(h.doc_title)}</td><td><span class="badge badge-blue">#${h.idx}</span> · <b>${h.score}</b></td><td class="small">${esc(h.snippet)}…</td></tr>`).join('') +
      '</tbody></table></div>';
  } catch (e) { safeToast(e); }
}

/* ---------- 任务中心（复用文档页的 DOC_BADGE 状态映射） ---------- */
async function loadTasks() {
  try {
    const [busy, docs] = await Promise.all([api('/api/system/busy'), api('/api/docs')]);
    const cnt = (k) => docs.filter(d => d.status === k).length;
    $('task-stats').innerHTML = [
      statCard('处理中', busy.active_count, '并发上限 2'),
      statCard('排队中', busy.queue_count, '超出并发闸', 'var(--warn)'),
      statCard('已就绪', cnt('ready'), '可被检索', 'var(--success)'),
      statCard('失败 / 降级', cnt('error'), '均留有具体原因', 'var(--danger)'),
    ].join('');
    $('task-queue').innerHTML = `
      <div class="kv-line"><span>状态</span><b>${busy.busy ? '繁忙' : '空闲'}</b></div>
      <div class="kv-line"><span>处理中文件</span><b class="small">${esc((busy.active || []).map(x => x.filename).join('、') || '—')}</b></div>
      <div class="kv-line"><span>排队中文件</span><b class="small">${esc((busy.queued || []).map(x => x.filename).join('、') || '—')}</b></div>
      <div class="note-box">队列排空后繁忙横幅自动消失；繁忙期间本页每 2.5 秒跟随系统状态刷新。</div>`;
    const recent = docs.slice(0, 8);
    $('task-docs').innerHTML = recent.length
      ? '<table class="table"><thead><tr><th>文档</th><th>状态</th><th>分块</th><th>说明 / 时间</th></tr></thead><tbody>' +
        recent.map(d => {
          const [c, l] = DOC_BADGE[d.status] || ['badge-gray', d.status];
          return `<tr><td>${esc(d.filename)}</td><td><span class="badge ${c}">${l}</span></td><td>${d.chunk_count}</td><td class="small">${esc(d.error_msg || fmtTime(d.updated_at))}</td></tr>`;
        }).join('') + '</tbody></table>'
      : '<div class="empty-state"><div class="big">◫</div>暂无文档，去「文档管理」上传。</div>';
  } catch (e) { safeToast(e); }
}

/* ---------- 评测中心 ---------- */
async function loadEval() {
  try {
    const r = await api('/api/eval/objects');
    const isAdmin = S.user.role === 'admin';
    $('eval-banner').innerHTML = '<div class="banner banner-note">档位 offline_placeholder · 质量结论 <b>NOT_COMPARABLE</b> · comparable=false · 真实 API 字段仍 __TBC__</div>';
    $('eval-stats').innerHTML = [
      statCard('版本化对象', r.count, '注册表 ' + esc(r.registry_id) + '@' + esc(r.registry_version)),
      statCard('版本策略', '禁原地改', '已产生 run 的对象必须升版本'),
      statCard('质量基线', '未冻结', '占位结果不写基线', 'var(--warn)'),
      isAdmin ? null : statCard('任务完成度', '仅 admin', '采集仍可用'),
    ].filter(Boolean).join('');
    if (isAdmin) {
      const c = await api('/api/admin/completion');
      $('eval-stats').innerHTML += statCard('评测完成率', (c.overall.completion_rate * 100).toFixed(1) + '%',
        c.overall.completed_total + ' / ' + c.overall.target_total + ' 样本');
    }
    $('eval-objects').innerHTML = '<table class="table"><thead><tr><th>#</th><th>对象 ID</th><th>类型</th><th>版本</th><th>文件</th></tr></thead><tbody>' +
      r.objects.map((o, i) => `<tr><td>${i + 1}</td><td><b>${esc(o.object_id)}</b></td><td>${esc(o.object_type)}</td><td><span class="badge badge-gray">${esc(o.version)}</span></td><td class="small">${esc(o.file)}</td></tr>`).join('') +
      '</tbody></table>';
  } catch (e) { safeToast(e); }
}

/* ---------- 审计日志（仅 admin） ---------- */
const ACT_LABEL = {
  login: '登录', register: '注册', upload: '上传', delete: '删除',
  chat: '问答', search: '检索调试', badcase: 'badcase', export: '导出',
};
async function loadAudit() {
  if (!S.user || S.user.role !== 'admin') return;
  const p = new URLSearchParams();
  if ($('a-action').value) p.set('action', $('a-action').value);
  if ($('a-actor').value.trim()) p.set('actor', $('a-actor').value.trim());
  try {
    const rows = await api('/api/admin/audit?' + p.toString());
    $('audit-table').innerHTML = rows.length
      ? '<table class="table"><thead><tr><th>时间</th><th>操作人</th><th>角色</th><th>动作</th><th>对象</th><th>详情</th></tr></thead><tbody>' +
        rows.map(x => `<tr><td class="small">${esc(fmtTime(x.created_at))}</td><td>${esc(x.actor)}</td><td><span class="badge ${x.role === 'admin' ? 'badge-purple' : 'badge-gray'}">${esc(x.role)}</span></td><td><b>${esc(ACT_LABEL[x.action] || x.action)}</b></td><td class="small">${esc(x.target || '—')}</td><td class="small">${esc(x.detail || '—')}</td></tr>`).join('') +
        '</tbody></table>'
      : '<div class="empty-state"><div class="big">◫</div>当前筛选下没有审计记录。<br/><span class="small">登录、上传、问答、导出都会写入台账。</span></div>';
  } catch (e) { safeToast(e); }
}

/* ---------- 系统设置与边界 ---------- */
async function loadSystem() {
  const isAdmin = S.user.role === 'admin';
  $('sys-me').innerHTML = `
    <div class="kv-line"><span>账号</span><b>${esc(S.user.username)}</b></div>
    <div class="kv-line"><span>角色</span><b>${esc(S.user.role)}</b></div>
    <div class="kv-line"><span>${esc(t('可见入口'))}</span><b>${esc(t(isAdmin ? '全部 {n} 项' : '{n} 项（无管理后台 / 审计日志 / 模型设置）', { n: isAdmin ? VIEWS.length : 8 }))}</b></div>
    <div class="note-box">生产骨架把权限判定收敛到 decide() 一个纯函数；切片里是 admin 门禁 + owner 过滤。
      逐行 / 逐段落 ACL 与多租户行级权限<b>只预留架构，未实现</b>。</div>`;
  try {
    const b = await api('/api/system/busy');
    $('sys-live').innerHTML = `
      <div class="kv-line"><span>连接</span><b style="color:var(--success)">正常</b></div>
      <div class="kv-line"><span>分析队列</span><b>${b.busy ? '处理 ' + b.active_count + ' / 排队 ' + b.queue_count : '空闲'}</b></div>
      <div class="kv-line"><span>真值存储</span><b>SQLite（本地文件）</b></div>
      <div class="kv-line"><span>外部依赖</span><b>可离线；可选外部 DeepSeek / 本地 LM Studio</b></div>
      <div class="kv-line"><span>断线恢复</span><b>2.5s 探测 + 自动刷新</b></div>`;
  } catch (_) { $('sys-live').innerHTML = '<div class="note-box">后端不可达，顶部横幅正在自动重连。</div>'; }
  if (!isAdmin) {
    $('sys-users').innerHTML = '<div class="empty-state">成员列表仅管理员可见（接口同样 403）。</div>';
    return;
  }
  try {
    const rows = await api('/api/admin/users');
    $('sys-users').innerHTML = '<table class="table"><thead><tr><th>账号</th><th>角色</th><th>文档数</th><th>badcase 数</th><th>注册时间</th></tr></thead><tbody>' +
      rows.map(x => `<tr><td><b>${esc(x.username)}</b><div class="small muted">${esc(x.email || '—')}</div></td><td><span class="badge ${x.role === 'admin' ? 'badge-purple' : 'badge-gray'}">${esc(x.role)}</span></td><td>${x.doc_count}</td><td>${x.badcase_count}</td><td class="small">${esc(fmtTime(x.created_at))}</td></tr>`).join('') +
      '</tbody></table>';
  } catch (e) { safeToast(e); }
}

/* ---------- 模型设置（仅 admin） ---------- */
async function loadSettings() {
  if (!S.user || S.user.role !== 'admin') return;
  try {
    const r = await api('/api/settings');
    S.settings = r;
    const c = r.config;
    $('st-llm-provider').value = c.llm_provider;
    $('st-llm-thinking').value = c.llm_thinking || 'disabled';
    $('st-local-llm-base').value = c.local_llm_base_url;
    $('st-local-emb-base').value = c.local_embedding_base_url;
    $('st-external-emb-base').value = c.external_embedding_base_url;
    $('st-external-emb-model').value = c.external_embedding_model;
    $('st-external-rerank-base').value = c.external_rerank_base_url;
    $('st-external-rerank-model').value = c.external_rerank_model;
    $('st-sensenova-model').textContent = c.sensenova_model;
    $('st-emb-provider').value = c.embedding_provider;
    $('st-rerank-provider').value = c.rerank_provider;
    fillModelSelect('st-local-llm-model', r.local.models, c.local_llm_model);
    fillModelSelect('st-local-emb-model', r.local.models, c.local_embedding_model);
    fillModelSelect('st-discovery-model', (r.discovery || {}).models, c.discovery_model);
    onLlmProviderChange(); onEmbProviderChange(); onRerankProviderChange();
    const okColor = r.local.reachable ? 'var(--success)' : 'var(--danger)';
    const extEmb = r.external_embedding || {};
    const extColor = extEmb.configured ? 'var(--success)' : 'var(--danger)';
    $('st-effective').innerHTML =
      `LLM：<b>${esc(r.effective.llm)}</b>（${esc(r.effective.llm_provider)}） · ` +
      `推理：<b>${esc(r.effective.thinking || '—')}</b> · ` +
      `向量：<b>${esc(r.effective.embedding)}</b> · 重排：<b>${esc(r.effective.rerank)}</b><br/>` +
      `硅基流动密钥：<b style="color:${extColor}">${extEmb.configured ? '已配置（嵌入 + 重排共用）' : '未配置'}</b>` +
      ` · ${esc(t('本地 LM Studio'))}：<b style="color:${okColor}">${esc(t(r.local.reachable ? '可达' : '不可达'))}</b>` +
      esc(t('（{n} 个模型）', { n: r.local.models.length }));
    $('st-llm-result').textContent = ''; $('st-emb-result').textContent = ''; $('st-rerank-result').textContent = '';
  } catch (e) { safeToast(e); }
}
function fillModelSelect(id, models, current) {
  const sel = $(id); if (!sel) return;
  const list = Array.from(new Set([...(models || []), ...(current ? [current] : [])]));
  sel.innerHTML = list.length
    ? list.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('')
    : `<option value="">（未检测到本地模型）</option>`;
  if (current) sel.value = current;
}
function onLlmProviderChange() {
  const v = $('st-llm-provider').value;
  $('st-local-llm-box').classList.toggle('hidden', v !== 'local');
  $('st-external-box').classList.toggle('hidden', v !== 'sensenova');
  $('st-discovery-box').classList.toggle('hidden', v !== 'discovery');
}
function onEmbProviderChange() {
  const v = $('st-emb-provider').value;
  $('st-local-emb-box').classList.toggle('hidden', v !== 'local');
  $('st-external-emb-box').classList.toggle('hidden', v !== 'external');
}
function onRerankProviderChange() {
  const v = $('st-rerank-provider').value;
  $('st-external-rerank-box').classList.toggle('hidden', v !== 'external');
  $('st-llm-rerank-box').classList.toggle('hidden', v !== 'llm');
}
async function testProvider(target) {
  const map = { llm: 'st-llm-result', embedding: 'st-emb-result', rerank: 'st-rerank-result' };
  const el = $(map[target]); if (el) el.textContent = '测试中…';
  try {
    const r = await api('/api/settings/test', { method: 'POST', body: JSON.stringify({ target }) });
    if (el) el.innerHTML = `<span style="color:var(${r.ok ? '--success' : '--danger'})">${r.ok ? '✓ ' : '✗ '}${esc(r.message)}</span>`;
  } catch (e) { if (el) el.textContent = '测试失败：' + e.message; }
}
async function saveSettings() {
  const patch = {
    llm_provider: $('st-llm-provider').value,
    llm_thinking: $('st-llm-thinking').value,
    local_llm_base_url: $('st-local-llm-base').value.trim(),
    local_llm_model: $('st-local-llm-model').value.trim(),
    discovery_model: $('st-discovery-model').value.trim(),
    embedding_provider: $('st-emb-provider').value,
    local_embedding_base_url: $('st-local-emb-base').value.trim(),
    local_embedding_model: $('st-local-emb-model').value.trim(),
    external_embedding_base_url: $('st-external-emb-base').value.trim(),
    external_embedding_model: $('st-external-emb-model').value.trim(),
    rerank_provider: $('st-rerank-provider').value,
    external_rerank_base_url: $('st-external-rerank-base').value.trim(),
    external_rerank_model: $('st-external-rerank-model').value.trim(),
  };
  try {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify({ patch }) });
    toast(t('设置已保存，已即时生效'), 'ok');
    await loadSettings();
  } catch (e) { safeToast(e); }
}

/* ================= 评估测试子页（可观测） ================= */
const ET_TABS = ['config', 'runs', 'metrics', 'logs', 'cost', 'gov'];
function switchEvalTab(tab) {
  if (!ET_TABS.includes(tab)) return;
  S.evalTab = tab;
  document.querySelectorAll('#et-tabs .tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  ET_TABS.forEach(x => $('et-' + x).classList.toggle('active', x === tab));
  loadEvalTest();
  if (window.applyI18n) applyI18n($('et-' + tab));
}
function loadEvalTest() {
  const map = {
    config: loadEtConfig, runs: loadEtRuns, metrics: loadEtMetrics,
    logs: loadEtLogs, cost: loadEtCost, gov: loadEtGov,
  };
  const fn = map[S.evalTab];
  if (fn) fn();
  const isAdmin = S.user && S.user.role === 'admin';
  ['et-save', 'et-run', 'et-budget-save'].forEach(id => {
    if ($(id)) $(id).classList.toggle('hidden', !isAdmin);
  });
  $('et-banner').innerHTML = `<div class="banner banner-note">${esc(t(isAdmin
    ? '管理员：可修改评估配置、预算与限流规则，并发起离线评估运行。'
    : '只读视图：用量与日志按本人范围过滤；配置与预算仅管理员可改。'))}</div>`;
}
function etTh(key) { return `<th data-i18n="${esc(key)}">${esc(t(key))}</th>`; }
function etEmpty(key) { return `<div class="empty-state"><div class="big">◫</div><span data-i18n="${esc(key)}">${esc(t(key))}</span></div>`; }

async function loadEtConfig() {
  try {
    const r = await api('/api/evaltest/config');
    const c = r.config || {}, o = r.options || {};
    const fill = (id, list, cur) => {
      const el = $(id); if (!el) return;
      el.innerHTML = list.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
      if (cur && list.includes(cur)) el.value = cur;
    };
    fill('et-prompt', o.prompts || [], c.prompt_version);
    fill('et-params', o.params || [], c.params_id);
    fill('et-samples', o.samples || ['all'], c.samples);
    if ($('et-profile')) $('et-profile').value = c.profile || 'offline_placeholder';
    const rows = [
      [t('评估档位'), esc(c.profile || '—')], [t('提示词版本'), esc(c.prompt_version || '—')],
      [t('参数组'), esc(c.params_id || '—')], [t('样本范围'), esc(c.samples || '—')],
      [t('项目'), esc(c.project || 'default')], [t('部门'), esc(c.department || 'default')],
      [t('统计范围'), esc(t(r.is_admin ? '全局（admin）' : '仅本人数据'))],
    ];
    $('et-config-view').innerHTML = rows.map(([k, v]) => `<div class="kv-line"><span>${k}</span><b>${v}</b></div>`).join('');
    $('et-config-msg').textContent = t('只读事实源：evalbench/ 资产 + eval_configs 表');
  } catch (e) { safeToast(e); }
}
async function saveEvalConfig() {
  const patch = {
    profile: $('et-profile').value, prompt_version: $('et-prompt').value,
    params_id: $('et-params').value, samples: $('et-samples').value,
  };
  try {
    await api('/api/evaltest/config', { method: 'PUT', body: JSON.stringify({ patch }) });
    $('et-config-msg').textContent = t('配置项已保存');
    toast(t('配置项已保存'), 'ok');
    await loadEtConfig();
  } catch (e) { $('et-config-msg').textContent = t('配置保存失败：') + e.message; }
}
async function runEvalOnce() {
  try {
    const r = await api('/api/evaltest/run', { method: 'POST' });
    if (!r.ok) { toast(t('预算超限：新的付费调用已被拒绝'), 'err'); return; }
    toast(t('评估已完成（离线，comparable=false）') + ' · ' + r.run_id, 'ok');
    $('et-config-msg').textContent = `${r.run_id} · ${esc(r.branch)}/${esc(r.commit)} · ${r.n_records} records`;
    await Promise.all([loadEtRuns(), loadEtCost(), loadEtLogs()]);
  } catch (e) { toast(t('评估运行失败：') + e.message, 'err'); }
}

async function loadEtRuns() {
  try {
    const r = await api('/api/evaltest/runs?limit=60');
    const runs = r.runs || [];
    $('et-runs-table').innerHTML = runs.length
      ? '<table class="table"><thead><tr>' +
        [etTh('运行 ID'), etTh('时间'), etTh('模型'), etTh('样本'), etTh('状态'), etTh('操作')].join('') +
        '</tr></thead><tbody>' + runs.map(x => {
          const k = storeModalData(x);
          const sc = x.status_counts || {};
          const ok = sc.ok != null ? sc.ok : x.n_records;
          return `<tr><td class="small mono">${esc(x.run_id)}</td>
            <td class="small">${esc(fmtTime(x.created_at))}</td>
            <td><span class="badge ${x.comparable ? 'badge-green' : 'badge-gray'}">${esc(x.mode)}</span></td>
            <td class="small">${esc(x.n_records)} · ${x.comparable ? 'comparable' : 'NOT_COMPARABLE'}</td>
            <td class="small">${esc(ok)} ok · ${esc(x.git_branch || '—')}/${esc(x.git_commit || '—')}</td>
            <td><button class="btn btn-sm" data-i18n="查看明细" onclick="openRunDetail('${k}')">${esc(t('查看明细'))}</button></td></tr>`;
        }).join('') + '</tbody></table>'
      : etEmpty('暂无运行记录');
    // 同步指标 / 日志的 run 下拉
    ['et-metric-run', 'et-log-run'].forEach(id => {
      const el = $(id); if (!el) return;
      const cur = el.value;
      el.innerHTML = '<option value="">' + esc(t('全部')) + '</option>' + runs.map(x =>
        `<option value="${esc(x.run_id)}">${esc(x.run_id)}</option>`).join('');
      if (cur) el.value = cur;
    });
  } catch (e) { safeToast(e); }
}
async function openRunDetail(key) {
  const x = takeModalData(key);
  if (!x) return;
  try {
    const d = await api('/api/evaltest/runs/' + encodeURIComponent(x.run_id));
    const s = d.summary || {};
    openModal(`<h3>${esc(t('运行记录'))}</h3><div class="sub mono">${esc(d.run_id)}</div>
      <div class="stack">
        <div class="kv-line"><span>${esc(t('时间'))}</span><b>${esc(fmtTime(x.created_at))}</b></div>
        <div class="kv-line"><span>${esc(t('状态'))}</span><b>${esc(x.mode)} · ${x.comparable ? 'comparable' : 'NOT_COMPARABLE'}</b></div>
        <div class="kv-line"><span>${esc(t('样本'))}</span><b>${esc(d.n_records)}</b></div>
        <div class="kv-line"><span>Git</span><b class="small mono">${esc(x.git_branch || '—')} / ${esc(x.git_commit || '—')}</b></div>
        <div class="kv-line"><span>${esc(t('主指标'))}</span><b class="small">${esc(JSON.stringify(s.status_counts || {}))}</b></div>
        <div class="quoted">${esc(JSON.stringify(d.config_snapshot || {}, null, 2).slice(0, 900))}</div>
      </div>
      <div class="actions"><button class="btn btn-primary" data-i18n="关闭" onclick="closeModal()">${esc(t('关闭'))}</button></div>`);
  } catch (e) { toast(e.message, 'err'); }
}

async function loadEtMetrics() {
  try {
    const rid = $('et-metric-run') ? $('et-metric-run').value : '';
    const model = $('et-metric-model') ? $('et-metric-model').value : '';
    const r = await api('/api/evaltest/metrics?run_id=' + encodeURIComponent(rid) +
      '&model=' + encodeURIComponent(model));
    const groups = r.groups || [];
    const models = Array.from(new Set(groups.map(g => g.model)));
    const sel = $('et-metric-model');
    if (sel) {
      const cur = sel.value;
      sel.innerHTML = '<option value="">' + esc(t('全部')) + '</option>' +
        models.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
      if (cur && models.includes(cur)) sel.value = cur;
    }
    $('et-metrics-table').innerHTML = groups.length
      ? '<table class="table"><thead><tr>' +
        [etTh('模型'), etTh('任务类别'), etTh('维度'), etTh('均值'), etTh('记录数')].join('') +
        '</tr></thead><tbody>' + groups.map(g =>
          `<tr><td class="small">${esc(g.model)}</td><td>${esc(g.task_category)}</td>
           <td>${esc(g.dimension)}</td><td><b>${Number(g.mean).toFixed(4)}</b>
           <div class="progress-mini"><i style="width:${Math.min(100, g.mean * 100)}%"></i></div></td>
           <td class="small">${esc(g.n)}</td></tr>`).join('') + '</tbody></table>'
      : etEmpty('暂无指标数据');
  } catch (e) { safeToast(e); }
}

async function loadEtLogs() {
  try {
    const rid = $('et-log-run') ? $('et-log-run').value : '';
    const lv = $('et-log-level') ? $('et-log-level').value : '';
    const r = await api('/api/evaltest/logs?run_id=' + encodeURIComponent(rid) +
      '&level=' + encodeURIComponent(lv) + '&limit=200');
    const logs = r.logs || [];
    $('et-logs-table').innerHTML = logs.length
      ? '<table class="table"><thead><tr>' +
        [etTh('时间'), etTh('级别'), etTh('事件'), etTh('详情')].join('') +
        '</tr></thead><tbody>' + logs.map(x =>
          `<tr><td class="log-time">${esc(fmtTime(x.ts))}</td>
           <td><span class="badge ${x.level === 'ERROR' ? 'badge-red' : (x.level === 'WARN' ? 'badge-amber' : 'badge-blue')}">${esc(x.level)}</span></td>
           <td class="small mono">${esc(x.event)}</td>
           <td class="small">${esc(x.detail || '—')}</td></tr>`).join('') + '</tbody></table>'
      : etEmpty('暂无日志');
  } catch (e) { safeToast(e); }
}

async function loadEtCost() {
  try {
    const dim = $('et-cost-dim') ? $('et-cost-dim').value : 'project';
    const [u, b] = await Promise.all([
      api('/api/evaltest/usage?dim=' + encodeURIComponent(dim)),
      api('/api/evaltest/budget'),
    ]);
    const rows = u.rows || [], t0 = u.totals || {};
    $('et-cost-summary').innerHTML =
      `<div class="kv-line"><span>${esc(t('调用次数'))}</span><b>${esc(t0.calls || 0)}</b></div>
       <div class="kv-line"><span>${esc(t('输入 token'))} / ${esc(t('输出 token'))}</span><b>${esc(t0.prompt_tokens || 0)} / ${esc(t0.completion_tokens || 0)}</b></div>
       <div class="kv-line"><span>${esc(t('总 token'))}</span><b>${esc(t0.total_tokens || 0)}</b></div>
       <div class="kv-line"><span>${esc(t('统计范围'))}</span><b>${esc(t(u.scope || '仅本人调用'))}</b></div>`;
    const max = Math.max(1, ...rows.map(x => x.cost_cny || 0));
    $('et-cost-rows').innerHTML = rows.length
      ? '<div class="cost-bar-row cost-head"><span>' + esc(t('按维度聚合')) + '</span><span>' +
        esc(t('成本（估算）')) + '</span><span>' + esc(t('调用次数')) + '</span><span>' +
        esc(t('总 token')) + '</span><span>' + esc(t('输入 token')) + '</span></div>' +
        rows.map(x => `<div class="cost-bar-row">
          <span class="truncate" title="${esc(x.key)}">${esc(x.key)}</span>
          <span><div class="cost-bar"><i style="width:${Math.round((x.cost_cny / max) * 100)}%"></i></div></span>
          <span class="mono">¥${Number(x.cost_cny).toFixed(4)}</span>
          <span>${esc(x.calls)}</span><span>${esc(x.total_tokens)}</span></div>`).join('')
      : etEmpty('暂无用量数据');
    $('et-budget-spend').textContent = '¥' + Number(b.spend_cny || 0).toFixed(4);
    $('et-budget-limit').textContent = Number(b.limit_cny || 0).toFixed(2);
    $('et-budget-rate').textContent = (Number(b.usage_rate || 0) * 100).toFixed(1) + '%';
    $('et-budget-left').textContent = '¥' + Number(b.remaining_cny || 0).toFixed(4);
    const track = $('et-budget-track');
    track.className = 'budget-track' + (b.exceeded ? ' over' : (b.usage_rate > 0.8 ? ' warn' : ''));
    track.querySelector('i').style.width = Math.min(100, Number(b.usage_rate || 0) * 100) + '%';
    if ($('et-budget-input') && document.activeElement !== $('et-budget-input')) {
      $('et-budget-input').value = b.limit_cny;
    }
    if ($('et-budget-policy')) $('et-budget-policy').value = b.policy || 'warn';
    $('et-budget-msg').textContent = b.exceeded
      ? t('预算超限：新的付费调用已被拒绝') + ' · policy=' + b.policy
      : t('超限处理') + ': ' + t('告警并拒绝新的付费调用（留痕）') + ' · policy=' + b.policy;
  } catch (e) { safeToast(e); }
}
async function saveBudget() {
  const limit = Number($('et-budget-input').value);
  if (!isFinite(limit) || limit < 0) { toast(t('预算保存失败：') + 'invalid limit', 'err'); return; }
  const policy = $('et-budget-policy') ? $('et-budget-policy').value : 'warn';
  try {
    await api('/api/evaltest/budget', {
      method: 'PUT', body: JSON.stringify({ limit_cny: limit, policy }),
    });
    toast(t('预算已更新'), 'ok');
    await loadEtCost();
  } catch (e) { toast(t('预算保存失败：') + e.message, 'err'); }
}

async function loadEtGov() {
  try {
    const [r, p] = await Promise.all([
      api('/api/evaltest/ratelimit'), api('/api/evaltest/plugins'),
    ]);
    const rules = r.rules || [], hits = r.hits || [];
    const dimKey = { user: '用户', endpoint: '接口', model: '模型调用',
                     department_id: '部门', project: '项目', call_type: '调用类型' };
    const hitCount = {};
    hits.forEach(h => { hitCount[h.dimension] = (hitCount[h.dimension] || 0) + 1; });
    $('et-rate-table').innerHTML = '<table class="table"><thead><tr>' +
      [etTh('配额维度'), etTh('限额'), etTh('窗口'), etTh('命中次数')].join('') +
      '</tr></thead><tbody>' + rules.map(x =>
        `<tr><td>${esc(t(dimKey[x.dimension] || x.dimension))}</td>
         <td class="mono">${esc(x.limit_count)}</td><td>${esc(x.window_s)} s</td>
         <td><span class="badge ${hitCount[x.dimension] ? 'badge-amber' : 'badge-green'}">${esc(hitCount[x.dimension] || 0)}</span></td></tr>`).join('') +
      '</tbody></table>';
    const stBadge = { '已落地': 'badge-green', '已落地（演示级）': 'badge-green',
      '演示级': 'badge-blue', '架构预留': 'badge-amber', '未做': 'badge-gray' };
    $('et-plugins').innerHTML = (p.plugins || []).map(x =>
      `<div class="plugin-card"><h5>${esc(x.name)}<span class="badge ${stBadge[x.status] || 'badge-gray'}">${esc(t(x.status))}</span></h5>
       <p><b>${esc(t('接入模块'))}：</b><span class="mono">${esc(x.module)}</span></p>
       <p style="margin-top:6px"><b>${esc(t('边界'))}：</b>${esc(x.boundary)}</p>
       <p style="margin-top:6px"><b>${esc(t('面试考点'))}：</b>${esc(x.why)}</p></div>`).join('');
  } catch (e) { safeToast(e); }
}

/* ================= 解析实验室（可插拔三级策略） ================= */
let PS_STATUS = null;

async function loadParsing() {
  try {
    const r = await api('/api/parse/status');
    PS_STATUS = r;
    const sel = $('ps-strategy');
    if (sel) {
      sel.innerHTML = (r.strategies || []).map(s =>
        `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('');
      sel.value = r.strategy;
    }
    const th = r.thresholds || {}, bud = r.budget || {};
    $('ps-budget').innerHTML =
      `<div class="kv-line"><span>最贵链路占比上限</span><b>${esc(String((bud.vision_escalation_ratio ?? 0.1) * 100) + '%')}</b></div>` +
      `<div class="kv-line"><span>单次任务最小名额</span><b>${esc(String(bud.vision_min_slots ?? 1))} 页</b></div>` +
      `<div class="kv-line"><span>质量门禁置信度阈值</span><b>${esc(String(th.confidence_min ?? '—'))}</b></div>` +
      `<div class="kv-line"><span>OCR 字符阈值</span><b>${esc(String(th.ocr_text_chars_min ?? '—'))}</b></div>` +
      `<p class="muted small" style="margin:10px 0 0">${esc(bud.meaning || '')}<br/>${esc(th.threshold_note || '')}</p>`;
    const vp = r.vision_models_probe || {};
    $('ps-vision').innerHTML =
      `<div class="kv-line"><span>探测端点</span><b>${esc(vp.gateway || '—')}</b></div>` +
      `<div class="kv-line"><span>可用模型数</span><b>${esc(String((vp.models || []).length))}</b></div>` +
      `<div class="kv-line"><span>视觉模型</span><b style="color:var(${vp.has_vision ? '--success' : '--danger'})">${vp.has_vision ? ((vp.vision_capable || []).length + ' 个支持 image') : '无'}</b></div>` +
      `<p class="muted small" style="margin:10px 0 0">${esc(vp.note || '')}</p>`;
    $('ps-levels').innerHTML = (r.levels || []).map(x =>
      `<div class="card padded" style="box-shadow:none">` +
      `<div class="kv-line"><span>${esc(x.name)}</span><b style="color:var(${x.available ? '--success' : '--danger'})">${x.available ? '可用' : '未接通'}</b></div>` +
      `<div class="muted small" style="margin-top:6px">${esc(x.source || x.reason || '')}</div>` +
      `<div class="muted small" style="margin-top:6px">成本：${esc(x.cost || '__TBC__')}</div></div>`).join('');
  } catch (e) { safeToast(e); }
}

async function saveParsingStrategy() {
  const v = $('ps-strategy').value;
  try {
    const r = await api('/api/parse/strategy', { method: 'PUT', body: JSON.stringify({ strategy: v }) });
    $('ps-strategy-msg').textContent = '当前策略：' + r.strategy + '（' + r.effective + '）';
    toast(t('设置已保存，已即时生效'), 'ok');
    await loadParsing();
  } catch (e) { safeToast(e); }
}

async function runParsingProbe() {
  const f = $('ps-file').files[0];
  if (!f) { toast('请先选择文件', 'warn'); return; }
  const msg = $('ps-probe-msg');
  msg.textContent = '解析中…（本演示不会发起任何外部 OCR / 视觉请求）';
  try {
    const fd = new FormData(); fd.append('file', f);
    const r = await api('/api/parse/probe', { method: 'POST', body: fd });
    msg.textContent = `完成：${r.page_count} 页，策略 ${r.strategy}，总耗时 ${r.summary.total_latency_ms} ms`;
    renderParsingReport(r);
    await loadParsing();
  } catch (e) { msg.textContent = '解析失败：' + e.message; safeToast(e); }
}

function renderParsingReport(rep) {
  const s = rep.summary || {};
  const byLevel = Object.entries(s.by_level || {}).map(([k, v]) => `${k}: ${v}`).join(' · ');
  $('ps-summary').innerHTML =
    `<div class="kv-line"><span>文件 / 页数</span><b>${esc(rep.filename)} · ${rep.page_count} 页</b></div>` +
    `<div class="kv-line"><span>级别分布</span><b>${esc(byLevel || '—')}</b></div>` +
    `<div class="kv-line"><span>升级 / 预算拦截 / 降级</span><b>${s.upgrades || 0} / ${s.budget_blocked || 0} / ${s.degraded || 0}</b></div>` +
    `<div class="kv-line"><span>视觉预算用量</span><b>${rep.budget.used} / ${rep.budget.slots} 页</b></div>` +
    `<p class="muted small" style="margin:8px 0 0">${esc(rep.note || '')}</p>`;
  const rows = (rep.traces || []).map(t => {
    const notes = (t.notes || []).slice(0, 3).join('；');
    return `<tr><td>${t.page_no}</td><td><span class="badge ${t.level === 'local' ? 'badge-green' : t.level === 'multimodal' ? 'badge-purple' : 'badge-blue'}">${esc(t.level)}</span></td>` +
      `<td>${esc(String(t.confidence))}</td><td>${t.latency_ms} ms</td>` +
      `<td>${esc(t.upgraded_from || '—')}</td><td>${t.budget_blocked ? '是' : '—'}</td>` +
      `<td class="small">${esc(t.reason || '—')}</td><td class="small">${esc(t.degraded || '—')}</td>` +
      `<td class="small">${esc(notes || '—')}</td></tr>`;
  }).join('');
  $('ps-traces').innerHTML =
    `<table class="table"><thead><tr><th>页</th><th>级别</th><th>置信度</th><th>耗时</th><th>升级自</th><th>预算拦截</th><th>原因</th><th>降级</th><th>判定依据</th></tr></thead><tbody>${rows}</tbody></table>`;
  const first = (rep.results || [])[0];
  if (!first) { $('ps-preview').textContent = '无结果'; return; }
  const sig = first.signals || {};
  $('ps-preview').innerHTML =
    `<div class="kv-line"><span>结构化块数 / 表格单元格</span><b>${(first.blocks || []).length} / ${sig.table_cells || 0}</b></div>` +
    `<div class="kv-line"><span>字符数 / 密度 / 置信度</span><b>${sig.text_chars || 0} / ${sig.text_density || 0} / ${sig.confidence || 0}</b></div>` +
    `<pre class="small" style="white-space:pre-wrap;margin:10px 0 0;max-height:220px;overflow:auto">${esc((first.text || '').slice(0, 1200))}</pre>`;
}

/* ================= 知识治理（新鲜度 + 事实冲突） ================= */
let KN_DATA = null;

async function loadKnowledge() {
  try {
    const [st, sc] = await Promise.all([
      api('/api/knowledge/status'), api('/api/knowledge/scenario'),
    ]);
    KN_DATA = sc;
    const p = st.policy;
    $('kn-enabled').value = p.enabled ? '1' : '0';
    $('kn-half').value = p.half_life_days;
    $('kn-expired').value = p.expired_action;
    $('kn-blend').value = p.blend;
    $('kn-policy-msg').textContent = '当前策略：' + (p.enabled ? '启用' : '关闭') +
      ' · 阈值说明：' + p.note;

    const s = sc.freshness.summary;
    $('kn-summary').innerHTML =
      `<div class="kv-line"><span>候选文档 / stale / 已过期</span><b>${s.count} / ${s.stale} / ${s.expired}</b></div>` +
      `<div class="kv-line"><span>未生效 / 被排除</span><b>${s.not_yet_effective} / ${s.excluded}</b></div>` +
      `<div class="kv-line"><span>stale 占比 / 告警线</span><b style="color:var(${s.alert ? '--danger' : '--success'})">${(s.stale_ratio * 100).toFixed(1)}% / ${(s.alert_threshold * 100).toFixed(0)}%</b></div>` +
      `<div class="kv-line"><span>年龄分布</span><b>${Object.entries(s.buckets).map(([k, v]) => k + ':' + v).join(' · ')}</b></div>` +
      `<p class="muted small" style="margin:8px 0 0">${esc(s.note)}</p>`;

    const ow = sc.freshness.order_with || [], owoff = sc.freshness.order_without || [];
    $('kn-order').innerHTML =
      `<div class="kv-line"><span>开启新鲜度</span><b>${esc(ow.join(' → ') || '—')}</b></div>` +
      `<div class="kv-line"><span>关闭（仅关衰减）</span><b>${esc(owoff.join(' → ') || '—')}</b></div>` +
      `<p class="muted small" style="margin:8px 0 0">${esc(sc.freshness.note)}</p>`;

    const rows = (sc.freshness.results || []).map(r =>
      `<tr><td><b>${esc(r.doc_id)}</b><br/><span class="small muted">${esc(r.title)}</span></td>` +
      `<td>${esc(String(r.age_days))}d</td><td>${esc(String(r.decay))}</td>` +
      `<td>${esc(String(r.base_score))}</td><td><b>${esc(String(r.effective_score))}</b></td>` +
      `<td>${esc(r.action)}${r.excluded ? ' · 排除' : ''}${r.is_expired ? ' · 已过期' : ''}${r.not_yet_effective ? ' · 未生效' : ''}</td>` +
      `<td class="small">${esc(r.reason)}</td></tr>`).join('');
    $('kn-freshness').innerHTML =
      `<table class="table"><thead><tr><th>文档</th><th>年龄</th><th>衰减</th><th>原分</th><th>有效分</th><th>处置</th><th>判定依据</th></tr></thead><tbody>${rows}</tbody></table>`;

    const ev = sc.evidence;
    $('kn-warnings').innerHTML = (ev.warnings || []).length
      ? ev.warnings.map(w => `<div class="banner banner-warn" style="margin-bottom:6px">${esc(w)}</div>`).join('')
      : '<div class="muted small">未检测到冲突</div>';
    const cRows = (ev.conflicts || []).map(c =>
      `<tr><td class="small">${esc(c.key)}</td><td><b>${esc(c.values.join(' vs '))}</b></td>` +
      `<td>${esc(c.kind)}</td><td>${esc(c.resolution)}</td>` +
      `<td>${c.needs_review ? '<span class="badge badge-amber">需复核</span>' : '<span class="badge badge-green">可按规则</span>'}</td>` +
      `<td>${esc(c.winner_id || '并列转人工')}</td><td class="small">${esc(c.reason)}</td></tr>`).join('');
    $('kn-conflicts').innerHTML = cRows
      ? `<table class="table"><thead><tr><th>冲突键</th><th>互斥取值</th><th>类型</th><th>裁决</th><th>复核</th><th>胜出</th><th>理由</th></tr></thead><tbody>${cRows}</tbody></table>`
      : '<div class="muted small">无冲突</div>';
    $('kn-evidence').innerHTML =
      `<div class="kv-line"><span>claim 数 / 冲突数 / conflict_signal</span><b>${ev.claims.length} / ${ev.conflicts.length} / ${ev.conflict_signal}</b></div>` +
      `<div class="kv-line"><span>选中 / 排除 claim</span><b>${ev.selected_claim_ids.length} / ${ev.excluded_claim_ids.length}</b></div>` +
      `<p class="muted small" style="margin:8px 0 0">${esc(ev.note)}</p>` +
      `<pre class="small" style="white-space:pre-wrap;max-height:180px;overflow:auto;margin:10px 0 0">${esc(JSON.stringify(ev.resolution_log, null, 2).slice(0, 1800))}</pre>`;

    $('kn-controls').innerHTML = '<ul class="small" style="padding-left:18px;margin:0">' +
      (sc.controls.negative_controls || []).map(x => `<li>${esc(x)}</li>`).join('') +
      `</ul><p class="muted small" style="margin:8px 0 0">${esc(sc.controls.note)}</p>`;
    $('kn-docs').innerHTML = (sc.documents || []).map(d =>
      `<div style="margin-bottom:8px"><b>${esc(d.doc_id)} · ${esc(d.title)}</b>` +
      `<span class="small muted">（revision=${esc(d.revision)}, authority=${esc(String(d.authority))}, scope=${esc(JSON.stringify(d.scope))}）</span>` +
      `<pre class="small" style="white-space:pre-wrap;margin:4px 0 0">${esc(d.text)}</pre></div>`).join('');
  } catch (e) { safeToast(e); }
}

async function saveKnowledgePolicy() {
  const patch = {
    enabled: $('kn-enabled').value === '1',
    half_life_days: Number($('kn-half').value),
    expired_action: $('kn-expired').value,
    blend: Number($('kn-blend').value),
  };
  try {
    await api('/api/knowledge/policy', { method: 'PUT', body: JSON.stringify(patch) });
    toast(t('设置已保存，已即时生效'), 'ok');
    await loadKnowledge();
  } catch (e) { safeToast(e); }
}

/* ================= 缓存与恢复（运行时状态） ================= */
let RT_DATA = null;

async function loadRuntime() {
  try {
    const r = await api('/api/runtime/status');
    RT_DATA = r;
    const p = r.cache_policy, st = r.stats, v = r.versions;
    $('rt-l1').value = p.l1_capacity; $('rt-l2').value = p.l2_capacity;
    $('rt-ttl').value = p.ttl_seconds;
    $('rt-policy').innerHTML =
      `<div class="kv-line"><span>L1 / L2 容量</span><b>${p.l1_capacity} / ${p.l2_capacity}</b></div>` +
      `<div class="kv-line"><span>正常 TTL / 负缓存 TTL</span><b>${p.ttl_seconds}s / ${p.negative_ttl_seconds}s</b></div>` +
      `<div class="kv-line"><span>命中 / 未命中 / 命中率</span><b>${st.hits} / ${st.misses} / ${(st.hit_rate * 100).toFixed(1)}%</b></div>` +
      `<div class="kv-line"><span>L1 / L2 命中</span><b>${st.l1_hits} / ${st.l2_hits}</b></div>` +
      `<div class="kv-line"><span>写入 / 淘汰 / 过期清理</span><b>${st.writes} / ${st.evictions} / ${st.expired_purged}</b></div>` +
      `<div class="kv-line"><span>版本 data / grant / policy</span><b>${esc(v.data_version)} / ${esc(v.grant_version)} / ${esc(v.policy_version)}</b></div>` +
      `<div class="kv-line"><span>L2 持久化条目</span><b>${r.l2_entries}</b></div>` +
      `<p class="muted small" style="margin:8px 0 0">${esc(p.note)}</p>` +
      `<p class="muted small" style="margin:6px 0 0">失效原因分布：${esc(JSON.stringify(st.invalidations))}</p>`;

    const ke = r.key_example;
    $('rt-key').innerHTML =
      `<div class="kv-line"><span>key</span><b class="small">${esc(ke.key)}</b></div>` +
      `<table class="table" style="margin-top:8px"><thead><tr><th>段</th><th>取值</th></tr></thead><tbody>` +
      Object.entries(ke.parts).map(([k, val]) => `<tr><td class="small">${esc(k)}</td><td class="small">${esc(String(val))}</td></tr>`).join('') +
      `</tbody></table><p class="muted small" style="margin:8px 0 0">${esc(ke.note)}</p>`;

    const rep = r.recovery_report;
    if (rep) {
      const CAT = { discardable: '可安全丢弃', rebuildable: '可从真值重建', must_persist: '必须持久化' };
      $('rt-recovery').innerHTML =
        `<div class="kv-line"><span>启动健康 / 启动次数</span><b style="color:var(${rep.healthy ? '--success' : '--danger'})">${rep.healthy ? '正常' : '异常'} · ${rep.boot_count}</b></div>` +
        `<div class="kv-line"><span>孤儿任务重投 / 幽灵向量清理</span><b>${rep.requeued} / ${rep.ghost_vectors}</b></div>` +
        `<div class="kv-line"><span>过期缓存清理 / 容量淘汰</span><b>${rep.expired_cache_purged} / ${rep.capacity_evicted}</b></div>` +
        `<div class="kv-line"><span>上次优雅关闭</span><b>${rep.graceful_last_shutdown ? '是' : '否（强停不写标记，真值层仍完好）'}</b></div>` +
        `<div class="kv-line"><span>恢复的策略</span><b>${esc((rep.restored_policies || []).join(', ') || '无（使用默认值）')}</b></div>` +
        `<table class="table" style="margin-top:8px"><thead><tr><th>检查项</th><th>分类</th><th>结论</th></tr></thead><tbody>` +
        (rep.checks || []).map(c => `<tr><td class="small">${esc(c.name)}</td><td class="small">${esc(CAT[c.category] || c.category)}</td><td class="small">${c.passed ? '✓' : '✗'} ${esc(c.detail)}</td></tr>`).join('') +
        `</tbody></table>`;
    } else {
      $('rt-recovery').textContent = '尚未启动自检';
    }
  } catch (e) { safeToast(e); }
}

async function runCacheDemo() {
  const q = $('rt-query').value.trim() || '配网';
  try {
    const d = await api('/api/runtime/cache-demo?question=' + encodeURIComponent(q));
    $('rt-demo').innerHTML =
      `<div class="kv-line"><span>首次查询</span><b>${esc(d.first_lookup)}</b></div>` +
      `<div class="kv-line"><span>先过滤后写</span><b>${d.permission_filter.before} → ${d.permission_filter.after} 条可见</b></div>` +
      `<div class="kv-line"><span>清 L1 后（模拟重启）</span><b style="color:var(${d.after_l1_cleared.hit ? '--success' : '--danger'})">${d.after_l1_cleared.hit ? 'L2 命中，缓存可恢复' : '未命中'}</b></div>` +
      `<div class="kv-line"><span>data_version 变化后新 key</span><b>${d.after_version_change.hit ? '命中（异常）' : '未命中（懒失效生效）'}</b></div>` +
      `<p class="muted small" style="margin:8px 0 0">key: ${esc(d.key)}</p>` +
      `<pre class="small" style="white-space:pre-wrap;max-height:180px;overflow:auto;margin:8px 0 0">${esc(JSON.stringify(d.parts, null, 2))}</pre>`;
    await loadRuntime();
  } catch (e) { safeToast(e); }
}

async function simulateRuntimeRestart() {
  try {
    const d = await api('/api/runtime/simulate-restart', { method: 'POST' });
    $('rt-demo').innerHTML =
      `<div class="kv-line"><span>L1 已清空</span><b>${d.l1_cleared}</b></div>` +
      `<div class="kv-line"><span>L2 条目 / 自检健康</span><b>${d.l2_entries} / ${d.healthy}</b></div>` +
      `<p class="muted small" style="margin:8px 0 0">${esc(d.note)}</p>`;
    await loadRuntime();
  } catch (e) { safeToast(e); }
}

async function invalidateRuntime() {
  try {
    const d = await api('/api/runtime/invalidate', { method: 'POST' });
    $('rt-msg').textContent = `已主动清空 ${d.purged} 条缓存（${d.reason}）`;
    await loadRuntime();
  } catch (e) { safeToast(e); }
}

async function saveRuntimePolicy() {
  const patch = {
    l1_capacity: Number($('rt-l1').value), l2_capacity: Number($('rt-l2').value),
    ttl_seconds: Number($('rt-ttl').value),
  };
  try {
    await api('/api/runtime/cache-policy', { method: 'PUT', body: JSON.stringify(patch) });
    $('rt-msg').textContent = '缓存策略已保存并落 runtime_state，重启后可恢复';
    toast(t('设置已保存，已即时生效'), 'ok');
    await loadRuntime();
  } catch (e) { safeToast(e); }
}

/* ================= 内容安全 / PII 防护（四出口） ================= */
let SF_DATA = null;
const SF_GATE_LABEL = { ingest: '入库前', context: '进模型前', output: '输出前', audit: '日志与导出前' };

async function loadSafety() {
  try {
    const r = await api('/api/safety/status');
    SF_DATA = r;
    const p = r.policy, led = r.ledger;
    $('sf-enabled').value = p.enabled ? '1' : '0';
    $('sf-action').value = p.default_action;
    $('sf-minconf').value = p.min_confidence;
    $('sf-policy').innerHTML =
      `<div class="kv-line"><span>规则版本 / 启用状态</span><b>${esc(r.rule_version || '')} · ${p.enabled ? '开启' : '关闭（对照演示）'}</b></div>` +
      `<div class="kv-line"><span>默认动作 / 最小置信度</span><b>${esc(p.default_action)} / ${p.min_confidence}</b></div>` +
      `<div class="kv-line"><span>拦截类别 / 可逆 token</span><b>${esc(p.block_kinds.join(', ') || '无')} / ${p.reversible_token ? '开启（有风险）' : '关闭'}</b></div>` +
      `<div class="kv-line"><span>哈希盐已配置</span><b style="color:var(${p.hash_salt_configured ? '--success' : '--danger'})">${p.hash_salt_configured ? '是' : '否（演示占位盐）'}</b></div>` +
      `<div class="section-title" style="margin-top:12px">四个出口命中台账（真实链路）</div>` +
      ['ingest', 'context', 'output', 'audit'].map(g => {
        const n = (led.by_gate[g] || {}).hits || 0;
        return `<div class="kv-line"><span>${esc(SF_GATE_LABEL[g])}（${g}）</span><b>${n}</b></div>`;
      }).join('') +
      `<div class="kv-line"><span>台账事件行 / 只存统计</span><b>${led.event_rows} / 是</b></div>` +
      `<p class="muted small" style="margin:8px 0 0">${esc(p.note)}</p>`;

    $('sf-integration').innerHTML =
      Object.entries(r.integration).map(([k, v]) =>
        `<div class="kv-line"><span class="small">${esc(k)}</span><b class="small">${esc(v)}</b></div>`).join('');

    $('sf-rules').innerHTML =
      `<table class="table" style="margin-top:4px"><thead><tr><th>类别</th><th>名称</th><th>置信度</th><th>校验 / 门控</th></tr></thead><tbody>` +
      r.rules.map(x => `<tr><td class="small">${esc(x.kind)}</td><td class="small">${esc(x.label)}</td>` +
        `<td class="small">${x.confidence}</td><td class="small">${esc(x.validator || (x.context_gated ? '上下文门控' : '格式'))}</td></tr>`).join('') +
      `</tbody></table>` +
      `<p class="muted small" style="margin:8px 0 0">共 ${r.rules.length} 条规则；每条命中都带规则版本，便于回放比对。${esc(r.samples_note)}</p>`;
  } catch (e) { safeToast(e); }
}

async function runSafetyDemo() {
  try {
    const d = await api('/api/safety/demo', { method: 'POST' });
    $('sf-demo').innerHTML =
      `<div class="kv-line"><span>规则版本 / 样本总命中</span><b>${esc(d.rule_version)} / ${d.total_hits}</b></div>` +
      `<div class="kv-line"><span>防误报负向对照</span><b style="color:var(${d.negative_controls.length === 4 ? '--success' : '--danger'})">${d.negative_controls.length} / 4 通过</b></div>` +
      `<table class="table" style="margin-top:8px"><thead><tr><th>样本</th><th>命中</th><th>类别</th><th>脱敏后</th></tr></thead><tbody>` +
      d.rows.map(x => `<tr><td class="small">${esc(x.label)}</td><td class="small">${x.hit_count}</td>` +
        `<td class="small">${esc(Object.keys(x.by_kind).join(', ') || '—')}</td><td class="small">${esc(x.redacted)}</td></tr>`).join('') +
      `</tbody></table>` +
      `<div class="section-title" style="margin-top:14px">四出口链路（同一段文本依次穿过）</div>` +
      `<table class="table"><thead><tr><th>出口</th><th>命中</th><th>结果文本</th></tr></thead><tbody>` +
      d.chain.map(c => `<tr><td class="small">${esc(SF_GATE_LABEL[c.gate] || c.gate)}</td><td class="small">${c.hit_count}</td>` +
        `<td class="small">${esc(c.text)}</td></tr>`).join('') +
      `</tbody></table>` +
      `<div class="section-title" style="margin-top:14px">流式逐 token 跨界验证</div>` +
      `<div class="kv-line"><span>下发字符数 / 扣留边界 / 命中</span><b>${d.stream.chunks} / ${d.stream.held_boundaries} / ${d.stream.hits}</b></div>` +
      `<div class="kv-line"><span>原文是否泄漏</span><b style="color:var(${d.stream.leaked ? '--danger' : '--success'})">${d.stream.leaked ? '是（异常）' : '否'}</b></div>` +
      `<pre class="small" style="white-space:pre-wrap;margin:8px 0 0">${esc(d.stream.output)}</pre>` +
      `<p class="muted small" style="margin:8px 0 0">${esc(d.note)}</p>`;
  } catch (e) { safeToast(e); }
}

async function runSafetyCheck() {
  const text = $('sf-text').value.trim();
  if (!text) { toast(t('请输入待检测文本'), 'err'); return; }
  try {
    const d = await api('/api/safety/check', { method: 'POST', body: JSON.stringify({ text }) });
    $('sf-check').innerHTML =
      `<div class="kv-line"><span>总命中（四出口合计）</span><b>${d.report.total_hits}</b></div>` +
      `<table class="table" style="margin-top:8px"><thead><tr><th>出口</th><th>命中</th><th>类别</th><th>脱敏后文本</th></tr></thead><tbody>` +
      d.report.results.map(x => `<tr><td class="small">${esc(SF_GATE_LABEL[x.gate] || x.gate)}</td>` +
        `<td class="small">${x.hit_count}</td><td class="small">${esc(Object.keys(x.by_kind).join(', ') || '—')}</td>` +
        `<td class="small">${esc(x.text)}</td></tr>`).join('') +
      `</tbody></table>` +
      `<p class="muted small" style="margin:8px 0 0">${esc(d.note)}</p>`;
  } catch (e) { safeToast(e); }
}

async function saveSafetyPolicy() {
  const patch = {
    enabled: $('sf-enabled').value === '1',
    default_action: $('sf-action').value,
    min_confidence: Number($('sf-minconf').value),
  };
  if (!(patch.min_confidence >= 0 && patch.min_confidence <= 1)) {
    toast(t('最小置信度必须在 0 到 1 之间'), 'err'); return;
  }
  try {
    const d = await api('/api/safety/policy', { method: 'PUT', body: JSON.stringify(patch) });
    $('sf-msg').textContent = d.effective + (d.warning ? ' · ' + d.warning : '');
    toast(t('设置已保存，已即时生效'), 'ok');
    await loadSafety();
  } catch (e) { safeToast(e); }
}

/* 语言切换后，评估页与解析页的动态内容需要重渲染 */
document.addEventListener('kb:langchange', () => { if (S.user && S.view === 'evaltest') loadEvalTest(); });

/* 启动外观控件（DOMContentLoaded 里已在下方调用） */
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAppearance);
} else {
  initAppearance();
}
