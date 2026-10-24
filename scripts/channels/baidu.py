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


def find_qq_node(node):
    """深度优先递归搜索属于 com.tencent.mobileqq 的数据字典"""
    if isinstance(node, dict):
        if node.get("package") == "com.tencent.mobileqq":
            if node.get("download_url") or node.get("downloadUrl") or node.get("shouzhuDownloadUrl"):
                return node
        for v in node.values():
            res = find_qq_node(v)
            if res:
                return res
    elif isinstance(node, list):
        for it in node:
            res = find_qq_node(it)
            if res:
                return res
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

    # 方案 A: 针对 window.__INITIAL_STATE__ 执行切片精准解析 (免疫分号与转义干扰)
    try:
        anchor = "window.__INITIAL_STATE__"
        idx = html_text.find(anchor)
        if idx != -1:
            start_json = html_text.find("{", idx)
            end_script = html_text.find("</script>", start_json)
            if start_json != -1 and end_script != -1:
                raw_json = html_text[start_json:end_script].strip()
                if raw_json.endswith(";"):
                    raw_json = raw_json[:-1].strip()

                state_json = json.loads(raw_json)
                qq_node = find_qq_node(state_json)
                if qq_node:
                    version = str(qq_node.get("versionname") or qq_node.get("versionNum") or qq_node.get("version") or "").strip()
                    raw_date = str(qq_node.get("updatetime") or qq_node.get("appUpdateDate") or qq_node.get("buildtime") or "").strip()
                    date_str = validate_date_str(raw_date)

                    cand_url = qq_node.get("download_url") or qq_node.get("downloadUrl") or qq_node.get("shouzhuDownloadUrl")
                    if cand_url and "gdown.baidu.com" in cand_url:
                        download_url = cand_url
    except Exception as e:
        print(f"[百度监控] 解析状态树微调，启动备用正则提取: {e}")

    # 方案 B: 字符流与正则兜底
    if not version or not download_url:
        soup = BeautifulSoup(html_text, "html.parser")
        text_content = soup.get_text()

        if not version:
            m_ver = re.search(r"版本[：:\s]*\s*([0-9]+(?:\.[0-9]+)+)", text_content)
            if m_ver: version = m_ver.group(1).strip()

        if not date_str:
            m_date = re.search(r"更新时间[：:\s]*\s*([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2})", text_content)
            if m_date: date_str = validate_date_str(m_date.group(1))

        if not download_url:
            # 兼容带有 \u002F 转义的 URL
            cand_matches = re.findall(
                r'https?(?:://|:\\u002F\\u002F)gdown\.baidu\.com(?:/|\\u002F)appcenter(?:/|\\u002F)pkg(?:/|\\u002F)upload(?:/|\\u002F)[a-f0-9]{32}(?:\?prodid=\d+)?',
                html_text
            )
            for c in cand_matches:
                cleaned = c.replace(r"\u002F", "/").replace(r"\/", "/")
                # 排除百度助手自身的包
                if "9197c4deaac0b93a3df17edf7d05969e" not in cleaned:
                    download_url = cleaned
                    break

    if not date_str:
        date_str = time.strftime("%Y-%m-%d")

    if not version or not download_url:
        print(f"[百度监控] 解析异常: 版本={version}, 下载URL={download_url}")
        return None

    download_url = download_url.replace(r"\u002F", "/").replace(r"\/", "/")

    return {
        "version": version,
        "date": date_str,
        "download_url": download_url,
        "channel": "百度",
    }
