# -*- coding: utf-8 -*-
import datetime
import hashlib
import re
import time
import xml.etree.ElementTree as ET
import requests
from extractor import validate_date_str

ODC_URL = "https://cn-ms.galaxyappstore.com/ods.as"
INST_ID = "appmarket-samsung-anonymous"


def get_samsung_credentials():
    stduk = hashlib.sha256((INST_ID + "kjk3syk6wkj5").encode()).hexdigest()
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hour_digits = "".join(filter(str.isdigit, now_iso[:13])).ljust(10, "0")
    day_str = str(int(now_iso[8:10])) if now_iso[8:10].isdigit() else "1"
    trans_id = day_str + hashlib.sha256((stduk + hour_digits + "GalaxyApps").encode()).hexdigest()[:7]
    return stduk, trans_id


def call_samsung_api(req_id: str, req_name: str, params: dict):
    stduk, trans_id = get_samsung_credentials()
    now_ms = int(time.time() * 1000)

    param_xml = "".join(f'<param name="{k}">{v}</param>' for k, v in params.items())
    body = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<SamsungProtocol networkType="0" version2="4.6.08.11" lang="zh_CN" openApiVersion="32" deviceModel="SM-S9280" deviceMakerName="samsung" deviceMakerType="0" mcc="460" mnc="00" csc="CHC" odcVersion="4.6.08.11" storeFilter="" supportFeature="" version="7.8" filter="1" odcType="01" storeMode="0" cacheVersion="1" systemId="{now_ms}" sessionId="{INST_ID}" logId="{stduk[:16]}" deviceFeature="locale=zh_CN||abi64=arm64-v8a||oneUiVersion=60100" userMode="0">
    <request name="{req_name}" id="{req_id}" numParam="{len(params)}" transactionId="{trans_id}">
        {param_xml}
    </request>
</SamsungProtocol>"""

    headers = {"Content-Type": "text/plain", "Accept": "image/webp"}
    resp = requests.post(f"{ODC_URL}?reqId={req_id}&ot=01&ct=B", data=body.encode("utf-8"), headers=headers, timeout=15)
    if resp.status_code != 200:
        return []

    try:
        root = ET.fromstring(resp.text)
        items = []
        for l_elem in root.findall(".//list"):
            d = {v.attrib.get("name"): (v.text or "") for v in l_elem if v.attrib.get("name")}
            if d:
                items.append(d)
        return items
    except Exception:
        return []


def get_samsung_info():
    """获取三星应用商店 (Galaxy Store) QQ 的版本信息与下载直链"""
    print("[三星监控] 正在向官方接口查询线上版本...")
    try:
        stduk, _ = get_samsung_credentials()

        # 1. 检索定位 QQ
        search_params = {
            "keyword": "QQ", "imgWidth": "135", "imgHeight": "135",
            "alignOrder": "bestMatch", "startNum": "1", "endNum": "5",
            "contentType": "all", "storeContentType": "Apps", "status": "0",
            "qlDomainCode": "sa", "qlDeviceType": "phone", "qlInputMethod": "",
            "imei": INST_ID, "stduk": stduk, "extuk": INST_ID, "runestoneYn": "N"
        }
        items = call_samsung_api("2040", "searchProductListEx2Notc", search_params)
        target = next((it for it in items if it.get("GUID") == "com.tencent.mobileqq" or "QQ" == it.get("productName", "").strip()), None)

        if not target:
            print("[三星监控] 未在应用列表中定位到 QQ")
            return None

        pid = target.get("productID", "")
        guid = target.get("GUID", "com.tencent.mobileqq")
        version_name = target.get("version", "").strip()

        raw_date = target.get("modifyDate") or target.get("createDate") or target.get("updateTime")
        date_str = validate_date_str(raw_date) or time.strftime("%Y-%m-%d")

        # 2. 换取下载直链 (优先标准通道，备用腾讯直通通道)
        dl_params = {
            "GUID": guid, "imei": INST_ID, "stduk": stduk, "extuk": INST_ID,
            "dowloadType": "new", "autoUpdateYN": "N", "deepLinkSource": "N",
            "predeployed": "0", "resumeYN": "N", "productID": pid
        }
        dl_res = call_samsung_api("2311", "downloadEx2", dl_params)
        download_url = dl_res[0].get("downLoadURI") or dl_res[0].get("downloadURI") if dl_res else ""

        if not download_url:
            t_params = {
                "stduk": stduk, "extuk": INST_ID, "GUID": guid,
                "tencentSource": "general", "lastInterfaceName": "searchProductListEx2Notc"
            }
            t_res = call_samsung_api("2801", "downloadInfoForTencent", t_params)
            download_url = t_res[0].get("downLoadURI") or t_res[0].get("downloadURI") if t_res else ""

        if not download_url:
            print(f"[三星监控] 未能提取到下载直链")
            return None

        # 智能版本推断：优先从 CDN 文件名 (例如 com.tencent.mobileqq_9.3.75.apk) 提炼最新版本
        m_fs = re.search(r"_(\d+\.\d+\.\d+(?:\.\d+)?)\.apk", download_url)
        final_ver = m_fs.group(1) if m_fs else version_name

        if not final_ver:
            print("[三星监控] 未能获取到有效版本号")
            return None

        return {
            "version": final_ver,
            "date": date_str,
            "download_url": download_url,
            "channel": "三星",
        }
    except Exception as e:
        print(f"[三星监控] 查询异常: {e}")
        return None
