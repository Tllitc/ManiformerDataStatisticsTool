'use strict';

/* ---------------------------------------------------------------- 状态 */

export var state = {
  drives: [],
  auth: null,          // /api/auth/status
  platform: null,      // /api/platform/status
  scan: null,          // 扫描结果 dict（/api/stats.scan）
  joined: null,        // /api/stats.joined（任务 + 平台字段）
  scanStatus: { state: 'idle', scanned_dirs: 0, episodes_found: 0, current_path: '' },
  activeTab: 'overview',
  pollTimer: null,
  polling: false,
  episodeFilters: { device: '', task: '', date: '', search: '' },
  episodePage: 0,
  groupsPage: 0
};

export var EPISODE_PAGE_SIZE = 100;
export var GROUP_PAGE_SIZE = 100;
