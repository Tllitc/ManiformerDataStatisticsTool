#!/usr/bin/env bash
# UMI 数据统计工具 —— 在线一键安装/升级/卸载脚本（用户级安装，仅依赖 bash + python3 标准库）
#
# 典型用法：
#   curl -s http://<发布机IP>:8766/install.sh | bash -s -- --source http://<发布机IP>:8766
#   ./install.sh --source http://<发布机IP>:8766           # 安装或升级
#   ./install.sh --source http://<发布机IP>:8766 --uninstall   # 卸载（保留用户数据）
#   ./install.sh --source http://<发布机IP>:8766 --purge       # 卸载并删除全部数据
#
# 安装位置：~/umi-stats-tool/    用户数据：~/.umi-stats-tool/（卸载默认保留）
set -euo pipefail

APP_NAME="UMI 数据统计工具"
APP_DIR_NAME="umi-stats-tool"
APP_PORT=8765
STATE_DIR="$HOME/.umi-stats-tool"
INSTALL_DIR="$HOME/$APP_DIR_NAME"
BACKUP_DIR="$HOME/$APP_DIR_NAME.bak"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_FILE="$DESKTOP_DIR/umi-stats-tool.desktop"
ICON_FILE="$ICON_DIR/umi-stats-tool.svg"

SOURCE=""
DO_UNINSTALL=0
DO_PURGE=0

for arg in "$@"; do
    case "$arg" in
        --source=*) SOURCE="${arg#--source=}" ;;
        --source)   shift_needed=1 ;;
        --uninstall) DO_UNINSTALL=1 ;;
        --purge)     DO_PURGE=1 ;;
        -h|--help)
            case "$0" in */bash|bash) die "帮助信息仅支持以脚本文件方式执行：./install.sh --help" ;; esac
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            # 支持 --source URL 的空格形式
            if [ "${shift_needed:-0}" = "1" ]; then SOURCE="$arg"; shift_needed=0; fi
            ;;
    esac
done
# 去掉 SOURCE 末尾斜杠
SOURCE="${SOURCE%/}"

log()  { printf '\033[1;32m[安装器]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[警告]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 卸载分支
uninstall() {
    log "开始卸载 $APP_NAME"
    if [ "$DO_PURGE" = "1" ]; then
        rm -rf "$STATE_DIR"
        log "已删除用户数据目录 $STATE_DIR"
    else
        log "保留用户数据目录 $STATE_DIR（如需一并删除请加 --purge）"
    fi
    rm -rf "$INSTALL_DIR" "$BACKUP_DIR"
    rm -f "$DESKTOP_FILE" "$ICON_FILE"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
    log "卸载完成"
    exit 0
}
# --purge 隐含 --uninstall
[ "$DO_PURGE" = "1" ] && DO_UNINSTALL=1
[ "$DO_UNINSTALL" = "1" ] && uninstall

[ -n "$SOURCE" ] || die "必须通过 --source http://<发布机IP>:8766 指定更新源地址"

# ---------------------------------------------------------------- 环境检查
log "检查运行环境"
case "$(uname -s)" in
    Linux*) ;;
    *) die "仅支持 Linux/Ubuntu 系统，当前系统：$(uname -s)" ;;
esac
[ "$(uname -m)" = "x86_64" ] || warn "未检测到 x86_64 架构（当前：$(uname -m)），如运行异常请联系维护者"

command -v python3 >/dev/null 2>&1 || die "未找到 python3，请先安装 Python 3.8+（sudo apt-get install -y python3）"
PY_VER="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
    || die "Python 版本过低（当前 $PY_VER），需要 3.8 及以上"
log "Python $PY_VER 检查通过"

# ---------------------------------------------------------------- 依赖处理（三级降级）
if ! python3 -c 'import requests' >/dev/null 2>&1; then
    log "安装唯一第三方依赖 requests"
    if command -v apt-get >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3-requests \
            || die "apt 安装 python3-requests 失败，请手动执行：sudo apt-get install -y python3-requests"
    elif command -v apt-get >/dev/null 2>&1; then
        warn "需要 sudo 权限安装系统依赖，按提示输入密码"
        sudo apt-get update -qq && sudo apt-get install -y -qq python3-requests \
            || die "apt 安装 python3-requests 失败，请手动执行：sudo apt-get install -y python3-requests"
    else
        python3 -m pip install --user requests \
            || die "pip 安装 requests 失败，请手动执行：python3 -m pip install --user requests"
    fi
fi
python3 -c 'import requests' >/dev/null 2>&1 || die "requests 依赖不可用，请手动安装后重试"
log "依赖检查通过（requests 可用）"

command -v xdg-open >/dev/null 2>&1 || warn "缺少 xdg-open（xdg-utils），安装后可能无法自动打开浏览器"
command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || die "curl 和 wget 均不可用，请先安装其中之一"

http_get() {  # $1=URL $2=输出文件
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --connect-timeout 10 --retry 2 -o "$2" "$1"
    else
        wget -q -T 10 -O "$2" "$1"
    fi
}

# ---------------------------------------------------------------- 拉取清单与更新包
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

log "从更新源获取版本清单：$SOURCE/version.json"
http_get "$SOURCE/version.json" "$TMP_DIR/version.json" || die "无法访问 $SOURCE/version.json，请确认发布机已执行 publish.py --serve 且端口可达"

read_manifest() { python3 -c 'import json,sys;m=json.load(open(sys.argv[1]));print(m[sys.argv[2]])' "$TMP_DIR/version.json" "$1"; }
REMOTE_VER="$(read_manifest version)"
PKG_REL="$(read_manifest url)"
EXPECT_SHA="$(read_manifest sha256)"
case "$PKG_REL" in http://*|https://*) PKG_URL="$PKG_REL" ;; *) PKG_URL="$SOURCE/$PKG_REL" ;; esac

OLD_VER="0.0.0"
[ -f "$INSTALL_DIR/VERSION" ] && OLD_VER="$(cat "$INSTALL_DIR/VERSION")"
if [ "$OLD_VER" = "$REMOTE_VER" ] && [ -d "$INSTALL_DIR/app" ]; then
    log "当前已安装 v$REMOTE_VER，将重新部署（幂等升级）"
else
    log "版本：v$OLD_VER → v$REMOTE_VER"
fi

log "下载更新包：$PKG_URL"
http_get "$PKG_URL" "$TMP_DIR/package.tar.gz" || die "更新包下载失败：$PKG_URL"

ACTUAL_SHA="$(python3 -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$TMP_DIR/package.tar.gz")"
[ "$ACTUAL_SHA" = "$EXPECT_SHA" ] || die "SHA-256 校验失败：期望 $EXPECT_SHA，实际 $ACTUAL_SHA"
log "SHA-256 校验通过"

# ---------------------------------------------------------------- 停止运行中的旧实例
if python3 - "$APP_PORT" <<'PY'
import socket, sys
s = socket.socket()
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
else:
    sys.exit(0)
finally:
    s.close()
PY
then
    warn "检测到端口 $APP_PORT 上有运行中的实例，尝试停止"
    pkill -f "python3 .*$INSTALL_DIR/app/server.py" 2>/dev/null || true
    sleep 1
fi

# ---------------------------------------------------------------- 解包与安装
log "解包并做语法检查"
mkdir -p "$TMP_DIR/extract"
tar -xzf "$TMP_DIR/package.tar.gz" -C "$TMP_DIR/extract"
python3 - "$TMP_DIR/extract" <<'PY'
import os, sys, compileall
root = sys.argv[1]
app = None
if os.path.isdir(os.path.join(root, "app")):
    app = os.path.join(root, "app")
else:
    for name in os.listdir(root):
        cand = os.path.join(root, name, "app")
        if os.path.isdir(cand):
            app = cand
            break
if app is None or not os.path.isfile(os.path.join(os.path.dirname(app), "start.sh")):
    sys.exit("更新包结构非法：缺少 app/ 或 start.sh")
if not compileall.compile_dir(app, quiet=1):
    sys.exit("新代码语法检查失败")
print(app)
PY

# 定位包根（支持一层子目录）
if [ -d "$TMP_DIR/extract/app" ]; then
    PKG_ROOT="$TMP_DIR/extract"
else
    PKG_ROOT="$(find "$TMP_DIR/extract" -mindepth 1 -maxdepth 1 -type d | head -1)"
fi

log "安装到 $INSTALL_DIR"
rm -rf "$BACKUP_DIR"
if [ -d "$INSTALL_DIR" ]; then
    mv "$INSTALL_DIR" "$BACKUP_DIR"
    log "旧版本已备份到 $BACKUP_DIR（确认新版正常后可手动删除）"
fi
mkdir -p "$INSTALL_DIR"
cp -a "$PKG_ROOT/app" "$PKG_ROOT/start.sh" "$PKG_ROOT/VERSION" "$INSTALL_DIR/"
chmod 755 "$INSTALL_DIR/start.sh"

# ---------------------------------------------------------------- 固化更新源（对接 app/updater.py）
mkdir -p "$STATE_DIR"
python3 - "$STATE_DIR/update.json" "$SOURCE/version.json" <<'PY'
import json, os, sys
path, url = sys.argv[1], sys.argv[2]
cfg = {}
if os.path.isfile(path):
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
cfg["manifest_url"] = url
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
log "已写入更新源配置：$SOURCE/version.json（后续启动自动检查更新）"

# ---------------------------------------------------------------- 桌面集成
log "创建桌面入口与图标"
mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
if [ -f "$PKG_ROOT/deploy/icon.svg" ]; then
    cp -a "$PKG_ROOT/deploy/icon.svg" "$ICON_FILE"
elif [ -f "$(dirname "$0")/icon.svg" ] && [ "$(readlink -f "$0")" != "/bin/bash" ] && [ "$(readlink -f "$0")" != "/usr/bin/bash" ]; then
    cp -a "$(dirname "$0")/icon.svg" "$ICON_FILE"
else
    # curl|bash 场景：从更新源直接下载图标；下载失败则生成简易 SVG
    http_get "$SOURCE/icon.svg" "$ICON_FILE" 2>/dev/null || cat > "$ICON_FILE" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <rect width="128" height="128" rx="24" fill="#2563eb"/>
  <text x="64" y="82" font-size="56" font-family="sans-serif" font-weight="bold" fill="#fff" text-anchor="middle">U</text>
</svg>
SVG
fi

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=UMI 数据统计工具
Comment=UMI 具身智能数据采集统计分析（本地 Web GUI）
Exec=$INSTALL_DIR/start.sh
Icon=umi-stats-tool
Path=$INSTALL_DIR
Terminal=true
Categories=Development;Utility;
StartupNotify=true
EOF
chmod 644 "$DESKTOP_FILE"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true

# ---------------------------------------------------------------- 启动自检
log "启动自检（HTTP 探测 127.0.0.1:$APP_PORT）"
# 若已有本应用实例在运行（端口被占且接口正常），直接视为通过，不再重复启动
PRE_EXISTING=0
if python3 - "$APP_PORT" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:%s/api/scan/status" % sys.argv[1], timeout=3) as r:
        json.loads(r.read().decode("utf-8"))
except Exception:
    sys.exit(1)
PY
then
    PRE_EXISTING=1
    SELFTEST_OK=1
    log "检测到已有实例在运行且接口正常，跳过重复启动"
else
    # 记录自检子进程 PID，精确终止（避免 pkill 模式在不同启动方式下误杀/漏杀）
    ( cd "$INSTALL_DIR" && nohup python3 app/server.py --port "$APP_PORT" >/tmp/umi-stats-tool-selftest.log 2>&1 & echo $! > /tmp/umi-stats-tool-selftest.pid )
    SELF_PID="$(cat /tmp/umi-stats-tool-selftest.pid 2>/dev/null || true)"
    SELFTEST_OK=0
    for _ in $(seq 1 15); do
        sleep 1
        if python3 - "$APP_PORT" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:%s/api/scan/status" % sys.argv[1], timeout=3) as r:
        json.loads(r.read().decode("utf-8"))
except Exception:
    sys.exit(1)
PY
        then
            SELFTEST_OK=1
            break
        fi
    done
    # 仅终止本次启动的自检进程
    [ -n "$SELF_PID" ] && kill "$SELF_PID" 2>/dev/null || true
    rm -f /tmp/umi-stats-tool-selftest.pid
fi

if [ "$SELFTEST_OK" = "1" ]; then
    log "自检通过，服务可正常启动"
else
    warn "自检未在 15 秒内通过接口检查，日志见 /tmp/umi-stats-tool-selftest.log"
fi

# ---------------------------------------------------------------- 完成
cat <<EOF

==================== 安装完成 ====================
  应用：$APP_NAME v$REMOTE_VER
  目录：$INSTALL_DIR
  数据：$STATE_DIR（扫描缓存、平台 token、更新备份）
  更新源：$SOURCE/version.json

启动方式（任选其一）：
  1) 应用菜单中点击「UMI 数据统计工具」
  2) 终端执行：$INSTALL_DIR/start.sh
  3) 浏览器直接访问：http://127.0.0.1:$APP_PORT

卸载：
  curl -s $SOURCE/install.sh | bash -s -- --source $SOURCE --uninstall
==================================================
EOF
