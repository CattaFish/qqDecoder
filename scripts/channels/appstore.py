# -*- coding: utf-8 -*-
import json
import os
import time
import requests
from extractor import validate_date_str

APPSTORE_API_URL = "https://itunes.apple.com/lookup?id=444934666&country=cn"
ARMCONVERTER_HOST = "https://armconverter.com/decryptedappstore"


def get_appstore_info():
    """获取苹果 App Store 官方线上版本信息，并通过 ARMConverter 自动生成脱壳 IPA 下载直链"""
    print("[AppStore监控] 正在向苹果官方 API 查询线上最新版本...")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        resp = requests.get(APPSTORE_API_URL, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"[AppStore监控] 请求苹果 API 失败: HTTP {resp.status_code}")
            return None

        res_json = resp.json()
        if res_json.get("resultCount", 0) == 0:
            print("[AppStore监控] 接口无返回匹配结果")
            return None

        app_item = res_json["results"][0]
        version = str(app_item.get("version", "")).strip()
        raw_date = app_item.get("currentVersionReleaseDate", "")
        date_str = validate_date_str(raw_date[:10]) if raw_date else time.strftime("%Y-%m-%d")

        if not version:
            print("[AppStore监控] 未能提取到版本号")
            return None

        # 换取已脱壳的明文 IPA 直链
        download_url = f"https://apps.apple.com/cn/app/qq/id444934666"
        session_token = os.environ.get("ARMCONVERTER_SESSION", "").strip()

        if session_token:
            try:
                prep_url = f"{ARMCONVERTER_HOST}/download/444934666/com.tencent.mqq/{version}/prepare"
                h_post = {
                    "Referer": "https://armconverter.com/decryptedappstore",
                    "User-Agent": "Mozilla/5.0",
                }
                r_prep = requests.post(
                    prep_url,
                    headers=h_post,
                    cookies={"session": session_token},
                    timeout=15,
                )
                if r_prep.status_code == 200:
                    token = r_prep.json().get("token")
                    if token and token != "null":
                        download_url = f"{ARMCONVERTER_HOST}/download/444934666/com.tencent.mqq/{version}?token={token}"
                        print(f"[AppStore监控] 成功解析到已脱壳直链！版本: {version}")
            except Exception as e:
                print(f"[AppStore监控] 换取脱壳直链受阻: {e}")

        return {
            "version": version,
            "date": date_str,
            "download_url": download_url,
            "channel": "AppStore",
            "platform": "iOS",
        }
    except Exception as e:
        print(f"[AppStore监控] 查询异常: {e}")
        return None
