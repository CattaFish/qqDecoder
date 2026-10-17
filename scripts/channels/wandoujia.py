# -*- coding: utf-8 -*-
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from extractor import validate_date_str

WANDOUJIA_URL = "https://wap.pp.cn/app_zYoOzyuL/"

def get_wandoujia_info():
    """获取豌豆荚页面的版本信息、更新日期及普通下载直链"""
    print("[豌豆荚监控] 正在抓取豌豆荚详情页...")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        }
        resp = requests.get(WANDOUJIA_URL, headers=headers, timeout=20)
        if resp.status_code != 200:
            print(f"[豌豆荚监控] 请求网页失败: HTTP {resp.status_code}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        version = None
        text_content = soup.get_text()
        m_ver = re.search(r"版本信息[：:\s]*\n*([0-9]+(?:\.[0-9]+)+)", text_content)
        if m_ver: version = m_ver.group(1).strip()

        date_str = None
        m_date = re.search(r"更新时间[：:\s]*\n*([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2})", text_content)
        if m_date: date_str = validate_date_str(m_date.group(1))
        if not date_str: date_str = time.strftime("%Y-%m-%d")

        download_url = None
        for a in soup.find_all("a", href=True):
            if "普通下载" in a.get_text():
                download_url = a["href"].strip()
                break

        if not download_url:
            for a in soup.find_all("a"):
                if "普通下载" in a.get_text().strip():
                    download_url = a.get("href") or a.get("data-download") or a.get("data-url")
                    break

        if not version or not download_url:
            print(f"[豌豆荚监控] 解析异常: 版本={version}, 下载URL={download_url}")
            return None

        download_url = urljoin(WANDOUJIA_URL, download_url.strip())

        return {
            "version": version,
            "date": date_str,
            "download_url": download_url,
            "channel": "豌豆荚",
        }
    except Exception as e:
        print(f"[豌豆荚监控] 抓取失败: {e}")
        return None
