'use strict';

import { state } from '../state.js';
import { esc, fmtNum } from '../format.js';
import { emptyHint, exportBar, tableHTML, countAnomalies } from '../ui.js';

function $(sel) { return document.querySelector(sel); }

/* ---------------- 异常（scanner 8 分类） ---------------- */

/* 路径类分区最多渲染前 200 条（大数据量下防卡死）；计数徽标与导出仍为全量 */
var PATH_LIMIT = 200;

function truncateHint(total) {
  return '<div class="muted struct-note">仅显示前 ' + PATH_LIMIT + ' 条，共 ' +
    fmtNum(total) + ' 条，完整清单可导出</div>';
}

function anomalySection(title, count, inner) {
  return '<div class="anomaly-section' + (count > 0 ? ' has-items' : '') + '">' +
    '<h3>' + esc(title) + ' <span class="badge' + (count > 0 ? ' badge-warn' : '') + '">' +
    fmtNum(count) + '</span></h3>' +
    (count > 0 ? inner : '<div class="muted">无</div>') + '</div>';
}

function pathReasonTable(items) {
  items = items || [];
  var rows = items.slice(0, PATH_LIMIT).map(function (it) {
    return ['<span class="mono path" title="' + esc(it.path) + '">' + esc(it.path) + '</span>',
      esc(it.reason == null ? '' : it.reason)];
  });
  var html = tableHTML(['路径', '原因'], rows);
  if (items.length > PATH_LIMIT) html += truncateHint(items.length);
  return html;
}

function pathListTable(items) {
  items = items || [];
  var rows = items.slice(0, PATH_LIMIT).map(function (p) {
    return ['<span class="mono path" title="' + esc(p) + '">' + esc(p) + '</span>'];
  });
  var html = tableHTML(['路径'], rows);
  if (items.length > PATH_LIMIT) html += truncateHint(items.length);
  return html;
}

function missingAidTable(items) {
  items = items || [];
  var rows = items.slice(0, PATH_LIMIT).map(function (it) {
    return ['<span class="mono path" title="' + esc(it.path) + '">' + esc(it.path) + '</span>',
      fmtNum(it.seconds)];
  });
  var html = tableHTML(['路径', '时长（秒）'], rows);
  if (items.length > PATH_LIMIT) html += truncateHint(items.length);
  return html;
}

export function renderAnomalies() {
  var p = $('#panel-anomalies');
  var s = state.scan;
  if (!s) { p.innerHTML = emptyHint(); return; }
  var an = s.anomalies || {};
  var nonUmi = an.non_umi || {};
  var nonUmiCount = 0;
  Object.keys(nonUmi).forEach(function (k) { nonUmiCount += Number(nonUmi[k]) || 0; });
  var nested = Number(an.nested_skipped) || 0;

  var html = exportBar('anomalies', '异常清单（共 ' + fmtNum(countAnomalies(an)) + ' 项）');

  html += anomalySection('无有效时长（missing_duration）', (an.missing_duration || []).length,
    pathReasonTable(an.missing_duration));
  html += anomalySection('meta 损坏（malformed_meta）', (an.malformed_meta || []).length,
    pathReasonTable(an.malformed_meta));
  html += anomalySection('读取错误（unreadable）', (an.unreadable || []).length,
    pathListTable(an.unreadable));
  html += anomalySection('目录读取错误（unreadable_dirs）', (an.unreadable_dirs || []).length,
    pathListTable(an.unreadable_dirs));
  html += anomalySection('层级过深（too_deep）', (an.too_deep || []).length,
    pathListTable(an.too_deep));
  html += anomalySection('缺少 AID（missing_aid）', (an.missing_aid || []).length,
    missingAidTable(an.missing_aid));
  html += anomalySection('非 umi 数据（non_umi）', nonUmiCount,
    tableHTML(['类型', '数量'], Object.keys(nonUmi).map(function (k) {
      return [esc(k), fmtNum(nonUmi[k])];
    })));
  html += anomalySection('嵌套跳过（nested_skipped）', nested,
    '<div class="muted">共跳过 ' + fmtNum(nested) + ' 个嵌套数据段（不计入统计）</div>');
  p.innerHTML = html;
}