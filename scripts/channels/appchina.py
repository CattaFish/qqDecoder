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

# 抑制 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

APPCHINA_TARGET = "http://m.appchina.com/app/com.tencent.mobileqq"


def fetch_appchina_html():
    """智能穿透获取应用汇网页 HTML"""
    api_key = os.environ.get("SCRAPER_API_KEY", "").strip()

    # 1. 如果配置了 ScraperAPI (GitHub Actions 环境)，优先走 ScraperAPI 云端穿透
    if api_key:
        print("[应用汇监控] 检测到 ScraperAPI 密钥，正在通过云端住宅节点穿透抓取...")
        try:
            payload = {
                "api_key": api_key,
                "url": APPCHINA_TARGET,
                # "country_code": "cn"  # 如果账号支持可开启中国节点
            }
            resp = requests.get("https://api.scraperapi.com/", params=payload, timeout=25)
            if resp.status_code == 200 and ("版本" in resp.text or "下载" in resp.text):
                print("[应用汇监控] ScraperAPI 穿透成功！")
                return resp.text
            else:
                print(f"[应用汇监控] ScraperAPI 返回状态码: {resp.status_code}")
        except Exception as e:
            print(f"[应用汇监控] ScraperAPI 请求异常: {e}")

    # 2. 本地直连尝试 (你本地电脑/手机直接秒开)
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    }
    try:
        r = requests.get(APPCHINA_TARGET, headers=headers, verify=False, timeout=10)
        if r.status_code == 200 and ("版本" in r.text or "下载" in r.text):
            return r.text
    except Exception:
        pass

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

        # 1. 提取版本号
        version = None
        m_ver = re.search(r"版本[\s\xa0:：]*([0-9]+(?:\.[0-9]+)+)", text_content)
        if m_ver:
            version = m_ver.group(1).strip()

        # 2. 提取更新日期
        date_str = None
        m_date = re.search(
            r"最后更新[^\d]*([12]\d{3}[-/年.](?:0?[1-9]|1[0-2])[-/月.](?:0?[1-9]|[12]\d|3[01]))",
            html_text
        )
        if m_date:
            date_str = validate_date_str(m_date.group(1))
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        # 3. 提取普通下载按钮 ("下载APK" - class: normal-down-btn)
        download_url = None
        btn = soup.find("a", class_="normal-down-btn")
        if btn and btn.get("href"):
            download_url = btn.get("href").strip()

        if not download_url:
            for a in soup.find_all("a", href=True):
                txt = a.get_text().strip()
                if "下载APK" in txt or "下载apk" in txt.lower():
                    download_url = a["href"].strip()
                    break

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
