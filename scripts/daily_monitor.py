# -*- coding: utf-8 -*-
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 环境与路径准备
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from extractor import (
    download_target_file,
    extract_subids_and_version,
    extract_version_from_filename,
)
from subid_compiler import compile_subid_map_to_runtime_dict

# 导入解耦后的各大渠道监控模块
from channels import (
    get_huawei_info,
    get_oppo_info,
    get_telegram_tasks,
    get_vivo_info,
    get_wandoujia_info,
    get_xiaomi_info,
    get_yingyongbao_info,
    get_honor_info,
)

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# 统一数据库与缓存路径
DATA_FILE = Path("data/qq_subid_map.json")
BAK_FILE = Path("data/qq_subid_map.json.bak")
TEMP_DL = Path("temp_dl")
TEMP_EXTRACT = Path("temp_extracted")

# 各市场独立通道注册表 (名称, 获取函数, 版本记录文件路径)
MARKET_CHANNELS = [
    ("豌豆荚", get_wandoujia_info, Path("data/wandoujia_latest_version.txt")),
    ("应用宝", get_yingyongbao_info, Path("data/yingyongbao_latest_version.txt")),
    ("vivo", get_vivo_info, Path("data/vivo_latest_version.txt")),
    ("华为", get_huawei_info, Path("data/huawei_latest_version.txt")),
    ("OPPO", get_oppo_info, Path("data/oppo_latest_version.txt")),
    ("小米", get_xiaomi_info, Path("data/xiaomi_latest_version.txt")),
    ("荣耀", get_honor_info, Path("data/honor_latest_version.txt")),
]


def main():
    pending_tasks = []
    versions_to_record = {}

    # 1. 采集 Telegram 官方更新
    tg_tasks = get_telegram_tasks()
    pending_tasks.extend(tg_tasks)

    # 2. 依次遍历轮询各大手机应用市场渠道
    for name, fetch_func, ver_file in MARKET_CHANNELS:
        recorded_ver = ver_file.read_text(encoding="utf-8").strip() if ver_file.exists() else ""
        info = fetch_func()
        if info:
            cur_ver = info["version"]
            print(f"[{name}监控] 线上版本: {cur_ver} | 本地记录版本: {recorded_ver or '无'}")
            if not recorded_ver or recorded_ver != cur_ver:
                print(f"[{name}监控] 检测到新版本/首发收录: {cur_ver}，加入待处理任务！")
                pending_tasks.append({
                    "platform": "Android",
                    "arch": "Package",
                    "version": cur_ver,
                    "date": info["date"],
                    "url": info["download_url"],
                    "is_beta": False,
                    "channel": name,
                    "mark_channel_name": name,
                    "mark_version_val": cur_ver,
                    "ver_file_path": ver_file,
                })
            else:
                print(f"[{name}监控] 线上版本与记录一致，无需下载。")

    if not pending_tasks:
        print("[状态] 遍历完毕，所有渠道均无新版本。")
        sys.exit(0)

    # 3. 读主数据库并做 URL 去重排队
    subid_map = {}
    if DATA_FILE.exists():
        try: subid_map = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception: subid_map = {}

    processed_urls = {
        item.get("source_url")
        for val in subid_map.values()
        for item in (val if isinstance(val, list) else [val])
        if item.get("source_url")
    }

    tasks_to_run = [t for t in pending_tasks if t["url"] not in processed_urls]
    github_output = os.environ.get("GITHUB_OUTPUT")

    if not tasks_to_run:
        print("[状态] 所有目标任务的直链均已录入，无需重复处理。")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write("has_new_version=false\ntrigger_decompile=false\n")
        return

    print(f"\n[任务队列] 共有 {len(tasks_to_run)} 个任务待处理！")
    has_android, android_info, has_subid_extracted = False, {}, False

    # 4. 执行批量下载、解包与 SubID 抽取
    for idx, t in enumerate(tasks_to_run, 1):
        print(f"\n--- [{idx}/{len(tasks_to_run)}] 正在处理: {t['platform']} - {t['arch']} ({t.get('channel', '官方')}) ---")
        print(f"URL: {t['url']}")

        if TEMP_DL.exists(): shutil.rmtree(TEMP_DL)
        TEMP_DL.mkdir()

        proc = download_target_file(t["url"], TEMP_DL)
        if proc is None or proc.returncode != 0:
            print("[跳过] 下载失败")
            continue

        dl_files = [f for f in TEMP_DL.rglob("*") if f.is_file() and not f.name.endswith(".tdl")]
        if not dl_files: continue

        downloaded_file = dl_files[0]
        fname = downloaded_file.name
        base_ver = extract_version_from_filename(fname) or t["version"]

        if TEMP_EXTRACT.exists(): shutil.rmtree(TEMP_EXTRACT)
        TEMP_EXTRACT.mkdir()

        try:
            lf = fname.lower()
            if lf.endswith(".deb"):
                subprocess.run(["dpkg-deb", "-x", str(downloaded_file), str(TEMP_EXTRACT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif lf.endswith(".rpm"):
                subprocess.run(f"cd '{TEMP_EXTRACT}' && rpm2cpio '{downloaded_file.resolve()}' | cpio -idmv", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.run(["7z", "x", str(downloaded_file), f"-o{TEMP_EXTRACT}", "-y"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                hfs = list(TEMP_EXTRACT.glob("**/*.hfs"))
                if hfs:
                    subprocess.run(["7z", "x", str(hfs[0]), f"-o{TEMP_EXTRACT}/hfs_out", "-y"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            subid_pairs, plat, clean_ver = extract_subids_and_version(TEMP_EXTRACT, downloaded_file, t["arch"], base_ver)
            if subid_pairs:
                has_subid_extracted = True
                if t.get("is_beta") and not clean_ver.endswith("-Beta"):
                    clean_ver = f"{clean_ver}-Beta"

                for s_id, a_val in subid_pairs:
                    ch_label = t.get("channel", "官方")
                    print(f"[提取成功] 平台: {plat} | 架构: {a_val} | 版本: {clean_ver} | 渠道: {ch_label} | SubID: {s_id}")
                    new_item = {
                        "version": clean_ver, "platform": plat, "arch": a_val,
                        "date": t["date"], "source_url": t["url"], "channel": ch_label,
                    }
                    if s_id not in subid_map: subid_map[s_id] = [new_item]
                    else:
                        if isinstance(subid_map[s_id], dict): subid_map[s_id] = [subid_map[s_id]]
                        subid_map[s_id].append(new_item)

                if t.get("mark_channel_name"):
                    versions_to_record[t["ver_file_path"]] = (t["mark_channel_name"], t["mark_version_val"])

                if plat == "Android" and t.get("channel") == "官方":
                    has_android = True
                    android_info = {"url": t["url"], "version": clean_ver, "is_beta": "true" if t.get("is_beta") else "false"}
        finally:
            shutil.rmtree(TEMP_DL, ignore_errors=True)
            shutil.rmtree(TEMP_EXTRACT, ignore_errors=True)

    if not has_subid_extracted:
        print("[状态] 本批次下载解析未能提取到有效 SubID，取消落盘。")
        return

    # 5. 原子落盘保存主数据库 data/qq_subid_map.json
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(subid_map, indent=2, ensure_ascii=False), encoding="utf-8")
    if DATA_FILE.exists(): shutil.copy2(DATA_FILE, BAK_FILE)
    temp_file.replace(DATA_FILE)

    # 6. 同步派生编译出运行时字典 data/subid.json
    try:
        compiled_data = compile_subid_map_to_runtime_dict(subid_map)
        runtime_file = DATA_FILE.parent / "subid.json"
        temp_rt = runtime_file.with_suffix(".tmp")
        temp_rt.write_text(json.dumps(compiled_data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_rt.replace(runtime_file)
        print(f"[运行时同步] 已派生编译 data/subid.json (收录 {len(compiled_data)} 条)")
    except Exception as e:
        print(f"[警告] 编译运行时字典异常: {e}")

    # 7. 满足要求：在所有 SubID 全部成功入库落盘后，最后批量写入版本记录文件
    for ver_path, (ch_name, ver_val) in versions_to_record.items():
        ver_path.parent.mkdir(parents=True, exist_ok=True)
        ver_path.write_text(ver_val.strip(), encoding="utf-8")
        print(f"[版本记录] {ch_name}最新版本 {ver_val} 已成功写入 {ver_path}")

    print("\n[更新完毕] 所有新版本数据已追加写入 data/qq_subid_map.json")

    # 8. GitHub Actions 联动输出
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
