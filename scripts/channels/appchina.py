# -*- coding: utf-8 -*-
import re
import time
import urllib3
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from extractor import validate_date_str

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

APPCHINA_URLS = [
    "http://m.appchina.com/app/com.tencent.mobileqq",
    "https://m.appchina.com/app/com.tencent.mobileqq",
    "http://www.appchina.com/app/com.tencent.mobileqq",
]


def get_appchina_info():
    """获取应用汇 (AppChina) QQ 的版本信息与下载直链 (带短连接防 RST 与多端点重试)"""
    print("[应用汇监控] 正在抓取应用汇网页端...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",  # 核心：通知服务端不要保持长连接，彻底规避 ConnectionResetError 104
    }

    for target_url in APPCHINA_URLS:
        try:
            resp = requests.get(target_url, headers=headers, verify=False, timeout=15)
            if resp.status_code != 200:
                continue

            html_text = resp.text
            soup = BeautifulSoup(html_text, "html.parser")
            text_content = soup.get_text()

            # 1. 提取版本号
            version = None
            m_ver = re.search(r"最新版本[：:\s]*\s*([0-9]+(?:\.[0-9]+)+)", text_content)
            if not m_ver:
                m_ver = re.search(r"版本[：:\s]*\s*([0-9]+(?:\.[0-9]+)+)", text_content)
            if m_ver:
                version = m_ver.group(1).strip()

            # 2. 提取更新日期
            date_str = None
            m_date = re.search(
                r"最后更新[：:\s]*\s*([12]\d{3}[-/年.](?:0?[1-9]|1[0-2])[-/月.](?:0?[1-9]|[12]\d|3[01]))",
                text_content,
            )
            if m_date:
                date_str = validate_date_str(m_date.group(1))
            if not date_str:
                date_str = time.strftime("%Y-%m-%d")

            # 3. 提取普通下载按钮 ("下载APK")
            download_url = None
            for a in soup.find_all("a", href=True):
                txt = a.get_text().strip()
                href = a["href"].strip()
                if "下载APK" in txt or "下载apk" in txt.lower():
                    download_url = href
                    break

            if not download_url:
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if "/market/d/" in href or (href.endswith(".apk") and "appchina" in href):
                        download_url = href
                        break

            if download_url:
                download_url = urljoin(target_url, download_url)

            if version and download_url:
                return {
                    "version": version,
                    "date": date_str,
                    "download_url": download_url,
                    "channel": "应用汇",
                }
        except Exception as e:
            time.sleep(1)
            continue

    print("[应用汇监控] 多端点重试均未成功")
    return None
