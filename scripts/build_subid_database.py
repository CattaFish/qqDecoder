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
BAK_FILE = Path("data/qq_subid_map.json.bak")
MORE_FLAG = Path("data/.has_more")
TEMP_DL = Path("temp_dl")
TEMP_EXTRACT = Path("temp_extracted")

START_DATE = os.environ.get("START_DATE", "2025-01-01")
MAX_RUNTIME_MINUTES = int(os.environ.get("MAX_RUNTIME_MINUTES", "280"))


def load_and_sanitize_database():
    """读取已有数据，并自动清洗残缺数据（包含自动重跑未提取 Pad 的旧 Android 记录）"""
    subid_map = {}
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    for f in [DATA_FILE, BAK_FILE]:
        if f.exists():
            try:
                subid_map = json.loads(f.read_text(encoding="utf-8"))
                break
            except Exception as e:
                print(
                    f"[警告] 读取 {f.name} 失败 ({e})，尝试备用文件...", flush=True
                )

    if not subid_map:
        return {}

    cleaned_map = {}
    purged_count = 0

    for s_id, item in subid_map.items():
        is_bad = False
        ver = str(item.get("version", ""))
        plat = str(item.get("platform", ""))
        arch = str(item.get("arch", ""))

        # 坏数据特征 1: 缺少版本、版本为 Unknown 或 SubID 非纯数字
        if not s_id or not str(s_id).isdigit() or not ver or ver == "Unknown":
            is_bad = True

        # 坏数据特征 2: 版本中混入汉字
        if re.search(r"[\u4e00-\u9fa5]", ver):
            is_bad = True

        # 坏数据特征 3: 包含十位以上的长数字（如 19 位小米推送 ID）
        if re.search(r"\d{10,}", ver):
            is_bad = True

        # 坏数据特征 4: Android 平台版本号只有 3 段（缺少构建号）或旧的 Universal 未拆分架构
        if plat == "Android":
            pure_v = ver.replace("-Beta", "").strip()
            if not re.fullmatch(r"\d+\.\d+\.\d+\.\d{4,6}", pure_v):
                is_bad = True
            if arch == "Universal":  # 标记旧版未拆分 Phone/Pad 的记录，使其自动重跑补全
                is_bad = True

        if is_bad:
            purged_count += 1
            print(
                f"[自愈清理] 检测到需升级或残缺数据，自动移除重跑: SubID={s_id} | 版本={ver} | 来源={item.get('source_url')}",
                flush=True,
            )
        else:
            cleaned_map[s_id] = item

    if purged_count > 0:
        print(
            f"[自愈完成] 历史数据体检完毕，已整理 {purged_count} 条数据进入重跑补全队列。\n",
            flush=True,
        )
        save_database_atomically(cleaned_map)

    return cleaned_map


def save_database_atomically(data_map):
    """原子写盘与备份，防止写入中断导致 JSON 文件损坏"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(".tmp")
    json_str = json.dumps(data_map, indent=2, ensure_ascii=False)

    temp_file.write_text(json_str, encoding="utf-8")
    if DATA_FILE.exists():
        shutil.copy2(DATA_FILE, BAK_FILE)
    temp_file.replace(DATA_FILE)


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
    """提取任务清单，严格优先提取备份直链"""
    print("[阶段 2] 正在分析提取文件下载链接（优先提取备份）...", flush=True)
    tasks = []

    for msg in messages:
        text = msg["text"]
        html = msg["html"]
        date = msg["date"]
        post_id = msg["post_id"]

        is_beta = bool(re.search(r"#Beta\b", text, re.IGNORECASE))

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
                                "is_beta": is_beta,
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
                                "arch": "Package",
                                "version": android_ver or "Unknown",
                                "date": date,
                                "url": href,
                                "post_id": post_id,
                                "is_beta": is_beta,
                            }
                        )
        else:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if ".apk" in href or "android_apk" in href:
                    tasks.append(
                        {
                            "platform": "Android",
                            "arch": "Package",
                            "version": android_ver or "Unknown",
                            "date": date,
                            "url": href,
                            "post_id": post_id,
                            "is_beta": is_beta,
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


def get_aapt_cmd():
    """获取 aapt 工具路径"""
    cmd = shutil.which("aapt")
    if cmd:
        return cmd
    for env in ["ANDROID_HOME", "ANDROID_SDK_ROOT"]:
        sdk = os.environ.get(env)
        if sdk and os.path.isdir(sdk):
            candidates = list(Path(sdk).glob("build-tools/*/aapt"))
            if candidates:
                return str(sorted(candidates)[-1])
    for p in [
        "/usr/local/lib/android/sdk/build-tools",
        "/usr/lib/android-sdk/build-tools",
    ]:
        candidates = list(Path(p).glob("*/aapt"))
        if candidates:
            return str(sorted(candidates)[-1])
    return None


def extract_subids_and_version(
    extract_dir, downloaded_file_path, default_arch, fallback_ver="Unknown"
):
    """
    自适应提取 SubID 列表和统合版本号：
    - 对 Android：同时提取 Phone 与 Pad 的 SubID；
    - 对 桌面端：提取对应的单 SubID。
    返回值: ([(subid, arch), ...], detected_platform, unified_version)
    """
    fname_str = str(downloaded_file_path).lower()

    # ----------------------------------------------------
    # 1. Android 端处理 (APK)
    # ----------------------------------------------------
    manifest_xml = extract_dir / "AndroidManifest.xml"
    if fname_str.endswith(".apk") or manifest_xml.exists():
        subid_phone = None
        subid_pad = None
        base_vname = None
        build_no = None

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
                # 提取 APK 内部权威主版本号
                if "versionName" in line and not base_vname:
                    m_vn = re.search(r'android:versionName[^=]*="([^"]+)"', line)
                    if m_vn:
                        base_vname = m_vn.group(1).strip()

                # 提取手机版 SubID (AppSetting_params)
                if "AppSetting_params" in line and not subid_phone:
                    if "AppSetting_params_pad" not in line:
                        ctx = "\n".join(
                            lines[max(0, idx - 1) : min(len(lines), idx + 3)]
                        )
                        m_sub = re.search(r"([0-9]{9})#", ctx)
                        if m_sub:
                            subid_phone = m_sub.group(1)

                # 提取平板版 SubID (AppSetting_params_pad)
                if "AppSetting_params_pad" in line and not subid_pad:
                    ctx = "\n".join(
                        lines[max(0, idx - 1) : min(len(lines), idx + 3)]
                    )
                    m_sub = re.search(r"([0-9]{9})#", ctx)
                    if m_sub:
                        subid_pad = m_sub.group(1)

                # 提取 ANDROID_QQ_BUILD_NO
                if "ANDROID_QQ_BUILD_NO" in line and not build_no:
                    ctx = "\n".join(
                        lines[max(0, idx - 1) : min(len(lines), idx + 3)]
                    )
                    # 十六进制转换: 0xa505 -> 42245, 0x8c87 -> 35975
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

        # 字节流兜底抽取双端 SubID
        if manifest_xml.exists() and (not subid_phone or not subid_pad):
            raw = manifest_xml.read_bytes()
            matches_u8 = [
                m.decode("ascii")
                for m in re.findall(rb"([0-9]{9})#[A-Fa-f0-9]{16}#", raw)
            ]
            matches_u16 = [
                m.replace(b"\x00", b"").decode("ascii")
                for m in re.findall(
                    rb"((?:[0-9]\x00){9})#\x00(?:[A-Fa-f0-9]\x00){16}#\x00",
                    raw,
                )
            ]
            all_found = matches_u8 + matches_u16
            if all_found:
                if not subid_phone:
                    subid_phone = all_found[0]
                if len(all_found) >= 2 and not subid_pad:
                    subid_pad = all_found[1]

        # 统合版本号
        if base_vname and build_no:
            clean_ver = f"{base_vname}.{build_no}"
        elif base_vname:
            clean_ver = base_vname
        else:
            clean_ver = fallback_ver

        extracted_pairs = []
        if subid_phone:
            extracted_pairs.append((subid_phone, "Phone"))
        if subid_pad:
            extracted_pairs.append((subid_pad, "Pad"))

        return extracted_pairs, "Android", clean_ver

    # ----------------------------------------------------
    # 2. 桌面端处理 (Win / Linux / macOS)
    # ----------------------------------------------------
    nodes = list(extract_dir.glob("**/major.node")) or list(
        extract_dir.glob("**/*.node")
    )
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

                clean_ver = fallback_ver
                m_already_4parts = re.search(
                    r"(\d+\.\d+\.\d+\.\d{4,6})", fallback_ver
                )
                if m_already_4parts:
                    clean_ver = m_already_4parts.group(1)
                else:
                    all_vers = pattern_ver.findall(data)
                    for v_base, v_build in all_vers:
                        v_str = (
                            f"{v_base.decode('ascii')}.{v_build.decode('ascii')}"
                        )
                        clean_ver = v_str
                        break

                return [(subid, default_arch)], plat, clean_ver
        except Exception:
            pass

    return [], "Unknown", fallback_ver


def extract_version_from_filename(filename):
    """从真实文件名提炼基础版本号"""
    m4 = re.search(r"(\d+\.\d+\.\d+)[-._](\d{4,6})", filename)
    if m4:
        return f"{m4.group(1)}.{m4.group(2)}"

    m3 = re.search(r"(\d+\.\d+\.\d+(?:\(\d+\))?)", filename)
    if m3:
        return m3.group(1)

    return None


def run_extraction_pipeline(tasks):
    """循环下载、解包、提取流水线（支持双端 SubID、接力限时与自愈）"""
    print("[阶段 3] 开始执行批量下载与 SubID 提取流水线...", flush=True)

    subid_map = load_and_sanitize_database()
    processed_urls = {item.get("source_url") for item in subid_map.values()}
    total_count = len(tasks)

    start_loop_time = time.time()

    for idx, t in enumerate(tasks, start=1):
        elapsed_min = (time.time() - start_loop_time) / 60
        if elapsed_min >= MAX_RUNTIME_MINUTES:
            print(
                f"\n[安全停机] 本次运行已达 {elapsed_min:.1f} 分钟，主动暂停保存当前进度，准备触发下一次接力！",
                flush=True,
            )
            break

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

            # 3. 提取 SubID 与权威版本 (支持多条 SubID 返回)
            subid_pairs, detected_platform, unified_version = (
                extract_subids_and_version(
                    TEMP_EXTRACT, downloaded_file, t["arch"], base_version
                )
            )

            if subid_pairs:
                final_platform = (
                    detected_platform
                    if t["platform"] == "Unknown"
                    else t["platform"]
                )

                if t.get("is_beta") and not unified_version.endswith("-Beta"):
                    unified_version = f"{unified_version}-Beta"

                for subid_val, arch_val in subid_pairs:
                    print(
                        f"[提取成功] 平台: {final_platform} | 架构: {arch_val:<6} | 统合版本: {unified_version} | SubID: {subid_val}",
                        flush=True,
                    )
                    subid_map[subid_val] = {
                        "version": unified_version,
                        "platform": final_platform,
                        "arch": arch_val,
                        "date": t["date"],
                        "source_url": t["url"],
                    }

                save_database_atomically(subid_map)
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

    remaining = [t for t in tasks if t["url"] not in processed_urls]
    if remaining:
        MORE_FLAG.write_text(f"remaining={len(remaining)}\n")
        print(
            f"\n[任务标记] 仍有 {len(remaining)} 个任务尚未处理完成，已写入接力标记。",
            flush=True,
        )
    else:
        if MORE_FLAG.exists():
            MORE_FLAG.unlink()
        print("\n[全部完成] 历史全量任务已全部处理完毕！", flush=True)

    print("\n" + "=" * 65, flush=True)
    print(
        f"[本批次结束] 已成功持久化保存至 data/qq_subid_map.json",
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
