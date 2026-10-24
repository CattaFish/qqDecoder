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
    """统一规范化硬件架构名称"""
    low = raw_text.lower()
    if any(k in low for k in ["arm64", "aarch64"]): return "Arm"
    if any(k in low for k in ["armv7", "arm"]): return "Arm"
    if any(k in low for k in ["x64", "amd64", "x86_64", "64位", "64"]): return "X64"
    if any(k in low for k in ["x86", "ia32", "i386", "32位", "32"]): return "X86"
    if "loongarch" in low: return "LoongArch"
    if "mips" in low: return "Mips"
    if any(k in low for k in ["dmg", "pkg"]): return "Universal"
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
    单条消息解析核心（彻底消除 Windows Package Unknown 幽灵任务）：
    1. 消息级平台预判（根据标签与版本号精准确定默认平台）；
    2. 绝对备份优先（有备份绝不下官方）；
    3. 严格过滤拦截任何版本为 Unknown 的伪任务。
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

    # 2. 检查本消息是否含有任何备份链接
    has_backup = ("t.me/linqiqi_backup" in html) or ("t.me/linqiqi_backup" in text)

    # 提取官方备用 APK（仅供下游反编译工作流应急备用）
    official_apk_url = None
    all_apks = re.findall(r'href="(https?://[a-zA-Z0-9_.-]*(?:qq\.com|myapp\.com)[^"]+\.apk[^"]*)"', html)
    if not all_apks:
        all_apks = re.findall(r'https?://[a-zA-Z0-9_.-]*(?:qq\.com|myapp\.com)[^"\';\s<>]+\.apk[^"\';\s<>]*', text)
    if all_apks:
        official_apk_url = all_apks[0]

    # ★ 核心改进 1：智能决定本消息的初始平台，绝不无脑写死为 Windows！
    is_pure_android = ("#QQ_NT_Phone" in text) or ("#QQ_Android" in text) or (android_ver and not (win_ver or mac_ver or linux_ver))
    is_pure_linux = ("#QQ_NT_Linux" in text) or (linux_ver and not (win_ver or mac_ver or android_ver))
    is_pure_mac = ("#QQ_NT_MacOS" in text) or (mac_ver and not (win_ver or linux_ver or android_ver))

    if is_pure_android:
        current_platform = "Android"
    elif is_pure_linux:
        current_platform = "Linux"
    elif is_pure_mac:
        current_platform = "macOS"
    else:
        current_platform = "Windows"

    tasks_map = {}
    in_backup_section = False

    for line in re.split(r"<br\s*/?>|\n", html):
        line_soup = BeautifulSoup(line, "html.parser")
        line_text = line_soup.get_text().strip()
        if not line_text and not line_soup.find("a"):
            continue

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
            if "t.me/s/" in href or "t.me/qq_updatelog" in href:
                continue

            is_this_backup = "t.me/linqiqi_backup" in href or "备份" in a_txt

            if has_backup and not is_this_backup:
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

            if target_plat == "Android":
                target_arch = "Package"
            elif target_plat == "macOS":
                target_arch = "Universal"
            else:
                target_arch = normalize_arch_name(a_txt)
                if target_arch == "Package":
                    target_arch = normalize_arch_name(line_text + " " + href)

            ver = (
                win_ver if target_plat == "Windows"
                else mac_ver if target_plat == "macOS"
                else linux_ver if target_plat == "Linux"
                else android_ver
            )

            # ★ 核心改进 2：坚决过滤 Unknown！如果当前平台根本没有提取出版本号，直接忽略该链接，绝不生成伪任务！
            if not ver or ver == "Unknown":
                continue

            key = (target_plat, target_arch)
            if key not in tasks_map or (is_this_backup and "t.me/linqiqi_backup" not in tasks_map[key]["url"]):
                tasks_map[key] = {
                    "platform": target_plat,
                    "arch": target_arch,
                    "version": ver,
                    "date": date,
                    "url": href,
                    "official_url": official_apk_url or href,
                    "is_beta": is_beta,
                    "channel": "官方",
                    "post_id": post_id,
                }

    # 3. 兼容处理纯文本中裸露的 APK 链接（如消息 731）
    if not has_backup and ("Android", "Package") not in tasks_map and android_ver:
        naked_urls = re.findall(r'https?://[a-zA-Z0-9_.-]*(?:qq\.com|myapp\.com)[^"\';\s<>]+\.apk[^"\';\s<>]*', text)
        if naked_urls:
            tasks_map[("Android", "Package")] = {
                "platform": "Android",
                "arch": "Package",
                "version": android_ver,
                "date": date,
                "url": naked_urls[0].strip(),
                "official_url": naked_urls[0].strip(),
                "is_beta": is_beta,
                "channel": "官方",
                "post_id": post_id,
            }

    return list(tasks_map.values())

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
