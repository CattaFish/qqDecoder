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

# 强制 Python 标准输出和错误流实时刷新，杜绝云端缓冲迟滞
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

CHANNEL_URL = "https://t.me/s/qq_updatelog"
DATA_FILE = Path("data/qq_subid_map.json")
TEMP_DL = Path("temp_dl")
TEMP_EXTRACT = Path("temp_extracted")
START_DATE = os.environ.get("START_DATE", "2025-01-01")


def get_history_messages(start_date_str):
    """从最新消息向前翻页爬取，直到早于指定起始日期"""
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

        # 严格过滤：只选取真正带有 data-post 属性且包含斜杠的消息节点
        msg_divs = [
            div
            for div in soup.find_all("div", class_="tgme_widget_message")
            if div.get("data-post") and "/" in div.get("data-post")
        ]

        if not msg_divs:
            print("[提示] 当前页面未检索到更多有效消息，停止翻页。", flush=True)
            break

        # 计算当前页面中最小的数字 ID 作为下一次的翻页游标
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
        # 从新到旧解析消息
        for msg in reversed(msg_divs):
            time_elem = msg.find("time")
            if not time_elem or "datetime" not in time_elem.attrs:
                continue

            dt_str = time_elem["datetime"][:10]  # YYYY-MM-DD
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
    """从历史消息中精准提取'备份'区的所有链接及其平台架构"""
    print("[阶段 2] 正在分析提取各消息中的备份文件下载链接...", flush=True)
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

        m_win = re.search(r"Windows QQ_NT\s+([0-9\.]+)", text)
        if m_win:
            win_ver = m_win.group(1)

        m_mac = re.search(r"Mac(?:OS)? QQ_NT\s+([0-9\.]+)", text)
        if m_mac:
            mac_ver = m_mac.group(1)

        m_linux = re.search(r"Linux QQ_NT\s+([0-9\.]+)", text)
        if m_linux:
            linux_ver = m_linux.group(1)

        m_android = re.search(r"QQ(?:_NT)?\s+([0-9\.]+)", text)
        if m_android:
            android_ver = m_android.group(1)

        # 针对 Android 独立推送消息
        if "备份" not in html:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if "t.me/linqiqi_backup" in href or ".apk" in href:
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
            continue

        # 截取“备份”关键字之后的区域
        backup_html = html.split("备份", 1)[1]
        current_platform = "Unknown"
        lines = backup_html.split("<br/>")

        for line in lines:
            line_soup = BeautifulSoup(line, "html.parser")
            line_text = line_soup.get_text()

            if "Windows" in line_text:
                current_platform = "Windows"
            elif "MacOS" in line_text or "Mac" in line_text:
                current_platform = "macOS"
            elif "Linux" in line_text:
                current_platform = "Linux"
            elif "Android" in line_text:
                current_platform = "Android"

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
                elif current_platform == "Android":
                    version = android_ver

                tasks.append(
                    {
                        "platform": current_platform,
                        "arch": arch_name,
                        "version": version,
                        "date": date,
                        "url": href,
                        "post_id": post_id,
                    }
                )

    # 依据 URL 去重
    unique_tasks = []
    seen = set()
    for t in tasks:
        if t["url"] not in seen:
            seen.add(t["url"])
            unique_tasks.append(t)

    print(
        f"[阶段 2 完成] 共整理出 {len(unique_tasks)} 个独立的架构备份任务。\n",
        flush=True,
    )
    return unique_tasks


def extract_subid(extract_dir, platform):
    """统一解包提取 SubID"""
    if platform == "Android":
        manifest_dump = extract_dir / "manifest_dump.txt"
        manifest_xml = extract_dir / "AndroidManifest.xml"

        if manifest_dump.exists():
            text = manifest_dump.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'AppSetting_params".*?value.*?="([0-9]+)#', text)
            if m:
                return m.group(1)

        if manifest_xml.exists():
            raw = manifest_xml.read_bytes()
            matches = re.findall(rb"([0-9]{9})#[A-Fa-f0-9]{16}#", raw)
            if matches:
                return matches[0].decode("ascii")

        return None
    else:
        nodes = list(extract_dir.glob("**/major.node"))
        if not nodes:
            nodes = list(extract_dir.glob("**/*.node"))

        pattern = re.compile(rb"QQAppId/([0-9]+)")
        for node in nodes:
            try:
                data = node.read_bytes()
                matched = pattern.findall(data)
                if matched:
                    return matched[0].decode("ascii")
            except Exception:
                pass
        return None


def run_extraction_pipeline(tasks):
    """循环下载、解包、提取并写入目标字典（实时显示进度）"""
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
            f"[{idx}/{total_count}] 正在处理: {t['platform']} - {t['arch']} | 版本: {t['version']} | 日期: {t['date']}",
            flush=True,
        )
        print(f"来源链接: {t['url']}", flush=True)

        if TEMP_DL.exists():
            shutil.rmtree(TEMP_DL)
        TEMP_DL.mkdir()

        # 1. 调用 tdl 下载文件（直接继承 stdout/stderr，让控制台实时输出下载进度条）
        dl_cmd = ["tdl", "dl", "-u", t["url"], "-d", str(TEMP_DL)]
        proc = subprocess.run(dl_cmd)
        if proc.returncode != 0:
            print(f"[下载异常] tdl 退出码: {proc.returncode}", flush=True)
            continue

        # 寻找下载落盘的安装包
        dl_files = [
            f
            for f in TEMP_DL.rglob("*")
            if f.is_file() and not f.name.endswith(".tdl")
        ]
        if not dl_files:
            print("[警告] 未能获取到下载文件实体，进入下一任务", flush=True)
            continue

        downloaded_file = dl_files[0]
        fname = downloaded_file.name.lower()
        file_size_mb = downloaded_file.stat().st_size / 1024 / 1024
        print(
            f"\n下载完成: {downloaded_file.name} ({file_size_mb:.2f} MB)，开始解包...",
            flush=True,
        )

        # 2. 解包
        if TEMP_EXTRACT.exists():
            shutil.rmtree(TEMP_EXTRACT)
        TEMP_EXTRACT.mkdir()

        try:
            if fname.endswith(".deb"):
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
            elif fname.endswith(".rpm"):
                cmd = f"cd '{TEMP_EXTRACT}' && rpm2cpio '{downloaded_file.resolve()}' | cpio -idmv"
                subprocess.run(
                    cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # 兼容 .exe, .dmg, .apk, .AppImage（静默 stderr，屏蔽 7z 的软链接安全警告）
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

                # 处理 macOS 嵌套 HFS 分区
                hfs_files = list(TEMP_EXTRACT.glob("**/*.hfs"))
                if hfs_files:
                    hfs_out = TEMP_EXTRACT / "hfs_extracted"
                    subprocess.run(
                        ["7z", "x", str(hfs_files[0]), f"-o{hfs_out}", "-y"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

            # 3. 提取 SubID
            subid = extract_subid(TEMP_EXTRACT, t["platform"])

            if subid:
                print(f"[提取成功] SubID: {subid}", flush=True)
                subid_map[subid] = {
                    "version": t["version"],
                    "platform": t["platform"],
                    "arch": t["arch"],
                    "date": t["date"],
                    "source_url": t["url"],
                }
                # 实时更新落盘，防止任务中断丢失进度
                DATA_FILE.write_text(
                    json.dumps(subid_map, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                processed_urls.add(t["url"])
            else:
                print("[警告] 解包后未检测到 SubID 特征码", flush=True)

        except Exception as e:
            print(f"[处理异常] {e}", flush=True)

        finally:
            # 4. 彻底物理清理当前包与解包目录，防止 Runner 磁盘爆满
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
