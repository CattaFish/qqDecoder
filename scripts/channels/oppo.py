# -*- coding: utf-8 -*-
import hashlib
import time
import urllib.parse
import requests
from extractor import validate_date_str

OPPO_APP_ID = "30334"
OPPO_SALT1 = b"cdb09c43063ea6bb08f4fe8a43775179bdc58acb383220be"
OPPO_SALT2 = (
    b"STORENEWMIICeAIBADANBgkqhkiG9w0BAQEFAASCAmIwggJeAgEAAoGBANYFY/UJGSzhIhpx6YM5KJ9yRHc7YeURxzb9tDvJvMfENHlnP3DtVkOIjERbpsSd76fjtZnMWY60TpGLGyrNkvuV40L15JQhHAo9yURpPQoI0eg3SLFmTEI/MUiPRCwfwYf2deqKKlsmMSysYYHX9JiGzQuWiYZaawxprSuiqDGvAgMBAAECgYEAtQ0QV00gGABISljNMy5aeDBBTSBWG2OjxJhxLRbndZM81OsMFysgC7dq+bUS6ke1YrDWgsoFhRxxTtx/2gDYciGp/c/h0Td5pGw7T9W6zo2xWI5oh1WyTnn0Xj17O9CmOk4fFDpJ6bapL+fyDy7gkEUChJ9+p66WSAlsfUhJ2TECQQD5sFWMGE2IiEuz4fIPaDrNSTHeFQQr/ZpZ7VzB2tcG7GyZRx5YORbZmX1jR7l3H4F98MgqCGs88w6FKnCpxDK3AkEA225CphAcfyiH0ShlZxEXBgIYt3V8nQuc/g2KJtiV6eeFkxmOMHbVTPGkARvt5VoPYEjwPTg43oqTDJVtlWagyQJBAOvEeJLno9aHNExvznyD4/pR4hec6qqLNgMyIYMfHCl6d3UodVvC1HO1/nMPl+4GvuRnxuoBtxj/PTe7AlUbYPMCQQDOkf4sVv58tqslO+I6JNyHy3F5RCELtuMUR6rG5x46FLqqwGQbO8ORq+m5IZHTV/Uhr4h6GXNwDQRh1EpVW0gBAkAp/v3tPI1riz6UuG0I6uf5er26yl5evPyPrjrD299L4Qy/1EIunayC7JYcSGlR01+EDYYgwUkec+QgrRC/NstV"
)
OPPO_OCS = "google%2FTrebleDroid+vanilla%2F33%2F13%2FUNKNOWN%2F2%2Flineage_arm64_bvN-userdebug+13+TQ3A.230901.001+eng.crossg.20231018.003713+test-keys%2F260700"
OPPO_UA = "google%2FTrebleDroid+vanilla%2F33%2F13%2FUNKNOWN%2F2%2F2101%2F260700%2F26.7.0_CN"
OPPO_ID_HEADER = "///"
OPPO_OAK = "cdb09c43063ea6bb"

def generate_oppo_sign(path, query_str, t_str):
    decoded_query = urllib.parse.unquote(query_str)
    raw_str = f"{OPPO_OCS}{t_str}{OPPO_ID_HEADER}{path}{decoded_query}"
    raw_bytes = raw_str.encode("utf-8")
    len_bytes = str(len(raw_str) + 48).encode("utf-8")
    m = hashlib.md5()
    m.update(OPPO_SALT1); m.update(raw_bytes); m.update(len_bytes); m.update(OPPO_SALT2)
    return m.hexdigest().lower()

def build_oppo_headers(path, query_str=""):
    t_str = str(int(time.time() * 1000))
    dynamic_sign = generate_oppo_sign(path, query_str, t_str)
    return {
        "User-Agent": OPPO_UA, "Accept": "application/json; charset=UTF-8",
        "id": OPPO_ID_HEADER, "cpu-abilist": "arm64-v8a,armeabi-v7a,armeabi",
        "ocs": OPPO_OCS, "oak": OPPO_OAK, "t": t_str, "sign": dynamic_sign, "Connection": "Keep-Alive",
    }

def get_oppo_info():
    """获取 OPPO 软件商店 QQ 的版本信息与下载直链"""
    print("[OPPO监控] 正在向官方接口查询线上版本...")
    try:
        detail_path = f"/detail/v5/{OPPO_APP_ID}"
        params = {"appId": OPPO_APP_ID, "query": "1,2,3,4,5,6,7,8,9,10,12,15,16,17,19,20"}
        query_str = urllib.parse.urlencode(params)
        headers = build_oppo_headers(detail_path, query_str)
        url = f"https://api-cn.store.heytapmobi.com{detail_path}"

        resp = requests.get(url, headers=headers, params=params, timeout=20)
        if resp.status_code != 200:
            print(f"[OPPO监控] 详情接口请求失败: HTTP {resp.status_code}")
            return None

        data = resp.json().get("base", {})
        if not data:
            print("[OPPO监控] 未能解析到应用详情数据")
            return None

        ver_name = str(data.get("verName", "")).strip()
        ver_id = data.get("verId")
        raw_date = data.get("publishTime") or data.get("updateTime") or data.get("createTime")
        date_str = validate_date_str(raw_date) or time.strftime("%Y-%m-%d")

        if not ver_name or not ver_id:
            print(f"[OPPO监控] 未能提取到 verName 或 verId: verName={ver_name}, verId={ver_id}")
            return None

        download_path = f"/download/v1/{ver_id}"
        download_api_url = f"https://api-cn.store.heytapmobi.com{download_path}"
        download_headers = build_oppo_headers(download_path, query_str="")

        r_dispatch = requests.get(download_api_url, headers=download_headers, allow_redirects=False, timeout=15)
        real_cdn_url = r_dispatch.headers.get("Location") if r_dispatch.status_code == 302 else (download_api_url if r_dispatch.status_code == 200 else None)

        if not real_cdn_url:
            print(f"[OPPO监控] 获取下载直链失败: HTTP {r_dispatch.status_code}")
            return None

        return {
            "version": ver_name,
            "date": date_str,
            "download_url": real_cdn_url,
            "channel": "OPPO",
        }
    except Exception as e:
        print(f"[OPPO监控] 查询异常: {e}")
        return None
