# -*- coding: utf-8 -*-
import re
import requests
from bs4 import BeautifulSoup
from extractor import validate_date_str

TG_CHANNEL_URL = "https://t.me/s/qq_updatelog"

def clean_version_str(v_str: str) -> str:
    """归一化版本号：剥离 (16580) 等冗余构建号括号"""
    if not v_str:
        return ""
    return re.sub(r"\s*\(\d+\)", "", str(v_str)).strip()

def normalize_arch_from_link(a_txt: str, href: str, platform: str = "") -> str:
    """
    硬件架构精准判定：
    仅基于当前链接自身文本和 URL 解析，绝不引入整行外部文字，杜绝 LoongArch 串扰。
    """
    t_low = a_txt.lower()
    h_low = href.lower()

    # 1. Arm
    if any(k in t_low for k in ["arm64", "aarch64", "arm"]) or any(k in h_low for k in ["arm64", "aarch64", "_arm"]):
        return "Arm"

    # 2. X64 / amd64
    if any(k in t_low for k in ["x64", "amd64", "x86_64", "64位"]) or any(k in h_low for k in ["amd64", "x86_64", "_x64"]):
        return "X64"

    # 3. X86 / 32位
    if any(k in t_low for k in ["x86", "ia32", "i386", "32位"]) or any(k in h_low for k in ["i386", "_x86"]):
        return "X86"

    # 4. LoongArch
    if "loongarch" in t_low or "loong64" in t_low or "loongarch" in h_low or "loong64" in h_low:
        return "LoongArch"

    # 5. Mips
    if "mips" in t_low or "mips" in h_low:
        return "Mips"

    # 6. macOS
    if platform == "macOS" or any(k in t_low for k in ["dmg", "pkg"]):
        return "Universal"

    # 7. Linux 默认规则：若为 DEB, RPM, Appimage 且未标 arm/loongarch，全行业标准默认为主流 X64
    if platform == "Linux" and any(k in t_low for k in ["deb", "rpm", "appimage"]):
        return "X64"

    return "Package"

def get_recent_messages(limit: int = 5):
    """抓取 Telegram 频道最新发布的增量消息"""
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
    单条消息解析核心：
    1. 严格只抓取【下载】区的所有官方下载直链，彻底跳过【备份】区链接；
    2. 全架构、全包格式独立保留（不按架构覆盖），确保每个链接都能被独立下载解包分析 SubID。
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

    # 智能预判默认平台
    if ("#QQ_NT_Linux" in text) or (linux_ver and not (win_ver or mac_ver or android_ver)):
        current_platform = "Linux"
    elif ("#QQ_NT_MacOS" in text) or (mac_ver and not (win_ver or linux_ver or android_ver)):
        current_platform = "macOS"
    elif ("#QQ_NT_Phone" in text) or ("#QQ_Android" in text) or (android_ver and not (win_ver or mac_ver or linux_ver)):
        current_platform = "Android"
    else:
        current_platform = "Windows"

    tasks = []
    seen_urls = set()
    in_backup_section = False

    for line in re.split(r"<br\s*/?>|\n", html):
        line_soup = BeautifulSoup(line, "html.parser")
        line_text = line_soup.get_text().strip()
        if not line_text and not line_soup.find("a"):
            continue

        # 状态机：一旦遇到备份区块，切换状态
        if "备份" in line_text:
            in_backup_section = True
        elif "下载" in line_text or "官网" in line_text:
            in_backup_section = False

        # 动态感知区块中的平台切换
        if any(k in line_text for k in ["Windows", "- Windows"]): current_platform = "Windows"
        elif any(k in line_text for k in ["MacOS", "Mac", "- MacOS", "- Mac"]): current_platform = "macOS"
        elif any(k in line_text for k in ["Linux", "- Linux"]): current_platform = "Linux"
        elif any(k in line_text for k in ["Android", "Phone", "- Android"]): current_platform = "Android"

        line_a_tags = line_soup.find_all("a", href=True)
        if not line_a_tags:
            continue

        for a in line_a_tags:
            href = a["href"].strip()
            a_txt = a.get_text().strip()

            # ★ 核心原则：彻底排除 Telegram 自身页面及任何【备份】链接
            if "t.me/s/" in href or "t.me/qq_updatelog" in href:
                continue
            if in_backup_section or "t.me/linqiqi_backup" in href or "备份" in a_txt or "t.me/" in href:
                continue

            target_plat = current_platform
            if ".apk" in href.lower() or "android" in href.lower():
                target_plat = "Android"
            elif ".exe" in href.lower():
                target_plat = "Windows"
            elif ".dmg" in href.lower() or ".pkg" in href.lower():
                target_plat = "macOS"
            elif any(ext in href.lower() for ext in [".deb", ".rpm", ".appimage"]):
                target_plat = "Linux"

            ver = (
                win_ver if target_plat == "Windows"
                else mac_ver if target_plat == "macOS"
                else linux_ver if target_plat == "Linux"
                else android_ver
            )

            if not ver or ver == "Unknown":
                continue

            target_arch = normalize_arch_from_link(a_txt, href, target_plat)

            if href not in seen_urls:
                seen_urls.add(href)
                tasks.append({
                    "platform": target_plat,
                    "arch": target_arch,
                    "version": ver,
                    "date": date,
                    "url": href,
                    "official_url": href,
                    "is_beta": is_beta,
                    "channel": "官方",
                    "post_id": post_id,
                })

    # 兼容处理纯文本中裸露的官方直链 APK（若有）
    if ("Android", "Package") not in [(t["platform"], t["arch"]) for t in tasks] and android_ver:
        naked_urls = re.findall(r'https?://[a-zA-Z0-9_.-]*(?:qq\.com|myapp\.com)[^"\';\s<>]+\.apk[^"\';\s<>]*', text)
        for nu in naked_urls:
            nu_clean = nu.strip()
            if nu_clean not in seen_urls:
                seen_urls.add(nu_clean)
                tasks.append({
                    "platform": "Android",
                    "arch": "Package",
                    "version": android_ver,
                    "date": date,
                    "url": nu_clean,
                    "official_url": nu_clean,
                    "is_beta": is_beta,
                    "channel": "官方",
                    "post_id": post_id,
                })

    return tasks

def get_telegram_tasks(*args, **kwargs):
    """获取 Telegram 频道近期最新有效官方下载任务清单"""
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
