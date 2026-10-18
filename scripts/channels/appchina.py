# -*- coding: utf-8 -*-
import re
import time
import urllib.parse
import urllib3
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from extractor import validate_date_str

# 抑制 SSL 不安全连接警告信息
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

APPCHINA_TARGET = "http://m.appchina.com/app/com.tencent.mobileqq"


def fetch_html_with_proxies():
    """4重穿透获取 HTML (解决 GitHub Actions 海外 IP 被应用汇 WAF 拦截问题)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    }

    # 1. 优先尝试本地直连 (国内机器/手机将直接秒过)
    try:
        r = requests.get(APPCHINA_TARGET, headers=headers, verify=False, timeout=10)
        if r.status_code == 200 and ("版本" in r.text or "下载" in r.text):
            return r.text
    except Exception:
        pass

    # 2. 穿透节点 A: AllOrigins 代理 (返回 JSON，HTML 藏在 contents 字段)
    try:
        r = requests.get(f"https://api.allorigins.win/get?url={urllib.parse.quote(APPCHINA_TARGET)}", headers=headers, timeout=15)
        if r.status_code == 200:
            html = r.json().get("contents", "")
            if "版本" in html or "下载" in html:
                print("[应用汇监控] 成功通过 AllOrigins 代理穿透海外拦截！")
                return html
    except Exception:
        pass

    # 3. 穿透节点 B: CodeTabs 代理
    try:
        r = requests.get(f"https://api.codetabs.com/v1/proxy?quest={APPCHINA_TARGET}", headers=headers, timeout=15)
        if r.status_code == 200 and ("版本" in r.text or "下载" in r.text):
            print("[应用汇监控] 成功通过 CodeTabs 代理穿透海外拦截！")
            return r.text
    except Exception:
        pass

    # 4. 穿透节点 C: CorsProxy.io 代理
    try:
        r = requests.get(f"https://corsproxy.io/?{urllib.parse.quote(APPCHINA_TARGET)}", headers=headers, timeout=15)
        if r.status_code == 200 and ("版本" in r.text or "下载" in r.text):
            print("[应用汇监控] 成功通过 CorsProxy 代理穿透海外拦截！")
            return r.text
    except Exception:
        pass

    return None


def get_appchina_info():
    """获取应用汇 (AppChina) QQ 的版本信息与下载直链 (带短连接防 RST 与精准 DOM 解析)"""
    print("[应用汇监控] 正在抓取应用汇网页端...")
    
    html_text = fetch_html_with_proxies()
    if not html_text:
        print("[应用汇监控] 直连与所有公共代理均被拒绝，放弃抓取。")
        return None

    try:
        soup = BeautifulSoup(html_text, "html.parser")
        # 用空格代替换行连接文本，避免“版本”和“9.3.75”被截断
        text_content = soup.get_text(separator=" ", strip=True)

        # 1. 提取版本号
        version = None
        m_ver = re.search(r"最新版本[\s\xa0:：]*([0-9]+(?:\.[0-9]+)+)", text_content)
        if not m_ver:
            m_ver = re.search(r"版本[\s\xa0:：]*([0-9]+(?:\.[0-9]+)+)", text_content)
        if m_ver:
            version = m_ver.group(1).strip()

        # 2. 提取更新日期 (跨标签提取，正则忽略中间的 HTML 残留)
        date_str = None
        m_date = re.search(
            r"最后更新[^\d]*([12]\d{3}[-/年.](?:0?[1-9]|1[0-2])[-/月.](?:0?[1-9]|[12]\d|3[01]))",
            html_text
        )
        if m_date:
            date_str = validate_date_str(m_date.group(1))
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        # 3. 提取普通下载按钮 ("下载APK" - 真实 class: normal-down-btn)
        download_url = None
        
        # 策略 A: 精确定位应用汇官方的普通下载按钮 class
        btn = soup.find("a", class_="normal-down-btn")
        if btn and btn.get("href"):
            download_url = btn.get("href").strip()
        
        # 策略 B: 文本包含“下载APK”
        if not download_url:
            for a in soup.find_all("a", href=True):
                txt = a.get_text().strip()
                if "下载APK" in txt or "下载apk" in txt.lower():
                    download_url = a["href"].strip()
                    break

        if download_url:
            # 必须硬编码回原始域名，防止 URL 拼接到 proxy 代理域名上
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
        print(f"[应用汇监控] 解析过程抛出异常: {e}")
        return None
