'use strict';

import { state, GROUP_PAGE_SIZE } from '../state.js';
import { esc, fmtNum, fmtBytes } from '../format.js';
import { emptyHint, exportBar, tableHTML } from '../ui.js';

function $(sel) { return document.querySelector(sel); }

/* ---------------- 分组（分页，每页 GROUP_PAGE_SIZE 条） ---------------- */

/* 扫描结果对象变化（新扫描/重新加载）时重置页码；同数据内翻页保留页码 */
var lastScan = null;

export function renderGroups() {
  var p = $('#panel-groups');
  var s = state.scan;
  if (!s) { p.innerHTML = emptyHint(); return; }
  if (s !== lastScan) { state.groupsPage = 0; lastScan = s; }
  p.innerHTML = exportBar('groups', '按分组统计') + '<div id="group-table-area"></div>';
  updateGroupTable();
}

function updateGroupTable() {
  var area = $('#group-table-area');
  var s = state.scan;
  if (!area || !s) return;
  /* 聚合基于全量 episodes，不随分页变化 */
  var aidsByGroup = {};
  (s.episodes || []).forEach(function (e) {
    if (!e.aid) return;
    var gid = e.group_id || '(无)';
    var m = aidsByGroup[gid] || (aidsByGroup[gid] = {});
    m[e.aid] = 1;
  });
  var list = s.by_group || [];
  var total = list.length;
  var pages = Math.max(1, Math.ceil(total / GROUP_PAGE_SIZE));
  if (state.groupsPage >= pages) state.groupsPage = pages - 1;
  var page = state.groupsPage;
  var slice = list.slice(page * GROUP_PAGE_SIZE, (page + 1) * GROUP_PAGE_SIZE);
  var rows = slice.map(function (g) {
    var aids = Object.keys(aidsByGroup[g.group_id] || {}).sort();
    return [
      '<span class="mono uuid" title="' + esc(g.group_id) + '">' + esc(g.group_id) + '</span>',
      aids.length ? aids.map(function (a) {
        return '<span class="mono" title="' + esc(a) + '">' + esc(a) + '</span>';
      }).join('<br>') : '—',
      esc(g.hm) + ' <span class="muted">(' + fmtNum(g.seconds) + 's)</span>',
      fmtNum(g.episodes),
      fmtNum((g.devices || []).length),
      esc(fmtBytes(g.bytes)),
      esc(g.start_time || '—')
    ];
  });
  var html = tableHTML(['分组', '设备 AID', '时长', '段数', '设备数', '数据量', '开始时间'], rows);
  html += '<div class="pager">' +
    '<button id="group-prev" class="btn small"' + (page <= 0 ? ' disabled' : '') + '>上一页</button>' +
    '<span class="muted">共 ' + fmtNum(total) + ' 条 · 第 ' + (page + 1) + ' / ' + pages +
    ' 页（每页 ' + GROUP_PAGE_SIZE + ' 条）</span>' +
    '<button id="group-next" class="btn small"' + (page >= pages - 1 ? ' disabled' : '') + '>下一页</button>' +
    '</div>';
  area.innerHTML = html;
  $('#group-prev').addEventListener('click', function () {
    if (state.groupsPage > 0) { state.groupsPage--; updateGroupTable(); }
  });
  $('#group-next').addEventListener('click', function () {
    if (state.groupsPage < pages - 1) { state.groupsPage++; updateGroupTable(); }
  });
}
