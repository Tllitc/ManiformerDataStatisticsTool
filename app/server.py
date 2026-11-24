# -*- coding: utf-8 -*-
"""UMI 数据统计工具本地 HTTP 后端（127.0.0.1:8765）。

路由契约见 .trae/specs/build-umi-data-stats-gui/spec.md「后端 API 契约」。
扫描在后台线程执行（全局单任务锁），结果持久化到 ~/.umi-stats-tool/last_scan.json。
密码只经 POST body 传给 platform_api.login，绝不写入日志。
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlparse

import platform_api
import scanner
import stats
import updater

STATE_DIR = os.path.expanduser("~/.umi-stats-tool")
LAST_SCAN_PATH = os.path.join(STATE_DIR, "last_scan.json")
SCAN_CACHE_PATH = os.path.join(STATE_DIR, "scan_cache.json")
SCAN_CACHE_VERSION = 1
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# 扫描状态与最近结果（均由 _scan_lock 保护）
_scan_lock = threading.Lock()
_scan_state = {
    "state": "idle",
    "scanned_dirs": 0,
    "episodes_found": 0,
    "current_path": "",
    "detail": "",
}
_last_result = None
# 进度心跳（单调时钟）与扫描代际号（均由 _scan_lock 保护）：
# 代际在 start_scan 与看门狗触发时递增，旧线程的写入按代际校验全部丢弃。
_last_progress_mono = 0.0
_scan_generation = 0

# 自动更新状态（由 _update_lock 保护），state ∈ idle/checking/updating/failed
_update_lock = threading.Lock()
_update_state = {
    "current_version": updater.read_local_version(),
    "last_check_at": None,
    "latest_version": None,
    "state": "idle",
}

DEFAULT_UPDATE_INTERVAL = 3600

_PSEUDO_FS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "ramfs", "cgroup", "cgroup2",
    "overlay", "squashfs", "autofs", "mqueue", "hugetlbfs", "debugfs", "tracefs",
    "securityfs", "pstore", "bpf", "configfs", "fusectl", "binfmt_misc", "nsfs",
    "efivarfs", "rpc_pipefs",
}

_STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 数据盘发现

def _unescape_mount(text):
    """/proc/mounts 中空格等字符以 \\040 形式转义。"""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def _is_data_drive(mount_point):
    try:
        if os.path.isfile(os.path.join(mount_point, ".tf2ssd-cards.json")):
            return True
        with os.scandir(mount_point) as it:
            for entry in it:
                if entry.name.startswith("tf-") and entry.is_dir(follow_symlinks=False):
                    return True
    except OSError:
        pass
    return False


def list_drives():
    drives = []
    seen = set()
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        lines = []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point = _unescape_mount(parts[1])
        fs_type = parts[2]
        if fs_type in _PSEUDO_FS:
            continue
        if not (mount_point.startswith("/media/") or mount_point.startswith("/mnt/")):
            continue
        if mount_point in seen:
            continue
        seen.add(mount_point)
        try:
            st = os.statvfs(mount_point)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        drives.append({
            "path": mount_point,
            "label": os.path.basename(mount_point.rstrip("/")) or mount_point,
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": st.f_bavail * st.f_frsize,
            "is_data_drive": _is_data_drive(mount_point),
        })
    drives.sort(key=lambda d: (not d["is_data_drive"], d["path"]))
    return {"drives": drives}


# ---------------------------------------------------------------- 扫描任务

def _root_key(path):
    """缓存 root 键归一化，与 scanner.scan 的 root 处理一致。"""
    key = os.path.abspath(path)
    if len(key) > 1:
        key = key.rstrip(os.sep) or os.sep
    return key


def _load_scan_cache():
    """读 scan_cache.json 的 roots；version 不符/损坏/结构不符一律视为空缓存。"""
    try:
        with open(SCAN_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if (isinstance(data, dict) and data.get("version") == SCAN_CACHE_VERSION
                and isinstance(data.get("roots"), dict)):
            return data["roots"]
    except Exception:
        pass
    return {}


def _scan_worker(path, gen, incremental):
    global _last_result

    def progress(scanned_dirs, episodes_found, current_path, detail=""):
        global _last_progress_mono
        with _scan_lock:
            if gen != _scan_generation:
                return
            _last_progress_mono = time.monotonic()
            _scan_state["last_progress_at"] = _now_iso()
            _scan_state["scanned_dirs"] = scanned_dirs
            _scan_state["episodes_found"] = episodes_found
            _scan_state["current_path"] = current_path
            _scan_state["detail"] = detail

    try:
        roots = _load_scan_cache()
        root_key = _root_key(path)
        cache = roots.get(root_key, {}) if incremental else {}
        scan_data = scanner.scan(path, progress=progress, cache=cache)
        dirs_done = scan_data["scanned_dirs"]
        found = len(scan_data["episodes"])
        progress(dirs_done, found, path, "汇总统计中…")
        scan_mode = "incremental" if incremental else "full"
        result = stats.build_result(path, scan_data, scan_mode=scan_mode)
        progress(dirs_done, found, path, "保存结果中…")
        os.makedirs(STATE_DIR, exist_ok=True)
        # tmp 写入在锁外；tmp 名带代际号，避免僵尸线程误删新扫描的 tmp。
        last_tmp = "%s.tmp%d" % (LAST_SCAN_PATH, gen)
        cache_tmp = "%s.tmp%d" % (SCAN_CACHE_PATH, gen)
        with open(last_tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        new_roots = dict(roots)
        new_roots[root_key] = scan_data["segment_cache"]
        with open(cache_tmp, "w", encoding="utf-8") as f:
            json.dump({"version": SCAN_CACHE_VERSION, "roots": new_roots},
                      f, ensure_ascii=False)
        with _scan_lock:
            if gen == _scan_generation:
                os.replace(last_tmp, LAST_SCAN_PATH)
                os.replace(cache_tmp, SCAN_CACHE_PATH)
                _last_result = result
                _scan_state["state"] = "done"
                _scan_state["finished_at"] = _now_iso()
                _scan_state["error"] = None
                _scan_state["scan_mode"] = scan_mode
                _scan_state["cache_stats"] = scan_data["cache_stats"]
                committed = True
            else:
                committed = False
        if not committed:
            for tmp in (last_tmp, cache_tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception as exc:
        with _scan_lock:
            if gen == _scan_generation:
                _scan_state["state"] = "error"
                _scan_state["error"] = str(exc)
                _scan_state["finished_at"] = _now_iso()


def start_scan(path, incremental=True):
    """返回 (http_status, dict)。"""
    global _scan_generation, _last_progress_mono
    if not isinstance(path, str) or not path.strip():
        return 400, {"ok": False, "error": "路径不能为空"}
    path = path.strip()
    if not os.path.isdir(path):
        return 400, {"ok": False, "error": "路径不存在或不是目录：%s" % path}
    with _scan_lock:
        if _scan_state["state"] == "running":
            return 200, {"ok": False, "error": "扫描进行中"}
        _scan_generation += 1
        gen = _scan_generation
        now = _now_iso()
        _scan_state.clear()
        _scan_state.update({
            "state": "running",
            "scanned_dirs": 0,
            "episodes_found": 0,
            "current_path": path,
            "detail": "",
            "started_at": now,
            "last_progress_at": now,
            "scan_mode": "incremental" if incremental else "full",
            "cache_stats": None,
        })
        _last_progress_mono = time.monotonic()
    threading.Thread(target=_scan_worker, args=(path, gen, incremental), daemon=True).start()
    return 200, {"ok": True}


def scan_status():
    global _scan_generation
    with _scan_lock:
        # 惰性看门狗：running 且超过 120 秒无进度 → 判 error 并递增代际使僵尸线程失效。
        if (_scan_state["state"] == "running"
                and time.monotonic() - _last_progress_mono > 120):
            _scan_state["state"] = "error"
            _scan_state["error"] = "扫描超过 120 秒无进展，磁盘 I/O 可能挂起"
            _scan_state["finished_at"] = _now_iso()
            _scan_generation += 1
        status = dict(_scan_state)
        if status["state"] == "running":
            status["progress_age_seconds"] = round(time.monotonic() - _last_progress_mono, 1)
        return status


def get_result():
    """最近完成的扫描结果：优先内存，其次 last_scan.json；无则 None。"""
    global _last_result
    with _scan_lock:
        if _last_result is not None:
            return _last_result
    try:
        with open(LAST_SCAN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    with _scan_lock:
        _last_result = data
    return data


# ---------------------------------------------------------------- 自动更新

def _update_interval_seconds():
    """检查间隔（秒）：环境变量 UMI_STATS_UPDATE_INTERVAL（正整数）可覆盖默认值。"""
    try:
        value = int(os.environ.get("UMI_STATS_UPDATE_INTERVAL", ""))
        if value > 0:
            return value
    except ValueError:
        pass
    return DEFAULT_UPDATE_INTERVAL


def _update_check_once():
    """执行一轮更新检查；扫描进行中则跳过本轮（推迟到扫描结束后）。"""
    with _scan_lock:
        if _scan_state["state"] == "running":
            return
    with _update_lock:
        _update_state["state"] = "checking"
    manifest = updater.check_update()
    with _update_lock:
        _update_state["last_check_at"] = _now_iso()
        if manifest:
            _update_state["latest_version"] = manifest.get("version")
    if manifest:
        with _update_lock:
            _update_state["state"] = "updating"
        # 成功时进程被 execv 替换，不会走到下面；失败则标记 failed，下一轮重试
        if not updater.apply_update(manifest, restart=True):
            with _update_lock:
                _update_state["state"] = "failed"
    else:
        with _update_lock:
            _update_state["state"] = "idle"


def _update_worker():
    """长轮询实时更新循环：空闲时挂起等待更新源版本变化，秒级感知新发布。"""
    while True:
        # 扫描进行中：推迟本轮（保持原有 gating；sleep 在锁外，避免饿死进度与状态请求）
        with _scan_lock:
            running = _scan_state["state"] == "running"
        if running:
            time.sleep(10)
            continue
        try:
            result = updater.wait_for_change(updater.read_local_version())
        except HTTPError as exc:
            if exc.code == 404:
                # 旧更新源无 /wait：降级为定时检查，下一轮再尝试长轮询（源升级后自动恢复）
                try:
                    _update_check_once()
                except Exception as e:
                    with _update_lock:
                        _update_state["state"] = "failed"
                    sys.stderr.write("更新检查异常：%s\n" % e)
                time.sleep(_update_interval_seconds())
            else:
                updater._log("长轮询失败（HTTP %s），60 秒后重试" % exc.code)
                time.sleep(60)
            continue
        except Exception as exc:
            # 断网/超时/更新源宕机：记日志并退避重试，不影响主服务
            updater._log("长轮询失败（%s），60 秒后重试" % exc)
            time.sleep(60)
            continue
        if result.get("changed"):
            try:
                # 成功时进程被 execv 替换；仍存活说明失败或远端版本不更高，
                # 按定时间隔退避，避免对更新源形成紧循环
                _update_check_once()
            except Exception as exc:
                # 任何异常都不让检查线程崩溃影响主服务
                with _update_lock:
                    _update_state["state"] = "failed"
                sys.stderr.write("更新检查异常：%s\n" % exc)
            time.sleep(_update_interval_seconds())
        # changed=False：立即开始下一轮长轮询


def update_status():
    with _update_lock:
        status = dict(_update_state)
    status["current_version"] = updater.read_local_version()
    return status


# ---------------------------------------------------------------- 导出

_EXPORT_COLUMNS = {
    "devices": ["device_id", "episodes", "seconds", "hm", "task_types", "roles"],
    "tasks": ["task_uuid", "task_name", "collectors", "episodes", "seconds", "hm",
              "devices", "groups", "expect_duration_seconds", "project_name"],
    "groups": ["group_id", "episodes", "seconds", "hm", "devices", "task_uuid", "bytes"],
    "dates": ["date", "episodes", "seconds", "hm"],
    "episodes": ["path", "device_id", "task_uuid", "group_id", "seconds", "hm",
                 "task_type", "role", "create_time", "dir_size_bytes", "file_size"],
    "anomalies": ["category", "path", "detail"],
}


def _anomaly_rows(anomalies):
    rows = []
    anomalies = anomalies if isinstance(anomalies, dict) else {}
    for category in ("missing_duration", "malformed_meta"):
        for item in anomalies.get(category) or []:
            if isinstance(item, dict):
                rows.append({"category": category, "path": item.get("path", ""),
                             "detail": item.get("reason", "")})
    for path in anomalies.get("unreadable") or []:
        rows.append({"category": "unreadable", "path": path, "detail": ""})
    for err in anomalies.get("unreadable_dirs") or []:
        rows.append({"category": "unreadable_dirs", "path": "", "detail": str(err)})
    for item in anomalies.get("missing_aid") or []:
        if isinstance(item, dict):
            rows.append({"category": "missing_aid", "path": item.get("path", ""),
                         "detail": "seconds=%s" % item.get("seconds", "")})
    for path in anomalies.get("too_deep") or []:
        rows.append({"category": "too_deep", "path": path, "detail": ""})
    non_umi = anomalies.get("non_umi")
    if isinstance(non_umi, dict):
        for robot_type, count in sorted(non_umi.items()):
            rows.append({"category": "non_umi", "path": robot_type, "detail": str(count)})
    nested = anomalies.get("nested_skipped")
    if isinstance(nested, int) and nested > 0:
        rows.append({"category": "nested_skipped", "path": "", "detail": str(nested)})
    return rows


def _export_rows(scan, export_type):
    if export_type == "devices":
        return scan.get("by_device") or []
    if export_type == "tasks":
        return platform_api.join_tasks(scan.get("by_task") or [], platform_api.load_cache())
    if export_type == "groups":
        return scan.get("by_group") or []
    if export_type == "dates":
        return [{"date": r["date"], "episodes": r["episodes"], "seconds": r["seconds"], "hm": r["hm"]}
                for r in scan.get("by_date", [])]
    if export_type == "episodes":
        return scan.get("episodes") or []
    if export_type == "anomalies":
        return _anomaly_rows(scan.get("anomalies"))
    return None


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ";".join(str(v) for v in value)
    return value


def build_export(scan, export_type, fmt):
    """返回 (http_status, content_type, filename, body_bytes) 或 (status, None, None, json_bytes)。"""
    rows = _export_rows(scan, export_type)
    if rows is None:
        return 400, None, None, _json_bytes({
            "ok": False,
            "error": "未知导出类型：%s（支持 devices/tasks/groups/dates/episodes/anomalies）" % export_type,
        })
    if fmt == "json":
        body = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
        return 200, "application/json; charset=utf-8", "umi_%s.json" % export_type, body
    if fmt == "csv":
        columns = _EXPORT_COLUMNS[export_type]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_csv_cell(row.get(col)) for col in columns])
        body = buf.getvalue().encode("utf-8-sig")
        return 200, "text/csv; charset=utf-8", "umi_%s.csv" % export_type, body
    return 400, None, None, _json_bytes({"ok": False, "error": "未知导出格式：%s" % fmt})


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- HTTP 处理

class Handler(BaseHTTPRequestHandler):
    server_version = "UmiStats/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # 只记请求行/状态，不记请求体（login 密码在 body 中）
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ---------------- 响应工具 ----------------

    def _send(self, status, body, content_type, extra_headers=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, obj, status=200):
        self._send(status, _json_bytes(obj), "application/json; charset=utf-8")

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # ---------------- 路由 ----------------

    def do_GET(self):
        try:
            self._route_get()
        except Exception as exc:
            self._send_json({"ok": False, "error": "服务器内部错误：%s" % exc}, 500)

    def do_POST(self):
        try:
            self._route_post()
        except Exception as exc:
            self._send_json({"ok": False, "error": "服务器内部错误：%s" % exc}, 500)

    def _route_get(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/drives":
            self._send_json(list_drives())
        elif path == "/api/scan/status":
            self._send_json(scan_status())
        elif path == "/api/scan/result":
            result = get_result()
            if result is None:
                self._send_json({"ok": False, "error": "尚未扫描"})
            else:
                self._send_json(result)
        elif path == "/api/update/status":
            self._send_json(update_status())
        elif path == "/api/auth/status":
            self._send_json(platform_api.auth_status())
        elif path == "/api/platform/status":
            self._send_json(platform_api.platform_status())
        elif path == "/api/stats":
            scan = get_result()
            joined = None
            if scan is not None:
                joined = platform_api.join_tasks(
                    scan.get("by_task") or [], platform_api.load_cache()
                )
            self._send_json({
                "scan": scan,
                "platform": platform_api.platform_status(),
                "joined": joined,
            })
        elif path == "/api/export":
            self._handle_export(parse_qs(parsed.query))
        elif path == "/" or path == "/index.html":
            self._serve_static("index.html")
        elif path.startswith("/static/"):
            self._serve_static(unquote(path[len("/static/"):]))
        elif path.startswith("/api/"):
            self._send_json({"ok": False, "error": "未知接口：%s" % path}, 404)
        else:
            self._send(404, b"Not Found", "text/plain; charset=utf-8")

    def _route_post(self):
        path = urlparse(self.path).path
        body = self._read_json_body()  # 统一消费请求体，避免 keep-alive 残留字节污染下一请求
        if path == "/api/scan":
            if body is None:
                self._send_json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
                return
            incremental = body.get("incremental", True)
            if not isinstance(incremental, bool):
                incremental = True
            status, payload = start_scan(body.get("path"), incremental)
            self._send_json(payload, status)
        elif path == "/api/login":
            if body is None:
                self._send_json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
                return
            result = platform_api.login(
                body.get("username") or "",
                body.get("password") or "",
                body.get("tenant") or platform_api.DEFAULT_TENANT,
            )
            self._send_json(result)
        elif path == "/api/logout":
            self._send_json(platform_api.logout())
        elif path == "/api/platform/sync":
            self._send_json(platform_api.sync())
        elif path.startswith("/api/"):
            self._send_json({"ok": False, "error": "未知接口：%s" % path}, 404)
        else:
            self._send(404, b"Not Found", "text/plain; charset=utf-8")

    def _handle_export(self, query):
        scan = get_result()
        if scan is None:
            self._send_json({"ok": False, "error": "尚未扫描"}, 400)
            return
        export_type = (query.get("type") or [""])[0]
        fmt = (query.get("format") or ["csv"])[0].lower()
        status, content_type, filename, body = build_export(scan, export_type, fmt)
        if content_type is None:
            self._send(status, body, "application/json; charset=utf-8")
            return
        self._send(status, body, content_type, {
            "Content-Disposition": 'attachment; filename="%s"' % filename,
        })

    def _serve_static(self, rel_path):
        rel_path = rel_path.lstrip("/")
        if not rel_path:
            rel_path = "index.html"
        target = os.path.realpath(os.path.join(STATIC_DIR, rel_path))
        static_root = os.path.realpath(STATIC_DIR)
        if not target.startswith(static_root + os.sep) or not os.path.isfile(target):
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(target)[1].lower()
        content_type = _STATIC_MIME.get(ext, "application/octet-stream")
        try:
            with open(target, "rb") as f:
                body = f.read()
        except OSError:
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        self._send(200, body, content_type)


# ---------------------------------------------------------------- 入口

def main():
    parser = argparse.ArgumentParser(description="UMI 数据统计工具本地服务")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（默认 8765）")
    args = parser.parse_args()

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        if exc.errno in (98, 48):  # EADDRINUSE (Linux/macOS)
            print("错误：端口 %d 已被占用（可能已有一个实例在运行）。"
                  "请关闭占用该端口的程序，或用 --port 指定其他端口。" % args.port,
                  file=sys.stderr)
        else:
            print("错误：无法监听 %s:%d：%s" % (args.host, args.port, exc), file=sys.stderr)
        sys.exit(1)

    threading.Thread(target=_update_worker, daemon=True).start()

    print("UMI 数据统计工具 v%s 已启动，请在浏览器打开：http://%s:%d"
          % (updater.read_local_version(), args.host, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()