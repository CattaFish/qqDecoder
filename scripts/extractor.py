# -*- coding: utf-8 -*-
import os
import re
import shutil
import subprocess
from pathlib import Path

SINGLE_DOWNLOAD_TIMEOUT = 600

# 采用标准安卓系统下载器 UA，彻底杜绝各大厂商对 PC 浏览器的异常重定向
ANDROID_DOWNLOAD_UA = "AndroidDownloadManager/13 (Linux; U; Android 13; Build/TQ3A.230901.001)"


def validate_date_str(date_str):
    """严格校验物理公历日期合法性 (彻底杜绝 97 月等伪日期)"""
    if not date_str:
        return None
    try:
        clean = re.sub(r"[年/.]", "-", str(date_str)).replace("月", "-").replace("日", "").strip()
        parts = clean.split("-")
        if len(parts) >= 3:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            if 2020 <= y <= 2035 and 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:
        pass
    return None


def download_target_file(url, target_dir):
    """跨市场通用静默下载，自动根据域名匹配防盗链 Referer 并使用安卓下载器 UA"""
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
            elif "heytap" in url or "oppo.com" in url:
                referer = "https://store.oppomobile.com/"
            elif "market.xiaomi.com" in url or "mi.com" in url:
                referer = "https://app.mi.com/"
            elif "hihonor" in url or "honor" in url:
                referer = "https://appmarket.hihonor.com/"
            elif "samsung" in url or "galaxyappstore.com" in url:
                referer = "https://galaxyappstore.com/"

            cmd = [
                "curl",
                "-fL",
                "-s",
                "-S",
                "--no-progress-meter",
                "-A",
                ANDROID_DOWNLOAD_UA,
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


def extract_subids_and_version(extract_dir, downloaded_file_path, default_arch, fallback_ver="Unknown"):
    """自适应提取 SubID 列表和统合版本号"""
    fname_str = str(downloaded_file_path).lower()

    if fname_str.endswith(".apk") or (extract_dir / "AndroidManifest.xml").exists():
        subid_phone, subid_pad, base_vname, build_no = None, None, None, None
        aapt_bin = get_aapt_cmd()
        dump_text = ""
        if aapt_bin and downloaded_file_path.exists():
            try:
                proc = subprocess.run(
                    [aapt_bin, "dump", "xmltree", str(downloaded_file_path), "AndroidManifest.xml"],
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
                    if m_vn: base_vname = m_vn.group(1).strip()
                if "AppSetting_params" in line and not subid_phone:
                    if "AppSetting_params_pad" not in line:
                        ctx = "\n".join(lines[max(0, idx - 1): min(len(lines), idx + 3)])
                        m_sub = re.search(r"([0-9]{9})#", ctx)
                        if m_sub: subid_phone = m_sub.group(1)
                if "AppSetting_params_pad" in line and not subid_pad:
                    ctx = "\n".join(lines[max(0, idx - 1): min(len(lines), idx + 3)])
                    m_sub = re.search(r"([0-9]{9})#", ctx)
                    if m_sub: subid_pad = m_sub.group(1)
                if "ANDROID_QQ_BUILD_NO" in line and not build_no:
                    ctx = "\n".join(lines[max(0, idx - 1): min(len(lines), idx + 3)])
                    m_hex = re.search(r"\(type 0x10\)0x([0-9a-fA-F]+)", ctx)
                    if m_hex: build_no = str(int(m_hex.group(1), 16))
                    if not build_no:
                        m_dec = re.search(r"\(type 0x10\)([0-9]+)", ctx)
                        if m_dec: build_no = m_dec.group(1)
                    if not build_no:
                        m_str = re.search(r'(?:Raw:\s*"|=")([0-9]+)"', ctx) or re.search(r'"([0-9]+)"', ctx)
                        if m_str: build_no = m_str.group(1)

        raw_v = base_vname or fallback_ver
        m_v3 = re.search(r"(\d+\.\d+\.\d+)", raw_v)
        base_3parts = m_v3.group(1) if m_v3 else raw_v
        clean_ver = f"{base_3parts}.{build_no}" if build_no else base_3parts

        pairs = []
        if subid_phone: pairs.append((subid_phone, "Phone"))
        if subid_pad: pairs.append((subid_pad, "Pad"))
        return pairs, "Android", clean_ver

    # 桌面端 NT 处理
    nodes = list(extract_dir.glob("**/major.node")) or list(extract_dir.glob("**/*.node"))
    pattern_new = re.compile(rb"QQAppId/([0-9]+)")
    pattern_cand = re.compile(rb"(?<![0-9])(537[0-9]{6})(?![0-9])")
    m_cur_build = re.search(r"\d{4,6}$", fallback_ver)
    cur_build_bytes = m_cur_build.group(0).encode("ascii") if m_cur_build else None

    for node in nodes:
        try:
            data = node.read_bytes()
            subid = None
            m_id = pattern_new.findall(data)
            if m_id:
                subid = m_id[0].decode("ascii")
            else:
                candidates = []
                for match in pattern_cand.finditer(data):
                    cand_subid = match.group(1).decode("ascii")
                    context = data[max(0, match.start() - 500): min(len(data), match.end() + 500)]
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
                plat = "macOS" if (".app" in path_str or "macos" in path_str) else "Linux" if ("opt" in path_str or "linux" in path_str) else "Windows"
                m_already = re.search(r"(\d+\.\d+\.\d+\.\d{4,6})", fallback_ver)
                clean_ver = m_already.group(1) if m_already else fallback_ver
                return [(subid, default_arch)], plat, clean_ver
        except Exception:
            pass
    return [], "Unknown", fallback_ver


def extract_version_from_filename(filename):
    m4 = re.search(r"(\d+\.\d+\.\d+)[-._](\d{4,6})", filename)
    if m4: return f"{m4.group(1)}.{m4.group(2)}"
    m3 = re.search(r"(\d+\.\d+\.\d+(?:\(\d+\))?)", filename)
    if m3: return m3.group(1)
    return None
