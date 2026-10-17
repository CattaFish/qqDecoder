# -*- coding: utf-8 -*-
import re
import time
import requests
from bs4 import BeautifulSoup
from extractor import validate_date_str

YYB_URL = "https://a.app.qq.com/o/simple.jsp?pkgname=com.tencent.mobileqq"

def get_yingyongbao_info():
    """获取腾讯应用宝页面的版本信息、更新日期及“通过第三方浏览器下载”直链"""
    print("[应用宝监控] 正在检索微下载页面...")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        resp = requests.get(YYB_URL, headers=headers, timeout=20)
        if resp.status_code != 200:
            print(f"[应用宝监控] 请求网页失败: HTTP {resp.status_code}")
            return None

        html_text = resp.text
        soup = BeautifulSoup(html_text, "html.parser")

        version = None
        m_ver = re.search(r"版本[：:\s]*\s*([0-9]+(?:\.[0-9]+)+)", html_text)
        if m_ver: version = m_ver.group(1).strip()

        download_url = None
        for tag in soup.find_all(["a", "div", "button", "span"]):
            t_text = tag.get_text().strip()
            if "通过第三方浏览器下载" in t_text or "第三方浏览器" in t_text:
                url_cand = tag.get("href") or tag.get("data-apkurl") or tag.get("data-url") or tag.get("data-download")
                if url_cand and ("http://" in url_cand or "https://" in url_cand or url_cand.startswith("//")):
                    download_url = url_cand
                    break

        if not download_url:
            m_apk = re.search(r'["\']?(?:apkUrl|downUrl|downloadUrl|apk_url)["\']?\s*[:=]\s*["\'](https?://[^"\']+\.apk[^"\']*)["\']', html_text)
            if m_apk: download_url = m_apk.group(1).strip()

        if not download_url:
            m_any_apk = re.search(r'https?://[a-zA-Z0-9_.-]+(?:myapp\.com|qq\.com)[^"\';\s<>]+\.apk[^"\';\s<>]*', html_text)
            if m_any_apk: download_url = m_any_apk.group(0).strip()

        if download_url and download_url.startswith("//"):
            download_url = "https:" + download_url

        date_str = None
        m_ctx = re.search(r"(?:更新时间|发布时间|更新日期|时间)[：:\s]*\s*([12]\d{3}[-/年.](?:0?[1-9]|1[0-2])[-/月.](?:0?[1-9]|[12]\d|3[01]))", html_text)
        if m_ctx: date_str = validate_date_str(m_ctx.group(1))

        if not date_str and download_url:
            try:
                head_resp = requests.head(download_url, headers=headers, timeout=5, allow_redirects=True)
                last_mod = head_resp.headers.get("Last-Modified")
                if last_mod:
                    gmt_time = time.strptime(last_mod, "%a, %d %b %Y %H:%M:%S GMT")
                    date_str = validate_date_str(time.strftime("%Y-%m-%d", gmt_time))
            except Exception: pass

        if not date_str: date_str = time.strftime("%Y-%m-%d")

        if not version or not download_url:
            print(f"[应用宝监控] 解析异常: 版本={version}, 下载URL={download_url}")
            return None

        return {
            "version": version,
            "date": date_str,
            "download_url": download_url,
            "channel": "应用宝",
        }
    except Exception as e:
        print(f"[应用宝监控] 抓取失败: {e}")
        return None
