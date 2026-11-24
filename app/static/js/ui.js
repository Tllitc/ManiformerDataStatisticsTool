'use strict';

import { esc } from './format.js';

function $(sel) { return document.querySelector(sel); }

export function unsynced() { return '<span class="badge badge-warn">未同步</span>'; }

export function toast(msg, kind) {
  var box = document.createElement('div');
  box.className = 'toast ' + (kind || 'info');
  box.textContent = msg;
  $('#toast-container').appendChild(box);
  setTimeout(function () {
    box.classList.add('fade');
    setTimeout(function () { box.remove(); }, 400);
  }, 3500);
}

export function emptyHint() {
  return '<div class="empty-state">请先扫描数据盘</div>';
}

export function exportBar(type, title) {
  return '<div class="toolbar">' +
    '<h2 class="toolbar-title">' + esc(title) + '</h2>' +
    '<div class="toolbar-actions">' +
    '<a class="btn small" href="/api/export?type=' + type + '&format=csv" download>导出CSV</a>' +
    '<a class="btn small" href="/api/export?type=' + type + '&format=json" download>导出JSON</a>' +
    '</div></div>';
}

export function sectionTitle(title) {
  return '<div class="toolbar"><h2 class="toolbar-title">' + esc(title) + '</h2></div>';
}

/* headers: 字符串数组；rows: 每行为已转义的 HTML 单元格数组 */
export function tableHTML(headers, rows) {
  var h = '<div class="table-wrap"><table><thead><tr>';
  headers.forEach(function (x) { h += '<th>' + esc(x) + '</th>'; });
  h += '</tr></thead><tbody>';
  if (!rows.length) {
    h += '<tr><td colspan="' + headers.length + '" class="muted center">暂无数据</td></tr>';
  }
  rows.forEach(function (r) {
    h += '<tr>';
    r.forEach(function (c) { h += '<td>' + c + '</td>'; });
    h += '</tr>';
  });
  return h + '</tbody></table></div>';
}

export function countAnomalies(an) {
  an = an || {};
  var keys = ['missing_duration', 'malformed_meta', 'unreadable', 'unreadable_dirs',
    'missing_aid', 'too_deep'];
  var n = 0;
  keys.forEach(function (k) { n += (an[k] || []).length; });
  var nonUmi = an.non_umi || {};
  Object.keys(nonUmi).forEach(function (k) { n += Number(nonUmi[k]) || 0; });
  n += Number(an.nested_skipped) || 0;
  return n;
}
