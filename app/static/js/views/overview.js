'use strict';

import { state } from '../state.js';
import { esc, fmtBytes, fmtNum, fmtHM, fmtISO } from '../format.js';
import { emptyHint, sectionTitle, countAnomalies } from '../ui.js';
import { hbarSVG } from '../charts.js';

function $(sel) { return document.querySelector(sel); }

export function renderOverview() {
  var p = $('#panel-overview');
  var s = state.scan;
  if (!s) { p.innerHTML = emptyHint(); return; }
  var an = s.anomalies || {};
  var anomalyCount = countAnomalies(an);
  var byDate = s.by_date || [];
  var firstDate = byDate.length ? byDate[0].date : '—';
  var lastDate = byDate.length ? byDate[byDate.length - 1].date : '—';
  var cards = [
    { label: '总时长', value: esc(s.total_duration_hm || fmtHM(s.total_duration_seconds)),
      sub: fmtNum(s.total_duration_seconds) + ' 秒' },
    { label: '数据段数', value: fmtNum(s.umi_episodes), sub: 'umi 数据段' },
    { label: '设备数', value: fmtNum(s.devices), sub: '唯一 device_id' },
    { label: '任务数', value: fmtNum(s.tasks), sub: '唯一 task_uuid' },
    { label: '分组数', value: fmtNum(s.groups), sub: '唯一 group_id' },
    { label: '数据量', value: esc(fmtBytes(s.total_file_size_bytes)),
      sub: fmtNum(s.total_file_size_bytes) + ' 字节' },
    { label: '异常总数', value: fmtNum(anomalyCount),
      sub: anomalyCount > 0 ? '详见「异常」页' : '无异常', warn: anomalyCount > 0 },
    { label: '采集日期范围', value: esc(firstDate) + ' ~ ' + esc(lastDate),
      sub: fmtNum(byDate.length) + ' 个采集日' }
  ];
  var html = sectionTitle('概览') + '<div class="cards">';
  cards.forEach(function (c) {
    html += '<div class="card' + (c.warn ? ' card-warn' : '') + '">' +
      '<div class="card-label">' + esc(c.label) + '</div>' +
      '<div class="card-value">' + c.value + '</div>' +
      '<div class="card-sub">' + esc(c.sub) + '</div></div>';
  });
  html += '</div>';
  if (s.total_is_reliable === false) {
    html += '<div class="warn-banner">存在读取异常，总数可能不完整</div>';
  }
  html += '<div class="meta-line">扫描根目录：<span class="mono">' + esc(s.root) + '</span>' +
    ' · 扫描时间：' + esc(fmtISO(s.scanned_at)) + '</div>';
  html += '<div class="chart-card"><h3>按设备时长</h3>' +
    hbarSVG(s.by_device || [], function (d) { return d.device_id; },
      function (d) { return d.seconds; }, fmtHM) + '</div>';
  p.innerHTML = html;
}