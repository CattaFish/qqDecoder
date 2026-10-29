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

# 引入运行时字典编译器
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from subid_compiler import compile_subid_map_to_runtime_dict
except ImportError:
    compile_subid_map_to_runtime_dict = None

# 强制 Python 标准输出和错误流实时无缓冲刷新
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# -----------------------------------------------------------------
# 永久黑名单与跳过列表（确认卡死、损坏或无法拉取的备份链接）
# -----------------------------------------------------------------
BLACKLIST_URLS = [
    "https://t.me/linqiqi_backup/1972",
    "https://t.me/linqiqi_backup/1917",
    "https://t.me/linqiqi_backup/1664",
]

# 人工强制重跑列表
FORCE_REPROCESS_PATTERNS = []

# 极端情况人工指定映射
MANUAL_SUBID_OVERRIDES = {}

CHANNEL_URL = "https://t.me/s/qq_updatelog"
DATA_FILE = Path("data/qq_subid_map.json")
BAK_FILE = Path("data/qq_subid_map.json.bak")
MORE_FLAG = Path("data/.has_more")
TEMP_DL = Path("temp_dl")
TEMP_EXTRACT = Path("temp_extracted")

START_DATE = os.environ.get("START_DATE", "2025-01-01")
MAX_RUNTIME_MINUTES = int(os.environ.get("MAX_RUNTIME_MINUTES", "280"))
SINGLE_DOWNLOAD_TIMEOUT = int(os.environ.get("SINGLE_DOWNLOAD_TIMEOUT", "600"))


def is_bad_item(s_id, item):
    """判断单个条目是否为异常坏数据"""
    ver = str(item.get("version", ""))
    plat = str(item.get("platform", ""))
    arch = str(item.get("arch", ""))
    url = str(item.get("source_url", ""))

    for pat in FORCE_REPROCESS_PATTERNS:
        if pat and pat in url:
            return True

    if not s_id or not str(s_id).isdigit() or not ver or ver == "Unknown":
        return True
    if re.search(r"[\u4e00-\u9fa5]", ver):
        return True
    if re.search(r"\d{10,}", ver):
        return True

    # Android 规则：兼容 3 段式与 4 段式
    if plat == "Android":
        pure_v = ver.replace("-Beta", "").strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d{4,6})?", pure_v):
            return True
        if arch == "Universal":
            return True

    # 桌面端基本规范
    if plat in ("Windows", "macOS", "Linux"):
        pure_v = ver.replace("-Beta", "").strip()
        if not re.search(r"\d+\.\d+\.\d+", pure_v):
            return True

    return False


def load_and_sanitize_database():
    """读取已有数据，平滑升级并自动补齐 is_beta 标记，清理残缺数据"""
    raw_map = {}
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    for f in [DATA_FILE, BAK_FILE]:
        if f.exists():
            try:
                raw_map = json.loads(f.read_text(encoding="utf-8"))
                break
            except Exception as e:
                print(
                    f"[警告] 读取 {f.name} 失败 ({e})，尝试备用文件...", flush=True
                )

    if not raw_map:
        return {}

    cleaned_map = {}
    purged_count = 0

    for s_id, val in raw_map.items():
        items = val if isinstance(val, list) else [val]
        valid_items = []

        for item in items:
            if is_bad_item(s_id, item):
                purged_count += 1
                print(
                    f"[自愈清理] 移除残缺/重跑项: SubID={s_id} | 版本={item.get('version')} | 来源={item.get('source_url')}",
                    flush=True,
                )
            else:
                # 自动为存量历史记录补齐 is_beta 标记
                if "is_beta" not in item:
                    v = str(item.get("version", ""))
                    u = str(item.get("source_url", ""))
                    item["is_beta"] = bool("-beta" in v.lower() or "beta" in v.lower() or "beta" in u.lower())
                valid_items.append(item)

        if valid_items:
            cleaned_map[s_id] = valid_items

    if purged_count > 0:
        print(
            f"[自愈完成] 历史数据体检完毕，已清理 {purged_count} 条数据进入重跑队列。\n",
            flush=True,
        )
        save_database_atomically(cleaned_map)

    return cleaned_map


def save_database_atomically(data_map):
    """原子写盘与备份，并同步派生编译 subid.json"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(".tmp")
    json_str = json.dumps(data_map, indent=2, ensure_ascii=False)

    temp_file.write_text(json_str, encoding="utf-8")
    if DATA_FILE.exists():
        shutil.copy2(DATA_FILE, BAK_FILE)
    temp_file.replace(DATA_FILE)

    # 同步派生运行时字典
    if compile_subid_map_to_runtime_dict:
        try:
            compiled = compile_subid_map_to_runtime_dict(data_map)
            runtime_file = DATA_FILE.parent / "subid.json"
            temp_rt = runtime_file.with_suffix(".tmp")
            temp_rt.write_text(json.dumps(compiled, indent=2, ensure_ascii=False), encoding="utf-8")
            temp_rt.replace(runtime_file)
        except Exception:
            pass


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
    """提取任务清单，严格优先提取备份直链并剔除黑名单"""
    print("[阶段 2] 正在分析提取文件下载链接（优先提取备份）...", flush=True)
    tasks = []

    for msg in messages:
        text = msg["text"]
        html = msg["html"]
        date = msg["date"]
        post_id = msg["post_id"]

        # 保持 Telegram 原则不变：识别 #Beta\b
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

        if not (win_ver or mac_ver or linux_ver or android_ver):
            lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
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

        has_desktop = any(p in text for p in ["Windows", "Linux", "MacOS", "Mac QQ_NT", "Linux QQ_NT", "Windows QQ_NT"])

        if has_desktop:
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
                    win_ver if current_platform == "Windows"
                    else mac_ver if current_platform == "macOS"
                    else linux_ver
                )

                line_a = line_soup.find_all("a", href=True)
                if not line_a:
                    continue

                backup_l = []
                official_l = []
                for a in line_a:
                    href = a["href"].strip()
                    a_txt = a.get_text().strip()
                    if any(b in href for b in BLACKLIST_URLS):
                        continue
                    if "t.me/linqiqi_backup" in href or "备份" in a_txt:
                        backup_l.append((href, a_txt))
                    elif any(ext in href.lower() for ext in [".exe", ".dmg", ".deb", ".rpm", ".appimage"]) or "官网" in a_txt:
                        official_l.append((href, a_txt))

                chosen = backup_l if backup_l else official_l
                for href, a_txt in chosen:
                    arch = a_txt.replace("官网链接", "").replace("频道备份", "").replace("官网", "").replace("备份", "").strip()
                    if not arch:
                        m_a = re.search(r"\b(x64|x86|arm64|arm|loongarch|mips)\b", line_text, re.IGNORECASE)
                        arch = m_a.group(1).upper() if m_a else "Package"
                    tasks.append({
                        "platform": current_platform,
                        "arch": arch or "Package",
                        "version": version or "Unknown",
                        "date": date,
                        "url": href,
                        "post_id": post_id,
                        "is_beta": is_beta,
                    })
        else:
            soup = BeautifulSoup(html, "html.parser")
            a_tags = soup.find_all("a", href=True)
            backup_l = []
            official_l = []

            for a in a_tags:
                href = a["href"].strip()
                a_txt = a.get_text().strip()
                if any(b in href for b in BLACKLIST_URLS) or "t.me/s/" in href or "t.me/qq_updatelog" in href:
                    continue
                if "t.me/linqiqi_backup" in href or "备份" in a_txt:
                    backup_l.append(href)
                elif ".apk" in href or "android_apk" in href or "downv6.qq.com" in href or "官网" in a_txt:
                    official_l.append(href)

            target_urls = backup_l if backup_l else official_l
            for u in list(dict.fromkeys(target_urls)):
                tasks.append({
                    "platform": "Android",
                    "arch": "Package",
                    "version": android_ver or "Unknown",
                    "date": date,
                    "url": u,
                    "post_id": post_id,
                    "is_beta": is_beta,
                })

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
    """带 600 秒硬超时的静默下载"""
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
    except subprocess.TimeoutExpired:
        print(
            f"\n[超时熔断] 单文件下载超过 {SINGLE_DOWNLOAD_TIMEOUT} 秒，强行跳过此任务！",
            flush=True,
        )
        return None
    except Exception as e:
        print(f"\n[下载抛出异常] {e}", flush=True)
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
    """自适应提取 SubID 列表和统合版本号"""
    fname_str = str(downloaded_file_path).lower()

    is_beta_hint = bool(
        "-beta" in str(fallback_ver).lower()
        or "beta" in str(fallback_ver).lower()
        or "beta" in fname_str
        or "rc" in fname_str
    )

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

        raw_v = base_vname or fallback_ver
        if "beta" in str(base_vname).lower():
            is_beta_hint = True

        m_v3 = re.search(r"(\d+\.\d+\.\d+)", raw_v)
        base_3parts = m_v3.group(1) if m_v3 else raw_v
        clean_ver = f"{base_3parts}.{build_no}" if build_no else base_3parts

        if is_beta_hint and not clean_ver.endswith("-Beta"):
            clean_ver = f"{clean_ver}-Beta"

        extracted_pairs = []
        if subid_phone:
            extracted_pairs.append((subid_phone, "Phone"))
        if subid_pad:
            extracted_pairs.append((subid_pad, "Pad"))

        return extracted_pairs, "Android", clean_ver

    # 桌面端处理
    nodes = list(extract_dir.glob("**/major.node")) or list(
        extract_dir.glob("**/*.node")
    )
    pattern_subid_new = re.compile(rb"QQAppId/([0-9]+)")
    pattern_ver = re.compile(rb"QQ(?:EX)?/(\d+\.\d+\.\d+)[-\.](\d+)")

    m_cur_build = re.search(r"\d{4,6}$", fallback_ver)
    cur_build_bytes = (
        m_cur_build.group(0).encode("ascii") if m_cur_build else None
    )

    for node in nodes:
        try:
            data = node.read_bytes()
            subid = None

            m_id = pattern_subid_new.findall(data)
            if m_id:
                subid = m_id[0].decode("ascii")
            else:
                pattern_candidate = re.compile(
                    rb"(?<![0-9])(537[0-9]{6})(?![0-9])"
                )
                candidates = []

                for match in pattern_candidate.finditer(data):
                    cand_subid = match.group(1).decode("ascii")
                    start_pos = match.start()
                    end_pos = match.end()

                    ctx_start = max(0, start_pos - 500)
                    ctx_end = min(len(data), end_pos + 500)
                    context = data[ctx_start:ctx_end]

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

                if is_beta_hint and not clean_ver.endswith("-Beta"):
                    clean_ver = f"{clean_ver}-Beta"

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


def run_extraction_pipeline(tasks):
    """循环下载、解包、提取流水线"""
    print("[阶段 3] 开始执行批量下载与 SubID 提取流水线...", flush=True)

    subid_map = load_and_sanitize_database()

    processed_urls = set()
    for val in subid_map.values():
        items = val if isinstance(val, list) else [val]
        for it in items:
            u = it.get("source_url")
            if u:
                processed_urls.add(u)

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

        if any(bad in t["url"] for bad in BLACKLIST_URLS):
            print(
                f"[{idx}/{total_count}] [黑名单跳过] 永久跳过已知损坏备份: {t['url']}",
                flush=True,
            )
            processed_urls.add(t["url"])
            continue

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

        override_key = f"{t['platform']}_{t['arch']}_{t['version']}"
        manual_override_subid = MANUAL_SUBID_OVERRIDES.get(override_key)
        if manual_override_subid:
            print(
                f"[人工指定命中] 直接采纳人工预设 SubID: {manual_override_subid}",
                flush=True,
            )
            is_beta_override = bool(t.get("is_beta") or "-beta" in t["version"].lower() or "beta" in t["version"].lower())
            new_item = {
                "version": t["version"],
                "platform": t["platform"],
                "arch": t["arch"],
                "date": t["date"],
                "source_url": t["url"],
                "is_beta": is_beta_override,
            }
            if manual_override_subid not in subid_map:
                subid_map[manual_override_subid] = [new_item]
            else:
                if isinstance(subid_map[manual_override_subid], dict):
                    subid_map[manual_override_subid] = [
                        subid_map[manual_override_subid]
                    ]
                subid_map[manual_override_subid].append(new_item)
            save_database_atomically(subid_map)
            processed_urls.add(t["url"])
            continue

        print("状态: 正在下载文件...", flush=True)

        if TEMP_DL.exists():
            shutil.rmtree(TEMP_DL)
        TEMP_DL.mkdir()

        proc = download_target_file(t["url"], TEMP_DL)
        if proc is None or proc.returncode != 0:
            err_msg = ""
            if proc and proc.stderr:
                err_msg = proc.stderr.strip()[:80]
            print(f"状态: [下载失败或超时，已跳过] {err_msg}", flush=True)
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

        if TEMP_EXTRACT.exists():
            shutil.rmtree(TEMP_EXTRACT)
        TEMP_EXTRACT.mkdir()

        try:
            lower_fname = fname.lower()
            if lower_fname.endswith(".deb"):
                res = subprocess.run(
                    [
                        "dpkg-deb",
                        "-x",
                        str(downloaded_file),
                        str(TEMP_EXTRACT),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if res.returncode != 0:
                    temp_deb_stage = Path(f"{TEMP_EXTRACT}_deb_raw")
                    if temp_deb_stage.exists():
                        shutil.rmtree(temp_deb_stage)
                    temp_deb_stage.mkdir(parents=True, exist_ok=True)

                    subprocess.run(
                        [
                            "7z",
                            "x",
                            str(downloaded_file),
                            f"-o{temp_deb_stage}",
                            "-y",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    data_tars = list(temp_deb_stage.glob("data.tar*"))
                    if data_tars:
                        subprocess.run(
                            [
                                "7z",
                                "x",
                                str(data_tars[0]),
                                f"-o{TEMP_EXTRACT}",
                                "-y",
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    if temp_deb_stage.exists():
                        shutil.rmtree(temp_deb_stage)

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
                    ["7z", "x", str(downloaded_file), f"-o{TEMP_EXTRACT}", "-y"],
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

                is_beta_flag = bool(
                    t.get("is_beta")
                    or "-beta" in unified_version.lower()
                    or "beta" in unified_version.lower()
                    or "beta" in fname.lower()
                    or "beta" in t.get("url", "").lower()
                )
                if is_beta_flag and not unified_version.endswith("-Beta"):
                    unified_version = f"{unified_version}-Beta"

                for subid_val, arch_val in subid_pairs:
                    new_item = {
                        "version": unified_version,
                        "platform": final_platform,
                        "arch": arch_val,
                        "date": t["date"],
                        "source_url": t["url"],
                        "is_beta": is_beta_flag,  # 显式打标写入
                    }

                    if subid_val not in subid_map:
                        subid_map[subid_val] = [new_item]
                        print(
                            f"[提取成功] 平台: {final_platform} | 架构: {arch_val:<6} | 统合版本: {unified_version} | Beta: {is_beta_flag} | SubID: {subid_val}",
                            flush=True,
                        )
                    else:
                        if isinstance(subid_map[subid_val], dict):
                            subid_map[subid_val] = [subid_map[subid_val]]

                        exist_urls = {
                            x.get("source_url") for x in subid_map[subid_val]
                        }
                        if t["url"] not in exist_urls:
                            subid_map[subid_val].append(new_item)
                            print(
                                f"[架构追加] 平台: {final_platform} | 架构: {arch_val:<6} | 统合版本: {unified_version} | Beta: {is_beta_flag} | SubID: {subid_val} (当前共关联 {len(subid_map[subid_val])} 项)",
                                flush=True,
                            )

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
