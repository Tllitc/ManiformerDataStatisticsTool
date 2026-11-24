#!/usr/bin/env bash
#
# RustDesk 一键安装 + 中继/ID 服务器自动配置 + 固定密码脚本
# 适用：Ubuntu/Debian（amd64），Wayland 无人值守安装包
#
# 用法一（curl 一键安装，从发布机远程下载）：
#   curl -fsSL http://<发布机IP>:8767/install_rustdesk.sh | sudo bash -s -- --source http://<发布机IP>:8767
# 用法二（本地执行，deb 与脚本同目录）：
#   sudo bash install_rustdesk.sh [deb包路径] [加密配置串] [固定密码]
# 不带参数时，使用脚本内置的配置串与固定密码。
#
# 换源选项（apt 源不可用导致安装失败时）：
#   默认行为：apt-get update 失败会自动切换国内镜像（阿里云/清华/USTC 择优）后重试
#   --change-source            强制先换源再更新
#   --mirror mirrors.aliyun.com  指定镜像主机名（默认自动择优）
#
set -Eeuo pipefail

# ===== 内置默认值（可被命令行参数覆盖）=====
DEFAULT_DEB_NAME="rustdesk-unattended-wayland-1.5.3-x86_64.deb"
RUSTDESK_CONFIG='==Qfi0TTjxkZqZVdBd3ZvhzblF3aNZzVwlFb3dEN3JFSQRXQxoncxZFWn9UZ1UGdiojI5V2aiwiIiojIpBXYiwiI3ETMxIjOyETMuMTOuYTOx4yM4EjI6ISehxWZyJCLiYTMxEjM6ITMx4yM54iN5EjLzgTMiojI0N3boJye'
RUSTDESK_PASSWORD='A12345678a'
SOURCE=""
CHANGE_SOURCE=0          # 1=强制先换源；默认 0=仅在 apt-get update 失败时自动换源
MIRROR=""                # 自定义镜像主机名，如 mirrors.aliyun.com；为空则自动择优
POSITIONAL=()

# 解析参数：--source URL（支持 = 或空格两种写法），其余视为位置参数
while [ $# -gt 0 ]; do
    case "$1" in
        --source=*) SOURCE="${1#--source=}" ;;
        --source)
            [ $# -ge 2 ] || { echo "错误：--source 后缺少 URL" >&2; exit 1; }
            SOURCE="$2"
            shift
            ;;
        --change-source) CHANGE_SOURCE=1 ;;
        --mirror=*) MIRROR="${1#--mirror=}" ;;
        --mirror)
            [ $# -ge 2 ] || { echo "错误：--mirror 后缺少镜像主机名" >&2; exit 1; }
            MIRROR="$2"
            shift
            ;;
        -h|--help)
            if [ -f "$0" ]; then
                grep '^#' "$0" | sed 's/^# \{0,1\}//'
            else
                cat <<'HELP'
RustDesk 一键安装（curl 管道模式）
  curl -fsSL http://<发布机IP>:8767/install_rustdesk.sh \
    | sudo bash -s -- --source http://<发布机IP>:8767
换源选项（apt 源失败时）：
  --change-source              强制先切换国内镜像再更新
  --mirror mirrors.aliyun.com  指定镜像主机名（默认自动择优）
HELP
            fi
            exit 0
            ;;
        *) POSITIONAL+=("$1") ;;
    esac
    shift
done
if [ ${#POSITIONAL[@]} -ge 1 ]; then LOCAL_DEB_ARG="${POSITIONAL[0]}"; else LOCAL_DEB_ARG=""; fi
if [ ${#POSITIONAL[@]} -ge 2 ]; then RUSTDESK_CONFIG="${POSITIONAL[1]}"; fi
if [ ${#POSITIONAL[@]} -ge 3 ]; then RUSTDESK_PASSWORD="${POSITIONAL[2]}"; fi

# 管道执行（curl | bash）时 $0 不是真实文件，据此识别为管道模式
PIPED=0
if [ ! -f "$0" ]; then PIPED=1; fi
SCRIPT_DIR=""
if [ "$PIPED" -eq 0 ]; then
    SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
fi

log()  { echo -e "\033[1;32m[*]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31m[x]\033[0m $*" >&2; exit 1; }
trap 'die "脚本在第 $LINENO 行执行失败"' ERR

# ---------- 0. 权限检查：非 root 自动通过 sudo 重新执行 ----------
if [ "$(id -u)" -ne 0 ]; then
    if ! command -v sudo >/dev/null 2>&1; then
        die "需要 root 权限运行，请用 sudo 执行本脚本"
    fi
    log "当前非 root，通过 sudo 重新执行脚本..."
    if [ "$PIPED" -eq 1 ]; then
        # 管道模式：脚本本身来自 stdin，带 --source 重新经 sudo 读入
        exec sudo -E bash -s -- --source "$SOURCE"
    else
        exec sudo bash "$(readlink -f "$0")" "$@"
    fi
fi

# ---------- 1. 准备 deb 安装包（远程下载或本地定位）----------
download_file() {
    # $1=URL $2=目标路径；优先 curl，回退 wget
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --connect-timeout 10 --retry 2 -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 30 -O "$2" "$1"
    else
        die "curl 和 wget 均不可用，无法下载安装包"
    fi
}

DOWNLOADED=0
if [ -n "$SOURCE" ] || [ "$PIPED" -eq 1 ]; then
    [ -n "$SOURCE" ] || die "管道模式必须通过 --source http://<发布机IP>:8767 指定更新源"
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
        || die "curl 和 wget 均不可用，请先安装其中之一"
    SOURCE="${SOURCE%/}"
    DEB_PATH="/tmp/$DEFAULT_DEB_NAME"
    log "从发布机下载安装包：$SOURCE/$DEFAULT_DEB_NAME"
    download_file "$SOURCE/$DEFAULT_DEB_NAME" "$DEB_PATH" || die "安装包下载失败：$SOURCE/$DEFAULT_DEB_NAME"
    DOWNLOADED=1
else
    if [ -n "$LOCAL_DEB_ARG" ]; then
        DEB_PATH="$(readlink -f "$LOCAL_DEB_ARG")"
    else
        DEB_PATH="$SCRIPT_DIR/$DEFAULT_DEB_NAME"
    fi
fi

[ -f "$DEB_PATH" ] || die "找不到安装包：$DEB_PATH"
case "$DEB_PATH" in *.deb) ;; *) die "不是 .deb 安装包：$DEB_PATH" ;; esac
# 校验确为 ar 归档（deb 魔数 !<arch>），防止下载到错误内容
if ! head -c 8 "$DEB_PATH" | grep -q '!<arch>'; then
    die "安装包内容异常（非有效 deb 文件）：$DEB_PATH"
fi

ARCH="$(dpkg --print-architecture 2>/dev/null || echo unknown)"
[ "$ARCH" = "amd64" ] || warn "当前架构为 $ARCH，本安装包仅适用于 amd64，可能安装失败"

if ! printf '%s' "$RUSTDESK_CONFIG" | grep -qE '^==[A-Za-z0-9+/=]+$'; then
    die "配置串格式异常（应以 == 开头的加密字符串）"
fi

# RustDesk 永久密码长度要求 6~20 个字符
if [ "${#RUSTDESK_PASSWORD}" -lt 6 ] || [ "${#RUSTDESK_PASSWORD}" -gt 20 ]; then
    die "固定密码长度必须在 6~20 个字符之间，当前为 ${#RUSTDESK_PASSWORD} 个字符"
fi

export DEBIAN_FRONTEND=noninteractive

# ---------- 1.5 软件源保障：更新失败时自动切换国内镜像 ----------
# 候选镜像（按顺序择优，取第一个可连通的）；--mirror 可强制指定
MIRROR_CANDIDATES=(mirrors.aliyun.com mirrors.tuna.tsinghua.edu.cn mirrors.ustc.edu.cn)
UBUNTU_SUITES="main restricted universe multiverse"

pick_mirror() {
    # 指定了镜像则直接用；否则返回第一个 80 端口可连通的候选
    if [ -n "$MIRROR" ]; then echo "$MIRROR"; return; fi
    local host
    for host in "${MIRROR_CANDIDATES[@]}"; do
        if (exec 3<>"/dev/tcp/$host/80") 2>/dev/null; then
            exec 3<&- 3>&- 2>/dev/null || true
            echo "$host"
            return
        fi
    done
    echo ""
}

APT_BACKUP_DIR=""   # 原软件源备份目录（结尾提示恢复用）

url_ok() {
    # 探测 URL 是否可正常访问（curl 优先，回退 wget）；内部探测，静默过程输出
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --connect-timeout 8 -o /dev/null "$1" 2>/dev/null
    else
        wget -q -T 15 --spider "$1" 2>/dev/null
    fi
}

detect_base_url() {
    # $1=镜像主机 $2=代号；回显该代号可用的源 base URL（标准镜像或官方归档）
    local host="$1" codename="$2"
    if url_ok "http://$host/ubuntu/dists/$codename/Release"; then
        echo "http://$host/ubuntu/"
        return
    fi
    # EOL 版本：国内镜像已清理，回退官方 old-releases（保留全部 suite 且 GPG 签名完整）
    if url_ok "http://old-releases.ubuntu.com/ubuntu/dists/$codename/Release"; then
        warn "代号 $codename 已停止维护（EOL），国内镜像无此版本，改用官方 old-releases 归档源"
        echo "http://old-releases.ubuntu.com/ubuntu/"
        return
    fi
    echo ""
}

quarantine_sources() {
    # 备份并隔离主源之外的所有源配置（第三方/cdrom/失效源是“无法安全更新”的常见根因）
    local stamp
    stamp="$(date +%Y%m%d%H%M%S)"
    APT_BACKUP_DIR="/var/backups/rustdesk-apt-$stamp"
    mkdir -p "$APT_BACKUP_DIR/sources.list.d"
    [ -f /etc/apt/sources.list ] && cp -a /etc/apt/sources.list "$APT_BACKUP_DIR/sources.list.bak"
    local f
    for f in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
        [ -e "$f" ] || continue
        mv -f "$f" "$APT_BACKUP_DIR/sources.list.d/"
    done
    log "已备份并隔离原有软件源到：$APT_BACKUP_DIR"
}

write_clean_sources() {
    # 写入只含 Ubuntu 官方四件套的干净源，签名由系统自带 ubuntu-keyring 验证
    local base="$1" codename="$2" f=/etc/apt/sources.list
    {
        echo "# 由 rustdesk 一键安装脚本生成（原文件已备份到 $APT_BACKUP_DIR）"
        echo "deb $base $codename $UBUNTU_SUITES"
        echo "deb $base $codename-updates $UBUNTU_SUITES"
        echo "deb $base $codename-security $UBUNTU_SUITES"
        echo "deb $base $codename-backports $UBUNTU_SUITES"
    } > "$f"
    log "已写入干净官方源：$base"
}

run_apt_update() {
    # 完整保留 apt 输出；失败时回显真实错误尾部，便于定位（不使用 allow-insecure 降级）
    local logf=/tmp/rustdesk-apt-update.log
    if apt-get update -y 2>&1 | tee "$logf"; then
        return 0
    fi
    echo "---------------- apt-get update 真实错误（尾部）----------------" >&2
    tail -n 25 "$logf" >&2
    echo "----------------------------------------------------------------" >&2
    return 1
}

rebuild_and_update() {
    local host codename base
    host="$(pick_mirror)"
    [ -n "$host" ] || { warn "所有候选镜像均不可达，请检查网络出口/DNS"; return 1; }
    codename="$(. /etc/os-release 2>/dev/null && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
    [ -n "$codename" ] || { warn "无法识别 Ubuntu 代号"; return 1; }
    base="$(detect_base_url "$host" "$codename")"
    [ -n "$base" ] || { warn "未找到代号 $codename 的可用源路径"; return 1; }
    log "切换 apt 软件源，镜像主机：$host"
    quarantine_sources
    write_clean_sources "$base" "$codename"
    log "刷新软件源索引..."
    run_apt_update
}

ensure_apt_update() {
    # 默认先正常更新；--change-source 强制换源；更新失败则隔离失效源并切换干净官方源
    log "更新软件源索引..."
    if [ "$CHANGE_SOURCE" -ne 1 ] && run_apt_update; then
        return 0
    fi
    [ "$CHANGE_SOURCE" -eq 1 ] && log "按参数要求强制换源..."
    warn "当前软件源不可用（常见于第三方/失效源或 EOL 版本），开始隔离并切换到干净官方源..."
    rebuild_and_update \
        || die "换源后 apt-get update 仍失败。原源已备份于 ${APT_BACKUP_DIR:-/var/backups/rustdesk-apt-*}，请检查网络/DNS/代理后手动恢复"
    log "软件源索引刷新成功"
}

# ---------- 2. 安装 deb 包（自动解决依赖） ----------
ensure_apt_update

log "安装 $DEB_PATH ..."
if ! apt-get install -y "$DEB_PATH"; then
    warn "apt 直接安装失败，改用 dpkg + 修复依赖的方式重试..."
    dpkg -i "$DEB_PATH" || true
    apt-get -f install -y
fi

command -v rustdesk >/dev/null 2>&1 || die "安装后未找到 rustdesk 命令，安装失败"
log "RustDesk 版本：$(rustdesk --version 2>/dev/null | head -n1 || echo '未知')"

# ---------- 3. 导入加密的中继/ID 服务器配置 ----------
# rustdesk --config 接受以 == 开头的加密配置串，写入 /root/.config/rustdesk/RustDesk2.toml
# rustdesk.service 以 root 运行，因此必须用 root 身份导入
log "导入中继/ID 服务器配置..."
timeout 60 rustdesk --config "$RUSTDESK_CONFIG" \
    || die "配置导入失败，请检查配置串是否正确"

CONFIG_FILE="/root/.config/rustdesk/RustDesk2.toml"
[ -f "$CONFIG_FILE" ] || warn "未发现 $CONFIG_FILE，请手动确认配置是否生效"

# ---------- 4. 重启服务使配置生效 ----------
SYSTEMD_ENV=0
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    log "重启 rustdesk 服务..."
    systemctl enable rustdesk.service >/dev/null 2>&1 || true
    systemctl restart rustdesk.service
    sleep 2
    if systemctl is-active --quiet rustdesk.service; then
        log "rustdesk 服务运行正常"
        SYSTEMD_ENV=1
    else
        warn "rustdesk 服务未处于 active 状态，可执行：systemctl status rustdesk 查看原因"
    fi
else
    warn "当前不是 systemd 环境，请手动执行：rustdesk --service"
fi

# ---------- 5. 设置固定（永久）无人值守密码 ----------
# rustdesk --password 通过 IPC 把密码哈希写入运行中的服务，因此服务必须先启动；
# 密码以哈希形式保存在 /root/.config/rustdesk/RustDesk.toml 的 permanent_password 字段。
log "设置固定无人值守密码..."
PW_OK=0
for _ in 1 2 3 4 5; do
    if timeout 30 rustdesk --password "$RUSTDESK_PASSWORD" >/dev/null 2>&1; then
        PW_OK=1
        break
    fi
    sleep 2
done
if [ "$PW_OK" -eq 1 ]; then
    log "固定密码设置成功"
else
    if [ "$SYSTEMD_ENV" -eq 1 ]; then
        die "固定密码设置失败（服务 IPC 无响应），可稍后手动执行：sudo rustdesk --password '你的密码'"
    else
        warn "非 systemd 环境未能设置密码，请在服务运行后手动执行：sudo rustdesk --password '你的密码'"
    fi
fi

# ---------- 6. 读取本机 ID ----------
RD_ID=""
for _ in 1 2 3 4 5; do
    RD_ID="$(timeout 20 rustdesk --get-id 2>/dev/null | tr -d '[:space:]' || true)"
    [ -n "$RD_ID" ] && break
    sleep 2
done

echo
log "==================== 安装完成 ===================="
echo "  配置文件：$CONFIG_FILE"
echo "  固定密码：$RUSTDESK_PASSWORD"
if [ -n "$RD_ID" ]; then
    echo "  本机 ID ：$RD_ID"
else
    warn "暂未读取到本机 ID，可稍后手动执行：sudo rustdesk --get-id"
fi
echo "  服务状态：systemctl status rustdesk"
log "=================================================="

# 若换源时隔离了第三方源，提示恢复方法（不自动恢复，避免再次触发安全验证失败）
if [ -n "$APT_BACKUP_DIR" ] && [ -d "$APT_BACKUP_DIR/sources.list.d" ] && ls "$APT_BACKUP_DIR/sources.list.d/" >/dev/null 2>&1; then
    warn "安装期间已隔离原第三方软件源，确认无误后可恢复："
    echo "    sudo mv $APT_BACKUP_DIR/sources.list.d/* /etc/apt/sources.list.d/ && sudo apt-get update"
fi

# 清理远程下载到 /tmp 的安装包（本地包不动）
if [ "$DOWNLOADED" -eq 1 ]; then
    rm -f "$DEB_PATH" 2>/dev/null || true
fi
