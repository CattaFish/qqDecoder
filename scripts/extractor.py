# -*- coding: utf-8 -*-
import os
import plistlib
import re
import shutil
import struct
import subprocess
from pathlib import Path

SINGLE_DOWNLOAD_TIMEOUT = 600

# 采用标准安卓系统下载器 UA
ANDROID_DOWNLOAD_UA = (
    "AndroidDownloadManager/13 (Linux; U; Android 13; Build/TQ3A.230901.001)"
)


def validate_date_str(date_str):
    """严格校验物理公历日期合法性 (彻底杜绝 97 月等伪日期)"""
    if not date_str:
        return None
    try:
        clean = (
            re.sub(r"[年/.]", "-", str(date_str))
            .replace("月", "-")
            .replace("日", "")
            .strip()
        )
        parts = clean.split("-")
        if len(parts) >= 3:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            if 2020 <= y <= 2035 and 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:
        pass
    return None


def download_target_file(url, target_dir):
    """跨市场通用静默下载，强制 IPv4 解析与忽略老旧 SSL，智能适配防盗链 Referer"""
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
            if not fname or not ("." in fname):
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
            elif "appchina" in url or "yingyonghui" in url:
                referer = "https://m.appchina.com/"
            elif "baidu.com" in url:
                referer = "https://mobile.baidu.com/"
            elif "armconverter.com" in url:
                referer = "https://armconverter.com/decryptedappstore"

            cmd = [
                "curl",
                "-k",
                "-4",
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


def extract_ios_subids_from_macho(macho_path: Path):
    """从脱壳的 iOS Mach-O 二进制中全自动提取 (Phone_SubID, Pad_SubID)

    原理：逆向定位高频调用的 ADRP + ADD (差值4字节) 指针加载链，读取只读常量区 SubID
    """
    try:
        data = macho_path.read_bytes()
    except Exception:
        return []

    header = data[:32]
    if len(header) < 32:
        return []

    ncmds = struct.unpack("<I", header[16:20])[0]
    sections = []
    text_sec = None
    pos = 32

    for _ in range(ncmds):
        if pos + 8 > len(data):
            break
        cmd, cmdsize = struct.unpack("<II", data[pos : pos + 8])
        if cmd == 0x19:  # LC_SEGMENT_64
            nsects = struct.unpack("<I", data[pos + 64 : pos + 68])[0]
            sec_pos = pos + 72
            for s in range(nsects):
                if sec_pos + 80 > len(data):
                    break
                s_name = (
                    data[sec_pos : sec_pos + 16].rstrip(b"\x00").decode("latin1")
                )
                s_vm, s_size, s_off = struct.unpack(
                    "<QQI", data[sec_pos + 32 : sec_pos + 52]
                )
                sec_info = {"name": s_name, "vm": s_vm, "size": s_size, "off": s_off}
                sections.append(sec_info)
                if s_name == "__text":
                    text_sec = sec_info
                sec_pos += 80
        pos += cmdsize

    def vm2off(vm):
        for sec in sections:
            if sec["vm"] <= vm < sec["vm"] + sec["size"]:
                return sec["off"] + (vm - sec["vm"])
        return None

    if not text_sec:
        return []

    text_off = text_sec["off"]
    text_size = text_sec["size"]
    text_vm = text_sec["vm"]

    candidates = set()

    for i in range(text_off, min(text_off + text_size - 32, len(data) - 32), 4):
        insn1, insn2 = struct.unpack("<II", data[i : i + 8])
        if (insn1 & 0x9F000000) == 0x90000000 and (
            insn2 & 0x7F800000
        ) == 0x11000000:
            off1 = (insn2 >> 10) & 0xFFF
            for j in range(i + 8, i + 32, 4):
                insn3, insn4 = struct.unpack("<II", data[j : j + 8])
                if (insn3 & 0x9F000000) == 0x90000000 and (
                    insn4 & 0x7F800000
                ) == 0x11000000:
                    off2 = (insn4 >> 10) & 0xFFF
                    if abs(off1 - off2) == 4:
                        immlo = (insn1 >> 29) & 0x3
                        immhi = (insn1 >> 5) & 0x7FFFF
                        imm21 = (immhi << 2) | immlo
                        if imm21 & (1 << 20):
                            imm21 -= 1 << 21

                        pc = text_vm + (i - text_off)
                        page = (pc & ~0xFFF) + (imm21 << 12)

                        f1 = vm2off(page + min(off1, off2))
                        f2 = vm2off(page + max(off1, off2))

                        if f1 and f2 and f2 + 4 <= len(data):
                            v1 = struct.unpack("<I", data[f1 : f1 + 4])[0]
                            v2 = struct.unpack("<I", data[f2 : f2 + 4])[0]
                            if (
                                537000000 <= v1 <= 537999999
                                and 537000000 <= v2 <= 537999999
                            ):
                                if 0 < (v2 - v1) <= 50:
                                    candidates.add((v1, v2))

    if candidates:
        best_phone, best_pad = sorted(list(candidates), key=lambda x: x[0])[-1]
        return [(str(best_phone), "Phone"), (str(best_pad), "Pad")]

    return []


def extract_subids_and_version(
    extract_dir, downloaded_file_path, default_arch, fallback_ver="Unknown"
):
    """自适应提取全平台（Android / iOS / Windows / Linux / macOS）SubID 与统合版本号"""
    fname_str = str(downloaded_file_path).lower()

    is_beta_hint = bool(
        "-beta" in str(fallback_ver).lower()
        or "beta" in str(fallback_ver).lower()
        or "beta" in fname_str
        or "rc" in fname_str
    )

    # ----------------------------------------------------
    # 分支 1: Android 平台 (.apk)
    # ----------------------------------------------------
    if (
        fname_str.endswith(".apk")
        or (extract_dir / "AndroidManifest.xml").exists()
    ):
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
        if "beta" in str(base_vname).lower():
            is_beta_hint = True

        m_v3 = re.search(r"(\d+\.\d+\.\d+)", raw_v)
        base_3parts = m_v3.group(1) if m_v3 else raw_v
        clean_ver = f"{base_3parts}.{build_no}" if build_no else base_3parts

        if is_beta_hint and not clean_ver.endswith("-Beta"):
            clean_ver = f"{clean_ver}-Beta"

        pairs = []
        if subid_phone:
            pairs.append((subid_phone, "Phone"))
        if subid_pad:
            pairs.append((subid_pad, "Pad"))
        return pairs, "Android", clean_ver

    # ----------------------------------------------------
    # 分支 2: iOS 平台 (.ipa)
    # ----------------------------------------------------
    ios_machos = list(extract_dir.glob("**/Payload/*.app/QQ")) or list(
        extract_dir.glob("**/QQ.app/QQ")
    )
    if fname_str.endswith(".ipa") or ios_machos:
        plist_files = list(extract_dir.glob("**/Payload/*.app/Info.plist")) or list(
            extract_dir.glob("**/Info.plist")
        )
        short_ver, build_ver = None, None

        if plist_files:
            try:
                pl = plistlib.loads(plist_files[0].read_bytes())
                short_ver = str(pl.get("CFBundleShortVersionString", "")).strip()
                build_ver = str(pl.get("CFBundleVersion", "")).strip()
            except Exception:
                pass

        clean_ver = (
            build_ver
            if (build_ver and len(build_ver.split(".")) >= 4)
            else (short_ver or fallback_ver)
        )
        if is_beta_hint and not clean_ver.endswith("-Beta"):
            clean_ver = f"{clean_ver}-Beta"

        if ios_machos:
            pairs = extract_ios_subids_from_macho(ios_machos[0])
            if pairs:
                return pairs, "iOS", clean_ver

    # ----------------------------------------------------
    # 分支 3: 桌面端 NT (Windows / Linux / macOS)
    # ----------------------------------------------------
    nodes = list(extract_dir.glob("**/major.node")) or list(
        extract_dir.glob("**/*.node")
    )
    pattern_new = re.compile(rb"QQAppId/([0-9]+)")
    pattern_cand = re.compile(rb"(?<![0-9])(537[0-9]{6})(?![0-9])")
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
                    else (
                        "Linux"
                        if ("opt" in path_str or "linux" in path_str)
                        else "Windows"
                    )
                )
                m_already = re.search(r"(\d+\.\d+\.\d+\.\d{4,6})", fallback_ver)
                clean_ver = m_already.group(1) if m_already else fallback_ver
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
