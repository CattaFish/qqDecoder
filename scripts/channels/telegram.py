# -*- coding: utf-8 -*-
import re
import requests
from bs4 import BeautifulSoup
from extractor import validate_date_str

TG_CHANNEL_URL = "https://t.me/s/qq_updatelog"

def clean_version_str(v_str: str) -> str:
    """归一化版本号：剥离 (16580) 等冗余构建号括号，确保与 AAPT 及数据库标准完全对齐"""
    if not v_str:
        return ""
    # 移除 (16580) 等括号内容
    v = re.sub(r"\s*\(\d+\)", "", str(v_str)).strip()
    return v

def get_latest_message():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(TG_CHANNEL_URL, headers=headers, timeout=30)
    if resp.status_code != 200:
        print(f"[TG 监控] 无法连接到 Telegram 频道: HTTP {resp.status_code}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    msg_divs = [
        div for div in soup.find_all("div", class_="tgme_widget_message")
        if div.get("data-post") and "/" in div.get("data-post")
    ]
    if not msg_divs: return None

    latest_msg = msg_divs[-1]
    time_elem = latest_msg.find("time")
    raw_date = time_elem["datetime"][:10] if time_elem else None
    date_str = validate_date_str(raw_date) or "Unknown"
    text_elem = latest_msg.find("div", class_="tgme_widget_message_text")

    return {
        "post_id": latest_msg.get("data-post"),
        "date": date_str,
        "html": str(text_elem) if text_elem else "",
        "text": text_elem.get_text() if text_elem else "",
    }

def get_telegram_tasks():
    """获取 Telegram 频道最新发布的架构下载任务 (日常监控模式：优先官方直链)"""
    msg = get_latest_message()
    if not msg:
        print("[TG 监控] 未获取到频道有效消息")
        return []

    print(f"[TG 监控] 检索到线上最新消息 ID: {msg['post_id']} | 日期: {msg['date']}")
    text, html, date = msg["text"], msg["html"], msg["date"]
    is_beta = bool(re.search(r"#Beta\b", text, re.IGNORECASE))

    win_ver, mac_ver, linux_ver, android_ver = "", "", "", ""
    m_win = re.search(r"Windows QQ_NT\s+([0-9]+(?:\.[0-9]+)+)", text)
    if m_win: win_ver = m_win.group(1)
    m_mac = re.search(r"Mac(?:OS)? QQ_NT\s+([0-9]+(?:\.[0-9]+)+)", text)
    if m_mac: mac_ver = m_mac.group(1)
    m_linux = re.search(r"Linux QQ_NT\s+([0-9]+(?:\.[0-9]+)+)", text)
    if m_linux: linux_ver = m_linux.group(1)
    m_android = re.search(r"QQ(?:_NT)?\s*[:：]?\s*([0-9]+(?:\.[0-9]+)+(?:\([0-9]+\))?)", text)
    if m_android: android_ver = m_android.group(1)

    if not (win_ver or mac_ver or linux_ver or android_ver):
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        if lines:
            m_top = re.search(r"^([0-9]+(?:\.[0-9]+)+(?:\s*\([0-9]+\))?)", lines[0])
            if m_top:
                extracted_v = m_top.group(1).replace(" ", "")
                if any(k in text for k in ["Windows QQ_NT", "Windows"]): win_ver = extracted_v
                elif any(k in text for k in ["Mac QQ_NT", "MacOS", "Mac"]): mac_ver = extracted_v
                elif any(k in text for k in ["Linux QQ_NT", "Linux"]): linux_ver = extracted_v
                else: android_ver = extracted_v

    # ★ 关键脱敏：清洗掉版本号中的括号噪音
    win_ver = clean_version_str(win_ver)
    mac_ver = clean_version_str(mac_ver)
    linux_ver = clean_version_str(linux_ver)
    android_ver = clean_version_str(android_ver)

    tasks = []
    has_desktop = any(p in text for p in ["Windows", "Linux", "MacOS", "Mac QQ_NT", "Linux QQ_NT", "Windows QQ_NT"])

    if has_desktop:
        current_platform = "Unknown"
        for line in re.split(r"<br\s*/?>|\n", html):
            line_soup = BeautifulSoup(line, "html.parser")
            line_text = line_soup.get_text()
            if "Windows" in line_text: current_platform = "Windows"
            elif "MacOS" in line_text or "Mac" in line_text: current_platform = "macOS"
            elif "Linux" in line_text: current_platform = "Linux"

            version = win_ver if current_platform == "Windows" else mac_ver if current_platform == "macOS" else linux_ver
            line_a_tags = line_soup.find_all("a", href=True)
            if not line_a_tags: continue

            backup_links, official_links = [], []
            for a in line_a_tags:
                href, a_text = a["href"].strip(), a.get_text().strip()
                if "t.me/linqiqi_backup" in href or "备份" in a_text:
                    backup_links.append((href, a_text))
                elif any(ext in href.lower() for ext in [".exe", ".dmg", ".deb", ".rpm", ".appimage"]) or "官网" in a_text:
                    official_links.append((href, a_text))

            chosen_links = official_links if official_links else backup_links
            for href, a_text in chosen_links:
                arch = a_text.replace("官网链接", "").replace("频道备份", "").replace("官网", "").replace("备份", "").strip()
                if not arch:
                    m_arch = re.search(r"\b(x64|x86|arm64|arm|loongarch|mips)\b", line_text, re.IGNORECASE)
                    arch = m_arch.group(1).upper() if m_arch else "Package"
                tasks.append({
                    "platform": current_platform, "arch": arch or "Package",
                    "version": version or "Unknown", "date": date, "url": href,
                    "is_beta": is_beta, "channel": "官方",
                })
    else:
        soup = BeautifulSoup(html, "html.parser")
        backup_links, official_links = [], []
        for a in soup.find_all("a", href=True):
            href, a_text = a["href"].strip(), a.get_text().strip()
            if "t.me/s/" in href or "t.me/qq_updatelog" in href: continue
            if "t.me/linqiqi_backup" in href or "备份" in a_text:
                backup_links.append(href)
            elif ".apk" in href.lower() or "android_apk" in href.lower() or "qq.com" in href.lower() or "官网" in a_text:
                official_links.append(href)

        target_urls = list(dict.fromkeys(official_links if official_links else backup_links))
        for u in target_urls:
            tasks.append({
                "platform": "Android", "arch": "Package",
                "version": android_ver or "Unknown", "date": date, "url": u,
                "is_beta": is_beta, "channel": "官方",
            })

    return tasks
