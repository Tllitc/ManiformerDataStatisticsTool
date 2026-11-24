# -*- coding: utf-8 -*-
"""客户机自更新器：检查 / 下载 / 校验 / 备份 / 替换 / 重启 / 回滚。

仅使用 Python3 标准库。更新范围只限于工具根目录下的 app/、start.sh、VERSION，
绝不触碰根目录 data/ 以及 ~/.umi-stats-tool/ 中除 backup/、update.log 外的内容。
"""

import argparse
import compileall
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request

# ============================================================================
# 更新源地址配置（仓库中不写入真实发布机地址）：
# 1) 客户机 ~/.umi-stats-tool/update.json 的 manifest_url 字段（优先级最高）；
# 2) 环境变量 UMI_MANIFEST_URL，例如 "http://<发布机IP>:8766/version.json"；
# 3) 都未配置时使用下面的本机占位默认值（检查更新会静默失败，不影响启动）。
# ============================================================================
DEFAULT_MANIFEST_URL = os.environ.get(
    "UMI_MANIFEST_URL", "http://127.0.0.1:8766/version.json"
)

# 工具根目录（本文件位于 <根目录>/app/updater.py）
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 参与更新/备份/回滚的条目
UPDATE_ITEMS = ("app", "start.sh", "VERSION")

# 网络超时（秒），规格要求 ≤ 10 秒
HTTP_TIMEOUT = 10


def _state_dir():
    """用户状态目录 ~/.umi-stats-tool/（不存在则创建）。"""
    path = os.path.join(os.path.expanduser("~"), ".umi-stats-tool")
    os.makedirs(path, exist_ok=True)
    return path


def _backup_dir():
    """备份目录 ~/.umi-stats-tool/backup/。"""
    return os.path.join(_state_dir(), "backup")


def _log(message):
    """追加日志到 ~/.umi-stats-tool/update.log，日志失败不影响主流程。"""
    try:
        line = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
        with open(os.path.join(_state_dir(), "update.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def get_manifest_url():
    """清单 URL：优先读 ~/.umi-stats-tool/update.json 的 manifest_url，否则用默认常量。"""
    try:
        cfg_path = os.path.join(_state_dir(), "update.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        url = cfg.get("manifest_url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    except (OSError, ValueError):
        pass
    return DEFAULT_MANIFEST_URL


def _persist_manifest_url():
    """更新成功后，若客户机未配置 manifest_url，把当前生效地址写入 update.json。

    保证从带硬编码地址的旧版本升级到新版本后，后续更新仍能找到更新源；
    已有有效配置时不改动，写入失败不影响更新结果。
    """
    cfg_path = os.path.join(_state_dir(), "update.json")
    cfg = {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            cfg = loaded
    except (OSError, ValueError):
        pass
    url = cfg.get("manifest_url")
    if isinstance(url, str) and url.strip():
        return
    cfg["manifest_url"] = get_manifest_url()
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        _log("已写入更新源配置：%s" % cfg["manifest_url"])
    except OSError:
        pass


def read_local_version():
    """读取工具根目录 VERSION，缺失/不可读返回 "0.0.0"。"""
    try:
        with open(os.path.join(ROOT_DIR, "VERSION"), "r", encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _parse_version(text):
    """把版本号解析为数字元组；非法版本号返回 None。"""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not re.fullmatch(r"\d+(\.\d+)*", text):
        return None
    return tuple(int(x) for x in text.split("."))


def _version_greater(remote, local):
    """按数字元组比较 remote > local（长度不同时补零对齐）。非法版本视为不更新。"""
    rv, lv = _parse_version(remote), _parse_version(local)
    if rv is None or lv is None:
        return False
    n = max(len(rv), len(lv))
    rv = rv + (0,) * (n - len(rv))
    lv = lv + (0,) * (n - len(lv))
    return rv > lv


def check_update():
    """拉取 version.json，有更高版本返回 manifest dict，否则/出错返回 None。"""
    url = get_manifest_url()
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("清单不是 JSON 对象")
        remote_ver = manifest.get("version")
        if _version_greater(remote_ver, read_local_version()):
            return manifest
        return None
    except Exception as exc:  # 网络/解析等任何错误静默跳过，仅记日志
        _log("检查更新失败（%s）：%s" % (url, exc))
        return None


def wait_for_change(since, timeout=60):
    """长轮询等待更新源版本变化，返回 {"changed": bool, "version": ...}。

    /wait 地址由清单 URL 同目录推导（兼容自定义清单路径）。
    HTTP 404 或其他错误按异常抛出，由调用方降级处理。
    """
    parts = urllib.parse.urlsplit(get_manifest_url())
    wait_path = parts.path.rsplit("/", 1)[0] + "/wait"
    query = urllib.parse.urlencode({"since": since, "timeout": timeout})
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, wait_path, query, ""))
    with urllib.request.urlopen(url, timeout=timeout + 10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("长轮询响应不是 JSON 对象")
    return data


def _sha256_of(path):
    """计算文件 sha256。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(tar, dest):
    """解压 tar，确保所有成员路径（含链接目标）都落在 dest 内，防目录穿越。"""
    dest = os.path.realpath(dest)

    def _within(path):
        real = os.path.realpath(path)
        return real == dest or real.startswith(dest + os.sep)

    for member in tar.getmembers():
        target = os.path.join(dest, member.name)
        if not _within(target):
            raise ValueError("更新包含非法路径成员：%s" % member.name)
        if member.issym() or member.islnk():
            link_target = os.path.join(dest, os.path.dirname(member.name), member.linkname)
            if not _within(link_target):
                raise ValueError("更新包含非法链接成员：%s" % member.name)
    tar.extractall(dest)


def _find_package_dir(extract_dir):
    """在解压目录中定位包含 app/、start.sh、VERSION 的包根（支持一层子目录）。"""

    def _ok(path):
        return (os.path.isdir(os.path.join(path, "app"))
                and os.path.isfile(os.path.join(path, "start.sh"))
                and os.path.isfile(os.path.join(path, "VERSION")))

    if _ok(extract_dir):
        return extract_dir
    for name in os.listdir(extract_dir):
        sub = os.path.join(extract_dir, name)
        if os.path.isdir(sub) and _ok(sub):
            return sub
    raise ValueError("更新包缺少 app/、start.sh 或 VERSION")


def _remove(path):
    """删除文件或目录（存在时）。"""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.exists(path) or os.path.islink(path):
        os.remove(path)


def apply_update(manifest, restart=True):
    """下载并应用更新。成功返回 True（restart=True 时直接 execv 重启进程）；失败返回 False。"""
    msg = "发现新版本 v%s（当前 v%s），开始更新" % (manifest.get("version"), read_local_version())
    _log(msg)
    print(msg, flush=True)
    tmp_dir = tempfile.mkdtemp(prefix="umi-update-")
    backup_dir = _backup_dir()
    backed_up = []   # 已移入备份目录的条目
    installed = []   # 已移入根目录的新条目
    try:
        # 1) 下载更新包（相对 URL 基于清单 URL 解析）
        pkg_url = urllib.request.urljoin(get_manifest_url(), manifest["url"])
        pkg_path = os.path.join(tmp_dir, "update.tar.gz")
        with urllib.request.urlopen(pkg_url, timeout=HTTP_TIMEOUT) as resp:
            with open(pkg_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        # 2) sha256 校验
        actual = _sha256_of(pkg_path)
        expected = str(manifest.get("sha256", "")).lower()
        if actual != expected:
            raise ValueError("sha256 校验失败：期望 %s，实际 %s" % (expected, actual))

        # 3) 安全解压并定位包根
        extract_dir = os.path.join(tmp_dir, "extract")
        os.makedirs(extract_dir)
        with tarfile.open(pkg_path, "r:*") as tar:
            _safe_extract(tar, extract_dir)
        pkg_dir = _find_package_dir(extract_dir)

        # 4) 新代码语法检查（compile_dir 无 doraise 参数，用返回值判定并捕获错误输出）
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(err_buf):
            compiled = compileall.compile_dir(os.path.join(pkg_dir, "app"), quiet=1)
        if not compiled:
            raise ValueError("新代码 compileall 语法检查失败：%s" % err_buf.getvalue().strip())

        # 5) 备份旧版本（清空旧备份，仅保留一代）
        _remove(backup_dir)
        os.makedirs(backup_dir)
        for name in UPDATE_ITEMS:
            src = os.path.join(ROOT_DIR, name)
            if os.path.exists(src):
                shutil.move(src, os.path.join(backup_dir, name))
                backed_up.append(name)

        # 6) 移入新文件，保持 start.sh 可执行位
        for name in UPDATE_ITEMS:
            shutil.move(os.path.join(pkg_dir, name), os.path.join(ROOT_DIR, name))
            installed.append(name)
        os.chmod(os.path.join(ROOT_DIR, "start.sh"), 0o755)

        msg = "已更新到 v%s" % manifest.get("version")
        _log(msg)
        print(msg, flush=True)

        # 8) 未配置更新源的客户机：固化当前生效地址，保证后续版本仍可自动更新
        _persist_manifest_url()
    except Exception as exc:
        msg = "更新失败：%s" % exc
        _log(msg)
        print(msg, flush=True)
        # 7) 失败恢复：移除已安装的新条目，把备份移回根目录
        for name in installed:
            try:
                _remove(os.path.join(ROOT_DIR, name))
            except OSError:
                pass
        for name in backed_up:
            try:
                shutil.move(os.path.join(backup_dir, name), os.path.join(ROOT_DIR, name))
            except OSError:
                pass
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if restart:
        # 9) 重启当前进程（cwd 不变）
        os.execv(sys.executable, [sys.executable] + sys.argv)
    return True


def rollback():
    """从备份恢复上一版本（当前版本与备份互换）。无备份返回 False。"""
    backup_dir = _backup_dir()
    if not (os.path.isdir(os.path.join(backup_dir, "app"))
            and os.path.isfile(os.path.join(backup_dir, "VERSION"))):
        return False
    tmp_dir = tempfile.mkdtemp(prefix="umi-rollback-")
    staged = []      # 已暂存到 tmp 的当前版本条目
    restored = []    # 已从备份恢复到根目录的条目
    try:
        # 当前版本先暂存到临时目录
        for name in UPDATE_ITEMS:
            src = os.path.join(ROOT_DIR, name)
            if os.path.exists(src):
                shutil.move(src, os.path.join(tmp_dir, name))
                staged.append(name)
        # 备份恢复到根目录
        for name in UPDATE_ITEMS:
            src = os.path.join(backup_dir, name)
            if os.path.exists(src):
                shutil.move(src, os.path.join(ROOT_DIR, name))
                restored.append(name)
        os.chmod(os.path.join(ROOT_DIR, "start.sh"), 0o755)
        # 当前版本移入备份目录（实现互换，备份始终保留一代）
        for name in staged:
            shutil.move(os.path.join(tmp_dir, name), os.path.join(backup_dir, name))
        _log("已回滚到 v%s" % read_local_version())
        return True
    except Exception as exc:
        _log("回滚失败：%s" % exc)
        # 尽量恢复：把已恢复的条目退回备份，把暂存的当前版本移回根目录
        for name in restored:
            try:
                shutil.move(os.path.join(ROOT_DIR, name), os.path.join(backup_dir, name))
            except OSError:
                pass
        for name in staged:
            try:
                shutil.move(os.path.join(tmp_dir, name), os.path.join(ROOT_DIR, name))
            except OSError:
                pass
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="工具自更新器")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-on-start", action="store_true",
                       help="启动时检查并应用更新（不重启），失败静默退出 0")
    group.add_argument("--check-only", action="store_true",
                       help="只检查是否有新版本，不应用")
    group.add_argument("--rollback", action="store_true",
                       help="回滚到上一版本")
    args = parser.parse_args()

    if args.rollback:
        if rollback():
            print("已回滚到 v%s" % read_local_version())
            sys.exit(0)
        print("回滚失败：未找到可用的备份（~/.umi-stats-tool/backup/）")
        sys.exit(1)

    if args.check_only:
        try:
            manifest = check_update()
            local = read_local_version()
            if manifest:
                print("发现新版本 v%s（当前 v%s）" % (manifest.get("version"), local))
            else:
                print("当前版本 v%s，未发现可用更新" % local)
        except Exception:
            pass
        sys.exit(0)

    # --check-on-start：任何失败静默退出 0，不阻塞 start.sh 启动
    try:
        manifest = check_update()
        if manifest:
            apply_update(manifest, restart=False)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
