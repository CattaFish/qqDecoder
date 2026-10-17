# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import json
import random
import time
import requests
from extractor import validate_date_str

HONOR_HOST = "https://appmarket-api-drcn.hispace.hihonorcloud.com"
DETAIL_PATH = "/api/market/app/v2/detail/byname/query"
DISPATCH_PATH = "/downloaddispatch/download/appmarket/getUriList"
REQUEST_KEY_BASE64 = "OTNZNTk0a0JPVGtDNkEyOWdlcWRTQkxTNXUxU1RwQVE="


def random_hex(length=32):
    return "".join(random.choices("0123456789abcdef", k=length))


def honor_sign(path: str, timestamp: int, trace_id: str, body_str: str) -> str:
    """荣耀客户端官方 HmacSHA256 签名算法"""
    key = base64.b64decode(REQUEST_KEY_BASE64)
    header_json = f'{{"market-timestamp":"{timestamp}","traceId":"{trace_id}"}}'
    message = (path + header_json + body_str).encode("utf-8")
    sign_key = key + hashlib.sha256(message).digest()
    return hmac.new(sign_key, message, hashlib.sha256).hexdigest().lower()


def build_honor_request(path: str, payload: dict):
    """构建包含合法设备指纹与签名的请求报文"""
    now_ts = int(time.time() * 1000)
    trace_id = random_hex(32)
    inst_id = f"a1b2c3d4-{random_hex(4)}-{random_hex(4)}-{random_hex(4)}-{random_hex(12)}"
    android_id = hashlib.sha256(f"honor-android:{inst_id}".encode()).hexdigest()[:16]
    udid = hashlib.sha256(f"honor-udid:{inst_id}".encode()).hexdigest()

    t_info = {
        "accessToken": "",
        "activationFlag": -1,
        "ageLimit": 3,
        "androidId": android_id,
        "androidApiVersion": "36",
        "apkVer": 160107301,
        "apkVerName": "16.1.7.301",
        "carrier": "Unknown",
        "chId": "HONOR_01",
        "compatibilityVar": {
            "cpuAbi": "arm64-v8a",
            "bintranslatorEnable": -1,
            "cpuAbilist": "arm64-v8a",
            "hasHCore": 1,
        },
        "compressScenes": [1],
        "connectionType": 2,
        "cpu": "arm64-v8a",
        "deliveryCountry": "cn",
        "deviceMode": 2,
        "dpi": "560",
        "hman": "HONOR",
        "htype": "PTP-AN00",
        "isDark": False,
        "isDemoType": 0,
        "isH5": 0,
        "isMonkey": 0,
        "isParallelSpace": 1,
        "isPersonalRecommend": True,
        "isRecommend": False,
        "kidsMode": False,
        "language": "zh-CN",
        "magicVersion": ".0.0",
        "marketType": 0,
        "miniGameInstall": 0,
        "netType": 3,
        "os": 0,
        "osVer": "16",
        "pName": "com.hihonor.appmarket",
        "patchSdkVersion": 0,
        "randomId": f"{random_hex(8)}-{random_hex(4)}-{random_hex(4)}-{random_hex(4)}-{random_hex(12)}",
        "requestSource": 1,
        "resolution": "1264x2800",
        "source": "1",
        "spreadModelName": "HONOR Magic7",
        "subTerminalType": "1_1",
        "supportGms": 0,
        "telecomOper": "",
        "terminalType": 1,
        "timestamp": now_ts,
        "udid": udid,
        "userId": "",
        "userType": "-1",
        "oaid": "",
    }

    body_obj = dict(payload)
    if "tInfo" not in body_obj:
        body_obj["tInfo"] = t_info

    body_str = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False)
    signature = honor_sign(path, now_ts, trace_id, body_str)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": (
            "Dalvik/2.1.0 (Linux; U; Android 16; PTP-AN00 Build/HONORPTP-AN00)"
            " com.hihonor.appmarket/160107301"
        ),
        "x-uuid": inst_id,
        "apkVer": "160107301",
        "flavor": "product",
        "No-ECDH": "1",
        "traceId": trace_id,
        "areaId": "CN",
        "udid": udid,
        "androidId": android_id,
        "uid": "",
        "model": "PTP-AN00",
        "sysVersion": ".0.0",
        "magicSysVersion": "MagicOS_10.0.0",
        "androidVersion": "16",
        "launchType": "-1",
        "caller": "com.hihonor.appmarket",
        "language": "zh-CN",
        "userType": "-1",
        "market-timestamp": str(now_ts),
        "market-package-name": "com.hihonor.appmarket",
        "market-sign-type": "HmacSHA256",
        "market-sign-value": signature,
    }

    return headers, body_str


def get_honor_info():
    """获取荣耀应用市场 QQ 的版本信息与下载直链"""
    print("[荣耀监控] 正在向官方接口查询线上版本...")
    try:
        # 1. 查询应用详情
        payload = {
            "adIntegrationScene": 0, "aiRecommendType": 0, "applyId": 0, "isAd": False,
            "isInternal": 0, "needGameNodeList": False, "needGift": False, "orderSceneFlag": 0,
            "orderType": 0, "pName": "com.tencent.mobileqq", "pkgChannel": -1,
            "redirectNoAvailableAppLandingPage": 0, "resId": 0, "resType": 1, "subChannel": "",
        }
        headers, body_str = build_honor_request(DETAIL_PATH, payload)
        resp = requests.post(HONOR_HOST + DETAIL_PATH, data=body_str, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"[荣耀监控] 请求接口失败: HTTP {resp.status_code}")
            return None

        res_json = resp.json()
        if res_json.get("errorCode") != 0:
            print(f"[荣耀监控] 接口返回错误: {res_json}")
            return None

        data = res_json.get("data") or {}
        version_name = str(data.get("verName", "")).strip()
        raw_date = data.get("verUptTime") or data.get("releaseDate") or data.get("pubTime")
        date_str = validate_date_str(raw_date) or time.strftime("%Y-%m-%d")

        if not version_name:
            print("[荣耀监控] 未能获取到有效版本号")
            return None

        # 2. 换取安装包下载 CDN 直链
        download_url = data.get("downUrl", "").strip()
        meta_path = data.get("downloadUrlMetaPath", "").strip()
        file_size = data.get("fileSize") or 0

        # 若未直接给出完整 https 链接，向调度接口换取真实直链
        if not download_url.startswith("https://") and meta_path:
            disp_payload = {
                "biz": "AppmarketActiveDownload",
                "downloadObject": {"name": "com.tencent.mobileqq", "path": meta_path, "size": file_size},
                "deviceInfo": {"deviceId": "", "ip": "127.0.0.1", "accept": "http"},
                "scene": {"code": ""},
            }
            d_headers, d_body = build_honor_request(DISPATCH_PATH, disp_payload)
            d_resp = requests.post(HONOR_HOST + DISPATCH_PATH, data=d_body, headers=d_headers, timeout=15)
            if d_resp.status_code == 200:
                d_json = d_resp.json()
                d_data = d_json.get("data") or {}
                uri_list = d_data.get("uriList") or []
                for u in uri_list:
                    if u.startswith("https://") or u.startswith("http://"):
                        download_url = u
                        break

        if not download_url:
            print(f"[荣耀监控] 未能解析到有效下载直链: {data}")
            return None

        return {
            "version": version_name,
            "date": date_str,
            "download_url": download_url,
            "channel": "荣耀",
        }
    except Exception as e:
        print(f"[荣耀监控] 查询异常: {e}")
        return None
