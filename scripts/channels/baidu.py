# -*- coding: utf-8 -*-
import json
import os
import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
from extractor import validate_date_str

BAIDU_URL = (
    "https://mobile.baidu.com/item?docid=5016045026"
    "&f0=search_searchContent%400_appBaseNormal%400"
    "&advitem=source%2Bnatural%40pos%2B1%40bid%2B0%40ctr%2B0.000000%40trans%2B%40keyword%2Bqq%40app_rcv%2B"
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Mobile) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://mobile.baidu.com/",
}


def fetch_page_html():
    """双通道请求：先直连，失败时自动调用 ScraperAPI 代理重试"""
    # 1. 尝试直连抓取
    try:
        resp = requests.get(BAIDU_URL, headers=DEFAULT_HEADERS, timeout=15)
        if resp.status_code == 200 and len(resp.text) > 1000:
            return resp.text
    except Exception as e:
        print(f"[百度监控] 直连请求异常: {e}")

    # 2. 直连失败，探测 ScraperAPI 进行代理穿透
    api_key = os.environ.get("SCRAPER_API_KEY", "").strip()
    if api_key:
        print("[百度监控] 直连受阻，正在调用 ScraperAPI 代理重试...")
        try:
            proxy_url = f"http://api.scraperapi.com?api_key={api_key}&url={urllib.parse.quote(BAIDU_URL)}"
            resp = requests.get(proxy_url, timeout=30)
            if resp.status_code == 200 and len(resp.text) > 1000:
                print("[百度监控] ScraperAPI 代理重试成功！")
                return resp.text
            else:
                print(f"[百度监控] ScraperAPI 返回状态码: {resp.status_code}")
        except Exception as e:
            print(f"[百度监控] ScraperAPI 请求异常: {e}")
    else:
        print("[百度监控] 未配置 SCRAPER_API_KEY，跳过代理重试。")

    return None


def get_baidu_info():
    """获取百度手机助手 QQ 的版本信息与真实普通下载直链"""
    print("[百度监控] 正在抓取百度手机助手详情页...")
    html_text = fetch_page_html()
    if not html_text:
        print("[百度监控] 获取网页内容失败")
        return None

    version = None
    date_str = None
    download_url = None

    # 方案 A: 从 window.__INITIAL_STATE__ 原生 SSR JSON 结构树中提取（最权威、无损）
    try:
        m_state = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.*?});\s*</script>", html_text, re.DOTALL)
        if m_state:
            state_json = json.loads(m_state.group(1))
            res_list = state_json.get("appDoc", {}).get("resList", {})
            for _, val in res_list.items():
                data_wrap = val.get("data", {})
                app_doc = data_wrap.get("appDoc", {}) or data_wrap.get("appDocNew", {})
                if app_doc.get("package") == "com.tencent.mobileqq":
                    version = str(app_doc.get("versionname") or app_doc.get("versionNum") or "").strip()
                    raw_date = str(app_doc.get("updatetime") or app_doc.get("appUpdateDate") or "").strip()
                    date_str = validate_date_str(raw_date)

                    # 提取纯净普通下载直链（排除百度的 appsearch）
                    dl_cand = app_doc.get("download_url") or app_doc.get("downloadUrl") or app_doc.get("shouzhuDownloadUrl")
                    if dl_cand and "gdown.baidu.com" in dl_cand:
                        download_url = dl_cand
                    break
    except Exception as e:
        print(f"[百度监控] 解析 SSR JSON 状态树微调，启用备用正则匹配: {e}")

    # 方案 B: DOM 与正则兜底提取
    if not version or not download_url:
        soup = BeautifulSoup(html_text, "html.parser")
        text_content = soup.get_text()

        # 版本提取
        if not version:
            m_ver = re.search(r"版本[：:\s]*\s*([0-9]+(?:\.[0-9]+)+)", text_content)
            if m_ver: version = m_ver.group(1).strip()

        # 日期提取
        if not date_str:
            m_date = re.search(r"更新时间[：:\s]*\s*([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2})", text_content)
            if m_date: date_str = validate_date_str(m_date.group(1))

        # 真实 APK 直链提取（排除百度助手的 9197c4deaac0b93a3df17edf7d05969e）
        if not download_url:
            cands = re.findall(r'https?://gdown\.baidu\.com/appcenter/pkg/upload/[a-f0-9]{32}\?prodid=\d+', html_text)
            # 过滤排除百度手机助手自身安装包
            pure_cands = [u for u in cands if "9197c4deaac0b93a3df17edf7d05969e" not in u]
            if pure_cands:
                download_url = pure_cands[0]

    if not date_str:
        date_str = time.strftime("%Y-%m-%d")

    if not version or not download_url:
        print(f"[百度监控] 解析异常: 版本={version}, 下载URL={download_url}")
        return None

    # 清理并转义 URL
    download_url = download_url.replace("\\/", "/")

    return {
        "version": version,
        "date": date_str,
        "download_url": download_url,
        "channel": "百度",
    }
