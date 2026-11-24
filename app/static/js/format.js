'use strict';

/* ---------------------------------------------------------------- 工具 */

export function esc(v) {
  var map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(v == null ? '' : v).replace(/[&<>"']/g, function (ch) { return map[ch]; });
}

export function fmtBytes(n) {
  n = Number(n) || 0;
  var units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  var i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  var num = i === 0 ? String(n) : n.toFixed(n >= 100 ? 0 : (n >= 10 ? 1 : 2));
  return num + ' ' + units[i];
}

export function fmtHM(seconds) {
  var total = Math.round(Number(seconds) || 0);
  var h = Math.floor(total / 3600);
  var m = Math.floor((total % 3600) / 60);
  if (h > 0 && m > 0) return h + 'h' + m + 'm';
  if (h > 0) return h + 'h';
  return m + 'm';
}

export function fmtNum(n) {
  return (Number(n) || 0).toLocaleString('zh-CN');
}

export function fmtEpoch(sec) {
  if (!sec) return '—';
  var d = new Date(Number(sec) * 1000);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString('zh-CN', { hour12: false });
}

export function fmtISO(s) {
  if (!s) return '—';
  var d = new Date(s);
  if (isNaN(d.getTime())) return String(s);
  return d.toLocaleString('zh-CN', { hour12: false });
}

export function trunc(s, n) {
  s = String(s == null ? '' : s);
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

export function joinArr(arr) {
  return Array.isArray(arr) && arr.length ? arr.join('、') : '—';
}
