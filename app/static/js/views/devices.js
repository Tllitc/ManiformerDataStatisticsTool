'use strict';

import { state } from '../state.js';
import { esc, fmtNum, fmtHM, joinArr } from '../format.js';
import { emptyHint, exportBar, tableHTML } from '../ui.js';
import { hbarSVG } from '../charts.js';

function $(sel) { return document.querySelector(sel); }

export function renderDevices() {
  var p = $('#panel-devices');
  var s = state.scan;
  if (!s) { p.innerHTML = emptyHint(); return; }
  var rows = (s.by_device || []).map(function (d) {
    return [
      '<span class="mono">' + esc(d.device_id) + '</span>',
      esc(d.hm) + ' <span class="muted">(' + fmtNum(d.seconds) + 's)</span>',
      fmtNum(d.episodes),
      esc(joinArr(d.task_types)),
      esc(joinArr(d.roles)),
      esc(d.first_time || '—') + ' ~ ' + esc(d.last_time || '—')
    ];
  });
  var html = exportBar('devices', '按设备统计') +
    tableHTML(['设备', '时长', '段数', '任务类型', '角色', '时间范围'], rows);
  html += '<div class="chart-card"><h3>设备时长对比</h3>' +
    hbarSVG(s.by_device || [], function (d) { return d.device_id; },
      function (d) { return d.seconds; }, fmtHM) + '</div>';
  p.innerHTML = html;
}