#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布脚本（仅标准库）。

用法：
  python3 publish.py [--notes "更新说明"]   默认自动递增版本号末位（如 1.0.0→1.0.1），打包到 dist/ 并生成 version.json
  python3 publish.py --version 2.0.0        显式指定版本号
  python3 publish.py --no-bump              不递增，按当前 VERSION 重新发布（覆盖同版本包）
  python3 publish.py --serve [--port N]     在本机 0.0.0.0:8766 提供只读静态更新源服务（仅暴露 dist/）
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tarfile
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT, "dist")
DEFAULT_PORT = 8766


def detect_ip():
    """探测本机出口 IP（UDP connect 不实际发包），失败回退 127.0.0.1。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def read_version():
    """读取根目录 VERSION 作为唯一版本来源。"""
    path = os.path.join(ROOT, "VERSION")
    try:
        with open(path, "r", encoding="utf-8") as f:
            ver = f.read().strip()
    except OSError:
        print("错误：根目录缺少 VERSION 文件，无法发布。")
        sys.exit(1)
    if not ver:
        print("错误：VERSION 文件为空，无法发布。")
        sys.exit(1)
    return ver


def _valid_version(ver):
    """版本号须为数字点分形式（如 1.2.3）。"""
    return re.fullmatch(r"\d+(\.\d+)*", ver) is not None


def write_version(ver):
    """把版本号写回根目录 VERSION。"""
    with open(os.path.join(ROOT, "VERSION"), "w", encoding="utf-8") as f:
        f.write(ver + "\n")


def resolve_version(explicit=None, no_bump=False):
    """确定发布版本：显式指定 > 不递增 > 自动递增末位 +1（并写回 VERSION）。"""
    if explicit is not None:
        if not _valid_version(explicit):
            print("错误：--version 格式非法（应为数字点分形式，如 1.2.3）：%s" % explicit)
            sys.exit(1)
        write_version(explicit)
        return explicit
    ver = read_version()
    if no_bump:
        return ver
    if not _valid_version(ver):
        print("错误：VERSION 内容非法，无法自动递增：%s（可用 --version 指定）" % ver)
        sys.exit(1)
    parts = ver.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    new_ver = ".".join(parts)
    write_version(new_ver)
    print("版本号自动递增：%s → %s" % (ver, new_ver))
    return new_ver


def sha256_file(path):
    """计算文件 sha256（十六进制）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(notes, ver):
    """打包发布：生成 dist/umi-stats-tool-<ver>.tar.gz 与 dist/version.json。"""
    os.makedirs(DIST_DIR, exist_ok=True)
    pkg_name = "umi-stats-tool-%s.tar.gz" % ver
    pkg_path = os.path.join(DIST_DIR, pkg_name)
    if os.path.exists(pkg_path):
        print("提示：dist/ 已存在同版本包 %s，将覆盖生成（以最新内容为准）。" % pkg_name)

    def _filter(ti):
        # 排除所有 __pycache__ 目录与 .pyc 文件
        if "__pycache__" in ti.name.split("/") or ti.name.endswith(".pyc"):
            return None
        return ti

    # tar 内路径相对工具根目录：app/...、start.sh、VERSION
    with tarfile.open(pkg_path, "w:gz") as tar:
        tar.add(os.path.join(ROOT, "app"), arcname="app", filter=_filter)
        tar.add(os.path.join(ROOT, "start.sh"), arcname="start.sh")
        tar.add(os.path.join(ROOT, "VERSION"), arcname="VERSION")

    digest = sha256_file(pkg_path)
    manifest = {
        "version": ver,
        "url": pkg_name,  # 相对路径，便于整体迁移托管位置
        "sha256": digest,
        "released_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "notes": notes,
    }
    manifest_path = os.path.join(DIST_DIR, "version.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    ip = detect_ip()
    print("发布完成：v%s" % ver)
    print("  更新包：%s" % pkg_path)
    print("  清单：  %s" % manifest_path)
    print("  sha256：%s" % digest)
    print("客户机清单地址：http://%s:%d/version.json" % (ip, DEFAULT_PORT))
    print("启动更新源服务：python3 publish.py --serve --port %d" % DEFAULT_PORT)


def guess_content_type(path):
    """按扩展名设置 Content-Type。"""
    if path.endswith(".json"):
        return "application/json"
    if path.endswith(".gz"):
        return "application/gzip"
    return "application/octet-stream"


class UpdateSourceHandler(BaseHTTPRequestHandler):
    """只读静态服务 dist/：仅 GET/HEAD，拒绝目录穿越与目录列出。"""

    server_version = "UmiUpdateSource/1.0"

    def _resolve(self):
        """将请求路径解析为 dist/ 内的真实路径；越界返回 None。"""
        try:
            path = unquote(urlsplit(self.path).path)
        except (ValueError, UnicodeError):
            return None
        if "\x00" in path:
            return None
        dist_real = os.path.realpath(DIST_DIR)
        target = os.path.realpath(os.path.join(dist_real, path.lstrip("/")))
        # realpath 校验：必须位于 dist/ 真实路径之内（防目录穿越与符号链接逃逸）
        if target != dist_real and not target.startswith(dist_real + os.sep):
            return None
        return target

    def _send_text(self, code, text, head_only=False):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _handle(self, head_only):
        target = self._resolve()
        if target is None:
            self._send_text(403, "403 禁止访问 dist/ 之外的路径\n", head_only)
            return
        if os.path.isdir(target):
            # 禁止列出目录
            self._send_text(403, "403 禁止列出目录\n", head_only)
            return
        if not os.path.isfile(target):
            self._send_text(404, "404 文件不存在\n", head_only)
            return
        # 每次请求读取磁盘最新文件，发布新版无需重启服务
        try:
            size = os.path.getsize(target)
            self.send_response(200)
            self.send_header("Content-Type", guess_content_type(target))
            self.send_header("Content-Length", str(size))
            self.end_headers()
            if not head_only:
                with open(target, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
        except (OSError, ConnectionError):
            # 文件读取失败或客户端断开，直接结束本次请求
            return

    def _manifest_version(self):
        """读取 dist/version.json 的 version；缺失/非法返回 None。"""
        try:
            with open(os.path.join(DIST_DIR, "version.json"), "r", encoding="utf-8") as f:
                manifest = json.load(f)
            if isinstance(manifest, dict):
                ver = manifest.get("version")
                if isinstance(ver, str) and ver:
                    return ver
        except (OSError, ValueError):
            pass
        return None

    def _handle_wait(self):
        """长轮询：版本相对 since 变化立即返回，否则等满 timeout（1~60s，缺省 60）。"""
        query = parse_qs(urlsplit(self.path).query)
        since = (query.get("since") or [None])[0]
        try:
            timeout = int((query.get("timeout") or ["60"])[0])
        except ValueError:
            timeout = 60
        timeout = max(1, min(60, timeout))
        deadline = time.monotonic() + timeout
        changed = False
        while True:
            version = self._manifest_version()
            if version is not None and version != since:
                changed = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(1)
        body = json.dumps({"changed": changed, "version": version}).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端断连：静默结束本线程
            pass

    def do_GET(self):
        # /wait 长轮询在静态文件防护之前分流，只返回版本号，不暴露文件
        if urlsplit(self.path).path == "/wait":
            self._handle_wait()
            return
        self._handle(head_only=False)

    def do_HEAD(self):
        self._handle(head_only=True)

    def _reject_method(self):
        body = "405 仅支持 GET/HEAD\n".encode("utf-8")
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def __getattr__(self, name):
        # 除 GET/HEAD 外的任意方法一律返回 405（只读服务）
        if name.startswith("do_"):
            return self._reject_method
        raise AttributeError(name)


def serve(port):
    """启动只读更新源服务：不打包、不修改 dist/ 内容。"""
    if not os.path.isfile(os.path.join(DIST_DIR, "version.json")):
        print("提示：dist/version.json 不存在，请先运行 python3 publish.py 发布；服务仍将启动。")
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", port), UpdateSourceHandler)
    except OSError as e:
        print("错误：无法监听端口 %d（可能已被占用）：%s" % (port, e))
        sys.exit(1)
    ip = detect_ip()
    print("更新源服务已启动：http://0.0.0.0:%d（只读，仅暴露 dist/）" % port)
    print("客户机清单地址：http://%s:%d/version.json" % (ip, port))
    print("按 Ctrl+C 停止服务")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(description="打包发布 / 本机只读更新源服务")
    parser.add_argument("--notes", default="", help="可选更新说明，写入 version.json 的 notes 字段")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--version", help="显式指定版本号（如 2.0.0）；默认自动递增当前 VERSION 末位 +1")
    group.add_argument("--no-bump", action="store_true", help="不递增版本号，按当前 VERSION 重新发布（覆盖同版本包）")
    parser.add_argument("--serve", action="store_true", help="启动更新源服务（不打包）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="更新源服务端口（默认 8766）")
    args = parser.parse_args()
    if args.serve:
        serve(args.port)
    else:
        ver = resolve_version(args.version, args.no_bump)
        publish(args.notes, ver)


if __name__ == "__main__":
    main()
