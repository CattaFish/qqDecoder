import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from subid_compiler import compile_subid_map_to_runtime_dict
import json
import os
import re
import shutil
import subprocess
import time
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import requests

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

TG_CHANNEL_URL = "https://t.me/s/qq_updatelog"
WANDOUJIA_URL = "https://wap.pp.cn/app_zYoOzyuL/"

DATA_FILE = Path("data/qq_subid_map.json")
BAK_FILE = Path("data/qq_subid_map.json.bak")
WDJ_VER_FILE = Path("data/wandoujia_latest_version.txt")

TEMP_DL = Path("temp_dl")
TEMP_EXTRACT = Path("temp_extracted")
SINGLE_DOWNLOAD_TIMEOUT = 600

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    )
}


# =====================================================================
# 1. 豌豆荚 (PP助手) 监控与解析模块
# =====================================================================
def get_wandoujia_info():
    """获取豌豆荚页面的版本信息、更新日期及普通下载直链"""
    try:
        resp = requests.get(WANDOUJIA_URL, headers=DEFAULT_HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"[豌豆荚] 请求网页失败: HTTP {resp.status_code}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        version = None
        date_str = time.strftime("%Y-%m-%d")

        text_content = soup.get_text()
        m_ver = re.search(r"版本信息[：:\s]*\n*([0-9]+(?:\.[0-9]+)+)", text_content)
        if m_ver:
            version = m_ver.group(1).strip()

        m_date = re.search(r"更新时间[：:\s]*\n*([0-9]{4}[-/][0-9]{2}[-/][0-9]{2})", text_content)
        if m_date:
            date_str = m_date.group(1).replace("/", "-").strip()

        download_url = None
        for a in soup.find_all("a"):
            a_text = a.get_text().strip()
            if "普通下载" in a_text:
                download_url = a.get("href") or a.get("data-download") or a.get("data-url")
                break

        if not download_url:
            for a in soup.find_all("a", href=True):
                if "普通下载" in a.get_text():
                    download_url = a["href"]
                    break

        if not version or not download_url:
            print(f"[豌豆荚] 解析异常: 版本={version}, 下载URL={download_url}")
            return None

        download_url = urljoin(WANDOUJIA_URL, download_url.strip())

        return {
            "version": version,
            "date": date_str,
            "download_url": download_url,
            "channel": "豌豆荚",
        }
    except Exception as e:
        print(f"[豌豆荚] 抓取失败: {e}")
        return None


# =====================================================================
# 2. Telegram 频道解析模块 (全面兼容 Markdown 超链接与新格式)
# =====================================================================
def get_latest_message():
    """获取频道的最新一条有效更新消息"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
    }
    resp = requests.get(TG_CHANNEL_URL, headers=headers, timeout=30)
    if resp.status_code != 200:
        print(f"[错误] 无法连接到 Telegram 频道: {resp.status_code}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    msg_divs = [
        div
        for div in soup.find_all("div", class_="tgme_widget_message")
        if div.get("data-post") and "/" in div.get("data-post")
    ]
    if not msg_divs:
        print("[错误] 未找到任何消息节点")
        return None

    latest_msg = msg_divs[-1]
    time_elem = latest_msg.find("time")
    date_str = time_elem["datetime"][:10] if time_elem else "Unknown"
    text_elem = latest_msg.find("div", class_="tgme_widget_message_text")

    return {
        "post_id": latest_msg.get("data-post"),
        "date": date_str,
        "html": str(text_elem) if text_elem else "",
        "text": text_elem.get_text() if text_elem else "",
    }


def parse_tasks_from_message(msg):
    """解析该消息包含的所有架构直链，兼容 Markdown 超链接与新版排版"""
    text = msg["text"]
    html = msg["html"]
    date = msg["date"]

    is_beta = bool(re.search(r"#Beta\b", text, re.IGNORECASE))

    win_ver = ""
    mac_ver = ""
    linux_ver = ""
    android_ver = ""

    # 1. 尝试常规带前缀的版本号提取
    m_win = re.search(r"Windows QQ_NT\s+([0-9]+(?:\.[0-9]+)+)", text)
    if m_win:
        win_ver = m_win.group(1)
    m_mac = re.search(r"Mac(?:OS)? QQ_NT\s+([0-9]+(?:\.[0-9]+)+)", text)
    if m_mac:
        mac_ver = m_mac.group(1)
    m_linux = re.search(r"Linux QQ_NT\s+([0-9]+(?:\.[0-9]+)+)", text)
    if m_linux:
        linux_ver = m_linux.group(1)
    m_android = re.search(
        r"QQ(?:_NT)?\s*[:：]?\s*([0-9]+(?:\.[0-9]+)+(?:\([0-9]+\))?)", text
    )
    if m_android:
        android_ver = m_android.group(1)

    # 2. 兼容新格式：首行无前缀直接是版本号（例如 "9.3.75.42350 (16580)"）
    if not (win_ver or mac_ver or linux_ver or android_ver):
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        if lines:
            m_top = re.search(r"^([0-9]+(?:\.[0-9]+)+(?:\s*\([0-9]+\))?)", lines[0])
            if m_top:
                extracted_v = m_top.group(1).replace(" ", "")
                if any(k in text for k in ["Windows QQ_NT", "Windows"]):
                    win_ver = extracted_v
                elif any(k in text for k in ["Mac QQ_NT", "MacOS", "Mac"]):
                    mac_ver = extracted_v
                elif any(k in text for k in ["Linux QQ_NT", "Linux"]):
                    linux_ver = extracted_v
                else:
                    android_ver = extracted_v

    tasks = []

    # 3. 区分桌面端与移动端进行超链接智能解析
    has_desktop_platform = any(
        p in text for p in ["Windows", "Linux", "MacOS", "Mac QQ_NT", "Linux QQ_NT", "Windows QQ_NT"]
    )

    if has_desktop_platform:
        current_platform = "Unknown"
        lines = re.split(r"<br\s*/?>|\n", html)
        for line in lines:
            line_soup = BeautifulSoup(line, "html.parser")
            line_text = line_soup.get_text()
            if "Windows" in line_text:
                current_platform = "Windows"
            elif "MacOS" in line_text or "Mac" in line_text:
                current_platform = "macOS"
            elif "Linux" in line_text:
                current_platform = "Linux"

            version = (
                win_ver
                if current_platform == "Windows"
                else mac_ver
                if current_platform == "macOS"
                else linux_ver
            )

            line_a_tags = line_soup.find_all("a", href=True)
            if not line_a_tags:
                continue

            backup_links = []
            official_links = []
            for a in line_a_tags:
                href = a["href"].strip()
                a_text = a.get_text().strip()
                if "t.me/linqiqi_backup" in href or "备份" in a_text:
                    backup_links.append((href, a_text))
                elif any(ext in href.lower() for ext in [".exe", ".dmg", ".deb", ".rpm", ".appimage"]) or "官网" in a_text:
                    official_links.append((href, a_text))

            chosen_links = backup_links if backup_links else official_links
            for href, a_text in chosen_links:
                arch = (
                    a_text.replace("官网链接", "")
                    .replace("频道备份", "")
                    .replace("官网", "")
                    .replace("备份", "")
                    .strip()
                )
                if not arch:
                    m_arch = re.search(r"\b(x64|x86|arm64|arm|loongarch|mips)\b", line_text, re.IGNORECASE)
                    arch = m_arch.group(1).upper() if m_arch else "Package"
                tasks.append(
                    {
                        "platform": current_platform,
                        "arch": arch or "Package",
                        "version": version or "Unknown",
                        "date": date,
                        "url": href,
                        "is_beta": is_beta,
                        "channel": "官方",
                    }
                )
    else:
        # Android 端解析（涵盖新版嵌入超链接：“官网链接 | 频道备份”）
        soup = BeautifulSoup(html, "html.parser")
        a_tags = soup.find_all("a", href=True)

        backup_links = []
        official_links = []

        for a in a_tags:
            href = a["href"].strip()
            a_text = a.get_text().strip()

            # 过滤非安装包下载类链接
            if "t.me/s/" in href or "t.me/qq_updatelog" in href:
                continue

            if "t.me/linqiqi_backup" in href or "备份" in a_text:
                backup_links.append(href)
            elif (
                ".apk" in href
                or "android_apk" in href
                or "downv6.qq.com" in href
                or "官网" in a_text
            ):
                official_links.append(href)

        # 优先选择备份链接，无备份则自动退守官网直链
        target_urls = backup_links if backup_links else official_links
        unique_urls = list(dict.fromkeys(target_urls))

        for u in unique_urls:
            tasks.append(
                {
                    "platform": "Android",
                    "arch": "Package",
                    "version": android_ver or "Unknown",
                    "date": date,
                    "url": u,
                    "is_beta": is_beta,
                    "channel": "官方",
                }
            )

    return tasks


# =====================================================================
# 3. 底层下载与反编译抽取工具
# =====================================================================
def download_target_file(url, target_dir):
    try:
        if "t.me/" in url:
            cmd = ["tdl", "dl", "-u", url, "-d", str(target_dir)]
            return subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=SINGLE_DOWNLOAD_TIMEOUT,
                text=True,
            )
        else:
            fname = url.split("?")[0].split("/")[-1]
            if not fname or not (fname.endswith(".apk") or "." in fname):
                fname = "target_package.apk"
            out_file = target_dir / fname
            cmd = [
                "curl",
                "-fL",
                "-s",
                "-S",
                "--no-progress-meter",
                "-A",
                (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                    " AppleWebKit/537.36"
                ),
                "-e",
                "https://wap.pp.cn/",
                "--retry",
                "2",
                "--connect-timeout",
                "20",
                "--max-time",
                str(SINGLE_DOWNLOAD_TIMEOUT),
                "-o",
                str(out_file),
                url,
            ]
            return subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=SINGLE_DOWNLOAD_TIMEOUT,
                text=True,
            )
    except Exception as e:
        print(f"[下载异常] {e}")
        return None


def get_aapt_cmd():
    cmd = shutil.which("aapt")
    if cmd:
        return cmd
    for env in ["ANDROID_HOME", "ANDROID_SDK_ROOT"]:
        sdk = os.environ.get(env)
        if sdk and os.path.isdir(sdk):
            candidates = list(Path(sdk).glob("build-tools/*/aapt"))
            if candidates:
                return str(sorted(candidates)[-1])
    return None


def extract_subids_and_version(
    extract_dir, downloaded_file_path, default_arch, fallback_ver="Unknown"
):
    fname_str = str(downloaded_file_path).lower()

    if fname_str.endswith(".apk") or (extract_dir / "AndroidManifest.xml").exists():
        subid_phone, subid_pad, base_vname, build_no = None, None, None, None
        aapt_bin = get_aapt_cmd()
        dump_text = ""
        if aapt_bin and downloaded_file_path.exists():
            try:
                proc = subprocess.run(
                    [
                        aapt_bin,
                        "dump",
                        "xmltree",
                        str(downloaded_file_path),
                        "AndroidManifest.xml",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                dump_text = proc.stdout
            except Exception:
                dump_text = ""

        if dump_text:
            lines = dump_text.splitlines()
            for idx, line in enumerate(lines):
                if "versionName" in line and not base_vname:
                    m_vn = re.search(r'android:versionName[^=]*="([^"]+)"', line)
                    if m_vn:
                        base_vname = m_vn.group(1).strip()
                if "AppSetting_params" in line and not subid_phone:
                    if "AppSetting_params_pad" not in line:
                        ctx = "\n".join(
                            lines[max(0, idx - 1) : min(len(lines), idx + 3)]
                        )
                        m_sub = re.search(r"([0-9]{9})#", ctx)
                        if m_sub:
                            subid_phone = m_sub.group(1)
                if "AppSetting_params_pad" in line and not subid_pad:
                    ctx = "\n".join(
                        lines[max(0, idx - 1) : min(len(lines), idx + 3)]
                    )
                    m_sub = re.search(r"([0-9]{9})#", ctx)
                    if m_sub:
                        subid_pad = m_sub.group(1)
                if "ANDROID_QQ_BUILD_NO" in line and not build_no:
                    ctx = "\n".join(
                        lines[max(0, idx - 1) : min(len(lines), idx + 3)]
                    )
                    m_hex = re.search(r"\(type 0x10\)0x([0-9a-fA-F]+)", ctx)
                    if m_hex:
                        build_no = str(int(m_hex.group(1), 16))
                    if not build_no:
                        m_dec = re.search(r"\(type 0x10\)([0-9]+)", ctx)
                        if m_dec:
                            build_no = m_dec.group(1)
                    if not build_no:
                        m_str = re.search(
                            r'(?:Raw:\s*"|=")([0-9]+)"', ctx
                        ) or re.search(r'"([0-9]+)"', ctx)
                        if m_str:
                            build_no = m_str.group(1)

        raw_v = base_vname or fallback_ver
        m_v3 = re.search(r"(\d+\.\d+\.\d+)", raw_v)
        base_3parts = m_v3.group(1) if m_v3 else raw_v

        clean_ver = f"{base_3parts}.{build_no}" if build_no else base_3parts
        pairs = []
        if subid_phone:
            pairs.append((subid_phone, "Phone"))
        if subid_pad:
            pairs.append((subid_pad, "Pad"))
        return pairs, "Android", clean_ver

    # 桌面端
    nodes = list(extract_dir.glob("**/major.node")) or list(
        extract_dir.glob("**/*.node")
    )
    pattern_new = re.compile(rb"QQAppId/([0-9]+)")
    m_cur_build = re.search(r"\d{4,6}$", fallback_ver)
    cur_build_bytes = (
        m_cur_build.group(0).encode("ascii") if m_cur_build else None
    )

    for node in nodes:
        try:
            data = node.read_bytes()
            subid = None
            m_id = pattern_new.findall(data)
            if m_id:
                subid = m_id[0].decode("ascii")
            else:
                pattern_cand = re.compile(rb"(?<![0-9])(537[0-9]{6})(?![0-9])")
                candidates = []
                for match in pattern_cand.finditer(data):
                    cand_subid = match.group(1).decode("ascii")
                    context = data[
                        max(0, match.start() - 500) : min(
                            len(data), match.end() + 500
                        )
                    ]
                    if b"fabu" not in context:
                        continue
                    score = 1
                    if cur_build_bytes and cur_build_bytes in context:
                        score += 2
                    candidates.append((score, cand_subid))
                if candidates:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    subid = candidates[0][1]

            if subid:
                path_str = str(node).lower()
                plat = (
                    "macOS"
                    if (".app" in path_str or "macos" in path_str)
                    else "Linux"
                    if ("opt" in path_str or "linux" in path_str)
                    else "Windows"
                )
                m_already_4parts = re.search(
                    r"(\d+\.\d+\.\d+\.\d{4,6})", fallback_ver
                )
                clean_ver = (
                    m_already_4parts.group(1)
                    if m_already_4parts
                    else fallback_ver
                )
                return [(subid, default_arch)], plat, clean_ver
        except Exception:
            pass
    return [], "Unknown", fallback_ver


def extract_version_from_filename(filename):
    m4 = re.search(r"(\d+\.\d+\.\d+)[-._](\d{4,6})", filename)
    if m4:
        return f"{m4.group(1)}.{m4.group(2)}"
    m3 = re.search(r"(\d+\.\d+\.\d+(?:\(\d+\))?)", filename)
    if m3:
        return m3.group(1)
    return None


# =====================================================================
# 4. 主控流程
# =====================================================================
def main():
    pending_tasks = []

    # -------------------------------------------------------------
    # A. 检查 Telegram 频道常规更新
    # -------------------------------------------------------------
    msg = get_latest_message()
    if msg:
        print(f"[TG 监控] 最新消息 ID: {msg['post_id']} | 日期: {msg['date']}")
        tg_tasks = parse_tasks_from_message(msg)
        print(f"[TG 监控] 成功解析到任务数: {len(tg_tasks)}")
        pending_tasks.extend(tg_tasks)
    else:
        print("[TG 监控] 未获取到频道消息")

    # -------------------------------------------------------------
    # B. 检查 豌豆荚 (PP助手) 渠道更新
    # -------------------------------------------------------------
    wdj_recorded_ver = ""
    if WDJ_VER_FILE.exists():
        wdj_recorded_ver = WDJ_VER_FILE.read_text(encoding="utf-8").strip()

    wdj_info = get_wandoujia_info()
    wdj_new_ver_to_record = None

    if wdj_info:
        cur_wdj_ver = wdj_info["version"]
        print(f"[豌豆荚监控] 当前线上版本: {cur_wdj_ver} | 本地记录版本: {wdj_recorded_ver or '无'}")

        if not wdj_recorded_ver or wdj_recorded_ver != cur_wdj_ver:
            print(f"[豌豆荚监控] 检测到新版本/首发收录: {cur_wdj_ver}，加入待处理任务！")
            pending_tasks.append(
                {
                    "platform": "Android",
                    "arch": "Package",
                    "version": cur_wdj_ver,
                    "date": wdj_info["date"],
                    "url": wdj_info["download_url"],
                    "is_beta": False,
                    "channel": "豌豆荚",
                    "mark_wdj_version": cur_wdj_ver,
                }
            )
        else:
            print("[豌豆荚监控] 线上版本与记录一致，无需下载。")

    if not pending_tasks:
        print("[状态] 未检索到任何新任务。")
        sys.exit(0)

    # -------------------------------------------------------------
    # C. 读取已有数据库并做 URL 去重排队
    # -------------------------------------------------------------
    subid_map = {}
    if DATA_FILE.exists():
        try:
            subid_map = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    processed_urls = set()
    for val in subid_map.values():
        items = val if isinstance(val, list) else [val]
        for it in items:
            u = it.get("source_url")
            if u:
                processed_urls.add(u)

    tasks_to_run = [t for t in pending_tasks if t["url"] not in processed_urls]
    github_output = os.environ.get("GITHUB_OUTPUT")

    if not tasks_to_run:
        print("[状态] 所有目标任务的直链均已录入，无需重复处理。")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write("has_new_version=false\n")
                f.write("trigger_decompile=false\n")
        return

    print(f"\n[任务队列] 共有 {len(tasks_to_run)} 个任务待处理！")

    has_android = False
    android_info = {}
    has_subid_extracted = False

    for idx, t in enumerate(tasks_to_run, 1):
        print(f"\n--- [{idx}/{len(tasks_to_run)}] 正在处理: {t['platform']} - {t['arch']} ({t.get('channel', '官方')}) ---")
        print(f"URL: {t['url']}")

        if TEMP_DL.exists():
            shutil.rmtree(TEMP_DL)
        TEMP_DL.mkdir()

        proc = download_target_file(t["url"], TEMP_DL)
        if proc is None or proc.returncode != 0:
            print("[跳过] 下载失败")
            continue

        dl_files = [
            f
            for f in TEMP_DL.rglob("*")
            if f.is_file() and not f.name.endswith(".tdl")
        ]
        if not dl_files:
            continue

        downloaded_file = dl_files[0]
        fname = downloaded_file.name
        base_ver = extract_version_from_filename(fname) or t["version"]

        if TEMP_EXTRACT.exists():
            shutil.rmtree(TEMP_EXTRACT)
        TEMP_EXTRACT.mkdir()

        try:
            lf = fname.lower()
            if lf.endswith(".deb"):
                subprocess.run(
                    ["dpkg-deb", "-x", str(downloaded_file), str(TEMP_EXTRACT)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif lf.endswith(".rpm"):
                cmd = f"cd '{TEMP_EXTRACT}' && rpm2cpio '{downloaded_file.resolve()}' | cpio -idmv"
                subprocess.run(
                    cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.run(
                    ["7z", "x", str(downloaded_file), f"-o{TEMP_EXTRACT}", "-y"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                hfs = list(TEMP_EXTRACT.glob("**/*.hfs"))
                if hfs:
                    subprocess.run(
                        [
                            "7z",
                            "x",
                            str(hfs[0]),
                            f"-o{TEMP_EXTRACT}/hfs_out",
                            "-y",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

            subid_pairs, plat, clean_ver = extract_subids_and_version(
                TEMP_EXTRACT, downloaded_file, t["arch"], base_ver
            )
            if subid_pairs:
                has_subid_extracted = True
                if t.get("is_beta") and not clean_ver.endswith("-Beta"):
                    clean_ver = f"{clean_ver}-Beta"

                for s_id, a_val in subid_pairs:
                    channel_label = t.get("channel", "官方")
                    print(
                        f"[提取成功] 平台: {plat} | 架构: {a_val} | 版本: {clean_ver} | 渠道: {channel_label} | SubID: {s_id}"
                    )
                    item = {
                        "version": clean_ver,
                        "platform": plat,
                        "arch": a_val,
                        "date": t["date"],
                        "source_url": t["url"],
                        "channel": channel_label,
                    }
                    if s_id not in subid_map:
                        subid_map[s_id] = [item]
                    else:
                        if isinstance(subid_map[s_id], dict):
                            subid_map[s_id] = [subid_map[s_id]]
                        subid_map[s_id].append(item)

                if t.get("mark_wdj_version"):
                    wdj_new_ver_to_record = t["mark_wdj_version"]

                if plat == "Android" and t.get("channel") != "豌豆荚":
                    has_android = True
                    android_info = {
                        "url": t["url"],
                        "version": clean_ver,
                        "is_beta": "true" if t.get("is_beta") else "false",
                    }
        finally:
            shutil.rmtree(TEMP_DL, ignore_errors=True)
            shutil.rmtree(TEMP_EXTRACT, ignore_errors=True)

    if not has_subid_extracted:
        print("[状态] 本批次下载解析未能提取到有效 SubID，取消落盘。")
        return

    # -------------------------------------------------------------
    # D. 原子落盘主数据库 data/qq_subid_map.json
    # -------------------------------------------------------------
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(subid_map, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if DATA_FILE.exists():
        shutil.copy2(DATA_FILE, BAK_FILE)
    temp_file.replace(DATA_FILE)

    # -------------------------------------------------------------
    # E. 同步派生编译出运行时字典 data/subid.json
    # -------------------------------------------------------------
    try:
        compiled_data = compile_subid_map_to_runtime_dict(subid_map)
        runtime_file = DATA_FILE.parent / "subid.json"
        temp_rt = runtime_file.with_suffix(".tmp")
        temp_rt.write_text(
            json.dumps(compiled_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_rt.replace(runtime_file)
        print(f"[运行时同步] 已派生编译 data/subid.json (收录 {len(compiled_data)} 条)")
    except Exception as e:
        print(f"[警告] 编译运行时字典异常: {e}")

    # -------------------------------------------------------------
    # F. 在所有 SubID 全部提取处理完成之后，最后保存版本文件
    # -------------------------------------------------------------
    if wdj_new_ver_to_record:
        WDJ_VER_FILE.parent.mkdir(parents=True, exist_ok=True)
        WDJ_VER_FILE.write_text(wdj_new_ver_to_record.strip(), encoding="utf-8")
        print(f"[版本记录] 豌豆荚最新版本 {wdj_new_ver_to_record} 已成功写入 {WDJ_VER_FILE}")

    print("\n[更新完毕] 所有新版本数据已追加写入 data/qq_subid_map.json")

    # 联动输出：告知 GitHub Actions
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write("has_new_version=true\n")
            if has_android:
                f.write("trigger_decompile=true\n")
                f.write(f"android_apk_url={android_info.get('url', '')}\n")
                f.write(f"android_version={android_info.get('version', '')}\n")
                f.write(f"android_is_beta={android_info.get('is_beta', 'false')}\n")
            else:
                f.write("trigger_decompile=false\n")


if __name__ == "__main__":
    main()
