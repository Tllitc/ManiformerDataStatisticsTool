'use strict';

import { esc, trunc, fmtHM } from './format.js';

/* ---------------------------------------------------------------- SVG 图表 */

/* 水平条形图：items 最多取前 20 条 */
export function hbarSVG(items, labelFn, valueFn, fmtFn) {
  if (!items.length) return '<div class="muted chart-empty">暂无数据</div>';
  var rows = items.slice(0, 20);
  var rowH = 30, labelW = 230, valueW = 100, chartW = 780;
  var barW = chartW - labelW - valueW - 20;
  var max = 1;
  rows.forEach(function (it) { max = Math.max(max, valueFn(it)); });
  var height = rows.length * rowH + 10;
  var svg = '<svg class="chart" viewBox="0 0 ' + chartW + ' ' + height + '" role="img">';
  rows.forEach(function (it, i) {
    var y = i * rowH + 5;
    var v = valueFn(it);
    var w = Math.max(2, Math.round(v / max * barW));
    svg += '<text x="' + (labelW - 10) + '" y="' + (y + 16) + '" text-anchor="end" class="chart-label">' +
      esc(trunc(labelFn(it), 28)) + '</text>';
    svg += '<rect x="' + labelW + '" y="' + (y + 3) + '" width="' + w + '" height="17" rx="3" class="chart-bar">' +
      '<title>' + esc(labelFn(it) + '：' + fmtFn(v)) + '</title></rect>';
    svg += '<text x="' + (labelW + w + 8) + '" y="' + (y + 16) + '" class="chart-value">' +
      esc(fmtFn(v)) + '</text>';
  });
  if (items.length > 20) {
    svg += '<text x="' + labelW + '" y="' + (height - 2) + '" class="chart-axis">仅展示前 20 项，共 ' +
      items.length + ' 项</text>';
  }
  return svg + '</svg>';
}

/* 按日期垂直柱状图 */
export function dateChartSVG(byDate) {
  if (!byDate.length) return '<div class="muted chart-empty">暂无数据</div>';
  var W = 780, H = 250, padL = 56, padR = 12, padT = 16, padB = 42;
  var plotW = W - padL - padR, plotH = H - padT - padB;
  var max = 1;
  byDate.forEach(function (d) { max = Math.max(max, d.seconds); });
  var n = byDate.length;
  var slot = plotW / n;
  var bw = Math.min(46, slot * 0.62);
  var labelEvery = Math.max(1, Math.ceil(n / 12));
  var svg = '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '" role="img">';
  for (var g = 0; g <= 4; g++) {
    var gy = padT + plotH - plotH * g / 4;
    svg += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy + '" class="chart-grid"/>';
    svg += '<text x="' + (padL - 8) + '" y="' + (gy + 4) + '" text-anchor="end" class="chart-axis">' +
      esc(fmtHM(max * g / 4)) + '</text>';
  }
  byDate.forEach(function (d, i) {
    var h = Math.max(2, d.seconds / max * plotH);
    var x = padL + i * slot + (slot - bw) / 2;
    var y = padT + plotH - h;
    svg += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) +
      '" height="' + h.toFixed(1) + '" rx="2" class="chart-bar">' +
      '<title>' + esc(d.date + '：' + d.hm + ' / ' + d.episodes + ' 段') + '</title></rect>';
    if (i % labelEvery === 0) {
      svg += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - padB + 18) +
        '" text-anchor="middle" class="chart-axis">' + esc(String(d.date).slice(5)) + '</text>';
    }
  });
  return svg + '</svg>';
}
