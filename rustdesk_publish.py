#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RustDesk 一键安装发布机（仅标准库）。

把安装脚本与 deb 包同步到 rustdesk-dist/，并以只读静态 HTTP 服务对外发布，
供其他 Ubuntu 主机通过 curl 一键安装。

用法：
  python3 rustdesk_publish.py                 # 同步资源后启动服务（默认端口 8767）
  python3 rustdesk_publish.py --sync          # 仅同步资源，不启动服务
  python3 rustdesk_publish.py --serve         # 仅启动服务（不同步）
  python3 rustdesk_publish.py --port 9000     # 指定端口

客户机一键安装：
  curl -fsSL http://<发布机公网IP>:8767/install_rustdesk.sh \
    | sudo bash -s -- --source http://<发布机公网IP>:8767
"""

import argparse
import os
import shutil
import socket
import sys
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
PUB_DIR = os.path.join(ROOT, "rustdesk-dist")
DEFAULT_PORT = 8767

# 发布到 rustdesk-dist/ 的资源：脚本名对外固定，deb 取工作目录内的安装包
INSTALL_SCRIPT = "install_rustdesk.sh"
DEB_NAME = "rustdesk-unattended-wayland-1.5.3-x86_64.deb"
SYNC_RESOURCES = (INSTALL_SCRIPT, DEB_NAME)


def detect_local_ip():
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


def detect_public_ip():
    """查询公网 IP，超时或失败返回空串（不影响本地/内网使用）。"""
    for url in ("https://ifconfig.me", "https://api.ipify.org"):
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                ip = resp.read().decode("utf-8", "ignore").strip()
                if ip:
                    return ip
        except OSError:
            continue
    return ""


def sync_resources():
    """把脚本与 deb 复制到发布目录，返回实际同步的文件名列表。"""
    os.makedirs(PUB_DIR, exist_ok=True)
    synced = []
    for name in SYNC_RESOURCES:
        src = os.path.join(ROOT, name)
        if not os.path.isfile(src):
            print("错误：缺少待发布文件：%s" % src)
            sys.exit(1)
        dst = os.path.join(PUB_DIR, name)
        shutil.copy2(src, dst)
        # 脚本保持可执行位（虽然 curl|bash 不依赖它，但本地直接执行时需要）
        if name == INSTALL_SCRIPT:
            os.chmod(dst, 0o755)
        synced.append(name)
    return synced


def guess_content_type(path):
    """按扩展名设置 Content-Type。"""
    if path.endswith(".sh"):
        return "text/x-shellscript; charset=utf-8"
    if path.endswith(".deb"):
        return "application/vnd.debian.binary-package"
    if path.endswith(".json"):
        return "application/json"
    return "application/octet-stream"


class RustDeskSourceHandler(BaseHTTPRequestHandler):
    """只读静态服务 rustdesk-dist/：仅 GET/HEAD，拒绝目录穿越与目录列出。"""

    server_version = "RustDeskSource/1.0"

    def log_message(self, fmt, *args):
        """精简访问日志：时间 + 客户端 + 请求行。"""
        print("%s %s - %s" % (datetime.now().strftime("%H:%M:%S"),
                              self.client_address[0], fmt % args))

    def _resolve(self):
        """将请求路径解析为发布目录内真实路径；越界或非法返回 None。"""
        try:
            path = unquote(urlsplit(self.path).path)
        except (ValueError, UnicodeError):
            return None
        if "\x00" in path:
            return None
        pub_real = os.path.realpath(PUB_DIR)
        target = os.path.realpath(os.path.join(pub_real, path.lstrip("/")))
        # realpath 校验：必须位于发布目录之内（防目录穿越与符号链接逃逸）
        if target != pub_real and not target.startswith(pub_real + os.sep):
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

    def _index_text(self):
        """根路径返回纯文本说明，不列出目录内容。"""
        return (
            "RustDesk 一键安装发布源\n"
            "安装命令：\n"
            "  curl -fsSL http://<host>:%d/%s | sudo bash -s -- --source http://<host>:%d\n"
            % (self.server.server_port, INSTALL_SCRIPT, self.server.server_port)
        )

    def _handle(self, head_only):
        target = self._resolve()
        if target is None:
            self._send_text(403, "403 禁止访问发布目录之外的路径\n", head_only)
            return
        if os.path.isdir(target):
            # 根路径给提示，其余目录一律禁止列出
            if os.path.realpath(target) == os.path.realpath(PUB_DIR):
                self._send_text(200, self._index_text(), head_only)
            else:
                self._send_text(403, "403 禁止列出目录\n", head_only)
            return
        if not os.path.isfile(target):
            self._send_text(404, "404 文件不存在\n", head_only)
            return
        # 每次请求读磁盘最新文件，更新资源后无需重启服务
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
            return

    def do_GET(self):
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
        # 除 GET/HEAD 外的任意方法一律 405（只读服务）
        if name.startswith("do_"):
            return self._reject_method
        raise AttributeError(name)


def print_banner(port, synced):
    local_ip = detect_local_ip()
    public_ip = detect_public_ip()
    print("=" * 60)
    print("RustDesk 发布源已就绪，托管目录：%s" % PUB_DIR)
    if synced:
        print("已同步资源：%s" % "、".join(synced))
    print("监听地址：0.0.0.0:%d（只读，仅暴露 rustdesk-dist/）" % port)
    print("内网地址：http://%s:%d" % (local_ip, port))
    if public_ip:
        print("公网地址：http://%s:%d" % (public_ip, port))
    print("-" * 60)
    host = public_ip or local_ip
    print("客户机一键安装命令：")
    print("  curl -fsSL http://%s:%d/%s | sudo bash -s -- --source http://%s:%d"
          % (host, port, INSTALL_SCRIPT, host, port))
    print("=" * 60)


def serve(port, do_sync):
    if do_sync:
        synced = sync_resources()
    else:
        if not os.path.isdir(PUB_DIR):
            print("错误：%s 不存在，请先运行 python3 rustdesk_publish.py --sync" % PUB_DIR)
            sys.exit(1)
        synced = []
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", port), RustDeskSourceHandler)
    except OSError as e:
        print("错误：无法监听端口 %d（可能已被占用）：%s" % (port, e))
        sys.exit(1)
    print_banner(port, synced)
    print("按 Ctrl+C 停止服务")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(description="RustDesk 一键安装发布源：同步资源并提供只读静态服务")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="服务端口（默认 %d）" % DEFAULT_PORT)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sync", action="store_true", help="仅同步资源到发布目录，不启动服务")
    group.add_argument("--serve", action="store_true", help="仅启动服务，不同步资源")
    args = parser.parse_args()

    if args.sync:
        synced = sync_resources()
        print("同步完成：%s -> %s" % ("、".join(synced), PUB_DIR))
    else:
        # 默认：先同步再启动；--serve 时跳过同步
        serve(args.port, do_sync=not args.serve)


if __name__ == "__main__":
    main()
