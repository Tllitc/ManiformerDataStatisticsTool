'use strict';

/* UMI 数据统计工具前端入口（原生 ES 模块，无外部依赖，离线可用）。
 * 字段名与后端 API 契约逐字对应，见 .trae/specs/build-umi-data-stats-gui/spec.md。
 */

import { state } from './state.js';
import { api, postJSON } from './api.js';
import { esc, fmtBytes, fmtNum, fmtEpoch, fmtISO } from './format.js';
import { toast } from './ui.js';
import { renderOverview } from './views/overview.js';
import { renderDevices } from './views/devices.js';
import { renderTasks } from './views/tasks.js';
import { renderGroups } from './views/groups.js';
import { renderStructure } from './views/structure.js';
import { renderDates } from './views/dates.js';
import { renderEpisodes } from './views/episodes.js';
import { renderAnomalies } from './views/anomalies.js';
import { renderStorage, updateStorageAlert } from './views/storage.js';

function $(sel) { return document.querySelector(sel); }

/* ---------------------------------------------------------------- 数据加载 */

function loadDrives() {
  return api('/api/drives').then(function (data) {
    state.drives = data.drives || [];
    var sel = $('#drive-select');
    var html = '<option value="">选择数据盘…</option>';
    state.drives.forEach(function (d) {
      var text = d.label + ' · 已用 ' + fmtBytes(d.used_bytes) + ' / ' + fmtBytes(d.total_bytes);
      if (d.is_data_drive) text += ' · 数据盘';
      html += '<option value="' + esc(d.path) + '">' + esc(text) + '</option>';
    });
    sel.innerHTML = html;
    var first = state.drives.find(function (d) { return d.is_data_drive; });
    if (first) sel.value = first.path;
    updateStorageAlert();
    if (state.activeTab === 'storage') renderStorage();
  }).catch(function (e) {
    $('#drive-select').innerHTML = '<option value="">数据盘加载失败</option>';
    toast('加载数据盘失败：' + e.message, 'error');
  });
}

function refreshAuth() {
  return api('/api/auth/status').then(function (data) {
    state.auth = data;
    renderAuth();
  }).catch(function (e) {
    state.auth = null;
    renderAuth();
    toast('获取登录状态失败：' + e.message, 'error');
  });
}

function refreshPlatform() {
  return api('/api/platform/status').then(function (data) {
    state.platform = data;
    renderCacheStatus();
  }).catch(function () { /* 静默：cache 状态非关键 */ });
}

function loadStats() {
  return api('/api/stats').then(function (data) {
    state.scan = data.scan || null;
    state.joined = data.joined || null;
    if (data.platform) {
      state.platform = data.platform;
      renderCacheStatus();
    }
    renderActivePanel();
  }).catch(function (e) {
    toast('加载统计失败：' + e.message, 'error');
  });
}

var VERSION_KEY = 'umi-stats-tool-version';

function loadVersion() {
  return api('/api/update/status').then(function (data) {
    var ver = data && data.current_version;
    if (!ver) return;
    $('#app-version').textContent = ' · v' + ver;
    var seen = localStorage.getItem(VERSION_KEY);
    if (seen && seen !== ver) toast('已自动更新到 v' + ver);
    localStorage.setItem(VERSION_KEY, ver);
  }).catch(function () { /* 静默：版本显示非关键 */ });
}

/* ---------------------------------------------------------------- 顶栏渲染 */

function renderAuth() {
  var area = $('#auth-area');
  var a = state.auth;
  if (a && a.logged_in) {
    var u = a.user || {};
    area.innerHTML =
      '<span class="auth-user" title="已登录">' + esc((u.tenant || '') + '/' + (u.name || '')) + '</span>' +
      '<span class="muted auth-expire">过期：' + esc(fmtEpoch(a.expires_at)) + '</span>' +
      '<button id="logout-btn" class="btn small">登出</button>';
    $('#logout-btn').addEventListener('click', onLogout);
  } else {
    var hint = a && a.expired ? '<span class="warn-text">登录已过期</span>' : '';
    area.innerHTML = hint + '<button id="login-btn" class="btn small primary">登录</button>';
    $('#login-btn').addEventListener('click', openLoginModal);
  }
}

function renderCacheStatus() {
  var el = $('#cache-status');
  var p = state.platform;
  if (p && p.cached) {
    el.textContent = '缓存 ' + fmtNum(p.submissions_count) + ' 条 · ' + fmtISO(p.synced_at);
    el.title = '平台数据缓存时间：' + (p.synced_at || '');
  } else {
    el.textContent = '平台数据未同步';
    el.title = '';
  }
  renderTaskCenterStatus();
}

function renderTaskCenterStatus() {
  var el = $('#task-center-status');
  var tc = state.platform && state.platform.task_center;
  if (!tc || !tc.cached) {
    el.textContent = '任务中心未同步';
    el.title = '';
    return;
  }
  var dates = tc.dates || [];
  el.textContent = '任务中心 ' + fmtNum(tc.jobs_count) + ' 作业 · ' + fmtNum(dates.length) +
    ' 个日期' + (tc.synced_at ? ' · ' + fmtISO(tc.synced_at) : '') +
    (tc.last_warning ? ' · 有警告' : '');
  var title = '已缓存日期：' + (dates.length ? dates.join('、') : '无');
  if (tc.last_warning) title += '\n最后警告：' + tc.last_warning;
  el.title = title;
}

function renderProgress() {
  var box = $('#scan-progress');
  var st = state.scanStatus || {};
  if (!st.state || st.state === 'idle') {
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  var fill = $('#progress-fill');
  var text = $('#progress-text');
  fill.className = 'progress-fill';
  if (st.state === 'running') {
    fill.classList.add('running');
    text.textContent = '扫描中… 已扫描 ' + fmtNum(st.scanned_dirs) + ' 个目录，发现 ' +
      fmtNum(st.episodes_found) + ' 个数据段';
    if (typeof st.progress_age_seconds === 'number' && st.progress_age_seconds > 10) {
      var warn = document.createElement('span');
      warn.className = 'warn-text';
      warn.textContent = ' · 已 ' + Math.round(st.progress_age_seconds) + 's 无进展，磁盘可能无响应';
      text.appendChild(warn);
    }
  } else if (st.state === 'done') {
    fill.classList.add('done');
    var doneText = '扫描完成：共 ' + fmtNum(st.scanned_dirs) + ' 个目录，' +
      fmtNum(st.episodes_found) + ' 个数据段' + (st.finished_at ? ' · ' + fmtISO(st.finished_at) : '');
    if (st.cache_stats) {
      doneText += ' · 增量复用 ' + fmtNum(st.cache_stats.reused) +
        ' · 重扫 ' + fmtNum(st.cache_stats.rescanned) + ' · 新增 ' + fmtNum(st.cache_stats.added);
    }
    text.textContent = doneText;
  } else if (st.state === 'error') {
    fill.classList.add('error');
    text.textContent = '扫描失败：' + (st.error || '未知错误');
  }
  var pathText = st.state === 'running' ? (st.detail || st.current_path || '') : (st.current_path || '');
  $('#progress-path').textContent = pathText;
  $('#progress-path').title = pathText;
}

/* ---------------------------------------------------------------- 扫描 */

function onScanClick() {
  var manual = $('#path-input').value.trim();
  var path = manual || $('#drive-select').value;
  if (!path) {
    toast('请选择数据盘或输入路径', 'error');
    return;
  }
  postJSON('/api/scan', { path: path, incremental: $('#incremental-toggle').checked }).then(function () {
    state.scanStatus = { state: 'running', scanned_dirs: 0, episodes_found: 0, current_path: path };
    renderProgress();
    startPolling();
  }).catch(function (e) {
    toast('无法启动扫描：' + e.message, 'error');
  });
}

function startPolling() {
  stopPolling();
  $('#scan-btn').disabled = true;
  state.pollTimer = setInterval(pollScanStatus, 1000);
  pollScanStatus();
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  $('#scan-btn').disabled = false;
}

function pollScanStatus() {
  if (state.polling) return;
  state.polling = true;
  var controller = new AbortController();
  var timeout = setTimeout(function () { controller.abort(); }, 8000);
  api('/api/scan/status', { signal: controller.signal }).then(function (st) {
    state.scanStatus = st;
    renderProgress();
    if (st.state === 'done') {
      stopPolling();
      toast('扫描完成');
      return loadStats();
    }
    if (st.state === 'error') {
      stopPolling();
      toast('扫描失败：' + (st.error || '未知错误'), 'error');
    }
  }).catch(function () { /* 瞬时失败/超时继续轮询 */ }).then(function () {
    clearTimeout(timeout);
    state.polling = false;
  });
}

/* ---------------------------------------------------------------- 登录 / 同步 */

function openLoginModal() {
  $('#login-error').classList.add('hidden');
  $('#login-modal').classList.remove('hidden');
  $('#login-password').focus();
}

function closeLoginModal() {
  $('#login-modal').classList.add('hidden');
  $('#login-password').value = '';
}

function onLoginSubmit(ev) {
  ev.preventDefault();
  var btn = $('#login-submit');
  btn.disabled = true;
  $('#login-error').classList.add('hidden');
  postJSON('/api/login', {
    tenant: $('#login-tenant').value.trim() || 'mego',
    username: $('#login-username').value.trim(),
    password: $('#login-password').value
  }).then(function (r) {
    closeLoginModal();
    var u = r.user || {};
    toast('登录成功：' + (u.tenant || '') + '/' + (u.name || ''));
    return Promise.all([refreshAuth(), refreshPlatform(), loadStats()]);
  }).catch(function (e) {
    var el = $('#login-error');
    el.textContent = e.message;
    el.classList.remove('hidden');
  }).then(function () {
    btn.disabled = false;
  });
}

function onLogout() {
  postJSON('/api/logout').then(function () {
    toast('已登出');
    return Promise.all([refreshAuth(), refreshPlatform()]);
  }).catch(function (e) {
    toast('登出失败：' + e.message, 'error');
  });
}

function onSync() {
  if (!state.auth || !state.auth.logged_in) {
    toast('请先登录平台', 'error');
    openLoginModal();
    return;
  }
  var btn = $('#sync-btn');
  btn.disabled = true;
  btn.textContent = '同步中…';
  postJSON('/api/platform/sync').then(function (r) {
    toast('同步完成：' + fmtNum(r.submissions) + ' 条任务记录');
    return Promise.all([refreshPlatform(), loadStats()]);
  }).catch(function (e) {
    toast('同步失败：' + e.message, 'error');
  }).then(function () {
    btn.disabled = false;
    btn.textContent = '同步平台数据';
  });
}

/* 任务中心同步可能耗时数分钟：fetch 不设超时；后端恒返回 HTTP 200，ok:false 由 api() 抛错 */
function onSyncTaskCenter() {
  if (!state.auth || !state.auth.logged_in) {
    toast('请先登录平台', 'error');
    openLoginModal();
    return;
  }
  var btn = $('#sync-task-center-btn');
  btn.disabled = true;
  btn.textContent = '同步中…';
  postJSON('/api/platform/sync-task-center').then(function (r) {
    toast('任务中心同步完成：' + fmtNum((r.synced_dates || []).length) + ' 个日期 · ' +
      fmtNum(r.jobs_indexed) + ' 个作业索引 · ' + fmtNum(r.enriched) + ' 个作业补全');
    var warnings = r.warnings || [];
    if (warnings.length) {
      toast('同步警告 ' + fmtNum(warnings.length) + ' 条：' + warnings[0], 'error');
    }
    return Promise.all([refreshPlatform(), loadStats()]);
  }).catch(function (e) {
    toast('任务中心同步失败：' + e.message, 'error');
  }).then(function () {
    btn.disabled = false;
    btn.textContent = '同步任务中心';
  });
}

/* ---------------------------------------------------------------- Tab 切换 */

var RENDERERS = {
  overview: renderOverview,
  devices: renderDevices,
  tasks: renderTasks,
  groups: renderGroups,
  structure: renderStructure,
  dates: renderDates,
  episodes: renderEpisodes,
  anomalies: renderAnomalies,
  storage: renderStorage
};

function renderActivePanel() {
  var fn = RENDERERS[state.activeTab];
  if (fn) fn();
}

function switchTab(tab) {
  if (!RENDERERS[tab]) return;
  state.activeTab = tab;
  document.querySelectorAll('#tabs .tab').forEach(function (btn) {
    var active = btn.getAttribute('data-tab') === tab;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('.panel').forEach(function (panel) {
    panel.classList.toggle('active', panel.id === 'panel-' + tab);
  });
  renderActivePanel();
}

/* ---------------------------------------------------------------- 初始化 */

var INCREMENTAL_KEY = 'umi-stats-incremental';

function bindEvents() {
  $('#scan-btn').addEventListener('click', onScanClick);
  var incToggle = $('#incremental-toggle');
  incToggle.checked = localStorage.getItem(INCREMENTAL_KEY) !== '0';
  incToggle.addEventListener('change', function () {
    localStorage.setItem(INCREMENTAL_KEY, incToggle.checked ? '1' : '0');
  });
  $('#path-input').addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter') onScanClick();
  });
  $('#sync-btn').addEventListener('click', onSync);
  $('#sync-task-center-btn').addEventListener('click', onSyncTaskCenter);
  $('#tabs').addEventListener('click', function (ev) {
    var btn = ev.target.closest('.tab');
    if (btn) switchTab(btn.getAttribute('data-tab'));
  });
  $('#storage-alert').addEventListener('click', function () { switchTab('storage'); });
  $('#login-form').addEventListener('submit', onLoginSubmit);
  $('#login-cancel').addEventListener('click', closeLoginModal);
  $('#login-modal').addEventListener('click', function (ev) {
    if (ev.target === this) closeLoginModal();
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') closeLoginModal();
  });
}

function init() {
  bindEvents();
  renderAuth();
  renderCacheStatus();
  renderActivePanel();
  Promise.all([loadDrives(), refreshAuth(), refreshPlatform(), loadStats(), loadVersion()]).then(function () {
    return api('/api/scan/status').catch(function () { return null; });
  }).then(function (st) {
    if (st && st.state === 'running') {
      state.scanStatus = st;
      renderProgress();
      startPolling();
    } else if (st && (st.state === 'done' || st.state === 'error')) {
      state.scanStatus = st;
      renderProgress();
    }
  });
}

document.addEventListener('DOMContentLoaded', init);
