'use strict';

import { state } from '../state.js';
import { esc, fmtNum, fmtHM, trunc } from '../format.js';
import { emptyHint, exportBar, tableHTML, unsynced } from '../ui.js';

function $(sel) { return document.querySelector(sel); }

/* 任务中心命中行的云端回传状态徽章；match_source=task_center 且 cloud_status 缺失时按未知处理 */
function cloudBadge(t) {
  if (t.match_source !== 'task_center') return '';
  switch (t.cloud_status) {
    case 'not_uploaded':
      return ' <span class="badge badge-cloud-warn">云端未回传</span>';
    case 'registered':
      return ' <span class="badge badge-cloud-ok">云端已登记</span>';
    default:
      return ' <span class="badge">云端状态未知</span>';
  }
}

export function renderTasks() {
  var p = $('#panel-tasks');
  var s = state.scan;
  if (!s) { p.innerHTML = emptyHint(); return; }
  var list = state.joined || s.by_task || [];
  var rows = list.map(function (t) {
    var collectors = t.collectors || [];
    var deviceTypes = t.device_type_names || [];
    var expect = t.expect_duration_seconds;
    return [
      (t.task_name ? esc(t.task_name) : unsynced()) + cloudBadge(t),
      '<span class="mono uuid" title="' + esc(t.task_uuid) + '">' + esc(trunc(t.task_uuid || '—', 34)) + '</span>',
      collectors.length ? esc(collectors.join('、')) : unsynced(),
      t.project_name ? esc(t.project_name) : unsynced(),
      deviceTypes.length ? esc(deviceTypes.join(' + ')) : unsynced(),
      fmtNum(t.episodes),
      esc(t.hm) + ' <span class="muted">(' + fmtNum(t.seconds) + 's)</span>',
      fmtNum((t.devices || []).length),
      fmtNum(t.groups),
      expect != null ? fmtNum(expect) + ' 秒 (' + esc(fmtHM(expect)) + ')' : unsynced()
    ];
  });
  p.innerHTML = exportBar('tasks', '按任务统计（关联平台详情）') +
    tableHTML(['任务名', 'task_uuid', '采集员', '项目', '设备类型', '段数', '时长', '设备数', '分组数', '期望时长'], rows);
}