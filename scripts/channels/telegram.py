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
    return re.sub(r"\s*\(\d+\)", "", str(v_str)).strip()

def normalize_arch_name(raw_text: str) -> str:
    """统一规范化硬件架构名称，绝不允许出现 DEB_x64 等非规范架构"""
    low = raw_text.lower()
    if any(k in low for k in ["arm64", "aarch64"]): return "Arm"
    if any(k in low for k in ["armv7", "arm"]): return "Arm"
    if any(k in low for k in ["x64", "amd64", "x86_64", "64位", "64"]): return "X64"
    if any(k in low for k in ["x86", "ia32", "i386", "32位", "32"]): return "X86"
    if "loongarch" in low: return "LoongArch"
    if "mips" in low: return "Mips"
    return "Package"

def get_recent_messages(limit: int = 5):
    """抓取 Telegram 频道最新发布的增量消息（按时间正序排列）"""
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
    单条消息解析核心（行内备份强制优先）：
    在每一行中，只要检测到备份链接，坚决丢弃官方直链，100% 选用备份链接！
    """
    text = msg["text"]
    html = msg["html"]
    date = msg["date"]
    post_id = msg["post_id"]
    is_beta = bool(re.search(r"#Beta\b", text, re.IGNORECASE))

    # 1. 提炼各平台版本号
    win_ver, mac_ver, linux_ver, android_ver = "", "", "", ""
    m_win = re.search(r"Windows(?:\s+QQ(?:_NT)?)?\s+([0-9]+(?:\.[0-9]+)+)", text, re.IGNORECASE)
    if m_win: win_ver = m_win.group(1)

    m_mac = re.search(r"Mac(?:OS)?(?:\s+QQ(?:_NT)?)?\s+([0-9]+(?:\.[0-9]+)+)", text, re.IGNORECASE)
    if m_mac: mac_ver = m_mac.group(1)

    m_linux = re.search(r"Linux(?:\s+QQ(?:_NT)?)?\s+([0-9]+(?:\.[0-9]+)+)", text, re.IGNORECASE)
    if m_linux: linux_ver = m_linux.group(1)

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

    # 2. 行内原子聚合字典: { (platform, arch): { "url": 最终选定URL, "official_url": 官方URL } }
    row_tasks = {}
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

        backup_urls = []
        official_urls = []

        for a in line_a_tags:
            href = a["href"].strip()
            a_txt = a.get_text().strip()
            if "t.me/s/" in href or "t.me/qq_updatelog" in href:
                continue

            if "t.me/linqiqi_backup" in href or "备份" in a_txt:
                backup_urls.append(href)
            elif any(ext in href.lower() for ext in [".exe", ".dmg", ".pkg", ".deb", ".rpm", ".appimage", ".apk"]) or "官网" in a_txt or "downv6" in href or "qqdl" in href:
                official_urls.append(href)

        if not backup_urls and not official_urls:
            continue

        # ★ 核心改动：行内强制备份优先！有备份就绝对不用官方直链
        chosen_url = backup_urls[0] if backup_urls else official_urls[0]
        off_url = official_urls[0] if official_urls else chosen_url

        # 判断平台
        target_plat = current_platform
        if ".apk" in off_url.lower() or "android" in off_url.lower():
            target_plat = "Android"
        elif ".exe" in off_url.lower():
            target_plat = "Windows"
        elif ".dmg" in off_url.lower() or ".pkg" in off_url.lower():
            target_plat = "macOS"
        elif any(ext in off_url.lower() for ext in [".deb", ".rpm", ".appimage"]):
            target_plat = "Linux"

        # 判断架构
        if target_plat == "Android":
            target_arch = "Package"
        elif target_plat == "macOS":
            target_arch = "Universal"
        else:
            target_arch = normalize_arch_name(line_text + " " + off_url)

        key = (target_plat, target_arch)
        # 若已有记录且当前是备份链接，则覆盖升级
        if key not in row_tasks or ("t.me/linqiqi_backup" in chosen_url and "t.me/linqiqi_backup" not in row_tasks[key]["url"]):
            row_tasks[key] = {"url": chosen_url, "official_url": off_url}

    # 3. 兼容处理纯文本中直接给出的裸链接（如 731 中的 Android 官方包）
    naked_urls = re.findall(r'https?://[a-zA-Z0-9_.-]*(?:qq\.com|myapp\.com)[^"\';\s<>]+\.apk[^"\';\s<>]*', text)
    naked_urls += re.findall(r'https?://t.me/linqiqi_backup/\d+', text)
    if naked_urls:
        b_cands = [u for u in naked_urls if "t.me/linqiqi_backup" in u]
        o_cands = [u for u in naked_urls if "t.me/linqiqi_backup" not in u]
        chosen_apk = b_cands[0] if b_cands else o_cands[0]
        off_apk = o_cands[0] if o_cands else chosen_apk

        # 仅在尚未解析出 Android 时补全
        if ("Android", "Package") not in row_tasks:
            row_tasks[("Android", "Package")] = {"url": chosen_apk, "official_url": off_apk}

    # 4. 生成规范任务
    tasks = []
    for (plat, arch), data in row_tasks.items():
        ver = (
            win_ver if plat == "Windows"
            else mac_ver if plat == "macOS"
            else linux_ver if plat == "Linux"
            else android_ver
        ) or "Unknown"

        tasks.append({
            "platform": plat,
            "arch": arch,
            "version": ver,
            "date": date,
            "url": data["url"],
            "official_url": data["official_url"],
            "is_beta": is_beta,
            "channel": "官方",
            "post_id": post_id,
        })

    return tasks

def get_telegram_tasks(*args, **kwargs):
    """获取 Telegram 频道近期最新有效下载任务"""
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
