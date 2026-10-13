import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from bs4 import BeautifulSoup
import requests

CHANNEL_URL = "https://t.me/s/qq_updatelog"
STATE_FILE = Path("data/last_message_id.txt")
TEMP_DIR = Path("temp_extracted")
WORK_DIR = Path("temp_download")


def get_latest_post():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
    }
    resp = requests.get(CHANNEL_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # 获取所有消息块
    messages = soup.find_all(
        "div",
        class_=lambda c: c
        and "tgme_widget_message" in c
        and "js-widget_message" in c,
    )
    if not messages:
        print("[错误] 未能获取到频道消息")
        return None, None, None

    # 取最后一条消息
    latest_msg = messages[-1]
    post_id = latest_msg.get("data-post")  # 例如 "qq_updatelog/1234"
    text_elem = latest_msg.find("div", class_="tgme_widget_message_text")

    return post_id, text_elem, latest_msg


def parse_download_targets(text_elem):
    """从消息 HTML 中解析出所有分类、架构及对应链接"""
    targets = []
    if not text_elem:
        return targets

    # 判断是否为 Android 单独推送
    apk_links = text_elem.find_all(
        "a", href=lambda h: h and ".apk" in h.lower()
    )
    if apk_links and (
        "Android" in text_elem.text or "QQ_NT_Phone" in text_elem.text
    ):
        for a in apk_links:
            targets.append(
                {
                    "platform": "Android",
                    "arch": "Universal",
                    "format": "apk",
                    "url": a["href"].strip(),
                }
            )
        return targets

    # 桌面多端推送逻辑：按行拆解
    # 只取“下载”与“备份”之间的内容，防止重复拉取备份网盘
    html_str = str(text_elem)
    download_section = html_str
    if "下载" in download_section:
        download_section = download_section.split("下载", 1)[1]
    if "备份" in download_section:
        download_section = download_section.split("备份", 1)[0]

    sub_soup = BeautifulSoup(download_section, "html.parser")

    current_platform = "Unknown"
    # 按行或者分段遍历
    text_lines = sub_soup.get_text("\n").split("\n")

    # 遍历所有超链接，根据它们前置的文本来判断属于哪个平台
    for a in sub_soup.find_all("a", href=True):
        href = a["href"].strip()
        arch_name = a.get_text().strip()
        if not href.startswith("http"):
            continue

        # 向上寻找关联的平台描述文本
        # 判断链接所处的前置标识
        parent_text = a.find_parent().get_text() if a.find_parent() else ""
        full_context = sub_soup.text

        # 简单稳健的后缀/名称推导规则
        fmt = "unknown"
        lower_href = href.lower()
        if ".exe" in lower_href:
            platform = "Windows"
            fmt = "exe"
        elif ".dmg" in lower_href:
            platform = "macOS"
            fmt = "dmg"
        elif (
            ".deb" in lower_href
            or ".rpm" in lower_href
            or ".appimage" in lower_href
        ):
            platform = "Linux"
            if ".deb" in lower_href:
                fmt = "deb"
            elif ".rpm" in lower_href:
                fmt = "rpm"
            elif ".appimage" in lower_href:
                fmt = "appimage"
        elif ".apk" in lower_href:
            platform = "Android"
            fmt = "apk"
        else:
            continue

        targets.append(
            {
                "platform": platform,
                "arch": arch_name,
                "format": fmt,
                "url": href,
            }
        )

    # 去重
    unique_targets = []
    seen = set()
    for t in targets:
        key = (t["platform"], t["arch"], t["url"])
        if key not in seen:
            seen.add(key)
            unique_targets.append(t)

    return unique_targets


def download_file(url, out_path):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
    }
    with requests.get(
        url, headers=headers, stream=True, timeout=120
    ) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def extract_package(pkg_path, fmt, extract_dir):
    extract_dir.mkdir(parents=True, exist_ok=True)
    pkg_str = str(pkg_path)

    if fmt == "apk":
        subprocess.run(
            ["7z", "x", pkg_str, f"-o{extract_dir}", "-y"],
            stdout=subprocess.DEVNULL,
            check=True,
        )
        # 尝试 aapt 转储明文
        aapt_proc = subprocess.run(
            ["which", "aapt"], capture_output=True, text=True
        )
        aapt_bin = aapt_proc.stdout.strip()
        if aapt_bin:
            dump_file = extract_dir / "manifest_dump.txt"
            with open(dump_file, "w", encoding="utf-8") as f:
                subprocess.run(
                    [
                        aapt_bin,
                        "dump",
                        "xmltree",
                        pkg_str,
                        "AndroidManifest.xml",
                    ],
                    stdout=f,
                    stderr=subprocess.DEVNULL,
                )

    elif fmt == "dmg":
        subprocess.run(
            ["7z", "x", pkg_str, f"-o{extract_dir}", "-y"],
            stdout=subprocess.DEVNULL,
            check=True,
        )
        # 嵌套分区检测
        hfs_files = list(extract_dir.glob("**/*.hfs"))
        if hfs_files:
            hfs_out = extract_dir / "hfs_extracted"
            subprocess.run(
                ["7z", "x", str(hfs_files[0]), f"-o{hfs_out}", "-y"],
                stdout=subprocess.DEVNULL,
            )

    elif fmt == "deb":
        subprocess.run(
            ["dpkg-deb", "-x", pkg_str, str(extract_dir)],
            stdout=subprocess.DEVNULL,
            check=True,
        )

    elif fmt == "rpm":
        # rpm 使用 rpm2cpio 解压
        cmd = f"cd '{extract_dir}' && rpm2cpio '{pkg_path.resolve()}' | cpio -idmv"
        subprocess.run(
            cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    else:
        # exe, appimage, 通用 7z
        subprocess.run(
            ["7z", "x", pkg_str, f"-o{extract_dir}", "-y"],
            stdout=subprocess.DEVNULL,
        )


def analyze_extracted(extract_dir, platform):
    """提取 SubID / AppId"""
    if platform == "Android":
        manifest_dump = extract_dir / "manifest_dump.txt"
        manifest_xml = extract_dir / "AndroidManifest.xml"

        # 优先读取 aapt 明文
        if manifest_dump.exists():
            text = manifest_dump.read_text(encoding="utf-8", errors="ignore")
            m_phone = re.search(
                r'AppSetting_params".*?value.*?="([0-9]+)#', text, re.DOTALL
            )
            m_pad = re.search(
                r'AppSetting_params_pad".*?value.*?="([0-9]+)#', text, re.DOTALL
            )
            phone_id = m_phone.group(1) if m_phone else ""
            pad_id = m_pad.group(1) if m_pad else ""
            if phone_id:
                return (
                    f"Phone: {phone_id} | Pad: {pad_id}"
                    if pad_id
                    else phone_id
                )

        # 兜底二进制字节流
        if manifest_xml.exists():
            raw = manifest_xml.read_bytes()
            matches_u8 = re.findall(rb"([0-9]{9})#[A-Fa-f0-9]{16}#", raw)
            matches_u16 = [
                m.replace(b"\x00", b"")
                for m in re.findall(
                    rb"((?:[0-9]\x00){9})#\x00(?:[A-Fa-f0-9]\x00){16}#\x00",
                    raw,
                )
            ]
            all_m = [m.decode("ascii") for m in (matches_u8 + matches_u16)]
            if len(all_m) >= 2:
                return f"Phone: {all_m[0]} | Pad: {all_m[1]}"
            elif len(all_m) == 1:
                return all_m[0]

        return "[未检测到ID]"

    else:
        # 桌面端 (Win / Linux / Mac) 查找 major.node
        nodes = list(extract_dir.glob("**/major.node"))
        if not nodes:
            nodes = list(extract_dir.glob("**/*.node"))

        pattern = re.compile(rb"QQAppId/([0-9]+)")
        matched_ids = set()

        for node_file in nodes:
            try:
                content = node_file.read_bytes()
                for match in pattern.findall(content):
                    matched_ids.add(match.decode("ascii"))
            except Exception:
                pass

        if matched_ids:
            return ", ".join(sorted(list(matched_ids)))

        return "[未检测到ID]"


def main():
    post_id, text_elem, _ = get_latest_post()
    if not post_id:
        print("[状态] 未能获取最新消息，终止执行。")
        return

    print(f"[最新消息] 当前频道最新消息 ID: {post_id}")

    # 读取旧状态
    last_id = ""
    if STATE_FILE.exists():
        last_id = STATE_FILE.read_text().strip()

    # 检查是否重复
    force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"
    if post_id == last_id and not force_run:
        print("[状态] 该版本已处理过，无需重复执行。")
        return

    targets = parse_download_targets(text_elem)
    if not targets:
        print("[状态] 消息中未找到符合条件的下载直链。")
        return

    print(f"[任务规划] 共解析出 {len(targets)} 个独立架构下载目标。")

    results = []
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    for idx, target in enumerate(targets, start=1):
        p_name = f"{target['platform']}_{target['arch']}"
        pkg_name = f"pkg_{idx}.{target['format']}"
        pkg_path = WORK_DIR / pkg_name

        print(
            f"\n--- [{idx}/{len(targets)}] 正在处理: {p_name} ({target['format']}) ---"
        )
        print(f"URL: {target['url']}")

        sub_id = "[提取失败]"
        try:
            print("下载中...")
            download_file(target["url"], pkg_path)
            print(f"下载完成 ({pkg_path.stat().st_size / 1024 / 1024:.2f} MB)，开始解压分析...")

            if TEMP_DIR.exists():
                shutil.rmtree(TEMP_DIR)
            TEMP_DIR.mkdir()

            extract_package(pkg_path, target["format"], TEMP_DIR)
            sub_id = analyze_extracted(TEMP_DIR, target["platform"])
            print(f"分析成功，提取结果: {sub_id}")

        except Exception as e:
            print(f"处理失败: {e}")
            sub_id = f"[错误: {str(e)[:30]}]"

        finally:
            # 立即清理当前安装包与解压临时目录，释放磁盘
            if pkg_path.exists():
                pkg_path.unlink()
            if TEMP_DIR.exists():
                shutil.rmtree(TEMP_DIR)

        results.append(
            {
                "platform": target["platform"],
                "arch": target["arch"],
                "format": target["format"],
                "sub_id": sub_id,
                "url": target["url"],
            }
        )

    # 结果持久化并记录 Message ID
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(post_id)

    # 写入 JSON 记录文件
    record_file = Path("data/history_records.json")
    all_records = {}
    if record_file.exists():
        try:
            all_records = json.loads(record_file.read_text())
        except Exception:
            pass
    all_records[post_id] = results
    record_file.write_text(json.dumps(all_records, indent=2, ensure_ascii=False))

    # 生成 Markdown 汇总表格输出到 Action Summary
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"### QQ 更新全架构 SubID 提取报告\n\n")
            f.write(f"- 消息来源: `https://t.me/{post_id}`\n")
            f.write(f"- 处理时间: UTC {os.environ.get('GITHUB_RUN_ID', '')}\n\n")
            f.write("| 平台 | 架构名称 | 封装格式 | 提取 SubID / AppId | 下载地址 |\n")
            f.write("| :--- | :--- | :--- | :--- | :--- |\n")
            for r in results:
                f.write(
                    f"| {r['platform']} | {r['arch']} | {r['format']} | `{r['sub_id']}` | [直链]({r['url']}) |\n"
                )

    print("\n[完成] 全架构分析完毕，结果已汇总。")


if __name__ == "__main__":
    main()
