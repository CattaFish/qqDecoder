import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
import requests

# 强制 Python 标准输出和错误流实时无缓冲刷新
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

CHANNEL_URL = "https://t.me/s/qq_updatelog"
DATA_FILE = Path("data/qq_subid_map.json")
TEMP_DL = Path("temp_dl")
TEMP_EXTRACT = Path("temp_extracted")
START_DATE = os.environ.get("START_DATE", "2025-01-01")


def get_history_messages(start_date_str):
    """从最新消息向前翻页爬取历史消息"""
    print(f"[阶段 1] 正在抓取频道历史消息（起始日期: {start_date_str}）...", flush=True)
    messages_data = []
    before_id = None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
    }

    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    stop_crawling = False

    while not stop_crawling:
        url = CHANNEL_URL
        if before_id:
            url = f"{CHANNEL_URL}?before={before_id}"

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"[警告] 页面请求失败: {resp.status_code}", flush=True)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        msg_divs = [
            div
            for div in soup.find_all("div", class_="tgme_widget_message")
            if div.get("data-post") and "/" in div.get("data-post")
        ]

        if not msg_divs:
            print("[提示] 当前页面未检索到更多有效消息，停止翻页。", flush=True)
            break

        post_ids = []
        for d in msg_divs:
            try:
                pid = int(d.get("data-post").split("/")[-1])
                post_ids.append(pid)
            except Exception:
                pass

        if not post_ids:
            break

        min_id = min(post_ids)
        if before_id is not None and min_id >= before_id:
            break
        before_id = min_id

        current_date_str = "Unknown"
        for msg in reversed(msg_divs):
            time_elem = msg.find("time")
            if not time_elem or "datetime" not in time_elem.attrs:
                continue

            dt_str = time_elem["datetime"][:10]
            current_date_str = dt_str
            try:
                msg_date = datetime.strptime(dt_str, "%Y-%m-%d").date()
                if msg_date < start_date:
                    stop_crawling = True
                    break
            except Exception:
                continue

            text_elem = msg.find("div", class_="tgme_widget_message_text")
            if text_elem:
                messages_data.append(
                    {
                        "post_id": msg.get("data-post"),
                        "date": dt_str,
                        "html": str(text_elem),
                        "text": text_elem.get_text(),
                    }
                )

        print(
            f"[翻页扫描] 消息游标 ID: {before_id} | 当前扫描至日期: {current_date_str} | 已收录消息: {len(messages_data)} 条",
            flush=True,
        )
        time.sleep(1)

    print(
        f"[阶段 1 完成] 成功收集到 {len(messages_data)} 条符合日期要求的历史消息。\n",
        flush=True,
    )
    return messages_data


def parse_backup_tasks(messages):
    """
    优先策略：
    1. 若消息中存在'备份'，严格只解析备份直链；
    2. 若消息中无'备份'，才提取直接下载链接；
    3. 版本号过滤排除中文。
    """
    print("[阶段 2] 正在分析提取文件下载链接（优先提取备份）...", flush=True)
    tasks = []

    for msg in messages:
        text = msg["text"]
        html = msg["html"]
        date = msg["date"]
        post_id = msg["post_id"]

        win_ver = ""
        mac_ver = ""
        linux_ver = ""
        android_ver = ""

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
            r"QQ(?:_NT)?\s*[:：]?\s*([0-9]+(?:\.[0-9]+)+(?:\([0-9]+\))?)",
            text,
        )
        if m_android:
            android_ver = m_android.group(1)

        # -----------------------------------------------------------------
        # 情况 A: 消息中包含“备份” -> 严格只提取备份链接
        # -----------------------------------------------------------------
        if "备份" in html:
            backup_html = html.split("备份", 1)[1]

            if (
                "Windows" in backup_html
                or "Linux" in backup_html
                or "MacOS" in backup_html
            ):
                current_platform = "Unknown"
                lines = re.split(r"<br\s*/?>|\n", backup_html)

                for line in lines:
                    line_soup = BeautifulSoup(line, "html.parser")
                    line_text = line_soup.get_text()

                    if "Windows" in line_text:
                        current_platform = "Windows"
                    elif "MacOS" in line_text or "Mac" in line_text:
                        current_platform = "macOS"
                    elif "Linux" in line_text:
                        current_platform = "Linux"

                    for a in line_soup.find_all("a", href=True):
                        href = a["href"].strip()
                        arch_name = a.get_text().strip()

                        if "t.me/linqiqi_backup" not in href:
                            continue

                        version = "Unknown"
                        if current_platform == "Windows":
                            version = win_ver
                        elif current_platform == "macOS":
                            version = mac_ver
                        elif current_platform == "Linux":
                            version = linux_ver

                        tasks.append(
                            {
                                "platform": current_platform,
                                "arch": arch_name or "Package",
                                "version": version,
                                "date": date,
                                "url": href,
                                "post_id": post_id,
                            }
                        )
            else:
                soup = BeautifulSoup(backup_html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if "t.me/linqiqi_backup" in href:
                        tasks.append(
                            {
                                "platform": "Android",
                                "arch": "Universal",
                                "version": android_ver or "Unknown",
                                "date": date,
                                "url": href,
                                "post_id": post_id,
                            }
                        )

        # -----------------------------------------------------------------
        # 情况 B: 消息中无“备份”，直接使用下载链接
        # -----------------------------------------------------------------
        else:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if ".apk" in href or "android_apk" in href:
                    tasks.append(
                        {
                            "platform": "Android",
                            "arch": "Universal",
                            "version": android_ver or "Unknown",
                            "date": date,
                            "url": href,
                            "post_id": post_id,
                        }
                    )

    unique_tasks = []
    seen = set()
    for t in tasks:
        if t["url"] not in seen:
            seen.add(t["url"])
            unique_tasks.append(t)

    print(
        f"[阶段 2 完成] 精简过滤后共计 {len(unique_tasks)} 个独立任务（已优先保留备份直链）。\n",
        flush=True,
    )
    return unique_tasks


def download_target_file(url, target_dir):
    """静默下载"""
    if "t.me/" in url:
        cmd = ["tdl", "dl", "-u", url, "-d", str(target_dir)]
        return subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
    else:
        fname = url.split("?")[0].split("/")[-1] or "installer_file"
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
            "--retry",
            "2",
            "--connect-timeout",
            "20",
            "-o",
            str(out_file),
            url,
        ]
        return subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )


def extract_subid_and_version(extract_dir, fallback_ver="Unknown"):
    """
    自适应提取 SubID，并从二进制/清单内部自动对齐、补全为标准的 4 段式版本号 (X.Y.Z.BUILD)
    """
    # ----------------------------------------------------
    # 1. Android 端处理
    # ----------------------------------------------------
    manifest_xml = extract_dir / "AndroidManifest.xml"
    manifest_dump = extract_dir / "manifest_dump.txt"

    if manifest_dump.exists() or manifest_xml.exists():
        subid = None
        build_no = None

        # 优先从 aapt 转储的明文中提取
        if manifest_dump.exists():
            text = manifest_dump.read_text(encoding="utf-8", errors="ignore")
            m_sub = re.search(r'AppSetting_params".*?value.*?="([0-9]+)#', text)
            if m_sub:
                subid = m_sub.group(1)
            m_b = re.search(r'ANDROID_QQ_BUILD_NO".*?value.*?="([0-9]+)"', text)
            if m_b:
                build_no = m_b.group(1)

        # 字节流兜底扫描
        if manifest_xml.exists():
            raw = manifest_xml.read_bytes()

            if not subid:
                matches_u8 = re.findall(rb"([0-9]{9})#[A-Fa-f0-9]{16}#", raw)
                matches_u16 = [
                    m.replace(b"\x00", b"")
                    for m in re.findall(
                        rb"((?:[0-9]\x00){9})#\x00(?:[A-Fa-f0-9]\x00){16}#\x00",
                        raw,
                    )
                ]
                all_m = [m.decode("ascii") for m in (matches_u8 + matches_u16)]
                if all_m:
                    subid = all_m[0]

            if not build_no:
                # 匹配紧随在 ANDROID_QQ_BUILD_NO 附近的数字构建号
                m_b_u8 = re.search(
                    rb"ANDROID_QQ_BUILD_NO.{0,100}?([0-9]{4,6})", raw
                )
                if m_b_u8:
                    build_no = m_b_u8.group(1).decode("ascii")
                else:
                    m_b_u16 = re.search(
                        rb"A\x00N\x00D\x00R\x00O\x00I\x00D\x00_\x00Q\x00Q\x00_\x00B\x00U\x00I\x00L\x00D\x00_\x00N\x00O\x00.{0,150}?((?:[0-9]\x00){4,6})",
                        raw,
                    )
                    if m_b_u16:
                        build_no = (
                            m_b_u16.group(1)
                            .replace(b"\x00", b"")
                            .decode("ascii")
                        )

        # 统合为标准 4 段版本号：例如 9.3.70 + 41925 -> 9.3.70.41925
        clean_ver = fallback_ver
        m_base = re.search(r"(\d+\.\d+\.\d+)", fallback_ver)
        if m_base:
            base_ver = m_base.group(1)
            m_full = re.search(r"(\d+\.\d+\.\d+\.\d+)", fallback_ver)
            if m_full:
                clean_ver = m_full.group(1)
            elif build_no:
                clean_ver = f"{base_ver}.{build_no}"
        elif build_no:
            clean_ver = f"Unknown.{build_no}"

        return subid, "Android", clean_ver

    # ----------------------------------------------------
    # 2. 桌面端处理 (Win / Linux / macOS)
    # ----------------------------------------------------
    nodes = list(extract_dir.glob("**/major.node"))
    if not nodes:
        nodes = list(extract_dir.glob("**/*.node"))

    pattern_subid = re.compile(rb"QQAppId/([0-9]+)")
    pattern_ver = re.compile(rb"QQ(?:EX)?/(\d+\.\d+\.\d+)[-\.](\d+)")

    for node in nodes:
        try:
            data = node.read_bytes()
            m_id = pattern_subid.findall(data)
            if m_id:
                subid = m_id[0].decode("ascii")
                path_str = str(node).lower()
                plat = "Windows"
                if ".app" in path_str or "macos" in path_str:
                    plat = "macOS"
                elif "opt" in path_str or "linux" in path_str:
                    plat = "Linux"

                # 从 UA 中自动提取原生 4 段版本号 (如 QQ/9.9.36-53644 -> 9.9.36.53644)
                clean_ver = fallback_ver
                m_v = pattern_ver.search(data)
                if m_v:
                    clean_ver = f"{m_v.group(1).decode('ascii')}.{m_v.group(2).decode('ascii')}"
                else:
                    m_fb = re.search(
                        r"(\d+\.\d+\.\d+(?:[\._]\d+)?)", fallback_ver
                    )
                    if m_fb:
                        clean_ver = m_fb.group(1).replace("_", ".")

                return subid, plat, clean_ver
        except Exception:
            pass

    return None, "Unknown", fallback_ver


def extract_version_from_filename(filename):
    """从真实下载的文件名中提炼基础版本号"""
    m = re.search(r"(\d+\.\d+\.\d+(?:[\._]\d+)?(?:\(\d+\))?)", filename)
    if m:
        return m.group(1).replace("_", ".")
    return None


def run_extraction_pipeline(tasks):
    """循环下载、解包、提取并写入目标字典"""
    print("[阶段 3] 开始执行批量下载与 SubID 提取流水线...", flush=True)

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    subid_map = {}
    if DATA_FILE.exists():
        try:
            subid_map = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            subid_map = {}

    processed_urls = {item.get("source_url") for item in subid_map.values()}
    total_count = len(tasks)

    for idx, t in enumerate(tasks, start=1):
        if t["url"] in processed_urls:
            print(
                f"[{idx}/{total_count}] [已存在跳过] {t['platform']} {t['arch']} ({t['version']})",
                flush=True,
            )
            continue

        print("\n" + "=" * 65, flush=True)
        print(
            f"[{idx}/{total_count}] 正在处理: {t['platform']} - {t['arch']} | 初始版本: {t['version']} | 日期: {t['date']}",
            flush=True,
        )
        print(f"来源链接: {t['url']}", flush=True)
        print("状态: 正在下载文件...", flush=True)

        if TEMP_DL.exists():
            shutil.rmtree(TEMP_DL)
        TEMP_DL.mkdir()

        # 1. 下载
        proc = download_target_file(t["url"], TEMP_DL)
        if proc.returncode != 0:
            err_msg = (
                proc.stderr.strip()[:80] if proc.stderr else "下载退出码非零"
            )
            print(f"状态: [下载失败，已跳过] 原因: {err_msg}", flush=True)
            if TEMP_DL.exists():
                shutil.rmtree(TEMP_DL)
            continue

        dl_files = [
            f
            for f in TEMP_DL.rglob("*")
            if f.is_file() and not f.name.endswith(".tdl")
        ]
        if not dl_files:
            print("状态: [下载未产出文件，已跳过]", flush=True)
            if TEMP_DL.exists():
                shutil.rmtree(TEMP_DL)
            continue

        downloaded_file = dl_files[0]
        fname = downloaded_file.name
        file_size_mb = downloaded_file.stat().st_size / 1024 / 1024

        # 获取基础版本号备用
        real_version_from_file = extract_version_from_filename(fname)
        base_version = (
            real_version_from_file
            if real_version_from_file
            else t["version"]
        )

        print(
            f"状态: 下载成功 ({fname}, {file_size_mb:.2f} MB)，开始解包...",
            flush=True,
        )

        # 2. 解包
        if TEMP_EXTRACT.exists():
            shutil.rmtree(TEMP_EXTRACT)
        TEMP_EXTRACT.mkdir()

        try:
            lower_fname = fname.lower()
            if lower_fname.endswith(".deb"):
                subprocess.run(
                    [
                        "dpkg-deb",
                        "-x",
                        str(downloaded_file),
                        str(TEMP_EXTRACT),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif lower_fname.endswith(".rpm"):
                cmd = f"cd '{TEMP_EXTRACT}' && rpm2cpio '{downloaded_file.resolve()}' | cpio -idmv"
                subprocess.run(
                    cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.run(
                    [
                        "7z",
                        "x",
                        str(downloaded_file),
                        f"-o{TEMP_EXTRACT}",
                        "-y",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                hfs_files = list(TEMP_EXTRACT.glob("**/*.hfs"))
                if hfs_files:
                    hfs_out = TEMP_EXTRACT / "hfs_extracted"
                    subprocess.run(
                        ["7z", "x", str(hfs_files[0]), f"-o{hfs_out}", "-y"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

            # 3. 提取 SubID 与权威 4 段式版本号
            subid, detected_platform, unified_version = (
                extract_subid_and_version(TEMP_EXTRACT, base_version)
            )

            if subid:
                final_platform = (
                    detected_platform
                    if t["platform"] == "Unknown"
                    else t["platform"]
                )
                print(
                    f"[提取成功] 平台: {final_platform} | 统合版本: {unified_version} | SubID: {subid}",
                    flush=True,
                )
                subid_map[subid] = {
                    "version": unified_version,
                    "platform": final_platform,
                    "arch": t["arch"],
                    "date": t["date"],
                    "source_url": t["url"],
                }
                DATA_FILE.write_text(
                    json.dumps(subid_map, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                processed_urls.add(t["url"])
            else:
                print("状态: [警告] 解包后未检测到 SubID 特征码", flush=True)

        except Exception as e:
            print(f"状态: [处理异常] {e}", flush=True)

        finally:
            if TEMP_DL.exists():
                shutil.rmtree(TEMP_DL)
            if TEMP_EXTRACT.exists():
                shutil.rmtree(TEMP_EXTRACT)

        time.sleep(2)

    print("\n" + "=" * 65, flush=True)
    print(
        f"[全量任务结束] 已成功整理并持久化保存至 data/qq_subid_map.json",
        flush=True,
    )


def main():
    messages = get_history_messages(START_DATE)
    if not messages:
        print("[结束] 未检索到任何指定日期范围内的历史消息。", flush=True)
        return

    tasks = parse_backup_tasks(messages)
    if not tasks:
        print("[结束] 未解析出可下载的备份任务。", flush=True)
        return

    run_extraction_pipeline(tasks)


if __name__ == "__main__":
    main()
