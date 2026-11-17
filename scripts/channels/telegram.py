# -*- coding: utf-8 -*-
import re
import requests
from bs4 import BeautifulSoup
from extractor import validate_date_str

TG_CHANNEL_URL = "https://t.me/s/qq_updatelog"

VALID_PACKAGE_EXTENSIONS = (".apk", ".exe", ".dmg", ".pkg", ".deb", ".rpm", ".appimage", ".ipa")
VALID_PACKAGE_DOMAINS = ("qq.com", "myapp.com", "gtimg.cn")

# 支持 9.3.85 / 9.3.85.43095 / 9.3.85(43095) / 9.3.85 (43095) / 3.2.25-43095 / 3.2.25_43095
VER_TOKEN = r"([0-9]+(?:\.[0-9]+)+(?:\s*\([0-9]+\)|[-_][0-9]{4,6})?)"


def clean_version_str(v_str: str) -> str:
    if not v_str:
        return ""
    s = str(v_str).strip()
    # 将 "9.3.85 (43095)" / "9.3.85(43095)" / "9.3.85-43095" / "9.3.85_43095" 统一归一化为 "9.3.85.43095"
    m4 = re.match(r"^(\d+\.\d+\.\d+)\s*[-._(]\s*(\d{4,6})\)?$", s)
    if m4:
        return f"{m4.group(1)}.{m4.group(2)}"
    # 去掉其余非构建号括号注释
    s = re.sub(r"\s*\([^)]*\)", "", s).strip()
    return s


def extract_4part_ver_from_url(url: str, base_ver: str) -> str:
    """当正文只有 3 段版本号时，尝试从直链文件名中补全点号或短横线连接的 4~6 位构建号"""
    if not url or not base_ver:
        return base_ver
    base_parts = re.findall(r"\d+", base_ver)
    if len(base_parts) != 3:
        return base_ver

    fname = url.split("?")[0].split("/")[-1]
    # 仅匹配 "." 或 "-" 连接的构建号，避免误把官网文件名里的 _260401_ 日期当成构建号
    m4 = re.search(r"(\d+\.\d+\.\d+)[.-](\d{4,6})", fname)
    if m4:
        u_parts = m4.group(1).split(".")
        if u_parts == base_parts:
            return f"{m4.group(1)}.{m4.group(2)}"
    return base_ver


def normalize_arch_from_link(a_txt: str, href: str, platform: str = "") -> str:
    t_low = a_txt.lower()
    h_low = href.lower()

    if any(k in t_low for k in ["arm64", "aarch64", "arm"]) or any(k in h_low for k in ["arm64", "aarch64", "_arm"]):
        return "Arm"
    if any(k in t_low for k in ["x64", "amd64", "x86_64", "64位"]) or any(k in h_low for k in ["amd64", "x86_64", "_x64"]):
        return "X64"
    if any(k in t_low for k in ["x86", "ia32", "i386", "32位"]) or any(k in h_low for k in ["i386", "_x86"]):
        return "X86"
    if "loongarch" in t_low or "loong64" in t_low or "loongarch" in h_low or "loong64" in h_low:
        return "LoongArch"
    if "mips" in t_low or "mips" in h_low:
        return "Mips"
    if platform == "macOS" or any(k in t_low for k in ["dmg", "pkg"]):
        return "Universal"
    if platform == "Linux" and any(k in t_low for k in ["deb", "rpm", "appimage"]):
        return "X64"

    return "Package"


def get_recent_messages(limit: int = 5):
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


def is_telegram_beta_message(text: str, html: str) -> bool:
    t_clean = text.strip()
    h_low = html.lower()

    # 1. 纯文本标签（支持全角 ＃ 与半角 #）
    if re.search(r"[#＃](?:Beta|Little_Beta)\b", t_clean, re.IGNORECASE):
        return True

    # 2. Telegram Web Tag 链接实体
    if "%23beta" in h_low or "%23little_beta" in h_low:
        return True

    # 3. 首行标题判定（如 "QQ_NT 9.3.85.43095 #Beta"）
    first_line = t_clean.splitlines()[0] if t_clean.splitlines() else ""
    if re.search(r"\bbeta\b", first_line, re.IGNORECASE):
        return True

    # 4. 灰度与内测标签
    if re.search(r"[#＃](?:体验版|内测|灰度)\b", t_clean):
        return True

    return False


def parse_single_message_tasks(msg: dict):
    text = msg["text"]
    html = msg["html"]
    date = msg["date"]
    post_id = msg["post_id"]

    is_beta = is_telegram_beta_message(text, html)

    win_ver, mac_ver, linux_ver, android_ver = "", "", "", ""
    m_win = re.search(rf"Windows(?:\s+QQ(?:_NT)?)?\s*[:：]?\s*{VER_TOKEN}", text, re.IGNORECASE)
    if m_win:
        win_ver = m_win.group(1)

    m_mac = re.search(rf"Mac(?:OS)?(?:\s+QQ(?:_NT)?)?\s*[:：]?\s*{VER_TOKEN}", text, re.IGNORECASE)
    if m_mac:
        mac_ver = m_mac.group(1)

    m_linux = re.search(rf"Linux(?:\s+QQ(?:_NT)?)?\s*[:：]?\s*{VER_TOKEN}", text, re.IGNORECASE)
    if m_linux:
        linux_ver = m_linux.group(1)

    # 使用 finditer 避免多平台混排消息在第一处桌面端处提前终止匹配
    for m_a in re.finditer(
        rf"QQ(?:_NT)?(?:\s*(?:Android|Phone|Mobile|安卓|手机版?))?\s*[:：]?\s*{VER_TOKEN}",
        text,
        re.IGNORECASE,
    ):
        pos = m_a.start()
        prefix = text[max(0, pos - 20):pos].lower()
        if not any(k in prefix for k in ["windows", "mac", "linux"]):
            android_ver = m_a.group(1)
            break

    if not android_ver:
        m_andr_only = re.search(rf"(?:Android|安卓)\s*[:：]?\s*{VER_TOKEN}", text, re.IGNORECASE)
        if m_andr_only:
            android_ver = m_andr_only.group(1)

    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if lines:
        m_top = re.search(rf"^{VER_TOKEN}", lines[0])
        if m_top:
            extracted_v = m_top.group(1)
            if re.search(r"(?<![a-zA-Z])Windows(?![a-zA-Z])", text, re.IGNORECASE) and not win_ver:
                win_ver = extracted_v
            elif re.search(r"(?<![a-zA-Z])(?:MacOS|Mac)(?![a-zA-Z])", text, re.IGNORECASE) and not mac_ver:
                mac_ver = extracted_v
            elif re.search(r"(?<![a-zA-Z])Linux(?![a-zA-Z])", text, re.IGNORECASE) and not linux_ver:
                linux_ver = extracted_v
            elif not android_ver:
                android_ver = extracted_v

    win_ver = clean_version_str(win_ver)
    mac_ver = clean_version_str(mac_ver)
    linux_ver = clean_version_str(linux_ver)
    android_ver = clean_version_str(android_ver)

    if ("#QQ_NT_Linux" in text) or (linux_ver and not (win_ver or mac_ver or android_ver)):
        current_platform = "Linux"
    elif ("#QQ_NT_MacOS" in text) or (mac_ver and not (win_ver or linux_ver or android_ver)):
        current_platform = "macOS"
    elif ("#QQ_NT_Phone" in text) or ("#QQ_Android" in text) or (android_ver and not (win_ver or mac_ver or linux_ver)):
        current_platform = "Android"
    else:
        current_platform = "Windows"

    tasks_by_arch = {}
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

        if re.search(r"(?<![a-zA-Z])Windows(?![a-zA-Z])", line_text, re.IGNORECASE):
            current_platform = "Windows"
        elif re.search(r"(?<![a-zA-Z])(?:MacOS|Mac)(?![a-zA-Z])", line_text, re.IGNORECASE):
            current_platform = "macOS"
        elif re.search(r"(?<![a-zA-Z])Linux(?![a-zA-Z])", line_text, re.IGNORECASE):
            current_platform = "Linux"
        elif re.search(r"(?<![a-zA-Z])(?:Android|Phone)(?![a-zA-Z])", line_text, re.IGNORECASE):
            current_platform = "Android"

        line_a_tags = line_soup.find_all("a", href=True)
        if not line_a_tags:
            continue

        for a in line_a_tags:
            href = a["href"].strip()
            a_txt = a.get_text().strip()

            if not href.startswith(("http://", "https://")):
                continue
            if "t.me/" in href or "telegram.me/" in href or in_backup_section or "备份" in a_txt:
                continue

            href_clean = href.split("?")[0].lower()
            is_valid_ext = any(href_clean.endswith(ext) for ext in VALID_PACKAGE_EXTENSIONS)
            is_valid_domain = any(domain in href.lower() for domain in VALID_PACKAGE_DOMAINS)
            if not (is_valid_ext or is_valid_domain):
                continue

            target_plat = current_platform
            if ".apk" in href_clean or "android" in href.lower():
                target_plat = "Android"
            elif ".exe" in href_clean:
                target_plat = "Windows"
            elif ".dmg" in href_clean or ".pkg" in href_clean:
                target_plat = "macOS"
            elif any(href_clean.endswith(ext) for ext in [".deb", ".rpm", ".appimage"]):
                target_plat = "Linux"

            ver = (
                win_ver if target_plat == "Windows"
                else mac_ver if target_plat == "macOS"
                else linux_ver if target_plat == "Linux"
                else android_ver
            )

            if not ver or ver == "Unknown":
                continue

            ver = extract_4part_ver_from_url(href, ver)
            target_arch = normalize_arch_from_link(a_txt, href, target_plat)

            arch_key = (target_plat, target_arch)
            is_deb = ".deb" in href_clean or "deb" in a_txt.lower()

            task_obj = {
                "platform": target_plat,
                "arch": target_arch,
                "version": ver,
                "date": date,
                "url": href,
                "official_url": href,
                "is_beta": is_beta,
                "channel": "官方",
                "post_id": post_id,
            }

            if arch_key not in tasks_by_arch:
                tasks_by_arch[arch_key] = task_obj
            elif is_deb:
                tasks_by_arch[arch_key] = task_obj

    if ("Android", "Package") not in tasks_by_arch and android_ver:
        naked_urls = re.findall(r'https?://[a-zA-Z0-9_.-]*(?:qq\.com|myapp\.com)[^"\';\s<>]+\.apk[^"\';\s<>]*', text)
        for nu in naked_urls:
            nu_clean = nu.strip()
            ver_final = extract_4part_ver_from_url(nu_clean, android_ver)
            tasks_by_arch[("Android", "Package")] = {
                "platform": "Android",
                "arch": "Package",
                "version": ver_final,
                "date": date,
                "url": nu_clean,
                "official_url": nu_clean,
                "is_beta": is_beta,
                "channel": "官方",
                "post_id": post_id,
            }
            break

    return list(tasks_by_arch.values())


def get_telegram_tasks(*args, **kwargs):
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
