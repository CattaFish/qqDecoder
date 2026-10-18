# -*- coding: utf-8 -*-
import re
import time
import random
import urllib3
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from extractor import validate_date_str

# 抑制 SSL 不安全连接警告信息
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_TARGET_URL = "http://m.appchina.com/app/com.tencent.mobileqq"

# 针对海外 IP 封锁，准备了直连与公益代理转发穿透通道
APPCHINA_URLS = [
    BASE_TARGET_URL,
    "https://m.appchina.com/app/com.tencent.mobileqq",
    f"https://api.allorigins.win/raw?url={BASE_TARGET_URL}",  # 专门用于 GitHub Actions 突破海外IP封锁
]

def get_random_cn_ip():
    """生成随机国内 IP 绕过部分低级 WAF"""
    return f"{random.randint(114, 115)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}"


def get_appchina_info():
    """获取应用汇 (AppChina) QQ 的版本信息与下载直链 (带短连接防 RST 与海外 IP 伪装穿透)"""
    print("[应用汇监控] 正在抓取应用汇网页端...")
    
    cn_ip = get_random_cn_ip()
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
        "X-Forwarded-For": cn_ip,  # 伪装国内源 IP
        "X-Real-IP": cn_ip,
    }

    for target_url in APPCHINA_URLS:
        try:
            resp = requests.get(target_url, headers=headers, verify=False, timeout=15)
            if resp.status_code != 200:
                continue

            html_text = resp.text
            soup = BeautifulSoup(html_text, "html.parser")
            # 用空格代替换行连接文本，避免“版本”和“9.3.75”被截断
            text_content = soup.get_text(separator=" ", strip=True)

            # 1. 提取版本号
            version = None
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
                # 代理请求时域名会变，必须硬编码源站 URL 补全
                download_url = urljoin("http://m.appchina.com/", download_url)

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

    print("[应用汇监控] 抓取失败 (大概率该海外IP已被彻底封锁，跳过本次检测)")
    return None
