'use strict';

import { state, EPISODE_PAGE_SIZE } from '../state.js';
import { esc, fmtNum, fmtBytes } from '../format.js';
import { emptyHint, exportBar, tableHTML } from '../ui.js';

function $(sel) { return document.querySelector(sel); }

/* ---------------- 明细（筛选 + 分页） ---------------- */

function episodeDate(e) {
  var keys = ['clip_start_time_ts', 'clip_start_time'];
  for (var i = 0; i < keys.length; i++) {
    var ts = e[keys[i]];
    if (typeof ts !== 'number' || !(ts > 0)) continue;
    if (ts > 1e14) ts /= 1e9;
    else if (ts > 1e11) ts /= 1e3;
    var d = new Date(ts * 1000);
    if (!isNaN(d.getTime())) {
      var mm = String(d.getMonth() + 1).padStart(2, '0');
      var dd = String(d.getDate()).padStart(2, '0');
      return d.getFullYear() + '-' + mm + '-' + dd;
    }
  }
  return 'unknown';
}

function filteredEpisodes() {
  var f = state.episodeFilters;
  var q = f.search.trim().toLowerCase();
  return (state.scan.episodes || []).filter(function (e) {
    if (f.device && e.device_id !== f.device) return false;
    if (f.task && (e.task_uuid || '') !== f.task) return false;
    if (f.date && episodeDate(e) !== f.date) return false;
    if (q && String(e.path || '').toLowerCase().indexOf(q) === -1) return false;
    return true;
  });
}

export function renderEpisodes() {
  var p = $('#panel-episodes');
  var s = state.scan;
  if (!s) { p.innerHTML = emptyHint(); return; }
  var eps = s.episodes || [];
  var devices = [], tasks = [], dateSet = {};
  eps.forEach(function (e) {
    if (e.device_id && devices.indexOf(e.device_id) === -1) devices.push(e.device_id);
    var t = e.task_uuid || '';
    if (tasks.indexOf(t) === -1) tasks.push(t);
    dateSet[episodeDate(e)] = true;
  });
  devices.sort(); tasks.sort();
  var dates = Object.keys(dateSet).sort();
  var f = state.episodeFilters;

  function opts(values, current, allLabel) {
    var h = '<option value="">' + esc(allLabel) + '</option>';
    values.forEach(function (v) {
      h += '<option value="' + esc(v) + '"' + (v === current ? ' selected' : '') + '>' +
        esc(v || '(空)') + '</option>';
    });
    return h;
  }

  var html = exportBar('episodes', '数据段明细') +
    '<div class="filters">' +
    '<select id="ep-filter-device" title="按设备筛选">' + opts(devices, f.device, '全部设备') + '</select>' +
    '<select id="ep-filter-task" title="按任务筛选">' + opts(tasks, f.task, '全部任务') + '</select>' +
    '<select id="ep-filter-date" title="按日期筛选">' + opts(dates, f.date, '全部日期') + '</select>' +
    '<input id="ep-search" type="text" placeholder="搜索路径…" value="' + esc(f.search) + '">' +
    '</div>' +
    '<div id="ep-table-area"></div>';
  p.innerHTML = html;

  $('#ep-filter-device').addEventListener('change', function () {
    state.episodeFilters.device = this.value;
    state.episodePage = 0;
    updateEpisodeTable();
  });
  $('#ep-filter-task').addEventListener('change', function () {
    state.episodeFilters.task = this.value;
    state.episodePage = 0;
    updateEpisodeTable();
  });
  $('#ep-filter-date').addEventListener('change', function () {
    state.episodeFilters.date = this.value;
    state.episodePage = 0;
    updateEpisodeTable();
  });
  $('#ep-search').addEventListener('input', function () {
    state.episodeFilters.search = this.value;
    state.episodePage = 0;
    updateEpisodeTable();
  });
  updateEpisodeTable();
}

function updateEpisodeTable() {
  var area = $('#ep-table-area');
  if (!area || !state.scan) return;
  var list = filteredEpisodes();
  var total = list.length;
  var pages = Math.max(1, Math.ceil(total / EPISODE_PAGE_SIZE));
  if (state.episodePage >= pages) state.episodePage = pages - 1;
  var page = state.episodePage;
  var slice = list.slice(page * EPISODE_PAGE_SIZE, (page + 1) * EPISODE_PAGE_SIZE);
  var rows = slice.map(function (e) {
    var badge = e.validate_ok
      ? '<span class="badge badge-ok">正常</span>'
      : '<span class="badge badge-warn">异常</span>';
    return [
      '<span class="mono path" title="' + esc(e.path) + '">' + esc(e.path) + '</span>',
      '<span class="mono">' + esc(e.device_id || '—') + '</span>',
      esc(e.hm) + ' <span class="muted">(' + fmtNum(e.seconds) + 's)</span>',
      esc(fmtBytes(e.dir_size_bytes)),
      badge
    ];
  });
  var html = tableHTML(['路径', '设备', '时长', '大小', '校验'], rows);
  html += '<div class="pager">' +
    '<button id="ep-prev" class="btn small"' + (page <= 0 ? ' disabled' : '') + '>上一页</button>' +
    '<span class="muted">共 ' + fmtNum(total) + ' 条 · 第 ' + (page + 1) + ' / ' + pages +
    ' 页（每页 ' + EPISODE_PAGE_SIZE + ' 条）</span>' +
    '<button id="ep-next" class="btn small"' + (page >= pages - 1 ? ' disabled' : '') + '>下一页</button>' +
    '</div>';
  area.innerHTML = html;
  $('#ep-prev').addEventListener('click', function () {
    if (state.episodePage > 0) { state.episodePage--; updateEpisodeTable(); }
  });
  $('#ep-next').addEventListener('click', function () {
    if (state.episodePage < pages - 1) { state.episodePage++; updateEpisodeTable(); }
  });
}