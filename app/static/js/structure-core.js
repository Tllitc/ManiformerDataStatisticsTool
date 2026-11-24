'use strict';

import { deviceKind } from './storage-core.js';
import { fmtHM } from './format.js';

/* 纯函数：扫描 episodes 按 task_uuid→group_id 两级聚合为采集结构层级。
 * bytes=Σdir_size_bytes；task.share 相对总量、group.share 相对所属 task；
 * 空 task_uuid/group_id 记 "(无)"；空 job_uuid 归 "(无)" job 节点但不计 job_count；
 * 空 device_id 不计入设备统计（与后端 by_group.devices 口径一致）；
 * job.start_time 取该 job 最早 create_time（原样保留后端值）。
 * 无扫描或无数据段时返回 null。 */
export function buildStructure(scan) {
  if (!scan || !(scan.episodes || []).length) return null;
  var tasks = {};
  var totalBytes = 0;
  scan.episodes.forEach(function (e) {
    var tKey = e.task_uuid || '(无)';
    var t = tasks[tKey];
    if (!t) t = tasks[tKey] = { episodes: 0, seconds: 0, bytes: 0, groups: {}, jobIds: {}, deviceIds: {} };
    var gKey = e.group_id || '(无)';
    var g = t.groups[gKey];
    if (!g) g = t.groups[gKey] = { episodes: 0, seconds: 0, bytes: 0, devices: {}, jobs: {}, jobIds: {} };
    var bytes = Number(e.dir_size_bytes) || 0;
    var seconds = Number(e.seconds) || 0;
    t.episodes += 1; t.seconds += seconds; t.bytes += bytes;
    g.episodes += 1; g.seconds += seconds; g.bytes += bytes;
    totalBytes += bytes;
    if (e.job_uuid) { t.jobIds[e.job_uuid] = true; g.jobIds[e.job_uuid] = true; }
    if (e.device_id) {
      t.deviceIds[e.device_id] = true;
      var d = g.devices[e.device_id];
      if (!d) {
        d = g.devices[e.device_id] = {
          device_id: e.device_id, aid: e.aid || '', kind: deviceKind(e.aid),
          episodes: 0, seconds: 0, bytes: 0
        };
      }
      d.episodes += 1; d.seconds += seconds; d.bytes += bytes;
    }
    var jKey = e.job_uuid || '(无)';
    var j = g.jobs[jKey];
    if (!j) j = g.jobs[jKey] = { job_uuid: jKey, episodes: 0, seconds: 0, bytes: 0, start_time: '' };
    j.episodes += 1; j.seconds += seconds; j.bytes += bytes;
    var ct = e.create_time || '';
    if (ct && (!j.start_time || ct < j.start_time)) j.start_time = ct;
  });

  var kindOrder = { head: 0, gripper: 1, other: 2 };
  var taskList = Object.keys(tasks).map(function (tKey) {
    var t = tasks[tKey];
    var groups = Object.keys(t.groups).map(function (gKey) {
      var g = t.groups[gKey];
      var head = 0, gripper = 0, other = 0;
      var devices = Object.keys(g.devices).map(function (id) {
        var d = g.devices[id];
        if (d.kind === 'head') head += 1;
        else if (d.kind === 'gripper') gripper += 1;
        else other += 1;
        return {
          device_id: d.device_id, aid: d.aid, kind: d.kind,
          episodes: d.episodes, seconds: d.seconds, hm: fmtHM(d.seconds), bytes: d.bytes
        };
      });
      devices.sort(function (a, b) {
        if (kindOrder[a.kind] !== kindOrder[b.kind]) return kindOrder[a.kind] - kindOrder[b.kind];
        return a.aid < b.aid ? -1 : (a.aid > b.aid ? 1 : 0);
      });
      var jobs = Object.keys(g.jobs).map(function (jKey) {
        var j = g.jobs[jKey];
        return {
          job_uuid: j.job_uuid, episodes: j.episodes, seconds: j.seconds,
          hm: fmtHM(j.seconds), bytes: j.bytes, start_time: j.start_time
        };
      });
      jobs.sort(function (a, b) {
        return a.start_time < b.start_time ? -1 : (a.start_time > b.start_time ? 1 : 0);
      });
      return {
        group_id: gKey,
        episodes: g.episodes, seconds: g.seconds, hm: fmtHM(g.seconds), bytes: g.bytes,
        share: t.bytes > 0 ? g.bytes / t.bytes * 100 : 0,
        job_count: Object.keys(g.jobIds).length,
        head: head, gripper: gripper, other: other,
        complete: head === 1 && gripper === 2,
        devices: devices, jobs: jobs
      };
    });
    groups.sort(function (a, b) {
      if (a.bytes !== b.bytes) return b.bytes - a.bytes;
      return a.group_id < b.group_id ? -1 : (a.group_id > b.group_id ? 1 : 0);
    });
    return {
      task_uuid: tKey,
      episodes: t.episodes, seconds: t.seconds, hm: fmtHM(t.seconds), bytes: t.bytes,
      share: totalBytes > 0 ? t.bytes / totalBytes * 100 : 0,
      group_count: Object.keys(t.groups).length,
      job_count: Object.keys(t.jobIds).length,
      device_count: Object.keys(t.deviceIds).length,
      groups: groups
    };
  });
  taskList.sort(function (a, b) {
    if (a.bytes !== b.bytes) return b.bytes - a.bytes;
    return a.task_uuid < b.task_uuid ? -1 : (a.task_uuid > b.task_uuid ? 1 : 0);
  });
  return { total_bytes: totalBytes, tasks: taskList };
}