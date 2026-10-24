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
    v = re.sub(r"\s*\(\d+\)", "", str(v_str)).strip()
    return v

def get_recent_messages(limit: int = 5):
    """
    抓取 Telegram 频道最新发布的增量消息（按时间/ID 正序排列）
    默认仅看最新 5 条，既能覆盖多端连续发布，又彻底杜绝翻出数月前的历史包
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(TG_CHANNEL_URL, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"[TG 监控] 无法连接到 Telegram 频道: HTTP {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        msg_divs = [
            div for div in soup.find_all("div", class_="tgme_widget_message")
            if div.get("data-post") and "/" in div.get("data-post")
        ]
        if not msg_divs:
            return []

        selected_divs = msg_divs[-limit:] if len(msg_divs) > limit else msg_divs
        messages = []
        for m in selected_divs:
            time_elem = m.find("time")
            raw_date = time_elem["datetime"][:10] if time_elem else None
            date_str = validate_date_str(raw_date) or "Unknown"
            text_elem = m.find("div", class_="tgme_widget_message_text")
            if not text_elem:
                continue

            messages.append({
                "post_id": m.get("data-post"),
                "date": date_str,
                "html": str(text_elem),
                "text": text_elem.get_text(),
            })
        return messages
    except Exception as e:
        print(f"[TG 监控] 抓取频道消息异常: {e}")
        return []

def parse_single_message_tasks(msg: dict):
    """
    单条消息解析核心（全端自适应兼顾）：
    1. 精准区分各平台独立版本号；
    2. 按 (platform, arch) 链接聚合；
    3. 严格备份优先（Backup-First），绝不重复下载；
    4. 消除 Desktop 与 Android 互斥遗漏隐患。
    """
    text = msg["text"]
    html = msg["html"]
    date = msg["date"]
    post_id = msg["post_id"]
    is_beta = bool(re.search(r"#Beta\b", text, re.IGNORECASE))

    win_ver, mac_ver, linux_ver, android_ver = "", "", "", ""

    m_win = re.search(r"Windows(?:\s+QQ(?:_NT)?)?\s+([0-9]+(?:\.[0-9]+)+)", text, re.IGNORECASE)
    if m_win: win_ver = m_win.group(1)

    m_mac = re.search(r"Mac(?:OS)?(?:\s+QQ(?:_NT)?)?\s+([0-9]+(?:\.[0-9]+)+)", text, re.IGNORECASE)
    if m_mac: mac_ver = m_mac.group(1)

    m_linux = re.search(r"Linux(?:\s+QQ(?:_NT)?)?\s+([0-9]+(?:\.[0-9]+)+)", text, re.IGNORECASE)
    if m_linux: linux_ver = m_linux.group(1)

    # Android 版本提取（精确匹配纯 Android 格式，如 731）
    m_android = re.search(r"QQ(?:_NT)?\s*[:：]?\s*([0-9]+(?:\.[0-9]+)+(?:\([0-9]+\))?)", text, re.IGNORECASE)
    if m_android:
        matched_str = m_android.group(0)
        pos = text.find(matched_str)
        prefix = text[max(0, pos - 15):pos].lower()
        if not any(k in prefix for k in ["windows", "mac", "linux"]):
            android_ver = m_android.group(1)

    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if lines:
        m_top = re.search(r"^([0-9]+(?:\.[0-9]+)+(?:\s*\([0-9]+\))?)", lines[0])
        if m_top:
            extracted_v = m_top.group(1).replace(" ", "")
            if any(k in text for k in ["Windows QQ_NT", "Windows"]) and not win_ver: win_ver = extracted_v
            elif any(k in text for k in ["Mac QQ_NT", "MacOS", "Mac"]) and not mac_ver: mac_ver = extracted_v
            elif any(k in text for k in ["Linux QQ_NT", "Linux"]) and not linux_ver: linux_ver = extracted_v
            elif not android_ver: android_ver = extracted_v

    win_ver = clean_version_str(win_ver)
    mac_ver = clean_version_str(mac_ver)
    linux_ver = clean_version_str(linux_ver)
    android_ver = clean_version_str(android_ver)

    cand_map = {}

    # 1. 解析桌面端链接（按段落/行追踪所属平台和架构）
    current_platform = "Windows"
    for line in re.split(r"<br\s*/?>|\n", html):
        line_soup = BeautifulSoup(line, "html.parser")
        line_text = line_soup.get_text()

        if "Windows" in line_text: current_platform = "Windows"
        elif "MacOS" in line_text or "Mac" in line_text: current_platform = "macOS"
        elif "Linux" in line_text: current_platform = "Linux"
        elif "Android" in line_text or "Phone" in line_text: current_platform = "Android"

        line_a_tags = line_soup.find_all("a", href=True)
        if not line_a_tags:
            continue

        for a in line_a_tags:
            href = a["href"].strip()
            a_txt = a.get_text().strip()
            if "t.me/s/" in href or "t.me/qq_updatelog" in href:
                continue

            # 若明确为 APK 链接，直接归属为 Android
            if ".apk" in href.lower() or "android_apk" in href.lower():
                is_backup = ("t.me/linqiqi_backup" in href) or ("备份" in a_txt)
                cand_map.setdefault(("Android", "Package"), []).append({
                    "url": href, "is_backup": is_backup, "text": a_txt
                })
                continue

            # 桌面端架构推断
            arch = a_txt.replace("官网链接", "").replace("频道备份", "").replace("官网", "").replace("备份", "").strip()
            if not arch:
                m_arch = re.search(r"\b(x64|x86|arm64|arm|loongarch|mips)\b", line_text + " " + href, re.IGNORECASE)
                arch = m_arch.group(1).upper() if m_arch else "Package"

            if arch.lower() in ("arm64", "arm"): arch = "Arm"
            elif arch.lower() == "x64": arch = "X64"
            elif arch.lower() == "x86": arch = "X86"

            is_backup = ("t.me/linqiqi_backup" in href) or ("备份" in a_txt)
            target_plat = current_platform
            if ".exe" in href.lower(): target_plat = "Windows"
            elif ".dmg" in href.lower() or ".pkg" in href.lower(): target_plat = "macOS"
            elif any(ext in href.lower() for ext in [".deb", ".rpm", ".appimage"]): target_plat = "Linux"

            cand_map.setdefault((target_plat, arch), []).append({
                "url": href, "is_backup": is_backup, "text": a_txt
            })

    # 2. 解析正文中可能出现的裸链接（如 731 中纯文本 APK 链接）
    naked_urls = re.findall(r'https?://[a-zA-Z0-9_.-]*(?:qq\.com|myapp\.com)[^"\';\s<>]+\.apk[^"\';\s<>]*', text)
    naked_urls += re.findall(r'https?://t.me/linqiqi_backup/\d+', text)
    for u in naked_urls:
        if "t.me/s/" in u or "t.me/qq_updatelog" in u:
            continue
        is_backup = "t.me/linqiqi_backup" in u
        # 若是 APK 链接，补充进 Android 候选池
        if ".apk" in u.lower() or "android_apk" in u.lower() or (not cand_map and is_backup):
            cand_map.setdefault(("Android", "Package"), []).append({
                "url": u.strip(), "is_backup": is_backup, "text": "下载"
            })

    # 3. 严格备份优先（Backup-First）选取
    final_tasks = []
    for (plat, arch), candidates in cand_map.items():
        ver = (
            win_ver if plat == "Windows"
            else mac_ver if plat == "macOS"
            else linux_ver if plat == "Linux"
            else android_ver
        ) or "Unknown"

        backup_cands = [c for c in candidates if c["is_backup"]]
        official_cands = [c for c in candidates if not c["is_backup"]]

        chosen = backup_cands[0] if backup_cands else (official_cands[0] if official_cands else None)
        if not chosen:
            continue

        official_url = official_cands[0]["url"] if official_cands else chosen["url"]

        final_tasks.append({
            "platform": plat,
            "arch": arch,
            "version": ver,
            "date": date,
            "url": chosen["url"],
            "official_url": official_url,
            "is_beta": is_beta,
            "channel": "官方",
            "post_id": post_id,
        })

    return final_tasks

def get_telegram_tasks(*args, **kwargs):
    """获取 Telegram 频道近期最新有效下载任务（支持自适应参数，避免 TypeError）"""
    limit = kwargs.get("limit", 5)
    messages = get_recent_messages(limit=limit)
    if not messages:
        print("[TG 监控] 近期无有效消息。")
        return []

    print(f"[TG 监控] 检索到线上最新增量消息 {len(messages)} 条 (范围: {messages[0]['post_id']} -> {messages[-1]['post_id']})")
    all_tasks = []
    for msg in messages:
        tasks = parse_single_message_tasks(msg)
        all_tasks.extend(tasks)

    return all_tasks
