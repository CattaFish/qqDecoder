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

# 渠道与数据源定义
TG_CHANNEL_URL = "https://t.me/s/qq_updatelog"
WANDOUJIA_URL = "https://wap.pp.cn/app_zYoOzyuL/"
YYB_URL = "https://a.app.qq.com/o/simple.jsp?pkgname=com.tencent.mobileqq"
VIVO_PACKAGE_URL = "https://main.appstore.vivo.com.cn/port/package/"
VIVO_DOWNLOAD_URL = "https://appstore.vivo.com.cn/appinfo/downloadApkFile?id=40439"
HUAWEI_API_URL = "https://store-drcn.hispace.dbankcloud.com/hwmarket/api/clientApi"

DATA_FILE = Path("data/qq_subid_map.json")
BAK_FILE = Path("data/qq_subid_map.json.bak")
WDJ_VER_FILE = Path("data/wandoujia_latest_version.txt")
YYB_VER_FILE = Path("data/yingyongbao_latest_version.txt")
VIVO_VER_FILE = Path("data/vivo_latest_version.txt")
HUAWEI_VER_FILE = Path("data/huawei_latest_version.txt")

TEMP_DL = Path("temp_dl")
TEMP_EXTRACT = Path("temp_extracted")
SINGLE_DOWNLOAD_TIMEOUT = 600

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    )
}


def validate_date_str(date_str):
    """严格校验物理公历日期合法性 (彻底杜绝 97 月等伪日期)"""
    if not date_str:
        return None
    try:
        clean = re.sub(r"[年/.]", "-", str(date_str)).replace("月", "-").replace("日", "").strip()
        parts = clean.split("-")
        if len(parts) >= 3:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            # 年份在 2020~2035 之间，月份在 1~12 之间，日在 1~31 之间
            if 2020 <= y <= 2035 and 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:
        pass
    return None


# =====================================================================
# 1. 华为应用市场 (HiSpace) 监控模块
# =====================================================================
def get_huawei_info():
    """获取华为应用市场 (HiSpace) 的版本信息与下载直链"""
    print("[华为监控] 正在向官方接口查询线上版本...")
    try:
        headers = {
            "User-Agent": "HiSpace##16.7.1.300##CatGril##MeeowPhone",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json",
        }
        data = {
            "method": "client.appDetailById",
            "serviceType": "0",
            "ver": "1.1",
            "sign": "CatGirl",
            "id": "C9319",
            "package": "com.tencent.mobileqq",
            "source": "autoList",
            "deviceSpecParams": json.dumps({"abis": "arm64-v8a,armeabi-v7a,armeabi"}),
        }
        resp = requests.post(HUAWEI_API_URL, headers=headers, data=data, timeout=20)
        if resp.status_code != 200:
            print(f"[华为监控] 请求接口失败: HTTP {resp.status_code}")
            return None

        res_json = resp.json()
        detail_list = res_json.get("detailInfo")
        if not detail_list or not isinstance(detail_list, list) or len(detail_list) == 0:
            print(f"[华为监控] 响应无有效 detailInfo 数据: {res_json}")
            return None

        item = detail_list[0]
        version_name = str(item.get("versionName", "")).strip()
        release_date = str(item.get("releaseDate", "")).strip()
        download_url = str(item.get("url", "")).strip()

        date_str = validate_date_str(release_date) or time.strftime("%Y-%m-%d")

        if not version_name or not download_url:
            print(f"[华为监控] 未能提取到版本号或直链: ver={version_name}")
            return None

        return {
            "version": version_name,
            "date": date_str,
            "download_url": download_url,
            "channel": "华为",
        }
    except Exception as e:
        print(f"[华为监控] 查询异常: {e}")
        return None


# =====================================================================
# 2. vivo 应用商店监控模块 (基于官方开放 Package 接口)
# =====================================================================
def get_vivo_info():
    """获取 vivo 应用商店 QQ 的版本信息与下载直链"""
    print("[vivo监控] 正在向官方接口查询线上版本...")
    try:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": (
                "AndroidAppStore/13 (Linux; U; Android 13; TrebleDroid vanilla Build/TQ3A.230901.001)"
            )
        }
        body_data = "id=40439&app_version=55910&versioncode=16410&content_complete=1&opFlags=2147219326&opFlagV2=____f9__3wc"

        resp = requests.post(VIVO_PACKAGE_URL, headers=headers, data=body_data, timeout=20)
        if resp.status_code != 200:
            print(f"[vivo监控] 请求接口失败: HTTP {resp.status_code}")
            return None

        res_json = resp.json()
        val = res_json.get("value")
        if not val or not isinstance(val, dict):
            print(f"[vivo监控] 响应无有效 value 数据: {res_json}")
            return None

        version_name = str(val.get("version_name", "")).strip()
        upload_time = str(val.get("upload_time", "")).strip()
        dl_url = str(val.get("download_url", "")).strip() or VIVO_DOWNLOAD_URL

        date_str = validate_date_str(upload_time.split(" ")[0]) if upload_time else None
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        if not version_name:
            print("[vivo监控] 未能获取到 version_name")
            return None

        return {
            "version": version_name,
            "date": date_str,
            "download_url": dl_url,
            "channel": "vivo",
        }
    except Exception as e:
        print(f"[vivo监控] 查询异常: {e}")
        return None


# =====================================================================
# 3. 腾讯应用宝 (微下载) 监控与解析模块
# =====================================================================
def get_yingyongbao_info():
    """获取腾讯应用宝页面的版本信息、更新日期及“通过第三方浏览器下载”直链"""
    print("[应用宝监控] 正在检索微下载页面...")
    try:
        headers = dict(DEFAULT_HEADERS)
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

        resp = requests.get(YYB_URL, headers=headers, timeout=20)
        if resp.status_code != 200:
            print(f"[应用宝监控] 请求网页失败: HTTP {resp.status_code}")
            return None

        html_text = resp.text
        soup = BeautifulSoup(html_text, "html.parser")

        # 1. 提取版本号
        version = None
        m_ver = re.search(r"版本[：:\s]*\s*([0-9]+(?:\.[0-9]+)+)", html_text)
        if m_ver:
            version = m_ver.group(1).strip()

        # 2. 提取“通过第三方浏览器下载”的 APK 直链
        download_url = None
        for tag in soup.find_all(["a", "div", "button", "span"]):
            t_text = tag.get_text().strip()
            if "通过第三方浏览器下载" in t_text or "第三方浏览器" in t_text:
                url_cand = tag.get("href") or tag.get("data-apkurl") or tag.get("data-url") or tag.get("data-download")
                if url_cand and ("http://" in url_cand or "https://" in url_cand or url_cand.startswith("//")):
                    download_url = url_cand
                    break

        if not download_url:
            m_apk = re.search(r'["\']?(?:apkUrl|downUrl|downloadUrl|apk_url)["\']?\s*[:=]\s*["\'](https?://[^"\']+\.apk[^"\']*)["\']', html_text)
            if m_apk:
                download_url = m_apk.group(1).strip()

        if not download_url:
            m_any_apk = re.search(r'https?://[a-zA-Z0-9_.-]+(?:myapp\.com|qq\.com)[^"\';\s<>]+\.apk[^"\';\s<>]*', html_text)
            if m_any_apk:
                download_url = m_any_apk.group(0).strip()

        if not download_url:
            try:
                d_resp = requests.get("https://sj.qq.com/appdetail/com.tencent.mobileqq", headers=headers, timeout=10)
                if d_resp.status_code == 200:
                    m_d_apk = re.search(r'["\']?(?:apkUrl|url|download_url)["\']?\s*[:=]\s*["\'](https?://[^"\']+\.apk[^"\']*)["\']', d_resp.text)
                    if m_d_apk:
                        download_url = m_d_apk.group(1).strip()
            except Exception:
                pass

        if download_url and download_url.startswith("//"):
            download_url = "https:" + download_url

        # 3. 严格提取合规公历日期 (4重校验，彻底根除 2011-97-12 假日期)
        date_str = None

        # 策略 A: 必须伴随“更新/发布时间”关键词，并进行物理日历校验
        m_ctx = re.search(
            r"(?:更新时间|发布时间|更新日期|时间)[：:\s]*\s*([12]\d{3}[-/年.](?:0?[1-9]|1[0-2])[-/月.](?:0?[1-9]|[12]\d|3[01]))",
            html_text
        )
        if m_ctx:
            date_str = validate_date_str(m_ctx.group(1))

        # 策略 B: 嗅探页面时间戳变量 (10位或13位)
        if not date_str:
            m_ts = re.search(r'["\']?(?:apkPublishTime|publishTime|updateTime)["\']?\s*[:=]\s*["\']?(\d{10,13})["\']?', html_text)
            if m_ts:
                raw_ts = int(m_ts.group(1))
                if raw_ts > 10000000000:
                    raw_ts = raw_ts // 1000
                date_str = validate_date_str(time.strftime("%Y-%m-%d", time.localtime(raw_ts)))

        # 策略 C: 直接向安装包直链发 HEAD 请求，读取腾讯 CDN 的实际构建上传时间 (Last-Modified)
        if not date_str and download_url:
            try:
                head_resp = requests.head(download_url, headers=headers, timeout=5, allow_redirects=True)
                last_mod = head_resp.headers.get("Last-Modified")
                if last_mod:
                    gmt_time = time.strptime(last_mod, "%a, %d %b %Y %H:%M:%S GMT")
                    date_str = validate_date_str(time.strftime("%Y-%m-%d", gmt_time))
            except Exception:
                pass

        # 策略 D: 兜底为当前系统日期
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        if not version or not download_url:
            print(f"[应用宝监控] 解析异常: 版本={version}, 下载URL={download_url}")
            return None

        return {
            "version": version,
            "date": date_str,
            "download_url": download_url,
            "channel": "应用宝",
        }
    except Exception as e:
        print(f"[应用宝监控] 抓取失败: {e}")
        return None


# =====================================================================
# 4. 豌豆荚 (PP助手) 监控与解析模块
# =====================================================================
def get_wandoujia_info():
    """获取豌豆荚页面的版本信息、更新日期及普通下载直链"""
    print("[豌豆荚监控] 正在抓取豌豆荚详情页...")
    try:
        resp = requests.get(WANDOUJIA_URL, headers=DEFAULT_HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"[豌豆荚监控] 请求网页失败: HTTP {resp.status_code}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        version = None
        text_content = soup.get_text()
        m_ver = re.search(r"版本信息[：:\s]*\n*([0-9]+(?:\.[0-9]+)+)", text_content)
        if m_ver:
            version = m_ver.group(1).strip()

        date_str = None
        m_date = re.search(r"更新时间[：:\s]*\n*([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2})", text_content)
        if m_date:
            date_str = validate_date_str(m_date.group(1))

        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

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
            print(f"[豌豆荚监控] 解析异常: 版本={version}, 下载URL={download_url}")
            return None

        download_url = urljoin(WANDOUJIA_URL, download_url.strip())

        return {
            "version": version,
            "date": date_str,
            "download_url": download_url,
            "channel": "豌豆荚",
        }
    except Exception as e:
        print(f"[豌豆荚监控] 抓取失败: {e}")
        return None


# =====================================================================
# 5. Telegram 频道解析模块 (兼容 Markdown 超链接与新格式)
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
        print(f"[TG 监控] 无法连接到 Telegram 频道: HTTP {resp.status_code}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    msg_divs = [
        div
        for div in soup.find_all("div", class_="tgme_widget_message")
        if div.get("data-post") and "/" in div.get("data-post")
    ]
    if not msg_divs:
        print("[TG 监控] 未找到任何消息节点")
        return None

    latest_msg = msg_divs[-1]
    time_elem = latest_msg.find("time")
    raw_date = time_elem["datetime"][:10] if time_elem else None
    date_str = validate_date_str(raw_date) or "Unknown"
    text_elem = latest_msg.find("div", class_="tgme_widget_message_text")

    return {
        "post_id": latest_msg.get("data-post"),
        "date": date_str,
        "html": str(text_elem) if text_elem else "",
        "text": text_elem.get_text() if text_elem else "",
    }


def parse_tasks_from_message(msg):
    """解析 TG 消息包含的所有架构直链"""
    text = msg["text"]
    html = msg["html"]
    date = msg["date"]

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
        r"QQ(?:_NT)?\s*[:：]?\s*([0-9]+(?:\.[0-9]+)+(?:\([0-9]+\))?)", text
    )
    if m_android:
        android_ver = m_android.group(1)

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
        soup = BeautifulSoup(html, "html.parser")
        a_tags = soup.find_all("a", href=True)

        backup_links = []
        official_links = []

        for a in a_tags:
            href = a["href"].strip()
            a_text = a.get_text().strip()

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
# 6. 跨市场通用静默下载与解包抽取
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

            referer = "https://wap.pp.cn/"
            if "vivo.com" in url:
                referer = "https://appstore.vivo.com.cn/"
            elif "qq.com" in url or "myapp.com" in url:
                referer = "https://a.app.qq.com/"
            elif "dbankcloud.com" in url or "huawei.com" in url:
                referer = "https://appgallery.huawei.com/"

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
                referer,
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
# 7. 主控流程 (五源联合全量轮询：TG官方 + 豌豆荚 + 腾讯应用宝 + vivo + 华为)
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
        print(f"[豌豆荚监控] 线上版本: {cur_wdj_ver} | 本地记录版本: {wdj_recorded_ver or '无'}")

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

    # -------------------------------------------------------------
    # C. 检查 腾讯应用宝 (微下载) 渠道更新
    # -------------------------------------------------------------
    yyb_recorded_ver = ""
    if YYB_VER_FILE.exists():
        yyb_recorded_ver = YYB_VER_FILE.read_text(encoding="utf-8").strip()

    yyb_info = get_yingyongbao_info()
    yyb_new_ver_to_record = None

    if yyb_info:
        cur_yyb_ver = yyb_info["version"]
        print(f"[应用宝监控] 线上版本: {cur_yyb_ver} | 本地记录版本: {yyb_recorded_ver or '无'}")

        if not yyb_recorded_ver or yyb_recorded_ver != cur_yyb_ver:
            print(f"[应用宝监控] 检测到新版本/首发收录: {cur_yyb_ver}，加入待处理任务！")
            pending_tasks.append(
                {
                    "platform": "Android",
                    "arch": "Package",
                    "version": cur_yyb_ver,
                    "date": yyb_info["date"],
                    "url": yyb_info["download_url"],
                    "is_beta": False,
                    "channel": "应用宝",
                    "mark_yyb_version": cur_yyb_ver,
                }
            )
        else:
            print("[应用宝监控] 线上版本与记录一致，无需下载。")

    # -------------------------------------------------------------
    # D. 检查 vivo 应用商店 渠道更新
    # -------------------------------------------------------------
    vivo_recorded_ver = ""
    if VIVO_VER_FILE.exists():
        vivo_recorded_ver = VIVO_VER_FILE.read_text(encoding="utf-8").strip()

    vivo_info = get_vivo_info()
    vivo_new_ver_to_record = None

    if vivo_info:
        cur_vivo_ver = vivo_info["version"]
        print(f"[vivo监控] 线上版本: {cur_vivo_ver} | 本地记录版本: {vivo_recorded_ver or '无'}")

        if not vivo_recorded_ver or vivo_recorded_ver != cur_vivo_ver:
            print(f"[vivo监控] 检测到新版本/首发收录: {cur_vivo_ver}，加入待处理任务！")
            pending_tasks.append(
                {
                    "platform": "Android",
                    "arch": "Package",
                    "version": cur_vivo_ver,
                    "date": vivo_info["date"],
                    "url": vivo_info["download_url"],
                    "is_beta": False,
                    "channel": "vivo",
                    "mark_vivo_version": cur_vivo_ver,
                }
            )
        else:
            print("[vivo监控] 线上版本与记录一致，无需下载。")

    # -------------------------------------------------------------
    # E. 检查 华为应用市场 渠道更新
    # -------------------------------------------------------------
    huawei_recorded_ver = ""
    if HUAWEI_VER_FILE.exists():
        huawei_recorded_ver = HUAWEI_VER_FILE.read_text(encoding="utf-8").strip()

    huawei_info = get_huawei_info()
    huawei_new_ver_to_record = None

    if huawei_info:
        cur_huawei_ver = huawei_info["version"]
        print(f"[华为监控] 线上版本: {cur_huawei_ver} | 本地记录版本: {huawei_recorded_ver or '无'}")

        if not huawei_recorded_ver or huawei_recorded_ver != cur_huawei_ver:
            print(f"[华为监控] 检测到新版本/首发收录: {cur_huawei_ver}，加入待处理任务！")
            pending_tasks.append(
                {
                    "platform": "Android",
                    "arch": "Package",
                    "version": cur_huawei_ver,
                    "date": huawei_info["date"],
                    "url": huawei_info["download_url"],
                    "is_beta": False,
                    "channel": "华为",
                    "mark_huawei_version": cur_huawei_ver,
                }
            )
        else:
            print("[华为监控] 线上版本与记录一致，无需下载。")

    if not pending_tasks:
        print("[状态] 遍历完毕，所有渠道均无新版本。")
        sys.exit(0)

    # -------------------------------------------------------------
    # F. 读取已有数据库并做 URL 去重排队
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
                if t.get("mark_yyb_version"):
                    yyb_new_ver_to_record = t["mark_yyb_version"]
                if t.get("mark_vivo_version"):
                    vivo_new_ver_to_record = t["mark_vivo_version"]
                if t.get("mark_huawei_version"):
                    huawei_new_ver_to_record = t["mark_huawei_version"]

                if plat == "Android" and t.get("channel") == "官方":
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
    # G. 原子落盘主数据库 data/qq_subid_map.json
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
    # H. 同步派生编译出运行时字典 data/subid.json
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
    # I. 核心要求：在所有 SubID 全部提取处理完成之后，最后保存版本文件
    # -------------------------------------------------------------
    if wdj_new_ver_to_record:
        WDJ_VER_FILE.parent.mkdir(parents=True, exist_ok=True)
        WDJ_VER_FILE.write_text(wdj_new_ver_to_record.strip(), encoding="utf-8")
        print(f"[版本记录] 豌豆荚最新版本 {wdj_new_ver_to_record} 已成功写入 {WDJ_VER_FILE}")

    if yyb_new_ver_to_record:
        YYB_VER_FILE.parent.mkdir(parents=True, exist_ok=True)
        YYB_VER_FILE.write_text(yyb_new_ver_to_record.strip(), encoding="utf-8")
        print(f"[版本记录] 应用宝最新版本 {yyb_new_ver_to_record} 已成功写入 {YYB_VER_FILE}")

    if vivo_new_ver_to_record:
        VIVO_VER_FILE.parent.mkdir(parents=True, exist_ok=True)
        VIVO_VER_FILE.write_text(vivo_new_ver_to_record.strip(), encoding="utf-8")
        print(f"[版本记录] vivo最新版本 {vivo_new_ver_to_record} 已成功写入 {VIVO_VER_FILE}")

    if huawei_new_ver_to_record:
        HUAWEI_VER_FILE.parent.mkdir(parents=True, exist_ok=True)
        HUAWEI_VER_FILE.write_text(huawei_new_ver_to_record.strip(), encoding="utf-8")
        print(f"[版本记录] 华为最新版本 {huawei_new_ver_to_record} 已成功写入 {HUAWEI_VER_FILE}")

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
