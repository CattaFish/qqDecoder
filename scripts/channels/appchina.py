# -*- coding: utf-8 -*-
import json
import time
import requests
from extractor import validate_date_str

APPCHINA_API_URL = "https://mobile.appchina.com/market/api"


def get_appchina_info():
    """获取应用汇 (AppChina) QQ 的版本信息与下载直链 (官方客户端原生 API，全球无阻)"""
    print("[应用汇监控] 正在向客户端移动 API 查询线上版本...")
    try:
        now_ms = int(time.time() * 1000)

        # 构造应用汇 Android 客户端官方鉴权报文
        param = {
            "apiVer": 23,
            "guid": "06b15137-908f-4f51-b035-e296d5fda1b6",
            "imei": "869394021134371",
            "type": "app.detail",
            "packagename": "com.tencent.mobileqq",
            "clientTime": now_ms,
            "imglevel": 3,
        }
        body = {
            "deviceId": "869394021134371",
            "referer": "06b15137-908f-4f51-b035-e296d5fda1b6",
            "api": "market.MarketAPI",
            "param": json.dumps(param, separators=(",", ":")),
        }
        headers = {
            "User-Agent": "AppChina/Android/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "Connection": "close",
        }

        resp = requests.post(APPCHINA_API_URL, data=body, headers=headers, timeout=20)
        if resp.status_code != 200:
            print(f"[应用汇监控] 请求接口失败: HTTP {resp.status_code}")
            return None

        data = resp.json()
        version_name = str(data.get("versionName", "")).strip()
        last_update = str(data.get("lastUpdate", "")).strip()
        download_url = str(data.get("apkUrl", "")).strip()

        date_str = validate_date_str(last_update) or time.strftime("%Y-%m-%d")

        if not version_name or not download_url:
            print(f"[应用汇监控] 未能提取到版本号或直链: ver={version_name}")
            return None

        return {
            "version": version_name,
            "date": date_str,
            "download_url": download_url,
            "channel": "应用汇",
        }
    except Exception as e:
        print(f"[应用汇监控] 查询异常: {e}")
        return None
