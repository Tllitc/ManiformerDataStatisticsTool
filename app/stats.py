"""把 scanner.scan 的结果聚合为 spec「扫描结果 JSON 结构」定义的完整统计。"""

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    _LOCAL_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # 缺少 tzdata 时回退到固定东八区
    _LOCAL_TZ = timezone(timedelta(hours=8))

NO_GROUP = "(无)"


def fmt_hm(seconds):
    """与脚本 formatHM 一致：72h14m / 72h / 14m。"""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    if h > 0 and m > 0:
        return "%dh%dm" % (h, m)
    if h > 0:
        return "%dh" % h
    return "%dm" % m


def fmt_hms(seconds):
    """单段展示：1h2m3s / 2m31s / 31s。"""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return "%dh%dm%ds" % (h, m, s)
    if m > 0:
        return "%dm%ds" % (m, s)
    return "%ds" % s


def _unique_sorted(values):
    return sorted({v for v in values if v})


def _episode_date(ep):
    create_time = ep.get("create_time") or ""
    if create_time:
        return create_time[:10]
    for key in ("clip_start_time_ts", "clip_start_time"):
        ts = ep.get(key)
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or ts <= 0:
            continue
        if ts > 1e14:  # 纳秒
            ts /= 1e9
        elif ts > 1e11:  # 毫秒
            ts /= 1e3
        try:
            return datetime.fromtimestamp(ts, _LOCAL_TZ).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            continue
    return "unknown"


def _first_last(eps):
    times = [e["create_time"] for e in eps if e.get("create_time")]
    return (min(times) if times else "", max(times) if times else "")


def _group_episodes(episodes, key_func):
    grouped = {}
    for ep in episodes:
        grouped.setdefault(key_func(ep), []).append(ep)
    return grouped


def build_result(root, scan_data, scanned_at=None, scan_mode="full"):
    """输入 scanner.scan 的返回，输出完整统计结果 dict。"""
    episodes = []
    for ep in scan_data.get("episodes", []):
        e = dict(ep)
        e["hm"] = fmt_hms(e.get("seconds", 0))
        episodes.append(e)

    total_seconds = sum(e["seconds"] for e in episodes)

    by_device = []
    for device_id, eps in _group_episodes(episodes, lambda e: e["device_id"]).items():
        seconds = sum(e["seconds"] for e in eps)
        first_time, last_time = _first_last(eps)
        by_device.append({
            "device_id": device_id,
            "episodes": len(eps),
            "seconds": seconds,
            "hm": fmt_hm(seconds),
            "task_types": _unique_sorted(e.get("task_type", "") for e in eps),
            "roles": _unique_sorted(e.get("role", "") for e in eps),
            "first_time": first_time,
            "last_time": last_time,
        })
    by_device.sort(key=lambda r: (-r["seconds"], r["device_id"]))

    by_task = []
    for task_uuid, eps in _group_episodes(episodes, lambda e: e.get("task_uuid", "")).items():
        seconds = sum(e["seconds"] for e in eps)
        first_time, last_time = _first_last(eps)
        by_task.append({
            "task_uuid": task_uuid,
            "job_uuids": _unique_sorted(e.get("job_uuid", "") for e in eps),
            "episodes": len(eps),
            "seconds": seconds,
            "hm": fmt_hm(seconds),
            "devices": _unique_sorted(e["device_id"] for e in eps),
            "groups": len({e.get("group_id") or NO_GROUP for e in eps}),
            "user_ids": _unique_sorted(e.get("user_id", "") for e in eps),
            "first_time": first_time,
            "last_time": last_time,
        })
    by_task.sort(key=lambda r: (-r["seconds"], r["task_uuid"]))

    by_group = []
    for group_id, eps in _group_episodes(episodes, lambda e: e.get("group_id") or NO_GROUP).items():
        seconds = sum(e["seconds"] for e in eps)
        start_time, _ = _first_last(eps)
        by_group.append({
            "group_id": group_id,
            "episodes": len(eps),
            "seconds": seconds,
            "hm": fmt_hm(seconds),
            "devices": _unique_sorted(e["device_id"] for e in eps),
            "task_uuid": eps[0].get("task_uuid", ""),
            "start_time": start_time,
            "bytes": sum(int(e.get("dir_size_bytes") or 0) for e in eps),
        })
    by_group.sort(key=lambda r: (-r["seconds"], r["group_id"]))

    by_date = []
    for date, eps in _group_episodes(episodes, _episode_date).items():
        seconds = sum(e["seconds"] for e in eps)
        by_date.append({
            "date": date,
            "episodes": len(eps),
            "seconds": seconds,
            "hm": fmt_hm(seconds),
        })
    by_date.sort(key=lambda r: r["date"])

    if scanned_at is None:
        scanned_at = datetime.now().astimezone().isoformat(timespec="seconds")

    return {
        "root": root,
        "scanned_at": scanned_at,
        "total_is_reliable": scan_data.get("total_is_reliable", True),
        "total_duration_seconds": total_seconds,
        "total_duration_hm": fmt_hm(total_seconds),
        "umi_episodes": len(episodes),
        "groups": len({e.get("group_id") or NO_GROUP for e in episodes}),
        "devices": len({e["device_id"] for e in episodes}),
        "tasks": len({e["task_uuid"] for e in episodes if e.get("task_uuid")}),
        "total_file_size_bytes": sum(int(e.get("dir_size_bytes") or 0) for e in episodes),
        "by_device": by_device,
        "by_task": by_task,
        "by_group": by_group,
        "by_date": by_date,
        "episodes": episodes,
        "anomalies": scan_data.get("anomalies", {}),
        "scan_mode": scan_mode,
        "cache_stats": scan_data.get("cache_stats"),
    }
