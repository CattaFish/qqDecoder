# -*- coding: utf-8 -*-
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from extractor import validate_date_str

APPCHINA_URL = "https://m.appchina.com/app/com.tencent.mobileqq"


def get_appchina_info():
    """获取应用汇 (AppChina) QQ 的版本信息与下载直链"""
    print("[应用汇监控] 正在抓取应用汇网页端...")
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        resp = requests.get(APPCHINA_URL, headers=headers, timeout=20)
        if resp.status_code != 200:
            print(f"[应用汇监控] 请求网页失败: HTTP {resp.status_code}")
            return None

        html_text = resp.text
        soup = BeautifulSoup(html_text, "html.parser")
        text_content = soup.get_text()

        # 1. 提取版本号 (优先匹配 "最新版本:9.3.75" 或 "版本 9.3.75")
        version = None
        m_ver = re.search(r"最新版本[：:\s]*\s*([0-9]+(?:\.[0-9]+)+)", text_content)
        if not m_ver:
            m_ver = re.search(r"版本[：:\s]*\s*([0-9]+(?:\.[0-9]+)+)", text_content)
        if m_ver:
            version = m_ver.group(1).strip()

        # 2. 提取更新日期 (匹配 "最后更新:2026-10-14")
        date_str = None
        m_date = re.search(
            r"最后更新[：:\s]*\s*([12]\d{3}[-/年.](?:0?[1-9]|1[0-2])[-/月.](?:0?[1-9]|[12]\d|3[01]))",
            text_content
        )
        if m_date:
            date_str = validate_date_str(m_date.group(1))

        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        # 3. 提取普通下载按钮 ("下载APK"，严格避开“高速下载”)
        download_url = None

        # 策略 A: 精确匹配包含“下载APK”的 a 标签
        for a in soup.find_all("a", href=True):
            txt = a.get_text().strip()
            href = a["href"].strip()
            if "下载APK" in txt or "下载apk" in txt.lower():
                download_url = href
                break

        # 策略 B: 扫描是否包含 /market/d/ 或 .apk 路径的链接
        if not download_url:
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if "/market/d/" in href or (href.endswith(".apk") and "appchina" in href):
                    download_url = href
                    break

        # 策略 C: 扫描 data-url / data-download 属性
        if not download_url:
            for elem in soup.find_all(["a", "button", "div"]):
                txt = elem.get_text().strip()
                if "下载APK" in txt:
                    d_url = elem.get("data-url") or elem.get("data-href") or elem.get("data-download")
                    if d_url:
                        download_url = d_url
                        break

        if download_url:
            download_url = urljoin(APPCHINA_URL, download_url)

        if not version or not download_url:
            print(f"[应用汇监控] 解析异常: 版本={version}, 下载URL={download_url}")
            return None

        return {
            "version": version,
            "date": date_str,
            "download_url": download_url,
            "channel": "应用汇",
        }
    except Exception as e:
        print(f"[应用汇监控] 抓取失败: {e}")
        return None
