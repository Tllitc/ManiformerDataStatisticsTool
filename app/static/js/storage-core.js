'use strict';

import { fmtHM } from './format.js';

/* ---------------------------------------------------------------- 存储预警口径 */

/* 口径（十进制）：头戴 23GB/H + 夹爪 27GB/H = 50GB/H，每套每天采集 4H。
 * critical：usage≥90% 或剩余天数<1；warning：usage≥80% 或剩余天数<3；取更严。 */
export var STORAGE_BYTES_PER_HOUR = 50e9;
export var STORAGE_HOURS_PER_DAY = 4;
export var STORAGE_DEVICE_KEY = 'umi-storage-device-count';

export function storageDeviceCount() {
  var n = parseInt(localStorage.getItem(STORAGE_DEVICE_KEY), 10);
  return isFinite(n) && n >= 1 ? n : 10;
}

/* 纯函数：单盘折算与预警等级。n 非法或 ≤0 按 1 处理。 */
export function storageMetrics(drive, n) {
  n = Number(n);
  if (!isFinite(n) || n <= 0) n = 1;
  var total = Number(drive.total_bytes) || 0;
  var used = Number(drive.used_bytes) || 0;
  var free = Number(drive.free_bytes) || 0;
  var capacityHours = total / STORAGE_BYTES_PER_HOUR;
  var remainingHours = free / STORAGE_BYTES_PER_HOUR;
  var remainingDays = remainingHours / (STORAGE_HOURS_PER_DAY * n);
  var usage = total > 0 ? used / total * 100 : 0;
  var level = 'normal';
  if (usage >= 90 || remainingDays < 1) level = 'critical';
  else if (usage >= 80 || remainingDays < 3) level = 'warning';
  return {
    capacity_hours: capacityHours,
    remaining_hours: remainingHours,
    set_days_capacity: capacityHours / STORAGE_HOURS_PER_DAY,
    remaining_days: remainingDays,
    usage_percent: usage,
    level: level
  };
}

/* 设备类型按 AID 前缀判定（纯函数）：ES→head（头戴）、GS→gripper（夹爪）、
 * 其余（含空/非字符串）→other。trim 后按大写前缀匹配。 */
export function deviceKind(aid) {
  if (typeof aid !== 'string' || !aid.trim()) return 'other';
  var s = aid.trim().toUpperCase();
  if (s.indexOf('ES') === 0) return 'head';
  if (s.indexOf('GS') === 0) return 'gripper';
  return 'other';
}

/* 扫描结果按 group_id / task_uuid 双维度汇总（纯函数）。
 * 返回 {by_group, by_task, total_bytes}；无扫描或无数据段时返回 null。
 * 口径：bytes=Σdir_size_bytes；gb_per_hour=Σfile_size/Σseconds*3600/1e9（seconds 为 0 记 0）；
 * share=bytes 占总 bytes 百分比；空 group_id/task_uuid 记 "(无)"；jobs=唯一非空 job_uuid 数。
 * group 行另含 head/gripper/other（按 deviceKind 分类的唯一 device_id 数）、
 * task_uuids（唯一非空 task_uuid 数组）、complete（head==1 且 gripper==2，即一套 1 头戴 + 2 夹爪）；
 * task 行另含 groups（唯一 group_id 数）、devices（唯一 device_id 数）。
 * 均按 bytes 降序、同值按 id 升序。 */
export function scanStorageBreakdown(scan) {
  if (!scan || !(scan.episodes || []).length) return null;
  var totalBytes = 0;
  var groupMap = {};
  var taskMap = {};
  scan.episodes.forEach(function (e) {
    var bytes = Number(e.dir_size_bytes) || 0;
    var seconds = Number(e.seconds) || 0;
    var fileSize = Number(e.file_size) || 0;
    totalBytes += bytes;
    var gid = e.group_id || '(无)';
    var tid = e.task_uuid || '(无)';
    var g = groupMap[gid];
    if (!g) {
      g = groupMap[gid] = {
        episodes: 0, seconds: 0, bytes: 0, file_size: 0,
        jobs: {}, task_uuids: {}, head: {}, gripper: {}, other: {}
      };
    }
    g.episodes += 1;
    g.seconds += seconds;
    g.bytes += bytes;
    g.file_size += fileSize;
    if (e.job_uuid) g.jobs[e.job_uuid] = 1;
    if (e.task_uuid) g.task_uuids[e.task_uuid] = 1;
    g[deviceKind(e.aid)][e.device_id] = 1;
    var t = taskMap[tid];
    if (!t) {
      t = taskMap[tid] = {
        episodes: 0, seconds: 0, bytes: 0, file_size: 0,
        jobs: {}, groups: {}, devices: {}
      };
    }
    t.episodes += 1;
    t.seconds += seconds;
    t.bytes += bytes;
    t.file_size += fileSize;
    if (e.job_uuid) t.jobs[e.job_uuid] = 1;
    t.groups[gid] = 1;
    t.devices[e.device_id] = 1;
  });
  var byGroup = Object.keys(groupMap).map(function (gid) {
    var g = groupMap[gid];
    var head = Object.keys(g.head).length;
    var gripper = Object.keys(g.gripper).length;
    return {
      group_id: gid,
      episodes: g.episodes,
      seconds: g.seconds,
      hm: fmtHM(g.seconds),
      bytes: g.bytes,
      share: totalBytes > 0 ? g.bytes / totalBytes * 100 : 0,
      gb_per_hour: g.seconds > 0 ? g.file_size / g.seconds * 3600 / 1e9 : 0,
      head: head,
      gripper: gripper,
      other: Object.keys(g.other).length,
      jobs: Object.keys(g.jobs).length,
      task_uuids: Object.keys(g.task_uuids),
      complete: head === 1 && gripper === 2
    };
  });
  byGroup.sort(function (a, b) {
    return b.bytes - a.bytes || (a.group_id < b.group_id ? -1 : (a.group_id > b.group_id ? 1 : 0));
  });
  var byTask = Object.keys(taskMap).map(function (tid) {
    var t = taskMap[tid];
    return {
      task_uuid: tid,
      episodes: t.episodes,
      seconds: t.seconds,
      hm: fmtHM(t.seconds),
      bytes: t.bytes,
      share: totalBytes > 0 ? t.bytes / totalBytes * 100 : 0,
      gb_per_hour: t.seconds > 0 ? t.file_size / t.seconds * 3600 / 1e9 : 0,
      jobs: Object.keys(t.jobs).length,
      groups: Object.keys(t.groups).length,
      devices: Object.keys(t.devices).length
    };
  });
  byTask.sort(function (a, b) {
    return b.bytes - a.bytes || (a.task_uuid < b.task_uuid ? -1 : (a.task_uuid > b.task_uuid ? 1 : 0));
  });
  return { by_group: byGroup, by_task: byTask, total_bytes: totalBytes };
}
