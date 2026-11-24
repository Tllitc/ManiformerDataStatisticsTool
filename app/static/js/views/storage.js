'use strict';

import { state } from '../state.js';
import { esc, fmtBytes, fmtNum, trunc } from '../format.js';
import { sectionTitle, tableHTML } from '../ui.js';
import {
  STORAGE_DEVICE_KEY,
  storageDeviceCount,
  storageMetrics,
  scanStorageBreakdown
} from '../storage-core.js';

function $(sel) { return document.querySelector(sel); }

/* ---------------- 存储预警与可视化 ---------------- */

function onStorageNChange() {
  var input = $('#storage-n');
  var n = parseInt(input.value, 10);
  if (!isFinite(n) || n < 1) n = 10;
  input.value = String(n);
  localStorage.setItem(STORAGE_DEVICE_KEY, String(n));
  renderStorage();
  updateStorageAlert();
}

/* 占比条：label=截断 id（title 全文），颜色按行循环 var(--bar-0..7)。 */
function storageBars(rows, idOf) {
  var html = '<div class="storage-bars">';
  rows.forEach(function (r, i) {
    var id = idOf(r);
    html += '<div class="storage-bar-row">' +
      '<span class="storage-bar-label mono" title="' + esc(id) + '">' + esc(trunc(id, 14)) + '</span>' +
      '<div class="storage-bar-track"><div class="storage-bar-fill" style="width:' + r.share.toFixed(1) +
      '%;background:var(--bar-' + (i % 8) + ')"></div></div>' +
      '<span class="storage-bar-value">' + r.share.toFixed(1) + '% · ' + esc(fmtBytes(r.bytes)) + '</span>' +
      '</div>';
  });
  return html + '</div>';
}

export function renderStorage() {
  var p = $('#panel-storage');
  var n = storageDeviceCount();
  var html = sectionTitle('存储');
  html += '<div class="storage-n-row">' +
    '<label for="storage-n">设备套数 N</label>' +
    '<input id="storage-n" type="number" min="1" step="1" value="' + n + '">' +
    '<span class="muted">每套每天采集 4H · 一套（1 头戴 ES + 2 夹爪 GS）≈50 GB/H · 调整后即时重算并记住</span>' +
    '</div>';

  html += '<div class="drive-cards">';
  if (!state.drives.length) {
    html += '<div class="muted">暂无候选盘</div>';
  }
  state.drives.forEach(function (d) {
    var m = storageMetrics(d, n);
    var levelText = m.level === 'critical' ? '紧急' : (m.level === 'warning' ? '预警' : '正常');
    var barWidth = Math.min(100, Math.max(0, m.usage_percent));
    html += '<div class="drive-card level-' + m.level + '" data-path="' + esc(d.path) + '">' +
      '<div class="drive-card-head">' +
      '<span class="drive-name">' + esc(d.label) + '</span>' +
      (d.is_data_drive ? '<span class="badge badge-data">数据盘</span>' : '') +
      '<span class="badge level-badge level-' + m.level + '">' + levelText + '</span>' +
      '</div>' +
      '<div class="mono muted drive-path" title="' + esc(d.path) + '">' + esc(d.path) + '</div>' +
      '<div class="usage-bar"><div class="usage-fill level-' + m.level +
      '" style="width:' + barWidth.toFixed(1) + '%"></div></div>' +
      '<div class="usage-text">' + m.usage_percent.toFixed(1) + '% · 已用 ' +
      esc(fmtBytes(d.used_bytes)) + ' / 共 ' + esc(fmtBytes(d.total_bytes)) + '</div>' +
      '<div class="drive-metrics">' +
      '<div class="drive-metric"><div class="card-label">总容量可录</div>' +
      '<div class="drive-metric-value metric-capacity-hours">' + m.capacity_hours.toFixed(1) + ' H</div></div>' +
      '<div class="drive-metric"><div class="card-label">剩余可录</div>' +
      '<div class="drive-metric-value metric-remaining-hours">' + m.remaining_hours.toFixed(1) + ' H</div></div>' +
      '<div class="drive-metric"><div class="card-label">等效套·天容量</div>' +
      '<div class="drive-metric-value metric-set-days">' + m.set_days_capacity.toFixed(1) + ' 套·天</div></div>' +
      '<div class="drive-metric"><div class="card-label">剩余天数（N=' + n + '）</div>' +
      '<div class="drive-metric-value metric-remaining-days">' + m.remaining_days.toFixed(1) + ' 天</div></div>' +
      '</div></div>';
  });
  html += '</div>';

  html += '<div class="chart-card"><h3>硬盘发放参考</h3>' +
    tableHTML(['标称容量', '可录时长', '等效套·天'], [
      ['2TB', '40H', '10 套·天'],
      ['4TB', '80H', '20 套·天'],
      ['8TB', '160H', '40 套·天']
    ]) +
    '<div class="muted storage-note">口径：头戴 23 GB/H + 夹爪 27 GB/H = 50 GB/H（十进制 1GB=10^9 字节），每套每天采集 4H；' +
    '等效套·天 = 可录制时长 ÷ 4H。剩余天数按当前设备套数 N 计算。</div>' +
    '</div>';

  var bd = scanStorageBreakdown(state.scan);
  html += '<div class="chart-card"><h3>存储构成 · 按分组（group）</h3>';
  if (!bd) {
    html += '<div class="muted chart-empty">暂无扫描数据</div>';
  } else {
    html += storageBars(bd.by_group, function (r) { return r.group_id; });
    html += '<div class="storage-table-scroll">' +
      tableHTML(['分组', '设备构成', '录制段数', '数据段', '时长', '数据量', '占比', '实测 GB/H'],
        bd.by_group.map(function (g) {
          var comp = g.head + ' 头戴 + ' + g.gripper + ' 夹爪';
          if (g.other > 0) comp += ' + ' + g.other + ' 其他';
          if (!g.complete) comp += ' <span class="badge badge-warn" title="期望 1 头戴 + 2 夹爪">不完整</span>';
          return [
            '<span class="mono uuid" title="' + esc(g.group_id) + '">' + esc(trunc(g.group_id, 34)) + '</span>',
            comp,
            fmtNum(g.jobs),
            fmtNum(g.episodes),
            esc(g.hm) + ' <span class="muted">(' + fmtNum(g.seconds) + 's)</span>',
            esc(fmtBytes(g.bytes)),
            g.share.toFixed(1) + '%',
            g.gb_per_hour.toFixed(1)
          ];
        })) + '</div>';
  }
  html += '</div>';

  html += '<div class="chart-card"><h3>存储构成 · 按任务（task）</h3>';
  if (!bd) {
    html += '<div class="muted chart-empty">暂无扫描数据</div>';
  } else {
    var nameByUuid = {};
    (state.joined || []).forEach(function (t) {
      if (t.task_uuid && t.task_name) nameByUuid[t.task_uuid] = t.task_name;
    });
    html += storageBars(bd.by_task, function (r) { return r.task_uuid; });
    html += '<div class="storage-table-scroll">' +
      tableHTML(['任务', '录制段数', '分组数', '数据段', '时长', '数据量', '占比', '实测 GB/H'],
        bd.by_task.map(function (t) {
          var cell = '<span class="mono uuid" title="' + esc(t.task_uuid) + '">' +
            esc(trunc(t.task_uuid, 34)) + '</span>';
          var name = nameByUuid[t.task_uuid];
          if (name) cell = esc(name) + ' ' + cell;
          return [
            cell,
            fmtNum(t.jobs),
            fmtNum(t.groups),
            fmtNum(t.episodes),
            esc(t.hm) + ' <span class="muted">(' + fmtNum(t.seconds) + 's)</span>',
            esc(fmtBytes(t.bytes)),
            t.share.toFixed(1) + '%',
            t.gb_per_hour.toFixed(1)
          ];
        })) + '</div>';
  }
  html += '</div>';
  p.innerHTML = html;
  $('#storage-n').addEventListener('change', onStorageNChange);
}

/* 顶栏徽标：取所有数据盘的最高预警等级；无数据盘或全部正常时隐藏。 */
export function updateStorageAlert() {
  var el = $('#storage-alert');
  var n = storageDeviceCount();
  var rank = { normal: 0, warning: 1, critical: 2 };
  var worst = 'normal';
  var hasData = false;
  state.drives.forEach(function (d) {
    if (!d.is_data_drive) return;
    hasData = true;
    var level = storageMetrics(d, n).level;
    if (rank[level] > rank[worst]) worst = level;
  });
  if (!hasData || worst === 'normal') {
    el.className = 'hidden';
    el.textContent = '';
    el.title = '';
    return;
  }
  el.className = 'storage-alert ' + worst;
  el.textContent = worst === 'critical' ? '存储紧急' : '存储预警';
  el.title = '点击查看存储详情';
}