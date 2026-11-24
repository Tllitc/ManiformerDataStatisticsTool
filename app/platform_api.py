# -*- coding: utf-8 -*-
"""觅蜂平台（geniestudio.agibot.com）集成：Casdoor 登录 / 任务历史同步 / 本地缓存 / 任务关联。

状态目录：~/.umi-stats-tool/（token.json 权限 0600，platform_cache.json）。
密码仅在登录请求的表单中使用，绝不写入任何文件或日志。
"""

import base64
import json
import os
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://geniestudio.agibot.com"
DEFAULT_TENANT = "mego"
DEFAULT_CLIENT_ID = "7fd3ac5466214bff6252"
STATE_DIR = os.path.expanduser("~/.umi-stats-tool")
TOKEN_PATH = os.path.join(STATE_DIR, "token.json")
CACHE_PATH = os.path.join(STATE_DIR, "platform_cache.json")
TIMEOUT = 30
PAGE_SIZE = 100

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
    return result


def load_cache():
    return _read_json(CACHE_PATH)


def join_tasks(by_task, cache):
    """按 task_uuid 将平台缓存关联到统计任务列表，返回新列表（不改原数据）。"""
    cache = cache if isinstance(cache, dict) else {}
    by_uuid = cache.get("by_task_uuid") or {}
    device_types = cache.get("device_types") or {}
    project_tags = cache.get("project_tags") or {}
    joined = []
    for item in by_task:
        row = dict(item)
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
        else:
            for field in _JOIN_FIELDS:
                row[field] = None
        joined.append(row)
    return joined