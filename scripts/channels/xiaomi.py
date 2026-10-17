# -*- coding: utf-8 -*-
import hashlib
import hmac
import random
import time
import urllib.parse
import requests
from extractor import validate_date_str

SALT = "good luck!"
TABLE = "leDTKhmg4MafVFp73x6djvLiHn2G9XPruARBwS0q1OzNJt8WobZsQcYyEICk5U-_"
SIGNED_KEYS = {
    "activedTimeInterval", "ad", "adExchangeFlag", "adFlag", "apkChannel", "appId",
    "bottomTab", "carrier", "clientId", "co", "count", "cpuArchitecture", "device",
    "deviceType", "digestParams", "downloadingAppInfo", "excludedAppIds", "ext_apkChannel",
    "ext_marketType", "flag", "folderName", "get", "gpId", "h5", "id", "imei",
    "installDay", "installError", "instance_id", "international", "keyword", "la",
    "launchDay", "lo", "marketVersion", "miuiBigVersionCode", "miuiBigVersionName",
    "model", "_n", "needLruCache", "network", "newUser", "oldApkHash", "oldVersionCode",
    "os", "packageName", "packageNameList", "page", "pageConfigVersion", "pageRef",
    "pageSize", "pageTag", "params", "pos", "posChain", "previousAppIds",
    "proxyTimeout", "query", "reason", "recentInstallCompleteAppInfo", "ref",
    "refPosition", "refresh", "refs", "resolution", "ro", "sco", "sdk", "searchScope",
    "shouldNativeInterceptRequest", "sid", "sla", "sourcePackage", "stamp",
    "targetVersionCode", "type", "update", "versionCode", "webResVersion", "zoneSuffix",
}

def xiaomi_base64(raw_bytes: bytes) -> str:
    out = []
    i, length = 0, len(raw_bytes)
    while i + 2 < length:
        b1, b2, b3 = raw_bytes[i], raw_bytes[i + 1], raw_bytes[i + 2]
        out.append(TABLE[(b1 >> 2) & 0x3F])
        out.append(TABLE[((b1 << 4) | (b2 >> 4)) & 0x3F])
        out.append(TABLE[((b2 << 2) | (b3 >> 6)) & 0x3F])
        out.append(TABLE[b3 & 0x3F])
        i += 3
    rem = length - i
    if rem == 1:
        b1 = raw_bytes[i]
        out.append(TABLE[(b1 >> 2) & 0x3F])
        out.append(TABLE[(b1 << 4) & 0x3F])
    elif rem == 2:
        b1, b2 = raw_bytes[i], raw_bytes[i + 1]
        out.append(TABLE[(b1 >> 2) & 0x3F])
        out.append(TABLE[((b1 << 4) | (b2 >> 4)) & 0x3F])
        out.append(TABLE[((b2 << 2) | (b3 >> 6)) & 0x3F])
    return "".join(out)

def xiaomi_arrange_exact(decoded_url: str, nonce: str) -> str:
    query_start = decoded_url.find("?")
    url_part = decoded_url[:query_start] if query_start >= 0 else decoded_url
    query = decoded_url[query_start + 1:] if query_start >= 0 and query_start + 1 < len(decoded_url) else ""

    slash_count, reversing_host, builder = 0, True, []
    for ch in url_part:
        if ch == "/" and reversing_host:
            slash_count += 1
            if slash_count == 3:
                builder.append("\n"); builder.append(ch); reversing_host = False
            else: builder.insert(0, ch)
        elif reversing_host: builder.insert(0, ch)
        else: builder.append(ch)
    builder.append("\n")

    params = [p for p in query.split("&") if p and not p.startswith(("_n=", "_s=", "_v="))]
    params.append(f"_n={nonce}")

    plist = []
    for idx, p in enumerate(params):
        k = p.split("=")[0]
        if k in SIGNED_KEYS:
            plist.append(k)
            if idx != len(params) - 1: plist.append(";")
    params.append(f"_p=" + "".join(plist))
    params.sort(key=lambda a: a.lower())

    for idx, param in enumerate(params):
        k = param.split("=")[0]
        signed = k in SIGNED_KEYS if "=" in param else True
        if not signed:
            if idx == len(params) - 1 and builder and builder[-1] == "=": builder.pop()
            continue
        segs = param.split("=")
        builder.append(segs[0])
        for s in segs[1:]:
            builder.append("&"); builder.append(s[::-1])
        if idx != len(params) - 1: builder.append("=")

    full_str = "".join(builder)
    lf = full_str.find("\n")
    return full_str[lf + 1:]

def xiaomi_sign_url(base_url: str) -> str:
    ts = int(time.time() * 1000)
    nonce = f"{ts}_{random.randint(0, 999)}"
    arranged = xiaomi_arrange_exact(urllib.parse.unquote_plus(base_url), nonce)
    algo = [hashlib.md5, hashlib.sha256, hashlib.sha1, hashlib.sha384][ts % 4]
    digest = hmac.new((SALT + nonce).encode("utf-8"), arranged.encode("utf-8"), algo).digest()
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}_n={urllib.parse.quote(nonce)}&_s={urllib.parse.quote(xiaomi_base64(digest))}&_v=1"

def xiaomi_sign_form_fields(url: str, fields: dict) -> dict:
    ts = int(time.time() * 1000)
    nonce = f"{ts}_{random.randint(0, 999)}"
    query_str = urllib.parse.urlencode(fields)
    full_url = f"{url}?{query_str}" if query_str else url
    arranged = xiaomi_arrange_exact(urllib.parse.unquote_plus(full_url), nonce)
    algo = [hashlib.md5, hashlib.sha256, hashlib.sha1, hashlib.sha384][ts % 4]
    digest = hmac.new((SALT + nonce).encode("utf-8"), arranged.encode("utf-8"), algo).digest()
    res = dict(fields)
    res["_n"] = nonce
    res["_s"] = xiaomi_base64(digest)
    res["_v"] = "1"
    return res

def get_xiaomi_info():
    """获取小米应用商店 QQ 的版本信息与下载直链 (APM 原生私有协议)"""
    print("[小米监控] 正在向官方更新接口查询线上版本...")
    try:
        update_url = "https://updateinfo.market.xiaomi.com/apm/updateinfo/v2?lo=CN"
        now_ts = int(time.time() * 1000)
        inst_id = f"a1b2c3d4-e5f6-7890-abcd-{random.randint(100000000000, 999999999999)}"

        fields = {
            "co": "CN", "la": "zh", "lo": "CN", "marketVersion": "40007460",
            "os": "OS3.0", "sdk": "36", "model": "2509FPN0BC", "device": "popsicle",
            "cpuArchitecture": "arm64-v8a", "androidVersion": "16", "apkSource": "0",
            "autoUpdateEnabled": "false", "background": "false", "downloadRestriction": "1",
            "downloadRestrictionMode": "0", "invalidSystemPackageHash": "null",
            "privacyCompliance": "true", "rankTypeV2": "true", "session_id": f"{inst_id}{now_ts}",
            "showUnfitnessApp": "true", "splits": "0", "packageName": "com.tencent.mobileqq",
            "versionCode": "0", "oldApkHash": "0", "installedByMarket": "1", "ref": "update"
        }
        signed_fields = xiaomi_sign_form_fields(update_url, fields)
        post_headers = {
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 16; 2509FPN0BC Build/BP2A.250605.031.A3)",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        r = requests.post(update_url, data=signed_fields, headers=post_headers, timeout=15)
        if r.status_code != 200:
            print(f"[小米监控] 更新接口请求失败: HTTP {r.status_code}")
            return None

        res_json = r.json()
        apps = res_json.get("listApp") or res_json.get("miuiApp") or []
        if not apps:
            print("[小米监控] 未能获取到匹配的应用列表")
            return None

        target = apps[0]
        version_name = str(target.get("versionName", "")).strip()
        app_id = str(target.get("appId") or target.get("id") or "1359")
        raw_update_time = target.get("updateTime") or target.get("createTime") or 0

        date_str = None
        if raw_update_time > 0:
            ts_clean = raw_update_time // 1000 if raw_update_time > 1e12 else raw_update_time
            date_str = validate_date_str(time.strftime("%Y-%m-%d", time.localtime(ts_clean)))
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        if not version_name:
            print("[小米监控] 未能获取到有效版本号")
            return None

        time.sleep(1.0) # 冷却避频控

        get_headers = {
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 16; 2509FPN0BC Build/BP2A.250605.031.A3)",
            "Accept": "application/json"
        }

        download_url = None
        for retry in range(2):
            cur_inst = f"a1b2c3d4-e5f6-7890-abcd-{random.randint(100000000000, 999999999999)}"
            dl_params = {
                "appId": app_id, "pName": "com.tencent.mobileqq", "packageName": "com.tencent.mobileqq",
                "clientId": "6f24320b1e9596bf", "co": "CN", "la": "zh", "lo": "CN", "marketVersion": "40007460",
                "os": "OS3.0", "sdk": "36", "model": "2509FPN0BC", "device": "popsicle",
                "cpuArchitecture": "arm64-v8a", "callerPackage": "com.xiaomi.market",
                "callerSignature": "88daa889de21a80bca64464243c9ede6", "ref": "detail",
                "instance_id": cur_inst,
            }
            dl_url = xiaomi_sign_url(f"https://app.market.xiaomi.com/apm/download/{app_id}?{urllib.parse.urlencode(dl_params)}")
            r_dl = requests.get(dl_url, headers=get_headers, timeout=15)
            if r_dl.status_code == 200:
                dl_json = r_dl.json()
                if "asscMsg" not in dl_json:
                    hosts = dl_json.get("hosts") or [dl_json.get("host")]
                    host = hosts[0] if hosts and hosts[0] else ""
                    apks = dl_json.get("apks") or []
                    for apk in apks:
                        apk_path = apk.get("url")
                        if apk_path:
                            download_url = urllib.parse.urljoin(host, apk_path)
                            if apk.get("type") in ("standalone", "base"):
                                break
                    if download_url: break
            time.sleep(1.5)

        if not download_url:
            print("[小米监控] 下载接口触发临时频控保护，本次暂缓")
            return None

        return {
            "version": version_name,
            "date": date_str,
            "download_url": download_url,
            "channel": "小米",
        }
    except Exception as e:
        print(f"[小米监控] 查询异常: {e}")
        return None
