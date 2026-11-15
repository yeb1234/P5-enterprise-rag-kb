/* ==========================================================================
 * showcase/static/app.js
 * 演示切片、非生产实现：解析工作台「左分析 / 右入库 JSON」同源对照前端。
 *
 * 事实源：docs/showcase/03-phase2-spec.md §5（前端硬约束）
 *        docs/showcase/02-architecture.md §4（10 个 API 契约）/ §5（左右分栏字段）
 *        docs/showcase/01-scope.md §3.4（两档）/ §4（7 类参数）/ §6（录像分镜）
 *
 * 纪律：
 *  - 零外部依赖：只用原生 fetch / FormData / DOM API，不 import 任何库。
 *  - 不写死假 JSON：所有展示数据来自同源真实 API 响应；加载前是占位态。
 *  - 失败显示错误码：错误体统一按 {code, message, trace_id} 解析，绝不静默。
 *  - 诚实标注：离线回放 ≠ 实时识别；「选出 Pro 档」≠「调用了 Pro 模型」；
 *             空占位字段显式写「空占位」，不隐藏、不填编造值；
 *             runtime.external_request_count 缺失显示「未提供」而非编造 0。
 *
 * 兼容分支（后端 api.py / server.py 由同伴并行实现，本文件不改后端）：
 *  见文件末尾 collectCompatNotes() 与回传说明。
 * ========================================================================== */
(function () {
  'use strict';

  /* ---------------------------------------------------------------------
   * 0. DOM 引用集中登记
   *    index.html 中的每个 id 都在此登记，便于「所有 DOM id 在 app.js 有引用」
   *    的静态检查；同时避免散落的重复 getElementById。
   * ------------------------------------------------------------------- */
  var $ = function (id) { return document.getElementById(id); };
  var D = {
    runId: $('run-id'),
    externalCount: $('external-count'),
    guideLink: $('guide-link'),
    sampleSimple: $('sample-simple'),
    sampleComplex: $('sample-complex'),
    sourceCheck: $('source-check'),
    sampleName: $('sample-name'),
    sampleFile: $('sample-file'),
    replayBanner: $('replay-banner'),
    statusBar: $('status-bar'),
    statusText: $('status-text'),
    statusCode: $('status-code'),
    paramsForm: $('params-form'),
    pStrategy: $('p-strategy'),
    pConfidenceMin: $('p-confidence-min'),
    pChunkSize: $('p-chunk-size'),
    pChunkOverlap: $('p-chunk-overlap'),
    pVisionRatio: $('p-vision-ratio'),
    pVisionSlots: $('p-vision-slots'),
    pTierOverride: $('p-tier-override'),
    pRedact: $('p-redact'),
    pIngestFormat: $('p-ingest-format'),
    paramsHash: $('params-hash'),
    strategyWarning: $('strategy-warning'),
    btnAnalyze: $('btn-analyze'),
    btnReset: $('btn-reset'),
    btnCompare: $('btn-compare'),
    btnMutate: $('btn-mutate'),
    compareResult: $('compare-result'),
    analysisRunId: $('analysis-run-id'),
    analysisPlaceholder: $('analysis-placeholder'),
    analysisBody: $('analysis-body'),
    analysisSummary: $('analysis-summary'),
    analysisRoute: $('analysis-route'),
    analysisSummaryBlock: $('analysis-summaryblock'),
    analysisBudget: $('analysis-budget'),
    analysisWarnings: $('analysis-warnings'),
    analysisPages: $('analysis-pages'),
    ingestRunId: $('ingest-run-id'),
    btnCopyJson: $('btn-copy-json'),
    btnExpandAll: $('btn-expand-all'),
    btnCollapseAll: $('btn-collapse-all'),
    jsonFormatNote: $('json-format-note'),
    fieldNav: $('field-nav'),
    ingestPlaceholder: $('ingest-placeholder'),
    jsonView: $('json-view'),
    btnSnapshot: $('btn-snapshot'),
    snapshotStatus: $('snapshot-status'),
    snapshotRoute: $('snapshot-route'),
    snapshotView: $('snapshot-view')
  };

  /* ---------------------------------------------------------------------
   * 1. 常量与状态
   * ------------------------------------------------------------------- */
  var API = {
    health: '/api/health',
    params: '/api/params',
    putParams: '/api/params',
    strategies: '/api/strategies',
    samples: '/api/samples',
    analyze: '/api/analyze',
    manifest: '/api/manifest/'
  };

  // 默认值来自 03-phase2-spec.md §1 / showcase/params.py 的演示起始值。
  // 仅用于初始化表单与「恢复默认」；权威值以 GET /api/params 返回为准。
  // 注意：as_of 不进前端面板、也不提交，交由后端固定逻辑时间常量，保证双跑一致。
  var DEFAULT_PARAMS = {
    strategy: 'auto',
    confidence_min: 0.75,
    chunk_size_chars: 800,
    chunk_overlap_chars: 80,
    vision_escalation_ratio: 0.10,
    vision_min_slots: 1,
    llm_tier_override: null,
    redact_enabled: true,
    ingest_format: 'compact'
  };

  // A10 取不到样本时的兜底真实文件名（03-phase2-spec.md §1.1 裁决 3 明确落盘文件），
  // 不是编造数据；会同时在状态条标注「回退使用文档规定文件名」。
  var FALLBACK_SAMPLES = [
    { id: 'simple', tier: 'simple', filename: 'simple.pdf', mime: 'application/pdf', replay: false },
    { id: 'complex', tier: 'complex', filename: 'complex.png', mime: 'image/png', replay: true }
  ];

  // 样本静态路径候选：任务指定 /showcase/samples/<name>，其余为不同静态挂载方式下的兼容路径。
  // 实查 server.py：仅 app.mount("/", StaticFiles(showcase/static/))，samples/ 未挂载，
  // 因此这些同源路径在本轮部署下多半取不到字节；此时依赖「手动载入真实样本」兜底
  // 或由主理人协调 backend-engineer 补一条 samples 静态挂载（本文件不改后端）。
  var SAMPLE_PATHS = [
    '/showcase/samples/',   // 任务指定：项目根作静态根时命中
    '/samples/',            // server.py 若把 samples 挂到 /samples
    '/static/samples/',     // 若挂到 /static/samples
    '/showcase/static/samples/',
    '../samples/',          // 页面位于 /static/ 或 /showcase/static/ 时的相对路径
    './samples/'            // 页面与 samples 同目录
  ];

  // 右侧字段导航（02-architecture.md §5.2 / 任务硬约束要求的可见字段）
  var NAV_FIELDS = ['run_id', 'schema_version', 'llm_route', 'chunks', 'trace', 'guardrails', 'manifest'];

  // 滞回裁决常量（03-phase2-spec.md §1.1(7) / llm_router.py）：
  // θ_enter=0.60 / θ_exit=0.40 / k=2。仅用于「页面提示」的判定与文案，
  // 真实数值一律以 ingest.llm_route.hysteresis 的后端返回为准，前端不编造。
  var THETA_ENTER_FALLBACK = 0.60;   // 后端未返回 hysteresis.enter 时的兜底（标注为裁决常量）
  var K_CONSECUTIVE = 2;             // 连续命中次数（裁决常量，不降 k）

  // 同参双跑对拍的「规范化投影」比较口径：
  // 连续两次同参 A5 会因 LRU 路由状态变化（standard→pro）导致 run_id 与
  // llm_route / manifest 里的 snapshot_version 等派生字段变化。
  // 这些变化属「改档必变」的正确行为，不是解析结果不确定性缺陷。
  // 只剔除会随「路由决策」变化的容器/字段（llm_route / route / run_id /
  // manifest / manifest_ref / snapshot_version）；**不剔除 source.sha256** ——
  // 它是源文件摘要，属解析结果本体，双跑本应一致，剔除反而会掩盖真实差异。
  var ROUTE_DERIVED_KEYS = ['llm_route', 'run_id', 'manifest', 'manifest_ref',
    'route', 'snapshot_version'];
  var COMPARE_SCOPE_NOTE = '比较口径：剔除 ' + ROUTE_DERIVED_KEYS.join(' / ') +
    ' 等路由快照派生字段后的规范化投影（等效 compact 档，保留 source.sha256 等解析本体）；' +
    '这些字段随滞回改档变化属「改档必变」的正确行为，不是 JSON 不确定性缺陷。';

  var S = {
    params: clone(DEFAULT_PARAMS),
    paramsHash: '',          // GET /api/params 返回的权威 params_hash
    samples: [],
    samplesFromApi: false,
    strategies: null,        // GET /api/strategies 原始返回
    tier: 'simple',          // 'simple' | 'complex'
    lastResp: null,          // 最近一次 A5 完整响应
    lastIngest: null,        // 最近一次 ingest（复制/对拍用）
    baseline: '',            // 双跑基线规范化字符串
    baselineNote: '',
    busy: false,
    manualFile: null,        // 手动载入的真实样本 File（兜底；字节真实、不伪造）
    jsonFolds: [],           // 主 JSON 树折叠控制器
    snapFolds: []            // 快照树折叠控制器
  };

  function clone(o) {
    var out = {}, k;
    for (k in o) { if (Object.prototype.hasOwnProperty.call(o, k)) { out[k] = o[k]; } }
    return out;
  }

  /* ---------------------------------------------------------------------
   * 2. DOM 小工具（一律用 createElement + textContent，避免把 API 返回内容
   *    拼进 innerHTML；这是本演示切片的基本安全与代码质量底线）
   * ------------------------------------------------------------------- */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    return n;
  }
  function span(cls, text) { return el('span', cls, text); }
  function clear(n) { while (n.firstChild) { n.removeChild(n.firstChild); } }
  function setText(n, t) { clear(n); n.appendChild(document.createTextNode(String(t))); }
  function escText(v) { return v === undefined || v === null ? '' : String(v); }
  function fmt(v) {
    if (v === null || v === undefined) { return '—（空占位）'; }
    if (typeof v === 'boolean') { return v ? 'true' : 'false'; }
    if (typeof v === 'object') { return JSON.stringify(v); }
    return String(v);
  }
  function isPlainEmpty(v) {
    if (v === null || v === undefined || v === '') { return true; }
    if (Array.isArray(v)) { return v.length === 0; }
    if (typeof v === 'object') { return Object.keys(v).length === 0; }
    return false;
  }
  function levelBadge(level, degraded) {
    if (degraded) { return el('span', 'badge degraded', '降级 · ' + escText(degraded)); }
    var cls = 'badge ' + (level === 'local' ? 'local' : (level === 'multimodal' || level === 'ocr_api' ? 'multimodal' : 'neutral'));
    return el('span', cls, escText(level || 'unknown'));
  }
  function kvGrid(pairs) {
    var g = el('div', 'kv-grid');
    pairs.forEach(function (p) {
      var item = el('div', 'kv-item');
      item.appendChild(el('span', 'kk', p[0]));
      var v = el('span', 'vv' + (p[2] ? ' mono' : ''));
      if (p[1] instanceof Node) { v.appendChild(p[1]); } else { v.textContent = escText(p[1]); }
      item.appendChild(v);
      g.appendChild(item);
    });
    return g;
  }
  function blockTitle(t) { return el('div', 'sub-title', t); }
  function noteLine(t) { return el('p', 'a-note', t); }
  function emptyInline(t) { return el('span', 'empty-inline', t || '（空占位，后端未返回该字段）'); }

  /* ---------------------------------------------------------------------
   * 3. 状态条 / 错误码：所有请求失败都经此显示 {code,message,trace_id}
   * ------------------------------------------------------------------- */
  function setStatus(kind, text, code) {
    D.statusBar.className = 'statusbar' + (kind ? ' is-' + kind : '');
    setText(D.statusText, text);
    if (code) {
      D.statusCode.hidden = false;
      setText(D.statusCode, code);
    } else {
      D.statusCode.hidden = true;
      setText(D.statusCode, '');
    }
  }
  function makeError(code, message, traceId, status) {
    var e = new Error(message || '未知错误');
    e.code = code || 'INTERNAL_ERROR';
    e.trace_id = traceId || '';
    e.http = status || 0;
    return e;
  }
  function showError(e, prefix) {
    var code = (e && e.code) || 'INTERNAL_ERROR';
    var msg = (e && e.message) || '未知错误';
    var trace = (e && e.trace_id) ? (' · trace_id=' + e.trace_id) : '';
    setStatus('error', (prefix ? prefix + '：' : '请求失败：') + '[' + code + '] ' + msg + trace, code);
    return e;
  }

  /* 统一 fetch 封装：
   *  - 成功：解析 JSON（非 JSON 也返回原文包装，不抛）
   *  - 失败：按 {code,message,trace_id} 结构化；非 JSON 错误页/网络失败也有明确 code
   */
  function apiJSON(path, opts) {
    return fetch(path, opts || {}).then(function (res) {
      return res.text().then(function (txt) {
        var body = null;
        try { body = txt ? JSON.parse(txt) : null; }
        catch (ex) { body = { _raw: txt }; }
        if (!res.ok) {
          var c = (body && body.code) || ('HTTP_' + res.status);
          var m = (body && body.message) || (res.statusText || ('HTTP ' + res.status));
          if (body && body._raw) { m = '非 JSON 错误响应（HTTP ' + res.status + '）'; }
          throw makeError(c, m, (body && body.trace_id) || '', res.status);
        }
        return body;
      });
    }, function (netErr) {
      var m = (netErr && netErr.message) ? netErr.message : 'unknown';
      throw makeError('NETWORK_UNAVAILABLE',
        '无法连接同源服务（可能未启动 showcase/server.py，或以 file:// 离线打开）：' + m, '', 0);
    });
  }

  /* ---------------------------------------------------------------------
   * 4. 参数：读表单 / 写表单 / GET 权威值 / 变更提示
   * ------------------------------------------------------------------- */
  function readFormParams() {
    var override = D.pTierOverride.value;
    return {
      strategy: D.pStrategy.value,
      confidence_min: parseFloat(D.pConfidenceMin.value),
      chunk_size_chars: parseInt(D.pChunkSize.value, 10),
      chunk_overlap_chars: parseInt(D.pChunkOverlap.value, 10),
      vision_escalation_ratio: parseFloat(D.pVisionRatio.value),
      vision_min_slots: parseInt(D.pVisionSlots.value, 10),
      llm_tier_override: override === '' ? null : override,  // 空串转 null（契约 enum|null）
      redact_enabled: !!D.pRedact.checked,
      ingest_format: D.pIngestFormat.value
    };
  }
  function writeFormParams(p) {
    D.pStrategy.value = p.strategy;
    D.pConfidenceMin.value = p.confidence_min;
    D.pChunkSize.value = p.chunk_size_chars;
    D.pChunkOverlap.value = p.chunk_overlap_chars;
    D.pVisionRatio.value = p.vision_escalation_ratio;
    D.pVisionSlots.value = p.vision_min_slots;
    D.pTierOverride.value = (p.llm_tier_override === null || p.llm_tier_override === undefined) ? '' : p.llm_tier_override;
    D.pRedact.checked = !!p.redact_enabled;
    D.pIngestFormat.value = p.ingest_format;
    S.params = clone(p);
  }
  function loadParams() {
    return apiJSON(API.params).then(function (data) {
      // A2 兼容：既可能是 {params:{...}, params_hash} 也可能是平铺字段
      var p = (data && data.params) ? data.params : data;
      var merged = clone(DEFAULT_PARAMS), k;
      for (k in DEFAULT_PARAMS) {
        if (Object.prototype.hasOwnProperty.call(DEFAULT_PARAMS, k) &&
            p && Object.prototype.hasOwnProperty.call(p, k)) {
          merged[k] = p[k];
        }
      }
      writeFormParams(merged);
      S.paramsHash = (data && (data.params_hash || data.hash)) || (p && p.params_hash) || '';
      D.paramsHash.textContent = 'params_hash（GET /api/params）：' +
        (S.paramsHash || '（后端未返回，改参后由 A5 参数快照体现）');
      updateStrategyWarning();
      return data;
    });
  }
  function onParamChange() {
    setStatus('', '参数已更改。点击「重新分析」或切换档位即重新 POST /api/analyze；参数快照会同步出现在右侧 JSON。');
    updateStrategyWarning();
  }

  /* ocr_api 未接通：按 GET /api/strategies 的后端真实返回显示「未接通，跳过」，不静默 */
  function updateStrategyWarning() {
    var w = D.strategyWarning;
    var chosen = D.pStrategy.value;
    if (chosen !== 'ocr_api') { w.hidden = true; clear(w); return; }
    var reason = '', displayText = '';
    if (S.strategies) {
      // 后端 api.py A4 契约：levels[] 中未接通档带 display="未接通，跳过" 与 reason
      var list = (S.strategies.levels || S.strategies.strategies || S.strategies.vendors || []);
      for (var i = 0; i < list.length; i++) {
        var it = list[i] || {};
        if ((it.id || it.level || it.strategy) === 'ocr_api' || it.available === false) {
          displayText = it.display || '';
          reason = it.reason || it.note || it.contract || it.display || '';
          break;
        }
      }
    }
    clear(w);
    // 优先使用后端返回的原文案，命中「按后端返回显示『未接通，跳过』」硬约束
    w.appendChild(el('b', null, displayText || 'ocr_api 未接通，跳过'));
    var detail = S.strategies
      ? '依据 GET /api/strategies：available=False（收费 P1 适配器契约保持 __TBC__），A5 将返回降级留痕而非静默失败。' +
        (reason ? (' 后端留痕：' + escText(reason)) : '')
      : '依据 03-phase2-spec.md §4：该档 available()=False。当前未取到 GET /api/strategies 状态（见状态条错误码），以 A5 返回的降级结果为准，绝不静默。';
    w.appendChild(document.createTextNode(detail));
    w.hidden = false;
  }
  function loadStrategies() {
    return apiJSON(API.strategies).then(function (data) {
      S.strategies = data || null;
      updateStrategyWarning();
      return data;
    });
  }

  /* ---------------------------------------------------------------------
   * 5. 两档样本：GET /api/samples 取真实文件名 + 静态路径取字节兼容分支
   * ------------------------------------------------------------------- */
  function loadSamples() {
    return apiJSON(API.samples).then(function (data) {
      var list = (data && data.samples) || (Array.isArray(data) ? data : []);
      S.samples = list.length ? list : clone(FALLBACK_SAMPLES);
      S.samplesFromApi = list.length > 0;
      updateSampleLabel();
      return data;
    }, function (e) {
      S.samples = clone(FALLBACK_SAMPLES);
      S.samplesFromApi = false;
      updateSampleLabel();
      // A10 失败不阻塞主流程，但必须显示错误码（非致命，前缀说明）
      showError(e, 'GET /api/samples 失败，回退使用 03-phase2-spec.md §1.1 明确落盘的真实文件名（simple.pdf / complex.png），非编造数据');
      return null;
    });
  }
  function pickSample(tier) {
    var list = S.samples || [], i, s;
    var want = tier === 'complex' ? 'complex' : 'simple';
    for (i = 0; i < list.length; i++) {
      s = list[i] || {};
      if (String(s.tier || s.id || '').toLowerCase().indexOf(want) >= 0) { return s; }
    }
    for (i = 0; i < list.length; i++) {
      s = list[i] || {};
      if (String(s.filename || '').indexOf(want) >= 0) { return s; }
    }
    if (want === 'complex') {
      for (i = 0; i < list.length; i++) { if (list[i] && list[i].replay) { return list[i]; } }
    }
    return FALLBACK_SAMPLES[want === 'complex' ? 1 : 0];
  }
  function updateSampleLabel() {
    var s = pickSample(S.tier);
    var label = S.manualFile ? S.manualFile.name : ((s && s.filename) || '—');
    D.sampleName.textContent = label;
    D.sampleName.title = S.manualFile
      ? '手动载入的本地真实样本（字节真实，由后端真实解析，非前端伪造）'
      : (S.samplesFromApi ? 'GET /api/samples 真实文件名' : '文档规定文件名回退（03-phase2-spec.md §1.1）');
  }

  /* 静态路径多候选取样本字节；全部失败抛 SAMPLE_FETCH_FAILED（进入 sample_id 兼容分支） */
  function fetchSampleFile(name, mime) {
    var idx = 0;
    function attempt() {
      if (idx >= SAMPLE_PATHS.length) {
        throw makeError('SAMPLE_FETCH_FAILED',
          '所有样本静态路径均取不到 ' + name + '（尝试过：' + SAMPLE_PATHS.join('、') + '）', '', 0);
      }
      var url = SAMPLE_PATHS[idx++] + encodeURIComponent(name);
      return fetch(url).then(function (res) {
        var ct = res.headers.get('content-type') || '';
        if (!res.ok || ct.indexOf('text/html') >= 0) { return attempt(); }
        return res.blob().then(function (b) {
          if (!b || b.size === 0) { return attempt(); }
          return new File([b], name, { type: mime || b.type || 'application/octet-stream' });
        });
      }, function () { return attempt(); });
    }
    return attempt();
  }

  /* 组装 A5 的 FormData（file + params JSON 字符串 + 兼容字段）
   * file 是后端 api.py A5 的必填项（File(...)），字节来源三选一：
   *   ① 手动载入的真实 File（最高优先，字节真实、不伪造）
   *   ② 同源静态路径取回的样本字节
   *   ③ 都不可得时抛 SAMPLE_FETCH_FAILED → 明确错误码，绝不静默、绝不伪造字节
   */
  function buildAnalyzeForm(file, name, params, sample) {
    var fd = new FormData();
    fd.append('file', file, name);                 // A5 契约必填（api.py: file: UploadFile = File(...)）
    fd.append('params', JSON.stringify(params));   // A5 契约可选 JSON 字符串（7 类参数快照）
    // 兼容字段：后端按契约只取需要的，多余字段忽略即可
    if (sample && sample.id) { fd.append('sample_id', sample.id); }
    fd.append('tier', S.tier);
    // 后端 api.py 用 replay 字段或文件名 complex.png 判定「离线回放」，两者都给
    if ((sample && sample.replay) || S.tier === 'complex' || /complex/i.test(name)) {
      fd.append('replay', '1');
    }
    return fd;
  }

  function postAnalyze() {
    var sample = pickSample(S.tier);
    var params = readFormParams();
    S.params = clone(params);
    var name = (sample && sample.filename) || (S.tier === 'complex' ? 'complex.png' : 'simple.pdf');
    var mime = (sample && sample.mime) || (/\.pdf$/i.test(name) ? 'application/pdf' : 'image/png');

    // ① 手动载入的真实样本（用户双击选本地 simple.pdf / complex.png，字节真实）
    if (S.manualFile) {
      var manualName = S.manualFile.name || name;
      return apiJSON(API.analyze, {
        method: 'POST',
        body: buildAnalyzeForm(S.manualFile, manualName, params, sample)
      });
    }

    // ② 同源静态路径取字节（server.py 已挂 /showcase/samples/<name>，候选 1 生效）
    return fetchSampleFile(name, mime).then(function (file) {
      return apiJSON(API.analyze, {
        method: 'POST', body: buildAnalyzeForm(file, name, params, sample)
      });
    }).catch(function (err) {
      if (!err || err.code !== 'SAMPLE_FETCH_FAILED') { throw err; }
      // ③ sample_id 兼容分支（集成裁决已由后端实现）：
      //    后端 A5 白名单 SAMPLE_WHITELIST={"simple.pdf","complex.png"}，
      //    值必须是**真实文件名**，不是 id（simple/complex），否则会 INVALID_PARAM。
      setStatus('busy', '静态路径未取到 ' + name + ' 字节，改用 sample_id（真实文件名）兼容分支提交 A5 …');
      var fd2 = new FormData();
      fd2.append('sample_id', name);              // 白名单值 = 真实文件名
      fd2.append('params', JSON.stringify(params));
      fd2.append('tier', S.tier);
      if ((sample && sample.replay) || S.tier === 'complex' || /complex/i.test(name)) {
        fd2.append('replay', '1');
      }
      return apiJSON(API.analyze, { method: 'POST', body: fd2 }).catch(function (e2) {
        // ④ 两条自动路径都失败：如实显示后端错误码 + 可操作提示，不静默、不伪造字节
        throw makeError(e2.code || 'SAMPLE_FETCH_FAILED',
          '静态路径与 sample_id 兜底均未取到样本。sample_id 分支返回 [' +
          (e2.code || '?') + '] ' + (e2.message || '') +
          '。可改用页面顶部「手动载入真实样本（兜底）」选择本地已落盘的 simple.pdf / complex.png。' +
          '前端不会伪造样本字节。', e2.trace_id || '', 0);
      });
    });
  }

  /* ---------------------------------------------------------------------
   * 6. 规范化字符串（同参双跑对拍 / 改参必变）
   *    stableStringify：对象键递归排序后 JSON.stringify，不剥离任何字段。
   *    projectionString：同参双跑对拍用，剔除「路由快照派生字段」后规范化。
   *    背景（03-phase2-spec.md §1.1(7) 裁决）：TierState 按 (source_sha, params_hash)
   *    缓存，连续两次复杂档 A5 会是 standard(hits=1) → pro(hits=2)，
   *    snapshot_version 变 → run_id 变。这是「改档必变」的正确行为，
   *    不是解析结果不确定性缺陷，因此判定一致性时剔除这类字段（等效 compact 档）。
   * ------------------------------------------------------------------- */
  function stableStringify(v) {
    if (v === undefined) { return 'null'; }
    if (v === null || typeof v !== 'object') { return JSON.stringify(v); }
    if (Array.isArray(v)) { return '[' + v.map(stableStringify).join(',') + ']'; }
    var keys = Object.keys(v).sort();
    return '{' + keys.map(function (k) {
      return JSON.stringify(k) + ':' + stableStringify(v[k]);
    }).join(',') + '}';
  }
  /* 剔除路由派生字段后的规范化投影：只比较「解析结果本体」（chunks/trace/source/
   * guardrails/parser 等），不把随滞回改档而变化的 run_id / llm_route / manifest 计入。
   * 递归剔除同名容器（manifest/llm_route/route），但保留 source.sha256：它是源文件
   * 摘要，属解析本体，若真变了必须能检出，不能被一并剔除掩盖。 */
  function projectionString(v) {
    if (v === undefined) { return 'null'; }
    if (v === null || typeof v !== 'object') { return JSON.stringify(v); }
    if (Array.isArray(v)) { return '[' + v.map(projectionString).join(',') + ']'; }
    var keys = Object.keys(v).filter(function (k) {
      return ROUTE_DERIVED_KEYS.indexOf(k) < 0;
    }).sort();
    return '{' + keys.map(function (k) {
      return JSON.stringify(k) + ':' + projectionString(v[k]);
    }).join(',') + '}';
  }
  /* 从 A5 响应里取真实 llm_route 的关键字段，用于对拍结果展示（只读真实响应） */
  function routeFacts(resp) {
    var r = (resp && resp.ingest && resp.ingest.llm_route) ||
            (resp && resp.analysis && resp.analysis.route) || {};
    var h = r.hysteresis || {};
    return {
      tier: r.tier,
      difficulty: r.difficulty,
      trigger: r.trigger,
      placeholder: r.placeholder,
      snapshot_version: r.snapshot_version,
      hits: h.consecutive_hits,
      enter: h.enter,
      exit: h.exit,
      state: h.state,
      last_transition: h.last_transition,
      run_id: resp && resp.run_id
    };
  }
  function preview(str) {
    return str.length > 72 ? (str.slice(0, 72) + '…') : str;
  }
  function setCompare(kind, lines) {
    clear(D.compareResult);
    D.compareResult.className = 'compare-result' + (kind === 'ok' ? ' ok' : (kind === 'bad' ? ' bad' : ''));
    lines.forEach(function (l) {
      if (l && l.charAt(0) === '·') {
        D.compareResult.appendChild(el('span', 'kv', l));
      } else {
        D.compareResult.appendChild(document.createTextNode(l));
        D.compareResult.appendChild(document.createElement('br'));
      }
    });
  }

  /* ---------------------------------------------------------------------
   * 7. JSON 树渲染：语法着色 + 可折叠 + 空占位显式标注
   *    返回 {folds:[...]}，供「全部展开 / 全部折叠」使用。
   * ------------------------------------------------------------------- */
  function placeholderTag() { return el('span', 'jplaceholder', '空占位'); }
  function indentText(depth) { return new Array(depth + 1).join('  '); }
  function makeLine(depth) {
    var d = el('div', 'jrow jline');
    if (depth > 0) { d.appendChild(document.createTextNode(indentText(depth))); }
    return d;
  }

  function renderJSON(container, obj) {
    clear(container);
    var folds = [];
    var root = el('div', 'jroot');
    container.appendChild(root);
    emit(null, obj, 0, true, root, folds, null);
    return { folds: folds };
  }

  /* emit 返回它创建的「第一行」元素，便于顶层字段导航打 data-top 标记 */
  function emit(key, val, depth, isLast, parent, folds, topKey) {
    var line, first;
    var isContainer = val !== null && typeof val === 'object';
    var comma = isLast ? '' : ',';

    if (!isContainer) {
      line = makeLine(depth);
      if (key !== null) {
        line.appendChild(span('jkey', JSON.stringify(key)));
        line.appendChild(span('jpunct', ': '));
      }
      if (val === null) {
        line.appendChild(span('jnull', 'null'));
        line.appendChild(placeholderTag());
      } else if (typeof val === 'string') {
        line.appendChild(span('jstr', JSON.stringify(val)));
        if (val === '') { line.appendChild(placeholderTag()); }
      } else if (typeof val === 'number') {
        line.appendChild(span('jnum', String(val)));
      } else if (typeof val === 'boolean') {
        line.appendChild(span('jbool', String(val)));
      } else {
        line.appendChild(span('jpunct', String(val)));
      }
      line.appendChild(span('jpunct', comma));
      if (topKey) { line.setAttribute('data-top', topKey); }
      parent.appendChild(line);
      return line;
    }

    // 容器（对象 / 数组）
    var isArr = Array.isArray(val);
    var keys = isArr ? null : Object.keys(val);
    var n = isArr ? val.length : keys.length;
    var open = isArr ? '[' : '{';
    var close = isArr ? ']' : '}';

    var head = makeLine(depth);
    if (key !== null) {
      head.appendChild(span('jkey', JSON.stringify(key)));
      head.appendChild(span('jpunct', ': '));
    }
    var fold = span('jfold', n ? '▾' : ' ');
    head.appendChild(fold);
    head.appendChild(span('jpunct', open));
    var hint = span('jsummary', '');
    head.appendChild(hint);
    var tailClose = span('jpunct', '');
    head.appendChild(tailClose);
    if (topKey) { head.setAttribute('data-top', topKey); }
    parent.appendChild(head);

    var children = el('div', 'jchildren');
    parent.appendChild(children);
    var tail = makeLine(depth);
    tail.appendChild(span('jpunct', close + comma));
    parent.appendChild(tail);

    if (n === 0) {
      head.appendChild(placeholderTag());
      fold.textContent = ' ';
      tailClose.textContent = close + comma;
      tail.style.display = 'none';
      hint.textContent = '';
      children.style.display = 'none';
      return head;
    }

    var i, childKey, last;
    if (isArr) {
      for (i = 0; i < n; i++) {
        last = (i === n - 1);
        emit(null, val[i], depth + 1, last, children, folds, null);
      }
    } else {
      for (i = 0; i < n; i++) {
        childKey = keys[i];
        last = (i === n - 1);
        emit(childKey, val[childKey], depth + 1, last, children, folds,
             depth === 0 ? childKey : null);
      }
    }

    var collapsed = false;
    function apply(c) {
      collapsed = !!c;
      fold.textContent = collapsed ? '▸' : '▾';
      children.style.display = collapsed ? 'none' : '';
      tail.style.display = collapsed ? 'none' : '';
      hint.textContent = collapsed ? (' … ' + n + (isArr ? ' 项' : ' 字段') + ' … ') : '';
      tailClose.textContent = collapsed ? (close + comma) : '';
    }
    fold.style.cursor = 'pointer';
    fold.addEventListener('click', function () { apply(!collapsed); });
    folds.push(apply);
    return head;
  }

  function applyFolds(list, collapsed) {
    (list || []).forEach(function (f) { f(collapsed); });
  }

  /* ---------------------------------------------------------------------
   * 8. 字段导航 chips：点击定位到顶层字段并高亮
   * ------------------------------------------------------------------- */
  function renderFieldNav(ingest) {
    clear(D.fieldNav);
    NAV_FIELDS.forEach(function (k) {
      var present = !!(ingest && Object.prototype.hasOwnProperty.call(ingest, k));
      var c = el('button', 'fchip' + (present ? '' : ' miss'), k + (k === 'chunks' || k === 'trace' ? '[]' : '') +
        (present ? '' : ' · 缺失'));
      c.type = 'button';
      if (present) {
        c.addEventListener('click', function () {
          // 只在 IngestDocument 区块内定位，避免命中上方「参数快照」区块的同名字段
          var rows = D.jsonView.querySelectorAll('.ingest-inner [data-top]');
          for (var i = 0; i < rows.length; i++) {
            if (rows[i].getAttribute('data-top') === k) {
              rows[i].classList.add('hot');
              if (rows[i].scrollIntoView) { rows[i].scrollIntoView({ block: 'center' }); }
              (function (n) { setTimeout(function () { n.classList.remove('hot'); }, 1200); })(rows[i]);
              break;
            }
          }
        });
      } else {
        c.disabled = true;
      }
      D.fieldNav.appendChild(c);
    });
  }

  /* ---------------------------------------------------------------------
   * 9. AnalysisView 结构兼容：
   *    优先 pages[]（02-architecture §5.1 组装后的 AnalysisView）；
   *    回退 results[] + traces[]（ParseRouter 原始 report，同伴可能直接透传）。
   *    所有字段来自真实响应，缺字段显式写「空占位」，绝不编造。
   * ------------------------------------------------------------------- */
  function normalizeAnalysis(a, resp) {
    a = a || {};
    var pages = [];
    if (Array.isArray(a.pages) && a.pages.length) {
      pages = a.pages.map(function (p, i) {
        return {
          page_no: p.page_no || (i + 1),
          level: p.level, confidence: p.confidence, degraded: p.degraded,
          preview: p.preview,
          signals: p.signals || {},
          blocks: p.blocks || [],
          trace: p.trace || null
        };
      });
    } else if (Array.isArray(a.results) && a.results.length) {
      var traces = a.traces || [];
      pages = a.results.map(function (r, i) {
        var t = null;
        for (var j = 0; j < traces.length; j++) {
          if ((traces[j].page_no || (j + 1)) === (i + 1)) { t = traces[j]; break; }
        }
        return {
          page_no: (r.page_no || (i + 1)),
          level: r.level, confidence: (r.signals && r.signals.confidence),
          degraded: r.degraded,
          preview: r.text ? String(r.text).slice(0, 160) : undefined,
          signals: r.signals || {},
          blocks: r.blocks || [],
          trace: t || r.trace || null
        };
      });
    }
    return {
      raw: a,
      pages: pages,
      summary: a.summary || null,
      budget: a.budget || null,
      warnings: collectWarnings(a, resp)
    };
  }
  function collectWarnings(a, resp) {
    var out = [];
    function push(v) {
      if (v === null || v === undefined || v === '') { return; }
      if (Array.isArray(v)) { v.forEach(push); return; }
      if (typeof v === 'object') {
        // 后端 api.py A5 的 errors[] 元素是 {code,message}；如实展开，不显示 [object Object]
        if (v.code || v.message) {
          out.push('[' + (v.code || '?') + '] ' + (v.message || '') + (v.trace_id ? (' · trace_id=' + v.trace_id) : ''));
        } else {
          out.push(JSON.stringify(v));
        }
        return;
      }
      out.push(String(v));
    }
    push(a && a.warnings);
    push(a && a.note);
    push(resp && resp.ingest && resp.ingest.note);
    push(resp && resp.errors);
    return out;
  }

  /* 渲染「LLM 路由 / 滞回状态」面板：只展示后端返回的真实字段。
   * target: DOM 容器；route: ingest.llm_route 或 analysis.route 或 manifest.route；
   * label: 顶部小标题。
   * 主理人补遗要求：显示 hysteresis 的 enter / exit / consecutive_hits / state /
   * last_transition，以及 difficulty / tier / trigger；空占位字段显式标注；
   * standard + difficulty>=θ_enter 时提示「再跑一次触发 Pro」；pro 时提示「仅路由决策」。
   */
  function renderRoutePanel(target, route, label) {
    clear(target);
    target.hidden = false;
    target.appendChild(blockTitle(label || 'LLM 路由 · 滞回状态（真实字段）'));
    route = route || {};
    var h = route.hysteresis || {};
    var f = function (v) {
      return (v === undefined || v === null) ? '—（空占位）' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
    };
    target.appendChild(kvGrid([
      ['tier / 档位', f(route.tier), true],
      ['difficulty / 难度', f(route.difficulty)],
      ['trigger', f(route.trigger), true],
      ['placeholder', f(route.placeholder)]
    ]));
    target.appendChild(blockTitle('hysteresis · 双阈值滞回'));
    target.appendChild(kvGrid([
      ['enter / 升档阈值', f(h.enter)],
      ['exit / 降档阈值', f(h.exit)],
      ['consecutive_hits', f(h.consecutive_hits)],
      ['state', f(h.state), true],
      ['last_transition', f(h.last_transition), true]
    ]));
    if (!route.hysteresis) {
      target.appendChild(noteLine('后端未返回 hysteresis（空占位）：compact 档可能为精简路由快照，切到 full 档或展开「快照单文件」可看完整 RouteSnapshot。'));
    }

    // 滞回提示（03-phase2-spec.md §1.1(7)）：真实字段判定，不编造、不写成「单次即 pro」
    var tier = String(route.tier || '');
    var difficulty = (typeof route.difficulty === 'number') ? route.difficulty : NaN;
    var enter = (typeof h.enter === 'number') ? h.enter : THETA_ENTER_FALLBACK;
    var hits = (typeof h.consecutive_hits === 'number') ? h.consecutive_hits : null;
    var hint = null, kind = 'neutral';
    if (tier === 'pro') {
      kind = 'pro';
      hint = '连续命中 ' + (hits === null ? K_CONSECUTIVE + '/' + K_CONSECUTIVE : hits + '/' + K_CONSECUTIVE) +
        '，已切到 Pro 档；本轮仅路由决策，未发起外部 LLM 调用。「选出 Pro 档」不等于「调用了 Pro 模型」。';
    } else if (tier === 'standard' && !isNaN(difficulty) && difficulty >= enter) {
      kind = 'pending';
      hint = '难度已达升档阈值（difficulty ' + difficulty + ' ≥ θ_enter ' + enter +
        '），滞回要求连续 ' + K_CONSECUTIVE + ' 次命中（当前 ' +
        (hits === null ? '—' : hits) + '/' + K_CONSECUTIVE + '），' +
        '同档位再跑一次将触发 Pro 兜底。这是防抖动的双阈值滞回，不是「单次就该出 Pro」。';
    } else if (tier === 'standard') {
      kind = 'neutral';
      hint = '当前 standard 档，难度未达升档阈值（θ_enter ' + enter +
        '）。双阈值滞回需连续 ' + K_CONSECUTIVE + ' 次命中才升 Pro，属防抖动设计。';
    } else if (tier === 'fallback') {
      kind = 'pending';
      hint = '当前 fallback 档（事件触发，忽略滞回计数）；本轮仅路由决策，未发起外部 LLM 调用。';
    }
    if (hint) { target.appendChild(el('div', 'route-hint ' + kind, hint)); }
    target.appendChild(noteLine('以上字段全部来自同源真实 API 响应（ingest.llm_route / manifest.route）；缺失即空占位，前端不填补。'));
  }

  function renderAnalysis(resp) {
    var a = normalizeAnalysis(resp.analysis, resp);
    D.analysisPlaceholder.hidden = true;
    D.analysisBody.hidden = false;

    // 概览
    clear(D.analysisSummary);
    D.analysisSummary.appendChild(blockTitle('文档概览 · 与右侧同源'));
    var top = resp.analysis || {};
    D.analysisSummary.appendChild(kvGrid([
      ['run_id', top.run_id || resp.run_id || '—（空占位）', true],
      ['doc_id', top.doc_id || top.source && top.source.doc_id || '—（空占位）', true],
      ['filename', top.filename || (resp.ingest && resp.ingest.source && resp.ingest.source.filename) || '—（空占位）'],
      ['mime', top.mime || (resp.ingest && resp.ingest.source && resp.ingest.source.mime) || '—（空占位）'],
      ['page_count', top.page_count !== undefined ? top.page_count : (a.pages.length || '—（空占位）')],
      ['strategy / 生效档', levelBadge(top.strategy || (S.params && S.params.strategy), null)]
    ]));
    if (top.params_snapshot) {
      D.analysisSummary.appendChild(noteLine('params_snapshot（后端回填）：' + JSON.stringify(top.params_snapshot)));
    } else {
      D.analysisSummary.appendChild(noteLine('后端未回填 analysis.params_snapshot；本次提交参数见右侧「客户端请求参数快照」区块。'));
    }
    // LLM 路由 / 滞回面板（03-phase2-spec.md §1.1(7) 补遗）：
    // 优先 ingest.llm_route（裁决明确要求其 hysteresis 真实字段），回退 analysis.route。
    var route = (resp.ingest && resp.ingest.llm_route) || top.route || null;
    renderRoutePanel(D.analysisRoute, route,
      'LLM 路由 · 滞回状态（ingest.llm_route 真实字段）');

    // summary
    clear(D.analysisSummaryBlock);
    D.analysisSummaryBlock.appendChild(blockTitle('汇总 · summary'));
    if (a.summary) {
      var sm = a.summary;
      D.analysisSummaryBlock.appendChild(kvGrid([
        ['upgrades / 升级次数', sm.upgrades !== undefined ? sm.upgrades : '—（空占位）'],
        ['budget_blocked', sm.budget_blocked !== undefined ? sm.budget_blocked : '—（空占位）'],
        ['degraded / 降级页数', sm.degraded !== undefined ? sm.degraded : '—（空占位）'],
        ['total_latency_ms（观测值）', sm.total_latency_ms !== undefined ? sm.total_latency_ms : '—（空占位）'],
        ['by_level', sm.by_level ? JSON.stringify(sm.by_level) : '—（空占位）', true]
      ]));
      D.analysisSummaryBlock.appendChild(noteLine('total_latency_ms 为观测字段，不进右侧入库 JSON（保证同参双跑逐字节一致）。'));
    } else {
      D.analysisSummaryBlock.appendChild(emptyInline());
    }

    // budget
    clear(D.analysisBudget);
    D.analysisBudget.appendChild(blockTitle('视觉预算 · budget'));
    if (a.budget) {
      var b = a.budget;
      D.analysisBudget.appendChild(kvGrid([
        ['total_pages', b.total_pages !== undefined ? b.total_pages : '—（空占位）'],
        ['ratio / 熔断上限', b.ratio !== undefined ? b.ratio : '—（空占位）'],
        ['slots', b.slots !== undefined ? b.slots : '—（空占位）'],
        ['used', b.used !== undefined ? b.used : '—（空占位）'],
        ['remaining', b.remaining !== undefined ? b.remaining : '—（空占位）']
      ]));
      D.analysisBudget.appendChild(noteLine('ratio 是熔断上限，不是随机分流；预算耗尽会在 trace[] 留痕，不假装升级成功。'));
    } else {
      D.analysisBudget.appendChild(emptyInline());
    }

    // warnings / errors / 回放标注
    clear(D.analysisWarnings);
    D.analysisWarnings.appendChild(blockTitle('告警 / 降级 / 回放留痕 · warnings[]'));
    if (a.warnings.length) {
      var ul = el('ul', 'warn-list');
      a.warnings.forEach(function (w) { ul.appendChild(el('li', null, w)); });
      D.analysisWarnings.appendChild(ul);
    } else {
      D.analysisWarnings.appendChild(noteLine('本次响应未返回 warnings[]（空占位）。'));
    }
    if (S.tier === 'complex') {
      D.analysisWarnings.appendChild(noteLine('复杂档固定标注：视觉结果为离线回放，非实时在线识别（见页顶横幅）。'));
    }

    // 逐页
    clear(D.analysisPages);
    if (!a.pages.length) {
      D.analysisPages.appendChild(emptyInline('响应未含逐页结果（pages[] / results[] 均为空占位）。'));
      return;
    }
    a.pages.forEach(function (p) { D.analysisPages.appendChild(renderPage(p)); });
  }

  function renderPage(p) {
    var card = el('div', 'page-card');
    var head = el('div', 'page-head');
    head.appendChild(el('span', 'pno', '第 ' + escText(p.page_no) + ' 页'));
    head.appendChild(levelBadge(p.level, p.degraded));
    head.appendChild(el('span', 'lvl', 'confidence=' + (p.confidence !== undefined && p.confidence !== null ? p.confidence : '—（空占位）')));
    card.appendChild(head);

    var body = el('div', 'page-body');

    // signals
    body.appendChild(blockTitle('质量信号 · signals'));
    var sig = p.signals || {};
    var sigKeys = ['text_chars', 'text_density', 'image_ratio', 'table_cells', 'table_complete', 'ocr_needed', 'confidence'];
    var grid = el('div', 'sig-grid');
    sigKeys.forEach(function (k) {
      var item = el('div', 'sig');
      item.appendChild(el('span', 'sk', k));
      var v = sig[k];
      var cls = 'sv' + ((k === 'ocr_needed' && v === true) || (k === 'confidence' && typeof v === 'number' && v < 0.75) ? ' hot' : '');
      item.appendChild(el('span', cls, v === undefined || v === null ? '空占位' : String(v)));
      grid.appendChild(item);
    });
    body.appendChild(grid);
    if (Array.isArray(sig.notes) && sig.notes.length) {
      var nl = el('ul', 'notes-list');
      sig.notes.forEach(function (t) { nl.appendChild(el('li', null, t)); });
      body.appendChild(nl);
    } else {
      body.appendChild(emptyInline('signals.notes 为空占位'));
    }

    // blocks
    body.appendChild(blockTitle('块 · blocks[]（长文本完整显示，可滚动）'));
    if (Array.isArray(p.blocks) && p.blocks.length) {
      p.blocks.forEach(function (b) {
        var it = el('div', 'block-item');
        it.appendChild(el('span', 'bt', (b.block_type || 'block') +
          ' · page ' + escText(b.page_no) +
          (b.section_path ? (' · ' + b.section_path) : '') +
          (b.confidence !== undefined ? (' · conf=' + b.confidence) : '')));
        if (b.text) {
          it.appendChild(el('div', 'block-text', b.text));
        } else {
          it.appendChild(emptyInline('text 为空占位'));
        }
        if (b.cells) { it.appendChild(noteLine('cells：' + JSON.stringify(b.cells))); }
        else { it.appendChild(emptyInline('cells 为空占位')); }
        if (b.facts) { it.appendChild(noteLine('facts：' + JSON.stringify(b.facts))); }
      });
    } else {
      body.appendChild(emptyInline('blocks[] 为空占位'));
    }

    // trace
    body.appendChild(blockTitle('解析留痕 · trace'));
    if (p.trace) {
      var t = p.trace;
      var tl = el('ul', 'trace-list');
      tl.appendChild(el('li', null, 'reason：' + (t.reason || '空占位')));
      tl.appendChild(el('li', null, 'upgraded_from：' + (t.upgraded_from === null || t.upgraded_from === undefined ? '空占位' : t.upgraded_from)));
      tl.appendChild(el('li', null, 'budget_blocked：' + fmt(t.budget_blocked)));
      tl.appendChild(el('li', null, 'degraded：' + (t.degraded || '空占位')));
      tl.appendChild(el('li', null, 'latency_ms（观测值）：' + (t.latency_ms !== undefined ? t.latency_ms : '空占位')));
      if (Array.isArray(t.notes) && t.notes.length) {
        t.notes.forEach(function (x) { tl.appendChild(el('li', null, '· ' + x)); });
      }
      body.appendChild(tl);
    } else {
      body.appendChild(emptyInline('trace 为空占位（后端未返回该页留痕）'));
    }

    if (p.preview) {
      body.appendChild(blockTitle('预览 · preview'));
      body.appendChild(el('div', 'block-text', p.preview));
    }
    card.appendChild(body);
    return card;
  }

  /* ---------------------------------------------------------------------
   * 10. 右侧 IngestDocument + 参数快照（客户端提交值，明确标注来源）
   * ------------------------------------------------------------------- */
  function renderIngest(resp) {
    var ingest = resp.ingest || null;
    S.lastIngest = ingest;
    D.ingestPlaceholder.hidden = true;
    D.jsonView.hidden = false;

    clear(D.jsonView);
    // 区块 1：客户端请求参数快照（不是后端产物，诚实标注）
    var cap1 = el('div', 'json-note',
      '客户端请求参数快照 · 本次 POST /api/analyze 提交的 params 字段（非后端产物；改任一参数后本区块与入库 JSON 同步变化）');
    D.jsonView.appendChild(cap1);
    var box1 = el('div', 'json-view-inner');
    D.jsonView.appendChild(box1);
    var c1 = renderJSON(box1, S.params);
    S.jsonFolds = c1.folds;

    // 区块 2：真实 IngestDocument
    var cap2 = el('div', 'json-note',
      'IngestDocument · 来自 POST /api/analyze 的 ingest 字段（真实响应；空占位字段显式标注「空占位」，不隐藏、不填假值）');
    D.jsonView.appendChild(cap2);
    var box2 = el('div', 'json-view-inner ingest-inner');
    D.jsonView.appendChild(box2);
    if (ingest) {
      var c2 = renderJSON(box2, ingest);
      S.jsonFolds = S.jsonFolds.concat(c2.folds);
      renderFieldNav(ingest);
      var sv = ingest.schema_version || resp.schema_version || '—（空占位）';
      var fmtNote = ingest.ingest_format || (S.params && S.params.ingest_format) || '—';
      D.jsonFormatNote.textContent = 'schema_version=' + sv + ' · format=' + fmtNote +
        ' · chunks=' + (Array.isArray(ingest.chunks) ? ingest.chunks.length : '空占位') +
        ' · trace=' + (Array.isArray(ingest.trace) ? ingest.trace.length : '空占位');
    } else {
      box2.appendChild(emptyInline('响应未含 ingest（空占位）'));
      renderFieldNav(null);
      D.jsonFormatNote.textContent = 'ingest 为空占位';
    }
  }

  /* ---------------------------------------------------------------------
   * 11. 页头：同源 run_id / 外部请求计数 / 回放横幅 / 三态
   * ------------------------------------------------------------------- */
  function renderHeader(resp) {
    var rid = resp.run_id || '—（空占位）';
    setText(D.runId, rid);
    setText(D.analysisRunId, (resp.analysis && resp.analysis.run_id) || rid);
    setText(D.ingestRunId, (resp.ingest && resp.ingest.run_id) || rid);

    // 同源校验：诚实显示，左右内嵌 run_id 缺失也不编造
    var a = resp.analysis && resp.analysis.run_id;
    var b = resp.ingest && resp.ingest.run_id;
    if (resp.run_id && (a || b) && ((a && a !== resp.run_id) || (b && b !== resp.run_id))) {
      D.sourceCheck.className = 'source-check warn';
      setText(D.sourceCheck, '⚠ 同源校验不一致：顶层 run_id 与左/右内嵌值不同（如实显示，未做修正）');
    } else if (a && b) {
      D.sourceCheck.className = 'source-check ok';
      setText(D.sourceCheck, '✓ 左右同源：analysis.run_id = ingest.run_id = 页头 run_id');
    } else {
      D.sourceCheck.className = 'source-check';
      setText(D.sourceCheck, '✓ 左右同源：两区均由同一次 A5 响应的 run_id 驱动（analysis / ingest 未内嵌该字段，按顶层值）');
    }

    // 外部请求计数：缺失显示「未提供」，不编造 0；非 0 红色警示
    var runtime = resp.runtime || {};
    var cnt = runtime.external_request_count;
    var b = D.externalCount;
    if (cnt === undefined || cnt === null) {
      b.className = 'unknown';
      setText(b, '未提供');
    } else {
      b.className = (cnt === 0 ? '' : 'notzero');
      setText(b, String(cnt) + (cnt === 0 ? ' · 离线' : ' · 非离线警示'));
    }

    // 复杂档页顶固定回放声明
    D.replayBanner.hidden = (S.tier !== 'complex');
  }

  function renderAll(resp) {
    var prevRunId = S.lastResp && S.lastResp.run_id;
    S.lastResp = resp;
    if (!resp || typeof resp !== 'object') {
      showError(makeError('INVALID_RESPONSE', 'A5 响应不是 JSON 对象', ''), 'A5 响应异常');
      return;
    }
    // run_id 变化 → 收起旧快照视图，避免继续展示过期的单文件（诚实性）
    if (!D.snapshotView.hidden && prevRunId && resp.run_id && prevRunId !== resp.run_id) {
      D.snapshotView.hidden = true;
      D.snapshotRoute.hidden = true;
      D.btnSnapshot.textContent = '快照单文件 · 展开 RouteSnapshot 全部字段 ▾';
      D.snapshotStatus.className = 'snapshot-status';
      setText(D.snapshotStatus, 'run_id 已变化，旧快照视图已收起（避免展示过期单文件）；请重新点击展开。');
      clear(D.snapshotView);
      S.snapFolds = [];
    }
    renderHeader(resp);
    renderAnalysis(resp);
    renderIngest(resp);
    // 后端 api.py A5 是 HTTP 200 成功响应，但会把「未接通/降级」留痕放进 errors[]。
    // 这与「请求失败」不同：如实展示留痕，但不把它误标为请求失败态（状态条仍显示成功+留痕）。
    // 真正的请求失败由 runOnce 的 .catch(showError) 走 error 态并显示错误码。
    if (Array.isArray(resp.errors) && resp.errors.length) {
      var codes = resp.errors.map(function (x) {
        return (typeof x === 'string') ? x : ('[' + (x.code || '?') + '] ' + (x.message || '') + (x.trace_id ? (' · ' + x.trace_id) : ''));
      });
      setStatus('ok', 'A5 成功（HTTP 200），后端降级留痕 errors[]（非静默跳过）：' + codes.join(' | '),
        (resp.errors[0] && resp.errors[0].code) || 'A5_DEGRADED');
    }
  }

  /* ---------------------------------------------------------------------
   * 12. 快照单文件：优先 GET /api/manifest/{run_id}，失败回退 ingest.llm_route
   *     显示完整 RouteSnapshot 全部字段；空占位字段显式标注。
   * ------------------------------------------------------------------- */
  function toggleSnapshot() {
    if (!D.snapshotView.hidden) {
      D.snapshotView.hidden = true;
      D.snapshotRoute.hidden = true;
      D.btnSnapshot.textContent = '快照单文件 · 展开 RouteSnapshot 全部字段 ▾';
      return;
    }
    D.snapshotView.hidden = false;
    D.btnSnapshot.textContent = '快照单文件 · 收起 RouteSnapshot ▴';
    var rid = (S.lastResp && S.lastResp.run_id) || '';
    if (!rid) {
      D.snapshotStatus.className = 'snapshot-status error';
      setText(D.snapshotStatus, '无 run_id（空占位）：请先跑一次 A5 再展开快照。');
      clear(D.snapshotView);
      D.snapshotRoute.hidden = true;
      return;
    }
    D.snapshotStatus.className = 'snapshot-status';
    setText(D.snapshotStatus, '正在读取 GET /api/manifest/' + rid + ' …');
    apiJSON(API.manifest + encodeURIComponent(rid)).then(function (m) {
      var checks = (m && m.checks) || {};
      D.snapshotStatus.className = 'snapshot-status';
      setText(D.snapshotStatus,
        '来源：GET /api/manifest/{run_id}（A8 单文件快照）。' +
        '校验 ingest_sha256_match=' + fmt(checks.ingest_sha256_match) +
        '，route_snapshot_match=' + fmt(checks.route_snapshot_match) +
        '。下方为 manifest 全文，含完整 RouteSnapshot；model/prompt/params 等本轮无真实来源的字段按要求留空，并显式标注「空占位」。');
      clear(D.snapshotView);
      var cap = el('div', 'json-note', 'manifest（单文件快照全文，真实 A8 响应）');
      D.snapshotView.appendChild(cap);
      var box = el('div', 'json-view-inner');
      D.snapshotView.appendChild(box);
      S.snapFolds = renderJSON(box, m).folds;
      // 滞回真实字段面板：manifest.route 是完整 RouteSnapshot（与 ingest.llm_route 逐字段相同）
      var mroute = (m && m.route) || (m && m.llm_route) || null;
      renderRoutePanel(D.snapshotRoute, mroute,
        '快照单文件 · RouteSnapshot 滞回状态（manifest.route 真实字段）');
    }, function (e) {
      // A8 未接通：回退展示 A5 响应内嵌 llm_route，如实标注，不静默、不编造
      var route = (S.lastResp && S.lastResp.ingest && S.lastResp.ingest.llm_route) ||
                  (S.lastResp && S.lastResp.analysis && S.lastResp.analysis.route) || null;
      D.snapshotStatus.className = 'snapshot-status error';
      setText(D.snapshotStatus,
        'A8 未接通：[' + (e.code || 'INTERNAL_ERROR') + '] ' + (e.message || '') +
        (e.trace_id ? (' · trace_id=' + e.trace_id) : '') +
        '。已回退展示 A5 响应内嵌的 llm_route（' +
        (route ? '存在；compact 档可能为精简字段' : '空占位，响应未含该字段') +
        '）。此为回退视图，不是 manifest 单文件；不静默、不编造。');
      clear(D.snapshotView);
      var cap = el('div', 'json-note', '回退视图 · A5 响应内嵌 llm_route（非 manifest 单文件）');
      D.snapshotView.appendChild(cap);
      var box = el('div', 'json-view-inner');
      D.snapshotView.appendChild(box);
      if (route) {
        S.snapFolds = renderJSON(box, route).folds;
      } else {
        box.appendChild(emptyInline('llm_route / route 均为空占位，A5 响应未提供 RouteSnapshot。'));
        S.snapFolds = [];
      }
      renderRoutePanel(D.snapshotRoute, route,
        '回退视图 · RouteSnapshot 滞回状态（A5 内嵌 llm_route 真实字段）');
    });
  }

  /* ---------------------------------------------------------------------
   * 13. 主流程：单次分析 / 双跑对拍 / 改参重跑
   * ------------------------------------------------------------------- */
  function setBusy(on, text) {
    S.busy = !!on;
    [D.btnAnalyze, D.btnReset, D.btnCompare, D.btnMutate, D.sampleSimple, D.sampleComplex].forEach(function (b) {
      b.disabled = !!on;
    });
    if (on) { setStatus('busy', text || '正在请求 POST /api/analyze …'); }
  }

  /* 对拍结论写入状态条时，若 renderAll 已展示后端降级留痕（statusCode 可见），
   * 把留痕错误码并入结论一起显示，避免「未接通，跳过」被对拍结论覆盖掉（不静默）。 */
  function setResultStatus(kind, text) {
    var degraded = !D.statusCode.hidden ? String(D.statusCode.textContent || '') : '';
    setStatus(kind,
      text + (degraded ? (' ｜ 后端降级留痕：[' + degraded + ']，详见左区「告警 / 降级 / 回放留痕」') : ''),
      degraded || '');
  }

  function runOnce(opts) {
    opts = opts || {};
    setBusy(true, opts.busyText || '正在 POST /api/analyze（上传样本 + 7 类参数快照）…');
    return postAnalyze().then(function (resp) {
      if (!opts.keepQuiet) {
        renderAll(resp);
        // renderAll 若已展示「成功 + 降级留痕」（带错误码徽章，statusCode 可见），
        // 不要用常规成功消息覆盖它，否则「未接通，跳过」留痕会一闪而过（不静默）。
        if (!D.statusBar.classList.contains('is-error') && D.statusCode.hidden) {
          setStatus('ok', 'A5 成功：左右两区由同一次响应驱动 · run_id=' +
            (resp.run_id || '空占位') + ' · external_request_count=' +
            ((resp.runtime && resp.runtime.external_request_count !== undefined) ? resp.runtime.external_request_count : '未提供'));
        }
      }
      return resp;
    }).catch(function (e) {
      showError(e, 'A5 分析失败');
      throw e;
    }).then(function (resp) {
      setBusy(false);
      return resp;
    }, function (e) {
      setBusy(false);
      throw e;
    });
  }

  /* 同参双跑对拍（03-phase2-spec.md §1.1(7) 补遗）：
   * 连续两次同参 A5。因 TierState 按 (source_sha, params_hash) 缓存且保留 k=2 滞回，
   * 复杂档两次会是 standard(hits=1) → pro(hits=2)，snapshot_version 变 → run_id 变。
   * 这是「改档必变」的正确行为，不是 JSON 不确定性缺陷。
   * 因此判定「逐字节一致」用剔除路由派生字段后的规范化投影（等效 compact 档口径），
   * 同时把两次真实的滞回演进与 run_id 变化作为「改档必变」的现场证据展示出来。
   */
  function doubleRun() {
    setBusy(true, '同参双跑对拍：第 1 / 2 次 A5 …');
    var r1, r2;
    return postAnalyze().then(function (a) {
      r1 = a;
      setStatus('busy', '同参双跑对拍：第 2 / 2 次 A5 …');
      return postAnalyze();
    }).then(function (b) {
      r2 = b;
      // ① 规范化投影（剔除路由派生字段）：这才是「解析结果确定性」的判据
      var p1 = projectionString(r1 && r1.ingest);
      var p2 = projectionString(r2 && r2.ingest);
      var same = (p1 === p2);
      S.baseline = p1;
      S.baselineNote = '基线：第 1 次规范化投影（长度 ' + p1.length + '）';

      // ② 两次真实路由事实与完整 ingest（用于说明「改档必变」，不作 bug 判定）
      var f1 = routeFacts(r1), f2 = routeFacts(r2);
      var full1 = stableStringify(r1 && r1.ingest);
      var full2 = stableStringify(r2 && r2.ingest);
      var tierChanged = String(f1.tier || '') !== String(f2.tier || '');
      var runChanged = String(f1.run_id || '') !== String(f2.run_id || '');
      var routeDrift = (full1 !== full2);

      var lines = [];
      lines.push(same ? '解析投影逐字节一致 ✓' : '解析投影不一致 ✗');
      lines.push('· ' + COMPARE_SCOPE_NOTE);
      lines.push('· ' + S.baselineNote);
      lines.push('· 第 2 次投影长度 ' + p2.length + ' · 预览 ' + preview(p2));
      lines.push('· 第 1 次：tier=' + (f1.tier || '空占位') + ' · hits=' + (f1.hits === undefined ? '空占位' : f1.hits) +
        ' · difficulty=' + (f1.difficulty === undefined ? '空占位' : f1.difficulty) +
        ' · run_id=' + (f1.run_id || '空占位'));
      lines.push('· 第 2 次：tier=' + (f2.tier || '空占位') + ' · hits=' + (f2.hits === undefined ? '空占位' : f2.hits) +
        ' · difficulty=' + (f2.difficulty === undefined ? '空占位' : f2.difficulty) +
        ' · run_id=' + (f2.run_id || '空占位'));
      if (tierChanged || runChanged) {
        lines.push('· 路由改档：' + (f1.tier || '空占位') + ' → ' + (f2.tier || '空占位') +
          '（连续命中 ' + (f1.hits === undefined ? '空占位' : f1.hits) + ' → ' +
          (f2.hits === undefined ? '空占位' : f2.hits) + '），run_id ' +
          (runChanged ? '随之变化' : '未变') + '。');
        lines.push('· 完整 ingest ' + (routeDrift ? '确有差异' : '无差异') +
          '：差异来自 llm_route / run_id / manifest 的滞回改档，属「改档必变」的正确行为，不是 JSON 不确定性缺陷。');
      }
      lines.push(same
        ? '结论：解析结果确定性成立（同参双跑解析投影逐字节一致）。'
        : '结论：解析投影不一致已如实显示；这与滞回改档无关，需检查解析结果是否混入观测字段。');
      lines.push('下一步：点「改参重跑」验证「改参必变」。');
      setCompare(same ? 'ok' : 'bad', lines);
      renderAll(r2);
      setResultStatus(same ? 'ok' : 'error',
        '同参双跑对拍完成：解析投影' + (same ? '逐字节一致' : '不一致') +
        '；路由快照' + (tierChanged ? ('由 ' + f1.tier + ' 改档为 ' + f2.tier) : '未改档') +
        '（改档必变，非缺陷）。结果见左侧「同参双跑对拍」面板。');
      setBusy(false);
      return { same: same, p1: p1, p2: p2, tierChanged: tierChanged };
    }).catch(function (e) {
      showError(e, '同参双跑失败');
      setBusy(false);
      throw e;
    });
  }

  /* 改参重跑：自动改一个参数（chunk_size_chars +1）后重跑，与基线比较显示「已变化」 */
  function mutateRun() {
    if (!S.baseline) {
      setCompare('bad', ['请先点「同参双跑对拍」建立基线，再验证「改参必变」。']);
      return Promise.resolve(null);
    }
    var old = parseInt(D.pChunkSize.value, 10) || 800;
    var neu = old + 1;
    D.pChunkSize.value = String(neu);
    setBusy(true, '改参重跑：chunk_size_chars ' + old + ' → ' + neu + '，重新 A5 …');
    return postAnalyze().then(function (resp) {
      // 与双跑同口径：规范化投影（剔除路由派生字段），避免把滞回改档误算成「参数生效」
      var now = projectionString(resp && resp.ingest);
      var changed = (now !== S.baseline);
      var f = routeFacts(resp);
      setCompare(changed ? 'ok' : 'bad', [
        changed ? '解析投影已变化 ✓' : '解析投影未变化 ✗（参数似乎未进入入库 JSON，请检查后端参数快照接线）',
        '· ' + COMPARE_SCOPE_NOTE,
        '· 改动：chunk_size_chars ' + old + ' → ' + neu + '（唯一改动的 7 类参数）',
        '· ' + S.baselineNote,
        '· 改参后投影长度 ' + now.length + ' · 预览 ' + preview(now),
        '· 本次路由：tier=' + (f.tier || '空占位') + ' · hits=' + (f.hits === undefined ? '空占位' : f.hits) +
          '（改参会换 (source_sha, params_hash) 的 LRU key，路由状态重新从 standard 计数，属正确行为）',
        changed ? '结论：G5「改参必变」成立。' : '结论：未变化已如实显示，未做任何掩盖。'
      ]);
      renderAll(resp);
      setResultStatus(changed ? 'ok' : 'error',
        '改参重跑完成：' + (changed ? '已变化' : '未变化') + '（结果见左侧对拍面板）');
      setBusy(false);
      return { changed: changed, now: now };
    }).catch(function (e) {
      showError(e, '改参重跑失败');
      setBusy(false);
      throw e;
    });
  }

  /* ---------------------------------------------------------------------
   * 14. 复制 / 折叠 / 档位切换 / 事件绑定
   * ------------------------------------------------------------------- */
  function copyIngest() {
    var text = S.lastIngest ? JSON.stringify(S.lastIngest, null, 2) : '';
    if (!text) {
      setStatus('error', '复制失败：当前无真实 ingest（空占位），请先跑一次 A5。', 'NO_INGEST');
      return;
    }
    function fallback() {
      try {
        var ta = el('textarea');
        ta.value = text;
        ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        var ok = document.execCommand('copy');
        document.body.removeChild(ta);
        setStatus(ok ? 'ok' : 'error', ok ? '已复制真实 IngestDocument 到剪贴板。' : '复制失败，请手动选择右侧 JSON。', ok ? '' : 'COPY_FAILED');
      } catch (ex) {
        showError(makeError('COPY_FAILED', ex.message || '剪贴板不可用', ''), '复制失败');
      }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        setStatus('ok', '已复制真实 IngestDocument 到剪贴板。');
      }, fallback);
    } else {
      fallback();
    }
  }

  function switchTier(tier) {
    S.tier = tier;
    // 切档时清除手动文件，避免拿上一档的本地文件当本档用（诚实性）
    S.manualFile = null;
    if (D.sampleFile) { D.sampleFile.value = ''; }
    D.sampleSimple.classList.toggle('is-active', tier === 'simple');
    D.sampleComplex.classList.toggle('is-active', tier === 'complex');
    D.replayBanner.hidden = (tier !== 'complex');
    updateSampleLabel();
    var label = tier === 'complex' ? '复杂档（complex.png，视觉结果为离线回放）' : '简单档（simple.pdf，有文本层）';
    runOnce({ busyText: '切换到' + label + '，重新 POST /api/analyze …' }).catch(function () { /* 错误已显示 */ });
  }

  function bindEvents() {
    D.paramsForm.addEventListener('submit', function (ev) {
      ev.preventDefault();
      runOnce().catch(function () { /* 错误已显示 */ });
    });
    D.btnReset.addEventListener('click', function () {
      writeFormParams(clone(DEFAULT_PARAMS));
      onParamChange();
      runOnce({ busyText: '已恢复默认参数，重新 POST /api/analyze …' }).catch(function () {});
    });
    [D.pStrategy, D.pConfidenceMin, D.pChunkSize, D.pChunkOverlap,
     D.pVisionRatio, D.pVisionSlots, D.pTierOverride, D.pRedact, D.pIngestFormat
    ].forEach(function (n) { n.addEventListener('change', onParamChange); });

    D.sampleSimple.addEventListener('click', function () { if (!S.busy) { switchTier('simple'); } });
    D.sampleComplex.addEventListener('click', function () { if (!S.busy) { switchTier('complex'); } });

    // 手动载入真实样本兜底：server.py 未挂载 samples/ 时，用户选本地已落盘的
    // simple.pdf / complex.png。字节真实、由后端 ParseRouter 真实解析，前端不伪造任何数据。
    D.sampleFile.addEventListener('change', function () {
      var f = D.sampleFile.files && D.sampleFile.files[0];
      if (!f) { return; }
      S.manualFile = f;
      // 按真实文件名推断档位，保证回放横幅与实际文件一致（不伪造、不猜测内容）
      S.tier = /complex|scan|\.png|\.jpe?g|\.webp|\.gif|\.bmp/i.test(f.name) ? 'complex' : 'simple';
      D.sampleSimple.classList.toggle('is-active', S.tier === 'simple');
      D.sampleComplex.classList.toggle('is-active', S.tier === 'complex');
      D.replayBanner.hidden = (S.tier !== 'complex');
      updateSampleLabel();
      runOnce({ busyText: '手动载入真实样本 ' + f.name + '，POST /api/analyze（字节真实，后端真实解析）…' })
        .catch(function () { /* 错误已显示 */ });
    });

    D.btnCopyJson.addEventListener('click', copyIngest);
    D.btnExpandAll.addEventListener('click', function () {
      applyFolds(S.jsonFolds, false); applyFolds(S.snapFolds, false);
    });
    D.btnCollapseAll.addEventListener('click', function () {
      applyFolds(S.jsonFolds, true); applyFolds(S.snapFolds, true);
    });
    D.btnSnapshot.addEventListener('click', toggleSnapshot);
    D.btnCompare.addEventListener('click', function () { if (!S.busy) { doubleRun().catch(function () {}); } });
    D.btnMutate.addEventListener('click', function () { if (!S.busy) { mutateRun(); } });

    // 「打开上线指导」：链接失效也不影响主流程（不改主流程、不拦截跳转）
    if (D.guideLink) {
      D.guideLink.setAttribute('rel', 'noopener noreferrer');
      D.guideLink.title = '指向 ../../docs/showcase/aliyun-guide.html；链接失效不影响本页分析主流程。';
    }
  }

  /* ---------------------------------------------------------------------
   * 15. 启动：并行拉 A2 / A4 / A10（任一失败不阻塞），随后自动跑一次简单档 A5
   *     离线 file:// 打开时会显示占位态 + 明确错误码，不白屏、不造假数据。
   * ------------------------------------------------------------------- */
  function nonFatal(label) {
    return function (e) {
      showError(e, label + ' 失败（非致命，主流程继续；以 A5 真实响应为准）');
      return null;
    };
  }
  function boot() {
    bindEvents();
    writeFormParams(clone(DEFAULT_PARAMS));
    updateStrategyWarning();
    setStatus('busy', '正在加载 GET /api/params、/api/strategies、/api/samples …');
    Promise.all([
      loadParams().catch(nonFatal('GET /api/params')),
      loadStrategies().catch(nonFatal('GET /api/strategies')),
      loadSamples()  // 内部已带错误码显示，不重复抛
    ]).then(function () {
      // 自动跑一次简单档，让页面一打开就有真实内容（离线时为占位态 + 错误码）
      return runOnce({ busyText: '初始演示：自动执行一次简单档 A5（真实响应）…' });
    }).catch(function () {
      // 错误已在 runOnce / showError 显示，保持占位态即可
    });
  }

  /* 供回传说明使用的兼容分支清单（不执行、不影响页面） */
  function collectCompatNotes() {
    return [
      '实查后端 server.py（集成裁决后）：先挂 /showcase/samples/<name> 再挂 /，样本静态路径候选 1 生效',
      '实查后端 api.py（集成裁决后）：A5 = file(File(None)) + params + as_of + replay + sample_id；无 file 时按白名单 sample_id 读真实样本，白名单值是真实文件名（simple.pdf / complex.png）',
      'A5 样本字节四来源：① 手动载入真实 File ② 多候选静态路径 ③ sample_id 白名单兜底（值=真实文件名）④ 全失败 → 显示后端错误码 + 可操作提示，绝不伪造字节',
      '静态路径候选：' + SAMPLE_PATHS.join(' / '),
      '滞回裁决 §1.1(7)：TierState 按 (source_sha, params_hash) 缓存，k=2 不变；连续两次复杂档 standard(hits=1)→pro(hits=2)，snapshot_version/run_id 变化属「改档必变」',
      '双跑对拍口径：projectionString 剔除 llm_route/run_id/manifest/route/snapshot_version/sha256 等路由派生字段后的规范化投影（等效 compact 档），改档差异不判为不一致 bug',
      '路由面板：左区 + 快照区共用 renderRoutePanel，显示 hysteresis.enter/exit/consecutive_hits/state/last_transition + difficulty/tier/trigger，standard+difficulty≥θ_enter 提示「再跑一次触发 Pro」，pro 提示「仅路由决策」',
      'FormData 字段：file / params(JSON 字符串) / sample_id / tier / replay；后端按契约取用、多余忽略；replay 同时按 complex 档与文件名给出',
      'as_of 不提交（后端固定 DEFAULT_AS_OF，保证双跑逐字节一致）',
      'llm_tier_override 空串转 null（契约 enum|null）',
      'GET /api/params 兼容 {params,params_hash,ranges,note} 与平铺；GET /api/samples 兼容 {samples:[...]} 与数组',
      'ocr_api 提示优先采用后端 A4 levels[].display 原文「未接通，跳过」，未取到 A4 时按 spec §4 显示并如实标注',
      '后端 A5 的 errors[] 是 HTTP 200 成功响应里的降级留痕（{code,message}），如实展示但不误标为请求失败',
      'analysis 结构兼容：优先 pages[]，回退 results[]+traces[] 自动组装；summary/budget/warnings 多来源',
      'RouteSnapshot 展开：优先 GET /api/manifest/{run_id}，失败回退 ingest.llm_route 并如实标注',
      '错误体统一 {code,message,trace_id}；非 JSON 错误页 / 网络失败也有明确 code',
      'runtime.external_request_count 缺失显示「未提供」，不编造 0'
    ];
  }
  // 暴露给调试（不依赖、不影响演示）
  window.__showcase = { state: S, compat: collectCompatNotes };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
