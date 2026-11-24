"""扫描数据盘上的 umi 数据段（episode）。

统计口径与 umi-total-duration.sh 对齐：
- 数据段 = 含 meta_info.json 的目录；相对根目录深度 <=4 计入，深度 5 仅报告 too_deep。
- 发现数据段后不再向下遍历（嵌套段只计数 nested_skipped，不参与统计）。
- 剪枝排除：. 开头、Thumbs.db、System Volume Information、$RECYCLE.BIN、RECYCLER。
- 仅 robot_type 归一化为 umi 且 duration 有效、AID 非空的段计入总时长。
"""

import inspect
import json
import math
import os
import stat
import time

EXCLUDED_NAMES = {"Thumbs.db", "System Volume Information", "$RECYCLE.BIN", "RECYCLER"}
MAX_EPISODE_DEPTH = 4
MAX_PROBE_DEPTH = MAX_EPISODE_DEPTH + 1  # 多探测一层，用于报告 too_deep
MAX_DURATION_SECONDS = 1000000
PROGRESS_EVERY = 50


def _is_excluded(name):
    return name.startswith(".") or name in EXCLUDED_NAMES


def _rel_depth(path, root):
    """目录相对 root 的深度：root 本身为 0，按路径分隔符分段计数。"""
    rel = os.path.relpath(path, root)
    if rel == ".":
        return 0
    return rel.count(os.sep) + 1


def _normalize_robot_type(raw):
    if not isinstance(raw, str):
        return "unknown"
    t = raw.strip().lower()
    if t in ("umi", "umi_master"):
        return "umi"
    if t.startswith("g2"):
        return "g2"
    if t.startswith("gopro"):
        return "gopro"
    return t or "unknown"


def _duration_seconds(meta):
    """返回 (整秒, 原因)；原因为 "" 表示有效。按段四舍五入 int(v+0.5)。"""
    if "duration" not in meta:
        return 0, "缺失"
    v = meta["duration"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return 0, "非数值"
    if v <= 0:
        return 0, "小于等于0"
    if v > MAX_DURATION_SECONDS:
        return 0, "超出合理范围"
    return int(v + 0.5), ""


def _text(meta, key):
    v = meta.get(key)
    if isinstance(v, str):
        return v.strip()
    if v is None:
        return ""
    return str(v)


def _int(meta, key):
    v = meta.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return 0
    return int(v)


def _validate_ok(meta):
    dv = meta.get("data_validate")
    if not isinstance(dv, dict):
        return False
    if "ok" in dv:
        return bool(dv["ok"])
    # 实际数据为 {AID: {code, reason, validate}} 的按设备结构。
    entries = [v for v in dv.values() if isinstance(v, dict)]
    if entries:
        return all(bool(e.get("ok", e.get("validate", False))) for e in entries)
    return False


def _segment_stats(segment_path, root, onerror, tick):
    """单次遍历数据段，返回 (total_bytes, file_count, nested_count)。

    大小/文件数口径（原 _dir_size）：全深度、不剪枝（隐藏文件也占磁盘）、
    不跟随符号链接递归；每条目仅一次 lstat（entry.stat(follow_symlinks=False)），
    按 stat.S_ISDIR/S_ISLNK 分类：指向目录的符号链接（追加 entry.is_dir() 跟随判定）
    不递归也不计大小，其余条目按 lstat 大小累加，stat OSError 跳过。
    nested 口径（原 _count_nested）：非段自身、含非目录 meta_info.json、
    无被排除祖先、相对根深度 <= MAX_PROBE_DEPTH 的目录计数。
    目录访问失败仅对"嵌套口径会访问的目录"（probe）调用 onerror，其余静默。
    tick 为可选回调 tick(已处理文件条目数)：entry 循环内每 256 个条目检查一次时间，
    距上次 >=0.5s 即调用；每处理完一个目录后同样检查调用。
    """
    total = 0
    file_count = 0
    nested = 0
    seen_files = 0
    last_tick = time.monotonic() if tick is not None else 0.0
    # 栈元素 (dirpath, probe)；probe 表示剪枝 walk 会访问该目录（用于 nested/onerror）。
    stack = [(segment_path, True)]
    while stack:
        dirpath, probe = stack.pop()
        child_probe = probe and _rel_depth(dirpath, root) < MAX_PROBE_DEPTH
        try:
            it = os.scandir(dirpath)
        except OSError as err:
            if probe:
                onerror(err)
            continue
        subdirs = []
        dir_total = 0
        dir_count = 0
        has_meta = False
        failed = False
        entry_count = 0
        with it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError as err:
                    # 与 os.walk 一致：迭代中途失败则放弃该目录的全部条目。
                    if probe:
                        onerror(err)
                    failed = True
                    break
                entry_count += 1
                if tick is not None and entry_count % 256 == 0:
                    now = time.monotonic()
                    if now - last_tick >= 0.5:
                        last_tick = now
                        tick(seen_files)
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    subdirs.append((entry.path, child_probe and not _is_excluded(entry.name)))
                else:
                    if stat.S_ISLNK(st.st_mode):
                        try:
                            points_dir = entry.is_dir()  # 跟随符号链接
                        except OSError:
                            points_dir = False
                        if points_dir:
                            continue
                    seen_files += 1
                    if entry.name == "meta_info.json":
                        has_meta = True
                    dir_total += st.st_size
                    dir_count += 1
        if failed:
            continue
        total += dir_total
        file_count += dir_count
        if probe and dirpath != segment_path and has_meta:
            nested += 1
        stack.extend(reversed(subdirs))
        if tick is not None:
            now = time.monotonic()
            if now - last_tick >= 0.5:
                last_tick = now
                tick(seen_files)
    return total, file_count, nested


def _accepts_detail(progress):
    """判断回调是否可接受 4 个位置参数（含 *args/**kwargs 情况）。"""
    try:
        inspect.signature(progress).bind(0, 0, "", "")
    except (TypeError, ValueError):
        return False
    return True


def scan(root, progress=None, cache=None):
    """自顶向下扫描 root，返回 {episodes, anomalies, total_is_reliable, scanned_dirs}。

    progress 为可选回调 progress(scanned_dirs, episodes_found, current_path, detail="")；
    仅接受三参的旧回调自动降级为三参调用。顶层每 50 个目录或距上次 >=0.5s 上报，
    数据段内部统计期间每 >=0.5s 上报一次（detail 描述已处理文件数）。

    cache 为可选 dict（段绝对路径→{dir_mtime_ns, meta_mtime_ns, meta_size, bytes,
    file_count, nested, unreadable_dirs}）。段目录与 meta_info.json 的
    mtime_ns/size 均未变时命中：跳过段内遍历，复用统计值与段内 unreadable_dirs；
    未命中则全量统计。meta_info.json 无论命中与否都重新读取解析。
    cache 非 None 时返回值额外含 segment_cache（本次全部可见段的新缓存，
    旧缓存中未出现的段自然剪枝）与 cache_stats={reused, rescanned, added}；
    cache=None 时返回结构与现状完全一致。
    """
    root = os.path.abspath(root)
    if len(root) > 1:
        root = root.rstrip(os.sep) or os.sep

    episodes = []
    anomalies = {
        "missing_duration": [],
        "malformed_meta": [],
        "unreadable": [],
        "unreadable_dirs": [],
        "missing_aid": [],
        "too_deep": [],
        "non_umi": {},
        "nested_skipped": 0,
    }
    scanned_dirs = 0
    last_path = root
    segment_cache = {} if cache is not None else None
    cache_stats = {"reused": 0, "rescanned": 0, "added": 0}

    def onerror(err):
        anomalies["unreadable_dirs"].append(str(err))

    report = None
    last_report = time.monotonic()
    if progress is not None:
        if _accepts_detail(progress):
            def report(dirs_done, found, path, detail=""):
                progress(dirs_done, found, path, detail)
        else:
            def report(dirs_done, found, path, detail=""):
                progress(dirs_done, found, path)

    for dirpath, dirs, files in os.walk(root, topdown=True, onerror=onerror):
        scanned_dirs += 1
        last_path = dirpath
        depth = _rel_depth(dirpath, root)
        dirs[:] = sorted(d for d in dirs if not _is_excluded(d))
        if depth >= MAX_PROBE_DEPTH:
            dirs[:] = []  # 深度 5 仅探测，不再深入
        if report is not None:
            now = time.monotonic()
            if scanned_dirs % PROGRESS_EVERY == 0 or now - last_report >= 0.5:
                last_report = now
                report(scanned_dirs, len(episodes), dirpath, "")

        if "meta_info.json" not in files:
            continue

        # 这是一个数据段。
        if depth > MAX_EPISODE_DEPTH:
            anomalies["too_deep"].append(dirpath)
            continue

        seg_errors = []

        def seg_onerror(err):
            seg_errors.append(str(err))

        meta_path = os.path.join(dirpath, "meta_info.json")
        old_entry = cache.get(dirpath) if cache is not None else None
        sig = None
        if cache is not None:
            try:
                dir_st = os.stat(dirpath)
                meta_st = os.stat(meta_path)
                sig = (dir_st.st_mtime_ns, meta_st.st_mtime_ns, meta_st.st_size)
            except OSError:
                sig = None  # 视为未命中；meta 读取流程照旧走 unreadable 异常
        hit = (old_entry is not None and sig is not None
               and (old_entry["dir_mtime_ns"], old_entry["meta_mtime_ns"],
                    old_entry["meta_size"]) == sig)
        if hit:
            dir_size_bytes = old_entry["bytes"]
            dir_file_count = old_entry["file_count"]
            nested_count = old_entry["nested"]
            anomalies["unreadable_dirs"].extend(old_entry["unreadable_dirs"])
            segment_cache[dirpath] = old_entry
            cache_stats["reused"] += 1
            if report is not None:
                last_report = time.monotonic()
                report(scanned_dirs, len(episodes), dirpath,
                       "增量复用（已复用 %d 段）" % cache_stats["reused"])
        else:
            seg_tick = None
            if report is not None:
                def seg_tick(nfiles):
                    nonlocal last_report
                    last_report = time.monotonic()
                    report(scanned_dirs, len(episodes), dirpath,
                           "统计大小/嵌套：已处理 %d 个文件" % nfiles)

            dir_size_bytes, dir_file_count, nested_count = _segment_stats(dirpath, root, seg_onerror, seg_tick)
            anomalies["unreadable_dirs"].extend(seg_errors)
            if cache is not None:
                segment_cache[dirpath] = {
                    "dir_mtime_ns": sig[0] if sig is not None else None,
                    "meta_mtime_ns": sig[1] if sig is not None else None,
                    "meta_size": sig[2] if sig is not None else None,
                    "bytes": dir_size_bytes,
                    "file_count": dir_file_count,
                    "nested": nested_count,
                    "unreadable_dirs": seg_errors,
                }
                if old_entry is not None:
                    cache_stats["rescanned"] += 1
                else:
                    cache_stats["added"] += 1
        anomalies["nested_skipped"] += nested_count
        dirs[:] = []  # 数据段内部不再向下遍历

        try:
            with open(meta_path, "rb") as f:
                content = f.read()
        except OSError:
            anomalies["unreadable"].append(dirpath)
            continue
        try:
            meta = json.loads(content)
        except ValueError:
            anomalies["malformed_meta"].append({"path": dirpath, "reason": "JSON 无法解析"})
            continue
        if not isinstance(meta, dict):
            anomalies["malformed_meta"].append({"path": dirpath, "reason": "不是 JSON 对象"})
            continue

        robot_type = _normalize_robot_type(meta.get("robot_type"))
        if robot_type != "umi":
            anomalies["non_umi"][robot_type] = anomalies["non_umi"].get(robot_type, 0) + 1
            continue

        seconds, reason = _duration_seconds(meta)
        if reason:
            anomalies["missing_duration"].append({"path": dirpath, "reason": reason})
            continue

        aid = _text(meta, "AID")
        if not aid:
            anomalies["missing_aid"].append({"path": dirpath, "seconds": seconds})
            continue

        device_id = _text(meta, "device_sn") or aid
        episodes.append({
            "path": dirpath,
            "device_id": device_id,
            "aid": aid,
            "task_uuid": _text(meta, "task_uuid"),
            "job_uuid": _text(meta, "job_uuid"),
            "group_id": _text(meta, "group_id"),
            "group_count": _int(meta, "group_count"),
            "seconds": seconds,
            "task_type": _text(meta, "task_type"),
            "role": _text(meta, "role"),
            "user_id": _text(meta, "user_id"),
            "create_time": _text(meta, "create_time"),
            "clip_start_time": meta.get("clip_start_time"),
            "clip_start_time_ts": meta.get("clip_start_time_ts"),
            "file_size": _int(meta, "file_size"),
            "dir_size_bytes": dir_size_bytes,
            "dir_file_count": dir_file_count,
            "sw_version": _text(meta, "sw_version"),
            "validate_ok": _validate_ok(meta),
            "is_encrypted": bool(meta.get("is_encrypted", False)),
        })

    if report is not None:
        report(scanned_dirs, len(episodes), last_path, "")

    result = {
        "episodes": episodes,
        "anomalies": anomalies,
        "total_is_reliable": not (anomalies["unreadable"] or anomalies["unreadable_dirs"]),
        "scanned_dirs": scanned_dirs,
    }
    if cache is not None:
        result["segment_cache"] = segment_cache
        result["cache_stats"] = cache_stats
    return result
