# -*- coding: utf-8 -*-
"""觅蜂平台（geniestudio.agibot.com）集成：Casdoor 登录 / 任务历史同步 / 本地缓存 / 任务关联。

状态目录：~/.umi-stats-tool/（token.json 权限 0600，platform_cache.json）。
密码仅在登录请求的表单中使用，绝不写入任何文件或日志。
"""

import base64
import json
import os
import re
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://geniestudio.agibot.com"
DEFAULT_TENANT = "mego"
DEFAULT_CLIENT_ID = "7fd3ac5466214bff6252"
STATE_DIR = os.path.expanduser("~/.umi-stats-tool")
TOKEN_PATH = os.path.join(STATE_DIR, "token.json")
CACHE_PATH = os.path.join(STATE_DIR, "platform_cache.json")
TASK_CENTER_CACHE_PATH = os.path.join(STATE_DIR, "task_center_cache.json")
TASK_CENTER_CACHE_VERSION = 1
TASK_CENTER_PAGE_SIZE = 500
JOB_PAGE_SIZE = 200
EPISODE_BATCH = 20
TIMEOUT = 30
PAGE_SIZE = 100

_TASK_DATE_RE = re.compile(r"-(\d{8})$")

# join_tasks 附加的平台字段（未命中时全部置 None）
_JOIN_FIELDS = (
    "task_name",
    "collectors",
    "project_name",
    "device_type_names",
    "expect_duration_seconds",
    "platform_created_at",
)


# ---------------------------------------------------------------- 内部工具

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _read_token():
    return _read_json(TOKEN_PATH)


def _write_token(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.chmod(TOKEN_PATH, 0o600)


def _network_error(exc):
    if isinstance(exc, requests.Timeout):
        return "连接觅蜂平台超时，请稍后重试"
    if isinstance(exc, requests.ConnectionError):
        return "无法连接觅蜂平台，请检查网络"
    return "网络请求失败：%s" % exc


def _extract_msg(payload):
    """从平台错误响应中提取可读信息。"""
    if not isinstance(payload, dict):
        return None
    for key in ("error_description", "message", "msg"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    err = payload.get("error")
    if isinstance(err, str) and err.strip():
        if err in ("invalid_grant", "invalid_client", "access_denied"):
            return "用户名或密码错误"
        return err.strip()
    return None


def _jwt_expires_at(token):
    """解析 JWT payload 中的 exp（不验签），失败返回 None。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
        if isinstance(exp, (int, float)) and exp > 0:
            return int(exp)
    except Exception:
        pass
    return None


def _get_client_id(tenant):
    """动态取租户 casdoor clientId，失败回退硬编码默认值。"""
    try:
        resp = requests.get(
            "%s/api/v1/tenants/%s" % (BASE_URL, tenant), timeout=TIMEOUT
        )
        payload = resp.json()
        client_id = (
            payload.get("data", {}).get("casdoor", {}).get("clientId")
        )
        if isinstance(client_id, str) and client_id:
            return client_id
    except (requests.RequestException, ValueError, AttributeError):
        pass
    return DEFAULT_CLIENT_ID


def _api_get(path, token, params=None):
    """带 Bearer 的 GET，解包 {code,message,data}。返回 (data, error)。"""
    resp = requests.get(
        BASE_URL + path,
        headers={"Authorization": "Bearer " + token},
        params=params,
        timeout=TIMEOUT,
    )
    try:
        payload = resp.json()
    except ValueError:
        return None, "平台返回非 JSON 响应（HTTP %s）" % resp.status_code
    if resp.status_code == 401:
        return None, "登录已失效，请重新登录"
    if resp.status_code != 200:
        return None, "平台请求失败：%s" % (
            _extract_msg(payload) or "HTTP %s" % resp.status_code
        )
    if isinstance(payload, dict) and payload.get("code", 0) != 0:
        return None, _extract_msg(payload) or "平台返回业务错误"
    data = payload.get("data") if isinstance(payload, dict) else payload
    return data, None


def _current_user(token):
    """校验 token 并返回 {tenant,name}。返回 (user, error)。"""
    data, err = _api_get("/api/v1/login/current-user", token)
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, "平台返回的用户信息格式异常"
    return {"tenant": data.get("tenant"), "name": data.get("name")}, None


def _dict_map(data):
    """兼容 data 为列表或 {list:[...]} 的字典接口，取 id→name 映射。"""
    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("list", "items", "records"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    mapping = {}
    for item in items or []:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        name = item.get("name") or item.get("label") or item.get("title")
        if name:
            mapping[str(item["id"])] = name
    return mapping


def _as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            num = float(value)
            return int(num) if num.is_integer() else num
        except ValueError:
            return None
    return None


def _aggregate(submissions):
    """按 task_uuid 聚合多条记录（每设备一条）。"""
    by_task = {}
    for rec in submissions:
        if not isinstance(rec, dict):
            continue
        uuid = rec.get("task_uuid")
        if not uuid:
            continue
        entry = by_task.setdefault(uuid, {
            "task_name": None,
            "collectors": [],
            "expect_duration_seconds": None,
            "project_tag_id": None,
            "device_type_ids": [],
            "platform_created_at": None,
        })
        if not entry["task_name"] and rec.get("task_name"):
            entry["task_name"] = rec["task_name"]
        collector = rec.get("collector_name")
        if collector and collector not in entry["collectors"]:
            entry["collectors"].append(collector)
        duration = _as_number(rec.get("expect_duration_seconds"))
        if duration is not None and (
            entry["expect_duration_seconds"] is None
            or duration > entry["expect_duration_seconds"]
        ):
            entry["expect_duration_seconds"] = duration
        project_tag_id = rec.get("project_tag_id")
        if entry["project_tag_id"] is None and project_tag_id not in (None, ""):
            entry["project_tag_id"] = project_tag_id
        device_type_id = rec.get("device_type_id")
        if device_type_id not in (None, "") and device_type_id not in entry["device_type_ids"]:
            entry["device_type_ids"].append(device_type_id)
        if not entry["platform_created_at"] and rec.get("created_at"):
            entry["platform_created_at"] = rec["created_at"]
    return by_task


# ------------------------------------------------ 任务中心（/api/v1/collect/*）

class TaskCenterError(Exception):
    """任务中心同步错误；is_permission=True 表示 40001 无权限（不写缓存）。"""

    def __init__(self, message, is_permission=False):
        super().__init__(message)
        self.is_permission = is_permission


def _is_not_authorized(err):
    """识别任务中心 40001 权限错误（账号级，需终止同步）。

    任务中心调用经 _tc_get 返回 (code, message) 元组；真实回包为
    {"code": 40001, "message": "验证失败: Not Authorized"}，code 才是可靠判据，
    message 文案不含 40001，不能只靠字符串匹配。
    """
    if isinstance(err, tuple) and len(err) == 2:
        code, message = err
        return code == 40001 or (
            isinstance(message, str) and "Not Authorized" in message
        )
    return isinstance(err, str) and "40001" in err and "Not Authorized" in err


# 资源级不可见（Data-Collector 可在 task/all 看到他人任务，但无权打开）：
# detail 返回 40401 资源不存在、job 返回 40005 权限不足，属正常权限边界，静默跳过。
_INVISIBLE_CODES = (40401, 40005)


def _is_invisible(err):
    return isinstance(err, tuple) and len(err) == 2 and err[0] in _INVISIBLE_CODES


def _add_warning(warnings, message, cap=20):
    """警告去重并封顶：最多 cap 条不同警告 + 1 条省略提示，共 cap+1 条。"""
    if len(warnings) > cap:  # 已追加省略提示，后续全部忽略
        return
    if message not in warnings:
        warnings.append(message)
    if len(warnings) == cap:
        warnings.append("其余同类问题已省略，详见服务日志")


def _tc_get(path, token, params=None):
    """任务中心专用 GET：同 _api_get，但业务错误返回 (code, message) 元组，

    保留原始 code 以便 _is_not_authorized 识别 40001（_api_get 会丢弃 code，
    而平台 message 为「验证失败: Not Authorized」不含 40001）。
    成功返回 (data, None)。
    """
    resp = requests.get(
        BASE_URL + path,
        headers={"Authorization": "Bearer " + token},
        params=params,
        timeout=TIMEOUT,
    )
    try:
        payload = resp.json()
    except ValueError:
        return None, (resp.status_code, "平台返回非 JSON 响应（HTTP %s）" % resp.status_code)
    if resp.status_code == 401:
        return None, (401, "登录已失效，请重新登录")
    if resp.status_code != 200:
        return None, (resp.status_code,
                      _extract_msg(payload) or "HTTP %s" % resp.status_code)
    if isinstance(payload, dict) and payload.get("code", 0) != 0:
        return None, (payload.get("code"),
                      _extract_msg(payload) or "平台返回业务错误")
    return (payload.get("data") if isinstance(payload, dict) else payload), None


def load_task_center_cache():
    """读取任务中心缓存；文件缺失/损坏/版本不符一律返回 None。"""
    try:
        with open(TASK_CENTER_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if (isinstance(data, dict)
            and data.get("version") == TASK_CENTER_CACHE_VERSION
            and isinstance(data.get("jobs"), dict)):
        return data
    return None


def _write_task_center_cache(cache):
    """tmp + os.replace 原子写入。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp_path = "%s.tmp%d" % (TASK_CENTER_CACHE_PATH, os.getpid())
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, TASK_CENTER_CACHE_PATH)


def _extract_local_dates(scan_result):
    """从扫描结果 episodes 的 create_time 前 10 位提取去重日期集合。"""
    dates = set()
    if not isinstance(scan_result, dict):
        return dates
    for ep in scan_result.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        create_time = ep.get("create_time")
        if isinstance(create_time, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", create_time[:10]):
            dates.add(create_time[:10])
    return dates


def _task_date_suffix(name):
    """任务名 -YYYYMMDD 后缀 → YYYY-MM-DD；无后缀/非法日期返回 None。"""
    if not isinstance(name, str):
        return None
    match = _TASK_DATE_RE.search(name)
    if not match:
        return None
    digits = match.group(1)
    try:
        return datetime.strptime(digits, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _iter_task_all_pages(token):
    """分页拉 /collect/task/all，逐页产出 (page_items, total)；按 id 降序。"""
    page_num = 1
    total = None
    seen = 0
    while True:
        data, err = _tc_get(
            "/api/v1/collect/task/all",
            token,
            params={"page_num": page_num, "page_size": TASK_CENTER_PAGE_SIZE},
        )
        if err:
            if _is_not_authorized(err):
                raise TaskCenterError(
                    "当前账号无任务中心访问权限（40001 Not Authorized），"
                    "请联系管理员开通后重试", True)
            raise TaskCenterError("拉取任务列表失败：%s" % err[1])
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise TaskCenterError("拉取任务列表失败：平台返回格式异常")
        items = data["list"]
        if isinstance(data.get("total"), int):
            total = data["total"]
        yield items, total
        if not items:
            return
        seen += len(items)
        if len(items) < TASK_CENTER_PAGE_SIZE:
            return
        if total is not None and seen >= total:
            return
        page_num += 1


def _iter_task_all(token):
    """逐个产出任务 {id, name}。"""
    for items, _total in _iter_task_all_pages(token):
        for item in items:
            if isinstance(item, dict) and item.get("id") is not None:
                yield item


def _fetch_jobs(token, task_id):
    """分页拉某任务的全部作业，返回作业列表。

    40001 账号级无权限由上层识别终止；40401/40005 资源级不可见（当前采集员
    无权查看该任务）静默返回空列表，不产生噪音警告。
    """
    jobs = []
    page_num = 1
    total = None
    while True:
        data, err = _tc_get(
            "/api/v1/collect/task/job",
            token,
            params={"task_id": task_id,
                    "page_num": page_num,
                    "page_size": JOB_PAGE_SIZE},
        )
        if err:
            if _is_not_authorized(err):
                raise TaskCenterError(
                    "当前账号无任务中心访问权限（40001 Not Authorized），"
                    "请联系管理员开通后重试", True)
            if _is_invisible(err):
                return []
            raise TaskCenterError("拉取任务 %s 的作业失败：%s" % (task_id, err[1]))
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise TaskCenterError("拉取任务 %s 的作业失败：平台返回格式异常" % task_id)
        items = data["list"]
        if isinstance(data.get("total"), int):
            total = data["total"]
        jobs.extend(items)
        if not items or len(items) < JOB_PAGE_SIZE:
            break
        if total is not None and len(jobs) >= total:
            break
        page_num += 1
    return jobs


def _fetch_device_type_name(token, task_id):
    """取任务详情中的设备型号名；失败返回 None。"""
    data, err = _tc_get(
        "/api/v1/collect/task/detail",
        token,
        params={"task_id": task_id},
    )
    if err:
        if _is_not_authorized(err):
            raise TaskCenterError(
                "当前账号无任务中心访问权限（40001 Not Authorized），"
                "请联系管理员开通后重试", True)
        # 40401/40005 资源级不可见与其他失败一样置空；可选字段，不产生警告。
        return None
    if isinstance(data, dict):
        name = data.get("device_type_name")
        return name if isinstance(name, str) and name else None
    return None


def _enrich_local_jobs(token, scan_result, jobs_index, warnings):
    """对本地扫描命中且未 enrich 的作业补全采集人与云端回传状态。"""
    local_uuids = set()
    for ep in (scan_result or {}).get("episodes") or []:
        if isinstance(ep, dict) and ep.get("job_uuid"):
            local_uuids.add(ep["job_uuid"])
    targets = sorted(
        uuid for uuid in local_uuids
        if uuid in jobs_index and jobs_index[uuid].get("enriched") is not True
    )
    enriched_count = 0

    # 采集人：assignment/stat 的 display_name 去重
    for uuid in targets:
        job = jobs_index[uuid]
        try:
            data, err = _tc_get(
                "/api/v1/collect/task/job/assignment/stat",
                token,
                params={"job_id": job["job_id"]},
            )
            if err:
                if _is_not_authorized(err):
                    raise TaskCenterError(
                        "当前账号无任务中心访问权限（40001 Not Authorized），"
                        "请联系管理员开通后重试", True)
                if _is_invisible(err):
                    job["collectors"] = []
                else:
                    _add_warning(
                        warnings,
                        "作业 %s 采集人信息获取失败：%s" % (job["job_id"], err[1]))
                    job["collectors"] = []
            else:
                names = []
                entries = data.get("list") if isinstance(data, dict) else None
                for entry in entries or []:
                    name = entry.get("display_name") if isinstance(entry, dict) else None
                    if name and name not in names:
                        names.append(name)
                job["collectors"] = names
        except requests.RequestException as exc:
            _add_warning(warnings, "作业 %s 采集人信息获取失败：%s"
                         % (job["job_id"], _network_error(exc)))
            job["collectors"] = []

    # 云端回传状态：job/episodes 按 EPISODE_BATCH 分批
    for start in range(0, len(targets), EPISODE_BATCH):
        batch = targets[start:start + EPISODE_BATCH]
        id_to_uuid = {str(jobs_index[uuid]["job_id"]): uuid for uuid in batch}
        try:
            data, err = _tc_get(
                "/api/v1/collect/task/job/episodes",
                token,
                params={"job_ids": json.dumps([int(jid) for jid in id_to_uuid])},
            )
            if err:
                if _is_not_authorized(err):
                    raise TaskCenterError(
                        "当前账号无任务中心访问权限（40001 Not Authorized），"
                        "请联系管理员开通后重试", True)
                _add_warning(warnings, "云端 episode 状态获取失败（%d 个作业）：%s"
                             % (len(batch), err[1]))
                for uuid in batch:
                    jobs_index[uuid]["cloud_status"] = "unknown"
            else:
                mapping = data.get("data") if isinstance(data, dict) else None
                mapping = mapping if isinstance(mapping, dict) else {}
                for job_id_str, uuid in id_to_uuid.items():
                    episodes = mapping.get(job_id_str)
                    if isinstance(episodes, list) and episodes:
                        jobs_index[uuid]["cloud_status"] = "registered"
                    else:
                        jobs_index[uuid]["cloud_status"] = "not_uploaded"
        except requests.RequestException as exc:
            _add_warning(warnings, "云端 episode 状态获取失败（%d 个作业）：%s"
                         % (len(batch), _network_error(exc)))
            for uuid in batch:
                jobs_index[uuid]["cloud_status"] = "unknown"

    for uuid in targets:
        jobs_index[uuid]["enriched"] = True
        enriched_count += 1
    return enriched_count


def sync_task_center(scan_result):
    """按本地扫描日期增量同步任务中心，构建并持久化 job_uuid 索引。"""
    state = _read_token()
    if not state or not state.get("token"):
        return {"ok": False, "error": "未登录"}
    if time.time() >= (state.get("expires_at") or 0):
        return {"ok": False, "error": "登录已过期，请重新登录"}
    token = state["token"]

    if not isinstance(scan_result, dict) or not scan_result.get("episodes"):
        return {"ok": False, "error": "请先扫描数据盘后再同步任务中心"}

    local_dates = _extract_local_dates(scan_result)
    if not local_dates:
        return {"ok": False, "error": "请先扫描数据盘后再同步任务中心"}

    cache = load_task_center_cache() or {}
    cached_dates = set(cache.get("dates") or [])
    new_dates = local_dates - cached_dates
    jobs_index = dict(cache.get("jobs") or {})
    warnings = []
    tasks_scanned = 0
    jobs_indexed = 0

    try:
        if new_dates:
            min_date = min(new_dates)
            matched_tasks = []
            # 任务按 id 降序、日期后缀随 id 变老：翻到整页后缀日期都早于
            # 目标日期最小值即可停止；无后缀任务不影响翻页，total 兜底终止。
            for items, total in _iter_task_all_pages(token):
                page_dates = []
                for item in items:
                    suffix = _task_date_suffix(item.get("name"))
                    if suffix is None:
                        continue
                    page_dates.append(suffix)
                    if suffix in new_dates:
                        matched_tasks.append(item)
                if page_dates and all(d < min_date for d in page_dates):
                    break

            for task in matched_tasks:
                task_id = task["id"]
                task_name = task.get("name")
                try:
                    # 可选字段：40401/40005 不可见或其他失败一律静默置空。
                    device_type_name = _fetch_device_type_name(token, task_id)
                except TaskCenterError:
                    raise
                except requests.RequestException as exc:
                    _add_warning(warnings, "任务 %s 详情获取失败：%s"
                                 % (task_id, _network_error(exc)))
                    device_type_name = None
                try:
                    remote_jobs = _fetch_jobs(token, task_id)
                except TaskCenterError:
                    raise
                except requests.RequestException as exc:
                    _add_warning(warnings, "任务 %s（%s）作业拉取失败：%s"
                                 % (task_id, task_name, _network_error(exc)))
                    continue
                tasks_scanned += 1
                for job in remote_jobs:
                    if not isinstance(job, dict) or not job.get("uuid"):
                        continue
                    jobs_index[job["uuid"]] = {
                        "job_id": job.get("id"),
                        "task_id": task_id,
                        "task_name": task_name,
                        "job_name": job.get("name"),
                        "status": job.get("status"),
                        "is_assign": job.get("is_assign"),
                        "is_finish": job.get("is_finish"),
                        "progress": job.get("progress"),
                        "created_at": job.get("created_at"),
                        "device_type_name": device_type_name,
                        "collectors": [],
                        "cloud_status": None,
                        "enriched": False,
                    }
                    jobs_indexed += 1

        # 即使没有新日期，也要对本地命中但未 enrich 的作业补全
        enriched = _enrich_local_jobs(token, scan_result, jobs_index, warnings)
    except TaskCenterError as exc:
        if exc.is_permission:
            return {
                "ok": False,
                "error": str(exc),
                "warnings": [str(exc)],
            }
        return {"ok": False, "error": str(exc), "warnings": warnings}
    except requests.RequestException as exc:
        return {"ok": False, "error": _network_error(exc), "warnings": warnings}

    synced_at = datetime.now(timezone.utc).isoformat()
    new_cache = {
        "version": TASK_CENTER_CACHE_VERSION,
        "synced_at": synced_at,
        "dates": sorted(cached_dates | local_dates),
        "jobs": jobs_index,
        "warnings": warnings,
    }
    try:
        _write_task_center_cache(new_cache)
    except OSError as exc:
        return {"ok": False, "error": "无法写入任务中心缓存：%s" % exc,
                "warnings": warnings}

    return {
        "ok": True,
        "synced_dates": sorted(new_dates),
        "tasks_scanned": tasks_scanned,
        "jobs_indexed": jobs_indexed,
        "enriched": enriched,
        "warnings": warnings,
        "synced_at": synced_at,
    }


# ---------------------------------------------------------------- 对外接口

def login(username, password, tenant=DEFAULT_TENANT):
    """Casdoor password grant 登录，token 持久化到 token.json（0600）。"""
    tenant = tenant or DEFAULT_TENANT
    client_id = _get_client_id(tenant)
    try:
        resp = requests.post(
            BASE_URL + "/casdoor/api/login/oauth/access_token",
            data={
                "grant_type": "password",
                "client_id": client_id,
                "username": username,
                "password": password,
                "scope": "read",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"ok": False, "error": _network_error(exc)}

    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "error": "登录失败：平台返回异常（HTTP %s）" % resp.status_code}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "登录失败：平台返回格式异常"}

    token = payload.get("access_token")
    if not token and isinstance(payload.get("data"), dict):
        token = payload["data"].get("access_token")
    if not token:
        msg = _extract_msg(payload) or "平台返回异常（HTTP %s）" % resp.status_code
        return {"ok": False, "error": "登录失败：%s" % msg}

    expires_at = _jwt_expires_at(token)
    if expires_at is None:
        expires_in = _as_number(payload.get("expires_in"))
        expires_at = int(time.time() + (expires_in if expires_in else 7 * 86400))

    try:
        user, err = _current_user(token)
    except requests.RequestException as exc:
        return {"ok": False, "error": _network_error(exc)}
    if err:
        return {"ok": False, "error": "登录校验失败：%s" % err}
    if not user.get("tenant"):
        user["tenant"] = tenant
    if not user.get("name"):
        user["name"] = username

    try:
        _write_token({
            "token": token,
            "username": username,
            "tenant": tenant,
            "expires_at": expires_at,
            "user": user,
        })
    except OSError as exc:
        return {"ok": False, "error": "无法写入登录状态：%s" % exc}
    return {"ok": True, "user": user, "expires_at": expires_at}


def auth_status():
    """读取本地登录态；未过期时调 current-user 复核，失败视为未登录。"""
    state = _read_token()
    if not state or not state.get("token"):
        return {"logged_in": False}
    expires_at = state.get("expires_at") or 0
    user = state.get("user") or {
        "tenant": state.get("tenant"),
        "name": state.get("username"),
    }
    expired = time.time() >= expires_at
    if expired:
        return {"logged_in": False, "user": user, "expires_at": expires_at, "expired": True}
    try:
        fresh_user, err = _current_user(state["token"])
    except requests.RequestException:
        err = "网络异常"
    if err:
        return {"logged_in": False, "user": user, "expires_at": expires_at, "expired": False}
    if fresh_user.get("tenant"):
        user["tenant"] = fresh_user["tenant"]
    if fresh_user.get("name"):
        user["name"] = fresh_user["name"]
    return {"logged_in": True, "user": user, "expires_at": expires_at, "expired": False}


def logout():
    try:
        os.remove(TOKEN_PATH)
    except FileNotFoundError:
        pass
    except OSError:
        pass
    return {"ok": True}


def sync():
    """分页拉取全部任务历史与字典，聚合后写入 platform_cache.json。"""
    state = _read_token()
    if not state or not state.get("token"):
        return {"ok": False, "error": "未登录"}
    if time.time() >= (state.get("expires_at") or 0):
        return {"ok": False, "error": "登录已过期，请重新登录"}
    token = state["token"]
    tenant = state.get("tenant") or DEFAULT_TENANT

    submissions = []
    total = None
    page_num = 1
    try:
        while True:
            data, err = _api_get(
                "/api/v1/%s/form/submission" % tenant,
                token,
                params={"page_num": page_num, "page_size": PAGE_SIZE},
            )
            if err:
                return {"ok": False, "error": "同步任务历史失败：%s" % err}
            if not isinstance(data, dict):
                return {"ok": False, "error": "同步任务历史失败：平台返回格式异常"}
            records = data.get("list") or []
            if not isinstance(records, list):
                return {"ok": False, "error": "同步任务历史失败：平台返回格式异常"}
            if isinstance(data.get("total"), int):
                total = data["total"]
            submissions.extend(records)
            if not records or len(records) < PAGE_SIZE:
                break
            if total is not None and len(submissions) >= total:
                break
            page_num += 1

        # 字典接口失败不中断同步，记为空映射
        device_types = {}
        project_tags = {}
        try:
            data, err = _api_get("/api/v1/collect/device_type", token)
            if not err:
                device_types = _dict_map(data)
        except requests.RequestException:
            pass
        try:
            data, err = _api_get("/api/v1/tag/project", token)
            if not err:
                project_tags = _dict_map(data)
        except requests.RequestException:
            pass
    except requests.RequestException as exc:
        return {"ok": False, "error": _network_error(exc)}

    synced_at = datetime.now(timezone.utc).isoformat()
    cache = {
        "synced_at": synced_at,
        "submissions": submissions,
        "device_types": device_types,
        "project_tags": project_tags,
        "by_task_uuid": _aggregate(submissions),
    }
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        return {"ok": False, "error": "无法写入平台缓存：%s" % exc}
    return {
        "ok": True,
        "submissions": len(submissions),
        "device_types": len(device_types),
        "project_tags": len(project_tags),
        "synced_at": synced_at,
    }


def platform_status():
    state = _read_token()
    logged_in = bool(state and state.get("token")) and (
        time.time() < (state.get("expires_at") or 0)
    )
    cache = load_cache()
    result = {"logged_in": logged_in, "cached": cache is not None}
    if cache is not None:
        result["synced_at"] = cache.get("synced_at")
        result["submissions_count"] = len(cache.get("submissions") or [])

    tc_cache = load_task_center_cache()
    if isinstance(tc_cache, dict):
        tc_warnings = tc_cache.get("warnings") or []
        result["task_center"] = {
            "cached": True,
            "synced_at": tc_cache.get("synced_at"),
            "dates": list(tc_cache.get("dates") or []),
            "jobs_count": len(tc_cache.get("jobs") or {}),
            "last_warning": tc_warnings[0] if tc_warnings else None,
        }
    else:
        result["task_center"] = {
            "cached": False,
            "synced_at": None,
            "dates": [],
            "jobs_count": 0,
            "last_warning": None,
        }
    return result


def load_cache():
    return _read_json(CACHE_PATH)


def _join_task_center(row, tc_jobs):
    """任务中心 job_uuid 命中分支，返回填充好的行（已 dict 复制）。"""
    matched = [tc_jobs[uuid] for uuid in (row.get("job_uuids") or [])
               if uuid in tc_jobs]
    task_name = next((j.get("task_name") for j in matched if j.get("task_name")), None)
    collectors = []
    for job in matched:
        for name in job.get("collectors") or []:
            if name and name not in collectors:
                collectors.append(name)
    device_type_names = []
    for job in matched:
        name = job.get("device_type_name")
        if name and name not in device_type_names:
            device_type_names.append(name)
    created_ats = [j.get("created_at") for j in matched if j.get("created_at")]
    statuses = [j.get("cloud_status") for j in matched]
    if "registered" in statuses:
        cloud_status = "registered"
    elif "not_uploaded" in statuses:
        cloud_status = "not_uploaded"
    else:
        cloud_status = "unknown"
    row["task_name"] = task_name
    row["collectors"] = collectors
    row["project_name"] = None
    row["device_type_names"] = device_type_names
    row["expect_duration_seconds"] = None
    row["platform_created_at"] = min(created_ats) if created_ats else None
    row["match_source"] = "task_center"
    row["cloud_status"] = cloud_status
    return row


def join_tasks(by_task, cache):
    """关联平台信息到统计任务列表，返回新列表（不改原数据、不发网络请求）。

    匹配顺序：① 任务中心 job_uuid → ② 表单 task_uuid → ③ 置空。
    cache 为表单缓存与任务中心缓存（cache["task_center"]）合并后的 dict。
    """
    cache = cache if isinstance(cache, dict) else {}
    by_uuid = cache.get("by_task_uuid") or {}
    device_types = cache.get("device_types") or {}
    project_tags = cache.get("project_tags") or {}
    tc_cache = cache.get("task_center")
    tc_jobs = tc_cache.get("jobs") if isinstance(tc_cache, dict) else None
    tc_jobs = tc_jobs if isinstance(tc_jobs, dict) else {}
    joined = []
    for item in by_task:
        row = dict(item)
        if tc_jobs and any(uuid in tc_jobs for uuid in (row.get("job_uuids") or [])):
            row = _join_task_center(row, tc_jobs)
        else:
            info = by_uuid.get(row.get("task_uuid"))
            if isinstance(info, dict):
                row["task_name"] = info.get("task_name")
                row["collectors"] = list(info.get("collectors") or [])
                project_tag_id = info.get("project_tag_id")
                row["project_name"] = (
                    project_tags.get(str(project_tag_id))
                    if project_tag_id not in (None, "")
                    else None
                )
                row["device_type_names"] = [
                    device_types[str(d)]
                    for d in (info.get("device_type_ids") or [])
                    if str(d) in device_types
                ]
                row["expect_duration_seconds"] = info.get("expect_duration_seconds")
                row["platform_created_at"] = info.get("platform_created_at")
                row["match_source"] = "form"
            else:
                for field in _JOIN_FIELDS:
                    row[field] = None
                row["match_source"] = "none"
            row["cloud_status"] = None
        joined.append(row)
    return joined