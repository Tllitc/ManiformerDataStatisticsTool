#!/usr/bin/env bash
# UMI 数据统计工具启动器：启动本地服务并打开浏览器。
set -e
cd "$(dirname "$(readlink -f "$0")")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "错误：未找到 python3，请先安装 Python 3。" >&2
    exit 1
fi

# 启动前检查并应用更新（失败静默跳过，绝不阻塞启动）
python3 app/updater.py --check-on-start || true

URL="http://127.0.0.1:8765"

# 启动前检测端口是否被占用（server 自身也会检测并给出中文提示）
if ! python3 - <<'PY'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", 8765))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
then
    echo "错误：端口 8765 已被占用（可能已有一个实例在运行）。请关闭占用该端口的程序后重试。" >&2
    exit 1
fi

if command -v xdg-open >/dev/null 2>&1; then
    # 稍等服务就绪后再打开浏览器
    ( sleep 1; xdg-open "$URL" >/dev/null 2>&1 || true ) &
else
    echo "未找到 xdg-open，请手动在浏览器打开：$URL"
fi

exec python3 app/server.py