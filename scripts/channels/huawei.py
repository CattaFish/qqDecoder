# -*- coding: utf-8 -*-
import json
import time
import requests
from extractor import validate_date_str

HUAWEI_API_URL = "https://store-drcn.hispace.dbankcloud.com/hwmarket/api/clientApi"

def get_huawei_info():
    """获取华为应用市场 (HiSpace) 的版本信息与下载直链"""
    print("[华为监控] 正在向官方接口查询线上版本...")
    try:
        headers = {
            "User-Agent": "HiSpace##16.7.1.300##CatGril##MeeowPhone",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json",
        }
        data = {
            "method": "client.appDetailById", "serviceType": "0", "ver": "1.1",
            "sign": "CatGirl", "id": "C9319", "package": "com.tencent.mobileqq",
            "source": "autoList", "deviceSpecParams": json.dumps({"abis": "arm64-v8a,armeabi-v7a,armeabi"}),
        }
        resp = requests.post(HUAWEI_API_URL, headers=headers, data=data, timeout=20)
        if resp.status_code != 200:
            print(f"[华为监控] 请求接口失败: HTTP {resp.status_code}")
            return None

        res_json = resp.json()
        detail_list = res_json.get("detailInfo")
        if not detail_list or not isinstance(detail_list, list) or len(detail_list) == 0:
            print(f"[华为监控] 响应无有效 detailInfo 数据: {res_json}")
            return None

        item = detail_list[0]
        version_name = str(item.get("versionName", "")).strip()
        release_date = str(item.get("releaseDate", "")).strip()
        download_url = str(item.get("url", "")).strip()
        date_str = validate_date_str(release_date) or time.strftime("%Y-%m-%d")

        if not version_name or not download_url:
            print(f"[华为监控] 未能提取到版本号或直链: ver={version_name}")
            return None

        return {
            "version": version_name,
            "date": date_str,
            "download_url": download_url,
            "channel": "华为",
        }
    except Exception as e:
        print(f"[华为监控] 查询异常: {e}")
        return None
