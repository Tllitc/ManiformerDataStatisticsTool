'use strict';

import { state } from '../state.js';
import { esc, fmtBytes, fmtNum, fmtISO, trunc } from '../format.js';
import { emptyHint, sectionTitle, tableHTML, unsynced } from '../ui.js';
import { buildStructure } from '../structure-core.js';

function $(sel) { return document.querySelector(sel); }

/* ---------------- 采集结构（task→group→设备/录制段落 层级树，两级懒渲染） ---------------- */

var KIND_LABEL = { head: '头戴', gripper: '夹爪', other: '其他' };

/* 本次渲染的聚合结果缓存：懒展开时按 data-ti/data-gi 索引复用，避免重复聚合；
 * 每次 renderStructure 重建 DOM 时重新计算，已渲染标记（dataset.rendered）随 DOM 一并重置。 */
var structData = null;

function taskInfo(uuid) {
  var list = state.joined || [];
  for (var i = 0; i < list.length; i++) {
    if (list[i].task_uuid === uuid) return list[i];
  }
  return null;
}

/* 采集员：平台缓存命中显示姓名（collectors）；未同步时回退扫描 user_id（采集账号）。 */
function collectorHTML(info, userIds) {
  var collectors = (info && info.collectors) || [];
  if (collectors.length) {
    var names = collectors.join('、');
    return '<span class="struct-collector" title="' + esc(names) + '">采集员：' + esc(names) + '</span>';
  }
  if (userIds.length) {
    var ids = userIds.join('、');
    return '<span class="struct-collector mono" title="未同步平台姓名，显示采集账号 user_id：' + esc(ids) + '">采集员：' + esc(ids) + '</span>';
  }
  return unsynced();
}

function miniBar(share, colorIndex) {
  var w = Math.min(100, Math.max(0, share));
  return '<span class="struct-mini-bar" title="占比 ' + share.toFixed(1) + '%">' +
    '<span class="struct-mini-fill" style="width:' + w.toFixed(1) +
    '%;background:var(--bar-' + (colorIndex % 8) + ')"></span></span>' +
    '<span class="muted struct-share">' + share.toFixed(1) + '%</span>';
}

function durationCell(seconds, hm) {
  return esc(hm) + ' <span class="muted">(' + fmtNum(seconds) + 's)</span>';
}

/* 分组内部：设备表 + 录制段落表（分组 details 首次展开时才生成） */
function groupTablesHTML(g) {
  var html = '<div class="struct-subtitle">设备</div>' +
    tableHTML(['设备 AID', '类型', '数据段', '时长', '数据量'], g.devices.map(function (d) {
      return [
        '<span class="mono" title="' + esc(d.aid) + '">' + esc(d.aid || '—') + '</span>',
        esc(KIND_LABEL[d.kind] || '其他'),
        fmtNum(d.episodes),
        durationCell(d.seconds, d.hm),
        esc(fmtBytes(d.bytes))
      ];
    }));
  html += '<div class="struct-subtitle">录制段落</div>' +
    tableHTML(['job_uuid', '数据段', '时长', '数据量', '开始时间'], g.jobs.map(function (j) {
      return [
        '<span class="mono uuid" title="' + esc(j.job_uuid) + '">' + esc(trunc(j.job_uuid, 34)) + '</span>',
        fmtNum(j.episodes),
        durationCell(j.seconds, j.hm),
        esc(fmtBytes(j.bytes)),
        esc(fmtISO(j.start_time))
      ];
    }));
  return html;
}

/* 任务内部：分组 summary 列表（任务 details 首次展开时才生成），分组表格再懒一级 */
function taskChildrenHTML(t, ti) {
  var html = '';
  t.groups.forEach(function (g, gi) {
    var comp = g.head + ' 头戴 + ' + g.gripper + ' 夹爪' +
      (g.other > 0 ? ' + ' + g.other + ' 其他' : '');
    html += '<details class="struct-group" data-ti="' + ti + '" data-gi="' + gi + '"><summary>' +
      '<span class="mono uuid" title="' + esc(g.group_id) + '">' + esc(trunc(g.group_id, 34)) + '</span>' +
      '<span class="badge">' + esc(comp) + '</span>' +
      (g.complete ? '' : '<span class="badge badge-warn" title="期望 1 头戴 + 2 夹爪">不完整</span>') +
      '<span class="muted struct-agg">' + fmtNum(g.job_count) + ' 录制段 · ' +
      fmtNum(g.episodes) + ' 数据段</span>' +
      '<span class="struct-metric">' + durationCell(g.seconds, g.hm) + '</span>' +
      '<span class="struct-metric">' + esc(fmtBytes(g.bytes)) + '</span>' +
      miniBar(g.share, gi) +
      '</summary><div class="struct-tables"></div></details>';
  });
  return html;
}

/* 按 details 上的 data-ti/data-gi 从缓存取数，填充其占位容器；dataset.rendered 防重复渲染 */
function renderDetailsContent(el) {
  if (!structData || el.dataset.rendered) return;
  el.dataset.rendered = '1';
  var ti = Number(el.dataset.ti);
  var t = structData.tasks[ti];
  if (el.classList.contains('struct-task')) {
    el.querySelector('.struct-children').innerHTML = taskChildrenHTML(t, ti);
  } else {
    var g = t.groups[Number(el.dataset.gi)];
    el.querySelector('.struct-tables').innerHTML = groupTablesHTML(g);
  }
}

export function renderStructure() {
  var p = $('#panel-structure');
  var html = sectionTitle('采集结构') +
    '<div class="muted struct-note">按 任务 → 分组 → 设备 / 录制段落 层级聚合。' +
    'AID 以 ES 开头为头戴、GS 开头为夹爪；一套 = 1 头戴 + 2 夹爪；' +
    'job_uuid 为录制段落唯一标识，一个任务可含多个录制段落。</div>';
  structData = buildStructure(state.scan);
  var st = structData;
  if (!st) { p.innerHTML = html + emptyHint(); return; }

  var userIdsByTask = {};
  (state.scan.episodes || []).forEach(function (e) {
    if (!e.user_id) return;
    var tid = e.task_uuid || '(无)';
    var m = userIdsByTask[tid] || (userIdsByTask[tid] = {});
    m[e.user_id] = 1;
  });

  /* 初始 DOM 只含任务 summary，任务内部为占位容器，展开时才渲染分组列表 */
  html += '<div class="struct-tree">';
  st.tasks.forEach(function (t, ti) {
    var info = taskInfo(t.task_uuid);
    var name = (info && info.task_name) || '';
    var userIds = Object.keys(userIdsByTask[t.task_uuid] || {}).sort();
    html += '<details class="struct-task" data-ti="' + ti + '"' + (st.tasks.length === 1 ? ' open' : '') + '><summary>' +
      (name ? '<span class="struct-task-name">' + esc(name) + '</span>' : '') +
      '<span class="mono uuid" title="' + esc(t.task_uuid) + '">' + esc(trunc(t.task_uuid, 34)) + '</span>' +
      collectorHTML(info, userIds) +
      '<span class="muted struct-agg">' + fmtNum(t.group_count) + ' 分组 · ' +
      fmtNum(t.job_count) + ' 录制段 · ' + fmtNum(t.episodes) + ' 数据段</span>' +
      '<span class="struct-metric">' + durationCell(t.seconds, t.hm) + '</span>' +
      '<span class="struct-metric">' + esc(fmtBytes(t.bytes)) + '</span>' +
      miniBar(t.share, ti) +
      '</summary><div class="struct-children"></div></details>';
  });
  html += '</div>';
  p.innerHTML = html;

  /* toggle 事件不冒泡，在树容器上用捕获阶段做事件委托；每次重渲染 DOM 重建，监听随之更新 */
  var tree = p.querySelector('.struct-tree');
  tree.addEventListener('toggle', function (ev) {
    var el = ev.target;
    if (el.tagName === 'DETAILS' && el.open) renderDetailsContent(el);
  }, true);
  /* 单任务自动 open：初始 HTML 自带 open 属性不触发 toggle，直接渲染其分组列表 */
  tree.querySelectorAll('details.struct-task[open]').forEach(function (el) {
    renderDetailsContent(el);
  });
}