# -*- coding: utf-8 -*-
import os
import re
import time
import urllib.parse
import urllib3
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from extractor import validate_date_str

# 抑制 SSL 不安全证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

APPCHINA_TARGET_M = "http://m.appchina.com/app/com.tencent.mobileqq"
APPCHINA_TARGET_PC = "http://www.appchina.com/app/com.tencent.mobileqq"


def fetch_via_scraperapi(target_url: str, api_key: str):
    """通过 ScraperAPI 进行住宅代理穿透 (官方推荐超时需至少 70 秒)"""
    print(f"[应用汇监控] 正在通过 ScraperAPI 住宅节点请求: {target_url}...")
    payload = {
        "api_key": api_key,
        "url": target_url,
        "keep_headers": "true",
    }
    # 增加 2 次尝试重试机制
    for attempt in range(1, 3):
        try:
            # 连接超时 10s，读取超时 75s (符合官方规范)
            resp = requests.get(
                "https://api.scraperapi.com/",
                params=payload,
                timeout=(10, 75)
            )
            if resp.status_code == 200 and ("版本" in resp.text or "下载" in resp.text):
                print(f"[应用汇监控] ScraperAPI (第 {attempt} 次) 穿透成功！")
                return resp.text
            else:
                print(f"[应用汇监控] ScraperAPI (第 {attempt} 次) 返回状态码: {resp.status_code}")
        except requests.exceptions.Timeout:
            print(f"[应用汇监控] ScraperAPI (第 {attempt} 次) 请求超时(>75s)，准备重试...")
        except Exception as e:
            print(f"[应用汇监控] ScraperAPI (第 {attempt} 次) 请求异常: {e}")
        time.sleep(2)
    return None


def fetch_via_jina_proxy(target_url: str):
    """通过免 Key 的公共边缘 Reader 节点进行反向穿透中转"""
    print(f"[应用汇监控] 尝试通过公共免 Key 代理通道穿透: {target_url}...")
    proxy_url = f"https://r.jina.ai/{target_url}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-No-Cache": "true",
    }
    try:
        resp = requests.get(proxy_url, headers=headers, timeout=30)
        if resp.status_code == 200 and ("版本" in resp.text or "下载" in resp.text):
            print("[应用汇监控] 公共中转穿透成功！")
            return resp.text
    except Exception as e:
        print(f"[应用汇监控] 公共中转穿透失败: {e}")
    return None


def fetch_via_direct(target_url: str):
    """本地直接连接 (适用于开发人员电脑或国内自建 Runner)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    }
    try:
        r = requests.get(target_url, headers=headers, verify=False, timeout=10)
        if r.status_code == 200 and ("版本" in r.text or "下载" in r.text):
            return r.text
    except Exception:
        pass
    return None


def fetch_appchina_html():
    """多策略混合获取网页内容 (ScraperAPI -> Jina 免Key中转 -> 直连)"""
    api_key = os.environ.get("SCRAPER_API_KEY", "").strip()

    # 通道 1: 优先走 ScraperAPI
    if api_key:
        html = fetch_via_scraperapi(APPCHINA_TARGET_M, api_key)
        if html:
            return html

    # 通道 2: 若无 Key 或 ScraperAPI 额度耗尽/失败，走公共中转
    html = fetch_via_jina_proxy(APPCHINA_TARGET_M)
    if html:
        return html

    # 通道 3: 尝试 PC 端地址中转
    html = fetch_via_jina_proxy(APPCHINA_TARGET_PC)
    if html:
        return html

    # 通道 4: 本地直连尝试
    html = fetch_via_direct(APPCHINA_TARGET_M)
    if html:
        return html

    return None


def get_appchina_info():
    """获取应用汇 (AppChina) QQ 的版本信息与下载直链"""
    print("[应用汇监控] 正在抓取应用汇网页端...")

    html_text = fetch_appchina_html()
    if not html_text:
        print("[应用汇监控] 无法获取网页内容，本次跳过。")
        return None

    try:
        soup = BeautifulSoup(html_text, "html.parser")
        text_content = soup.get_text(separator=" ", strip=True)

        # 1. 提取版本号 (兼容 HTML 标签和纯文本/Markdown 格式)
        version = None
        m_ver = re.search(r"版本[\s\xa0:：]*([0-9]+(?:\.[0-9]+)+)", text_content)
        if not m_ver:
            m_ver = re.search(r"\b([0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)\b", text_content)
        if m_ver:
            version = m_ver.group(1).strip()

        # 2. 提取更新日期
        date_str = None
        m_date = re.search(
            r"(?:最后更新|更新时间|时间)[^\d]*([12]\d{3}[-/年.](?:0?[1-9]|1[0-2])[-/月.](?:0?[1-9]|[12]\d|3[01]))",
            html_text
        )
        if m_date:
            date_str = validate_date_str(m_date.group(1))
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        # 3. 提取下载链接 (支持标准 a 标签与 Markdown [链接](url) 格式)
        download_url = None
        btn = soup.find("a", class_="normal-down-btn")
        if btn and btn.get("href"):
            download_url = btn.get("href").strip()

        if not download_url:
            for a in soup.find_all("a", href=True):
                txt = a.get_text().strip()
                if "下载APK" in txt or "下载apk" in txt.lower() or "普通下载" in txt:
                    download_url = a["href"].strip()
                    break

        if not download_url:
            # 兼容正则匹配应用汇的 APK 下载直链特征 (例如 /market/d/ 或直接 .apk 路径)
            m_dl = re.search(r'https?://[^\s"\'\)]+?(?:appchina\.com/market/d/|\.apk[^\s"\'\)]*)', html_text)
            if not m_dl:
                m_dl = re.search(r'(/market/d/[^\s"\'\)]+)', html_text)
            if m_dl:
                download_url = m_dl.group(1).strip()

        if download_url:
            download_url = urljoin("http://m.appchina.com/", download_url)

        if version and download_url:
            return {
                "version": version,
                "date": date_str,
                "download_url": download_url,
                "channel": "应用汇",
            }

        print(f"[应用汇监控] 解析异常: 版本={version}, 下载URL={download_url}")
        return None

    except Exception as e:
        print(f"[应用汇监控] 解析异常: {e}")
        return None
