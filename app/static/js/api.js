'use strict';

/* ---------------------------------------------------------------- API 封装 */

export function api(path, options) {
  return fetch(path, options).catch(function () {
    throw new Error('无法连接本地服务，请确认后端已启动');
  }).then(function (resp) {
    return resp.json().catch(function () { return null; }).then(function (data) {
      if (!resp.ok) {
        throw new Error(data && data.error ? data.error : '请求失败（HTTP ' + resp.status + '）');
      }
      if (data && data.ok === false) {
        throw new Error(data.error || '操作失败');
      }
      return data;
    });
  });
}

export function postJSON(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
}
