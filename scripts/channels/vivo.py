# -*- coding: utf-8 -*-
import re
import time
import requests
from extractor import validate_date_str

VIVO_PACKAGE_URL = "https://main.appstore.vivo.com.cn/port/package/"
VIVO_DOWNLOAD_URL = "https://appstore.vivo.com.cn/appinfo/downloadApkFile?id=40439"

def get_vivo_info():
    """获取 vivo 应用商店 QQ 的版本信息与下载直链"""
    print("[vivo监控] 正在向官方接口查询线上版本...")
    try:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "AndroidAppStore/13 (Linux; U; Android 13; TrebleDroid vanilla Build/TQ3A.230901.001)"
        }
        body_data = "id=40439&app_version=55910&versioncode=16410&content_complete=1&opFlags=2147219326&opFlagV2=____f9__3wc"

        resp = requests.post(VIVO_PACKAGE_URL, headers=headers, data=body_data, timeout=20)
        if resp.status_code != 200:
            print(f"[vivo监控] 请求接口失败: HTTP {resp.status_code}")
            return None

        res_json = resp.json()
        val = res_json.get("value")
        if not val or not isinstance(val, dict):
            print(f"[vivo监控] 响应无有效 value 数据: {res_json}")
            return None

        version_name = str(val.get("version_name", "")).strip()
        upload_time = str(val.get("upload_time", "")).strip()
        dl_url = str(val.get("download_url", "")).strip() or VIVO_DOWNLOAD_URL

        date_str = validate_date_str(upload_time.split(" ")[0]) if upload_time else None
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        if not version_name:
            print("[vivo监控] 未能获取到 version_name")
            return None

        return {
            "version": version_name,
            "date": date_str,
            "download_url": dl_url,
            "channel": "vivo",
        }
    except Exception as e:
        print(f"[vivo监控] 查询异常: {e}")
        return None
