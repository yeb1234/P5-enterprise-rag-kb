/* 外观引擎：主题色(data-theme) × 明暗(data-mode) 两个正交维度，自由组合。
 * - 三份独立 localStorage：kb_lang(i18n) / kb_theme / kb_mode，互不覆盖；
 * - head 同步执行，在 CSS 应用前写好属性 → 首屏无主题闪烁；
 * - kb-boot class 在首帧禁用过渡，DOMContentLoaded 后移除，避免切换时大面积动画。
 */
(function () {
  'use strict';

  var THEMES = ['indigo', 'teal', 'violet', 'amber', 'rose', 'graphite'];
  var THEME_LABELS = {
    indigo: { zh: '靛蓝', en: 'Indigo' }, teal: { zh: '青碧', en: 'Teal' },
    violet: { zh: '紫罗兰', en: 'Violet' }, amber: { zh: '琥珀', en: 'Amber' },
    rose: { zh: '玫红', en: 'Rose' }, graphite: { zh: '石墨', en: 'Graphite' }
  };
  var K_THEME = 'kb_theme';
  var K_MODE = 'kb_mode';
  var booted = false;

  function read(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } }
  function write(k, v) { try { localStorage.setItem(k, v); } catch (_) { } }
  function validTheme(v) { return THEMES.indexOf(v) >= 0 ? v : 'indigo'; }
  function validMode(v) { return v === 'dark' ? 'dark' : 'light'; }

  function getTheme() { return validTheme(read(K_THEME, 'indigo')); }
  function getMode() { return validMode(read(K_MODE, 'light')); }

  function apply() {
    var h = document.documentElement;
    var theme = getTheme(), mode = getMode();
    h.setAttribute('data-theme', theme);
    h.setAttribute('data-mode', mode);
    h.style.colorScheme = mode;
    return { theme: theme, mode: mode };
  }

  function emit(type) {
    try { document.dispatchEvent(new CustomEvent(type, { detail: apply() })); } catch (_) { }
  }

  function setTheme(v) { write(K_THEME, validTheme(v)); emit('kb:themechange'); }
  function setMode(v) { write(K_MODE, validMode(v)); emit('kb:themechange'); }
  function toggleMode() { setMode(getMode() === 'dark' ? 'light' : 'dark'); }

  window.KBTheme = {
    themes: THEMES, labels: THEME_LABELS,
    getTheme: getTheme, getMode: getMode,
    setTheme: setTheme, setMode: setMode, toggleMode: toggleMode,
    apply: apply
  };

  // head 同步执行：CSS 应用前已就位
  document.documentElement.classList.add('kb-boot');
  apply();

  function finishBoot() {
    if (booted) return;
    booted = true;
    // 两帧后恢复过渡，避开首屏绘制
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { document.documentElement.classList.remove('kb-boot'); });
    });
    emit('kb:themechange');
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', finishBoot);
  else finishBoot();
})();
