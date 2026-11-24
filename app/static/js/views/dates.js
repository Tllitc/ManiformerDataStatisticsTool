'use strict';

import { state } from '../state.js';
import { esc, fmtNum } from '../format.js';
import { emptyHint, exportBar, tableHTML } from '../ui.js';
import { dateChartSVG } from '../charts.js';

function $(sel) { return document.querySelector(sel); }

export function renderDates() {
  var p = $('#panel-dates');
  var s = state.scan;
  if (!s) { p.innerHTML = emptyHint(); return; }
  var byDate = s.by_date || [];
  var html = exportBar('dates', '按日期统计') +
    '<div class="chart-card"><h3>每日采集时长（hm）</h3>' + dateChartSVG(byDate) + '</div>';
  var rows = byDate.map(function (d) {
    return [esc(d.date), esc(d.hm) + ' <span class="muted">(' + fmtNum(d.seconds) + 's)</span>',
      fmtNum(d.episodes)];
  });
  html += tableHTML(['日期', '时长', '段数'], rows);
  p.innerHTML = html;
}