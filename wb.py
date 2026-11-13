#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 每日自动化（GitHub Actions 版）

两件事：
  1. Buddy 加油站每日签到（+100，连签第 7 天 +1000）
  2. 派猫猫旅行：派出 -> 等回来 -> 领取积分

纯标准库实现，无需 pip 依赖。所有接口均是「先查后做」的幂等调用，
重复运行不会多领、不会扣分。

环境变量：
  WB_TOKEN        必填，accessToken
  WB_UID          必填，账号 uid
  WB_REFRESH      可选，refreshToken（token 过期时自动续期）
  WB_DOMAIN       可选，默认 www.workbuddy.cn
  WB_HOST         可选，默认 https://www.workbuddy.cn
  WB_LOCATION_ID  可选，旅行地点，默认 1（咖啡馆，3 小时）
  WB_STATE_FILE   可选，状态文件，默认 state.json
  WB_DRY_RUN      可选，设为 1 时只打印计划、不改任何状态
  SERVERCHAN_KEY  可选，Server酱 SendKey，失败时推微信
  PUSHPLUS_TOKEN  可选，PushPlus Token，同上
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

def log(msg):
    print(msg, flush=True)


HOST = os.environ.get("WB_HOST", "https://www.workbuddy.cn").rstrip("/")
DOMAIN = os.environ.get("WB_DOMAIN", "www.workbuddy.cn")
UID = os.environ.get("WB_UID", "")
TOKEN = os.environ.get("WB_TOKEN", "")
REFRESH = os.environ.get("WB_REFRESH", "")
STATE_FILE = os.environ.get("WB_STATE_FILE", "state.json")
def _env_int(name, default):
    """环境变量可能是空串（Actions 里未定义的 vars 会传空），要兜住。"""
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        log("⚠️ %s=%r 不是整数，改用默认值 %s" % (name, raw, default))
        return default


LOCATION_ID = _env_int("WB_LOCATION_ID", 1)
DRY_RUN = os.environ.get("WB_DRY_RUN", "") == "1"

STATUS_PATH = "/v2/billing/meter/checkin-activity-status"
CHECKIN_PATH = "/v2/billing/meter/daily-checkin"
TRAVEL_STATUS = "/v2/activity/growth/buddy/travel/status"
TRAVEL_DEPART = "/v2/activity/growth/buddy/travel/depart"
TRAVEL_CLAIM = "/v2/activity/growth/buddy/travel/claim"
REFRESH_PATH = "/v2/plugin/auth/token/refresh"


# ---------------------------------------------------------------- HTTP

def _headers(token=None):
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Authorization": "Bearer " + (token or TOKEN),
        "X-User-Id": UID,
        "X-Domain": DOMAIN,
    }


def request(method, path, body=None, token=None, extra_headers=None):
    """返回 (http_status, json_dict)。网络异常返回 (0, {...})。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = _headers(token)
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(HOST + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, {"raw": raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}
    except Exception as e:  # 网络层
        return 0, {"error": repr(e)}


# ---------------------------------------------------------------- token 续期

def _dig(obj, names):
    """在嵌套 JSON 里找第一个匹配字段名的非空字符串。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names and isinstance(v, str) and v:
                return v
        for v in obj.values():
            got = _dig(v, names)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _dig(v, names)
            if got:
                return got
    return None


def try_refresh():
    """用 refreshToken 换新 token。成功则更新全局并返回 True。"""
    global TOKEN, REFRESH
    if not REFRESH:
        log("⚠️ 没有 refreshToken，无法自动续期")
        return False
    st, js = request(
        "POST",
        REFRESH_PATH,
        {},
        token="",
        extra_headers={"X-Refresh-Token": REFRESH},
    )
    if st != 200 or js.get("code") not in (0, "0", None):
        log("⚠️ token 续期失败 HTTP=%s %s" % (st, json.dumps(js, ensure_ascii=False)[:200]))
        return False
    new_access = _dig(js, {"access_token", "accessToken", "token"})
    new_refresh = _dig(js, {"refresh_token", "refreshToken"})
    if not new_access:
        log("⚠️ 续期响应里没找到 access_token")
        return False
    TOKEN = new_access
    if new_refresh:
        REFRESH = new_refresh
    log("🔑 token 已自动续期")
    return True


def call_authed(method, path, body=None):
    """带自动续期的请求：401 时续期后重试；续期不了就回退到 Secret 里的原始 token。"""
    global TOKEN
    st, js = request(method, path, body)
    if st not in (401, 403):
        return st, js

    if try_refresh():
        return request(method, path, body)

    # 续期失败：可能是缓存里的 token 失效了，而 Secret 里的是新的
    env_token = os.environ.get("WB_TOKEN", "")
    if env_token and env_token != TOKEN:
        log("↩️ 回退使用 Secret 中的原始 token 重试")
        TOKEN = env_token
        return request(method, path, body)
    return st, js


# ---------------------------------------------------------------- 签到

def do_checkin():
    st, js = call_authed("POST", STATUS_PATH, {})
    if st != 200:
        return "签到状态查询失败 HTTP=%s %s" % (st, json.dumps(js, ensure_ascii=False)[:160])

    data = js.get("data") or {}
    if not data.get("active"):
        return "加油站活动未开启（可能已换期），跳过签到"

    streak = data.get("streak_days", 0)
    if data.get("today_checked_in"):
        return "今日已签到（连签 %s 天），无需重复" % streak

    if DRY_RUN:
        return "[DRY] 今天未签到，将执行签到"

    st2, js2 = call_authed("POST", CHECKIN_PATH, {})
    code = js2.get("code")
    if st2 == 200 and code in (0, "0"):
        d2 = js2.get("data") or {}
        got = d2.get("today_credit") or d2.get("credit") or 100
        return "✅ 签到成功 +%s 积分（连签 %s 天）" % (got, d2.get("streak_days", streak + 1))
    if code == 10001:
        return "今日已签到（并发兜底），无需重复"
    return "❌ 签到失败 HTTP=%s %s" % (st2, json.dumps(js2, ensure_ascii=False)[:160])


# ---------------------------------------------------------------- 猫猫旅行

def do_travel():
    st, js = call_authed("GET", TRAVEL_STATUS)
    if st != 200:
        return "旅行状态查询失败 HTTP=%s %s" % (st, json.dumps(js, ensure_ascii=False)[:160])

    d = js.get("data") or {}
    state = d.get("state")

    if state == "arrived":
        # 猫已经回来，领奖励
        if DRY_RUN:
            return "[DRY] 猫已到达，将领取奖励"
        st2, js2 = call_authed("GET", TRAVEL_CLAIM)
        if st2 == 200:
            d2 = js2.get("data") or {}
            credit = d2.get("reward_credit") or d2.get("credit") or 0
            loc = (d.get("location") or {}).get("name", "目的地")
            letter = (d2.get("letter") or {}).get("text", "")
            head = "🎁 已领回 %s 的旅行奖励 +%s 积分" % (loc, credit)
            if letter:
                head += "\n    信件片段：" + letter.replace("\n", " ").strip()[:60] + "…"
            return head
        return "❌ 领取失败 HTTP=%s %s" % (st2, json.dumps(js2, ensure_ascii=False)[:160])

    if state == "traveling":
        import time as _t
        left = int(d.get("arrive_at", 0)) - int(d.get("server_now", 0))
        return "🚶 猫还在路上，约 %d 分钟后回来" % max(left // 60, 1)

    if state == "idle":
        if d.get("daily_limit_reached"):
            return "今日派遣次数已用完"
        if DRY_RUN:
            return "[DRY] 猫空闲，将派往 location_id=%s" % LOCATION_ID
        st2, js2 = call_authed("POST", TRAVEL_DEPART, {"location_id": LOCATION_ID})
        if st2 == 200:
            d2 = js2.get("data") or {}
            hours = d2.get("duration_hours") or (d.get("location") or {}).get("duration_hours") or 0
            loc = (d2.get("location") or (d.get("location") or {})).get("name", "目的地")
            tail = "，约 %s 小时后回来领取" % hours if hours else ""
            return "🐾 已派出，前往%s%s" % (loc, tail)
        return "❌ 派出失败 HTTP=%s %s" % (st2, json.dumps(js2, ensure_ascii=False)[:160])

    return "未知状态：%s" % state


# ---------------------------------------------------------------- 状态与通知

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log("⚠️ 状态写入失败：%r" % (e,))


def notify(title, content):
    """失败时推送。两个渠道都可选，都未配置则静默。"""
    key = os.environ.get("SERVERCHAN_KEY", "").strip()
    if key:
        url = "https://sctapi.ftqq.com/%s.send" % key
        try:
            data = urllib.parse.urlencode({"title": title, "desp": content}).encode()
            urllib.request.urlopen(url, data=data, timeout=15).read()
            log("📨 已通过 Server酱 通知")
        except Exception as e:
            log("⚠️ Server酱 通知失败 %r" % (e,))
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if token:
        try:
            payload = json.dumps({"token": token, "title": title, "content": content}).encode()
            req = urllib.request.Request(
                "https://www.pushplus.plus/send", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=15).read()
            log("📨 已通过 PushPlus 通知")
        except Exception as e:
            log("⚠️ PushPlus 通知失败 %r" % (e,))


# ---------------------------------------------------------------- main

def main():
    global TOKEN, REFRESH

    if not TOKEN or not UID:
        log("❌ 缺少 WB_TOKEN 或 WB_UID，请检查 Secrets")
        return 2

    state = load_state()
    # 本地缓存的 token 优先（可能比 Secret 里的更新）
    if state.get("access_token"):
        TOKEN = state["access_token"]
    if state.get("refresh_token"):
        REFRESH = state["refresh_token"]

    log("=" * 46)
    log("WorkBuddy 自动化  uid=%s****" % UID[:8])
    log("=" * 46)

    results = []
    ok = True

    try:
        line = do_checkin()
        log("[签到] " + line)
        results.append(line)
        if line.startswith(("❌", "签到状态查询失败")):
            ok = False
    except Exception as e:
        log("[签到] 异常 %r" % (e,))
        results.append("签到异常 %r" % (e,))
        ok = False

    try:
        line = do_travel()
        log("[猫猫] " + line)
        results.append(line)
        if line.startswith("❌"):
            ok = False
    except Exception as e:
        log("[猫猫] 异常 %r" % (e,))
        results.append("猫猫异常 %r" % (e,))
        ok = False

    # 回写（含可能续期过的 token）
    if not DRY_RUN:
        state.update({
            "access_token": TOKEN,
            "refresh_token": REFRESH,
            "uid": UID,
            "domain": DOMAIN,
            "updated_at": int(__import__("time").time()),
        })
        save_state(state)

    if not ok:
        notify("WorkBuddy 自动化失败", "\n".join(results))
        return 1

    log("-" * 46)
    log("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
