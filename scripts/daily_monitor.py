# -*- coding: utf-8 -*-
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from extractor import (
    download_target_file,
    extract_subids_and_version,
    extract_version_from_filename,
)
from subid_compiler import compile_subid_map_to_runtime_dict

from channels import (
    get_appchina_info,
    get_baidu_info,
    get_honor_info,
    get_huawei_info,
    get_oppo_info,
    get_samsung_info,
    get_telegram_tasks,
    get_vivo_info,
    get_wandoujia_info,
    get_xiaomi_info,
    get_yingyongbao_info,
)

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

DATA_FILE = Path("data/qq_subid_map.json")
BAK_FILE = Path("data/qq_subid_map.json.bak")
TEMP_DL = Path("temp_dl")
TEMP_EXTRACT = Path("temp_extracted")

TG_PLATFORM_VER_FILES = {
    "Android": Path("data/telegram_android_latest_version.txt"),
    "Windows": Path("data/telegram_windows_latest_version.txt"),
    "macOS": Path("data/telegram_macos_latest_version.txt"),
    "Linux": Path("data/telegram_linux_latest_version.txt"),
}

MARKET_CHANNELS = [
    ("豌豆荚", get_wandoujia_info, Path("data/wandoujia_latest_version.txt")),
    ("应用宝", get_yingyongbao_info, Path("data/yingyongbao_latest_version.txt")),
    ("vivo", get_vivo_info, Path("data/vivo_latest_version.txt")),
    ("华为", get_huawei_info, Path("data/huawei_latest_version.txt")),
    ("OPPO", get_oppo_info, Path("data/oppo_latest_version.txt")),
    ("小米", get_xiaomi_info, Path("data/xiaomi_latest_version.txt")),
    ("荣耀", get_honor_info, Path("data/honor_latest_version.txt")),
    ("三星", get_samsung_info, Path("data/samsung_latest_version.txt")),
    ("应用汇", get_appchina_info, Path("data/appchina_latest_version.txt")),
    ("百度", get_baidu_info, Path("data/baidu_latest_version.txt")),
]


def get_url_fingerprint(url: str, channel: str = "") -> str:
    """提取静态特征指纹，剥离动态鉴权 Token 与时间戳"""
    if not url:
        return ""
    clean = url.strip()

    if "appchina" in clean or channel == "应用汇":
        clean = re.sub(r"/\d{10,}/package_(\d+)\.\d+", r"/package_\1", clean)
        clean = re.sub(r"/\d{10,}/", "/", clean)
        return clean.split("?")[0]

    if "gdown.baidu.com" in clean or channel == "百度":
        return clean.split("?")[0]

    if "heytap" in clean or "oppo" in clean or channel == "OPPO":
        return clean.split("?")[0]

    if ".apk" in clean.lower():
        return clean.split("?")[0]

    return clean.split("?")[0]


def is_newer_version(new_ver: str, old_ver: str, is_new_beta: bool = False, is_old_beta: bool = None) -> bool:
    """严格版本单调性判断（全面兼顾 Beta 测试版晋级到正式版的情形）"""
    if not old_ver:
        return True
    if not new_ver:
        return False
    if new_ver.strip() == old_ver.strip() and is_new_beta == is_old_beta:
        return False

    if is_old_beta is None:
        is_old_beta = ("-beta" in old_ver.lower() or "beta" in old_ver.lower())
    is_new_b = is_new_beta or ("-beta" in new_ver.lower() or "beta" in new_ver.lower())

    v1_clean = new_ver.split("-")[0].strip()
    v2_clean = old_ver.split("-")[0].strip()
    p1 = [int(x) for x in re.findall(r"\d+", v1_clean)]
    p2 = [int(x) for x in re.findall(r"\d+", v2_clean)]

    # 1. 逐段前缀数值比较
    for a, b in zip(p1, p2):
        if a > b:
            return True
        if a < b:
            return False

    # 2. 前 3 段主版本完全相同时（如 9.3.80 vs 9.3.80.42670），正式版胜出
    if len(p1) >= 3 and len(p2) >= 3 and p1[:3] == p2[:3]:
        if is_old_beta and not is_new_b:
            return True

    # 3. 常规长度段数比较
    if len(p1) != len(p2):
        return len(p1) > len(p2)

    if is_old_beta and not is_new_b:
        return True

    return False


def sanitize_and_deduplicate_map(raw_map: dict) -> tuple[dict, int]:
    """全自动自愈去重：合并同 SubID 内部具有相同 (platform, arch, version) 的重复实体，优先保留官方直链，补齐 is_beta 标记"""
    cleaned_map = {}
    purged_count = 0
    for s_id, val in raw_map.items():
        items = val if isinstance(val, list) else [val]
        unique_entities = {}
        for item in items:
            v = item.get("version", "").strip()
            p = item.get("platform", "").strip()
            a = item.get("arch", "").strip()
            c = item.get("channel", "").strip()
            url = item.get("source_url", "").strip()

            # 自动自愈补齐历史遗留条目的 is_beta 标记
            if "is_beta" not in item:
                item["is_beta"] = bool(
                    "-beta" in v.lower()
                    or "beta" in v.lower()
                    or "beta" in url.lower()
                )

            key = (p, a, c, v)
            if key not in unique_entities:
                unique_entities[key] = item
            else:
                purged_count += 1
                old_item = unique_entities[key]
                old_url = old_item.get("source_url", "")
                if "t.me/" in old_url and "t.me/" not in url:
                    unique_entities[key] = item
        cleaned_map[s_id] = list(unique_entities.values())
    return cleaned_map, purged_count


def save_database_and_runtime(subid_map: dict):
    """原子写盘保存主数据库并派生编译运行时字典"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(subid_map, indent=2, ensure_ascii=False), encoding="utf-8")
    if DATA_FILE.exists():
        shutil.copy2(DATA_FILE, BAK_FILE)
    temp_file.replace(DATA_FILE)

    try:
        compiled_data = compile_subid_map_to_runtime_dict(subid_map)
        runtime_file = DATA_FILE.parent / "subid.json"
        temp_rt = runtime_file.with_suffix(".tmp")
        temp_rt.write_text(json.dumps(compiled_data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_rt.replace(runtime_file)
        print(f"[运行时同步] 已派生编译 data/subid.json (收录 {len(compiled_data)} 条)")
    except Exception as e:
        print(f"[警告] 编译运行时字典异常: {e}")


def get_platform_watermark(plat: str, subid_map: dict) -> tuple[str, str, Path]:
    """获取指定平台的水位线（版本、URL、文件路径）"""
    v_file = TG_PLATFORM_VER_FILES.get(plat, Path(f"data/telegram_{plat.lower()}_latest_version.txt"))
    rec_ver, rec_url = "", ""

    if v_file.exists():
        lines = [l.strip() for l in v_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        if len(lines) >= 1: rec_ver = lines[0]
        if len(lines) >= 2: rec_url = lines[1]

    db_highest_ver = ""
    if subid_map:
        plat_vers = [
            item.get("version", "").replace("-Beta", "").strip()
            for val in subid_map.values()
            for item in (val if isinstance(val, list) else [val])
            if item.get("platform") == plat and item.get("version")
        ]
        for pv in plat_vers:
            if is_newer_version(pv, db_highest_ver):
                db_highest_ver = pv

    if db_highest_ver and (not rec_ver or is_newer_version(db_highest_ver, rec_ver)):
        rec_ver = db_highest_ver
        v_file.parent.mkdir(parents=True, exist_ok=True)
        v_file.write_text(f"{rec_ver}\n\n", encoding="utf-8")

    return rec_ver, rec_url, v_file


def main():
    pending_tasks = []
    versions_to_record = {}
    has_synced_watermark = False

    # 1. 预载数据库
    subid_map = {}
    purged_count = 0
    if DATA_FILE.exists():
        try:
            subid_map = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            subid_map, purged_count = sanitize_and_deduplicate_map(subid_map)
            if purged_count > 0:
                print(f"[自愈引擎] 已自动合并清理历史冗余克隆记录并补齐 Beta 标记: {purged_count} 条！立即持久化落盘...")
                save_database_and_runtime(subid_map)
        except Exception:
            subid_map = {}

    processed_url_fps = set()
    platform_version_arches = {}

    for val in subid_map.values():
        for item in (val if isinstance(val, list) else [val]):
            u = item.get("source_url")
            p = item.get("platform")
            v = item.get("version", "").replace("-Beta", "").strip()
            a = item.get("arch")
            if u:
                processed_url_fps.add(get_url_fingerprint(u))
            if p and v and a:
                platform_version_arches.setdefault((p, v), set()).add(a)

    # 2. 采集 Telegram 官方更新（保持原样）
    tg_tasks = get_telegram_tasks()
    seen_platform_tasks = set()

    for t in tg_tasks:
        plat = t["platform"]
        arch = t["arch"]
        cur_ver = t["version"]
        cur_url = t["url"]
        is_beta = t.get("is_beta", False)
        fp_cur = get_url_fingerprint(cur_url)

        if fp_cur in processed_url_fps:
            print(f"[TG 监控 - {plat}] {arch} {cur_ver} (链接已在库中)，跳过。")
            continue

        task_dedup_key = (plat, arch, cur_ver)
        if task_dedup_key in seen_platform_tasks:
            continue

        recorded_tg_ver, recorded_tg_url, tg_ver_file = get_platform_watermark(plat, subid_map)
        is_old_beta = ("-beta" in recorded_tg_ver.lower())

        should_update = False
        update_reason = ""

        if not recorded_tg_ver:
            should_update = True
            update_reason = "首次收录"
        elif is_newer_version(cur_ver, recorded_tg_ver, is_new_beta=is_beta, is_old_beta=is_old_beta):
            should_update = True
            update_reason = f"版本升级 ({recorded_tg_ver} -> {cur_ver})"
        else:
            clean_cur = cur_ver.replace("-Beta", "").strip()
            clean_rec = recorded_tg_ver.replace("-Beta", "").strip()
            known_arches = platform_version_arches.get((plat, clean_cur), set())

            if clean_cur == clean_rec and arch not in known_arches and "t.me/linqiqi_backup" in cur_url:
                should_update = True
                update_reason = f"同版本缺失架构自愈补全 ({arch})"
            else:
                print(f"[TG 监控 - {plat}] {arch} 线上版本 ({cur_ver}) <= 本地记录 ({recorded_tg_ver})，跳过。")

        if should_update:
            print(f"[TG 监控 - {plat}] ★ 触发更新: [{update_reason}]！加入待处理任务: {arch} -> {cur_url}")
            t["mark_channel_name"] = f"官方TG({plat})"
            t["mark_version_val"] = cur_ver
            t["mark_url_val"] = cur_url
            t["ver_file_path"] = tg_ver_file
            pending_tasks.append(t)
            seen_platform_tasks.add(task_dedup_key)

    # 3. 九大厂商应用市场比对
    for name, fetch_func, ver_file in MARKET_CHANNELS:
        recorded_ver = ""
        recorded_url = ""
        if ver_file.exists():
            lines = [l.strip() for l in ver_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            if len(lines) >= 1: recorded_ver = lines[0]
            if len(lines) >= 2: recorded_url = lines[1]

        info = fetch_func()
        if not info:
            continue

        cur_ver = info["version"]
        cur_url = info["download_url"]
        fp_cur = get_url_fingerprint(cur_url, name)

        # 感知应用市场渠道包是否为测试版
        is_market_beta = bool(
            re.search(r"beta|内测|体验|preview|rc", cur_ver, re.IGNORECASE)
            or re.search(r"beta|内测|体验", cur_url, re.IGNORECASE)
        )

        print(f"[{name}监控] 线上版本: {cur_ver} | 本地记录版本: {recorded_ver or '无'} | Beta: {is_market_beta}")

        if fp_cur in processed_url_fps:
            print(f"[{name}监控] 安装包链接已在库中，无需下载。")
            continue

        should_update = False
        update_reason = ""

        if not recorded_ver:
            should_update = True
            update_reason = "首次收录"
        elif is_newer_version(cur_ver, recorded_ver, is_new_beta=is_market_beta):
            should_update = True
            update_reason = f"版本升级 ({recorded_ver} -> {cur_ver})"
        elif cur_ver == recorded_ver:
            fp_rec = get_url_fingerprint(recorded_url, name)
            if recorded_url and fp_cur != fp_rec:
                should_update = True
                update_reason = "同版本静默换包 (直链变动)"
            elif not recorded_url:
                should_update = True
                update_reason = "未处理直链补全"
            else:
                print(f"[{name}监控] 版本与下载直链均一致，无需下载。")
        else:
            print(f"[{name}监控] 线上版本 ({cur_ver}) 落后于本地最高记录 ({recorded_ver})，跳过。")

        if should_update:
            print(f"[{name}监控] ★ 触发更新: [{update_reason}]！加入待处理任务。")
            pending_tasks.append({
                "platform": "Android",
                "arch": "Package",
                "version": cur_ver,
                "date": info["date"],
                "url": cur_url,
                "is_beta": is_market_beta,
                "channel": name,
                "mark_channel_name": name,
                "mark_version_val": cur_ver,
                "mark_url_val": cur_url,
                "ver_file_path": ver_file,
            })

    # 4. 二次精细去重
    tasks_to_run = []
    for t in pending_tasks:
        fp_t = get_url_fingerprint(t["url"], t.get("channel"))
        if fp_t not in processed_url_fps:
            tasks_to_run.append(t)
        else:
            if t.get("ver_file_path") and t.get("mark_channel_name"):
                v_file = t["ver_file_path"]
                c_name = t["mark_channel_name"]
                c_ver = t["mark_version_val"]
                c_url = t["mark_url_val"]

                v_file.parent.mkdir(parents=True, exist_ok=True)
                v_file.write_text(f"{c_ver.strip()}\n{c_url.strip()}\n", encoding="utf-8")
                has_synced_watermark = True
                print(f"[水位线同步] {c_name} 安装包已在库中，已就地补齐本地记录文件: {c_ver}")

    github_output = os.environ.get("GITHUB_OUTPUT")

    if not tasks_to_run:
        print("[状态] 所有目标任务在此版本下的包体均已录入，无需下载任何文件。")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as f:
                has_any_change = "true" if (has_synced_watermark or purged_count > 0) else "false"
                f.write(f"has_changes={has_any_change}\n")
                f.write("has_new_version=false\ntrigger_decompile=false\n")
        return

    print(f"\n[任务队列] 共有 {len(tasks_to_run)} 个新包待处理！")
    has_android, android_info, has_subid_extracted = False, {}, False

    # 5. 执行增量下载、解包与 SubID 抽取
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

        dl_files = [f for f in TEMP_DL.rglob("*") if f.is_file() and not f.name.endswith(".tdl")]
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

                # 识别当前 SubID 是否为 Beta 测试版
                is_beta_flag = bool(
                    t.get("is_beta")
                    or "-beta" in clean_ver.lower()
                    or "beta" in clean_ver.lower()
                    or "beta" in base_ver.lower()
                    or "beta" in fname.lower()
                    or "beta" in t.get("url", "").lower()
                )
                if is_beta_flag and not clean_ver.endswith("-Beta"):
                    clean_ver = f"{clean_ver}-Beta"

                for s_id, a_val in subid_pairs:
                    ch_label = t.get("channel", "官方")
                    print(f"[提取成功] 平台: {plat} | 架构: {a_val} | 版本: {clean_ver} | 渠道: {ch_label} | Beta: {is_beta_flag} | SubID: {s_id}")
                    new_item = {
                        "version": clean_ver, "platform": plat, "arch": a_val,
                        "date": t["date"], "source_url": t["url"], "channel": ch_label,
                        "is_beta": is_beta_flag,  # 显式打标写入主数据库
                    }

                    if s_id not in subid_map:
                        subid_map[s_id] = [new_item]
                    else:
                        if isinstance(subid_map[s_id], dict):
                            subid_map[s_id] = [subid_map[s_id]]
                        exist_idx = -1
                        for i, ex in enumerate(subid_map[s_id]):
                            if (ex.get("platform") == plat and
                                ex.get("arch") == a_val and
                                ex.get("channel") == ch_label and
                                ex.get("version") == clean_ver):
                                exist_idx = i
                                break

                        if exist_idx >= 0:
                            old_url = subid_map[s_id][exist_idx].get("source_url", "")
                            if "t.me/" in old_url and "t.me/" not in t["url"]:
                                print(f"[直链晋级] SubID {s_id} 历史备份链接已升级替换为官方直链: {t['url']}")
                            subid_map[s_id][exist_idx] = new_item
                        else:
                            subid_map[s_id].append(new_item)

                real_v_clean = clean_ver.replace("-Beta", "").strip()
                if t.get("mark_channel_name"):
                    versions_to_record[t["ver_file_path"]] = (
                        t["mark_channel_name"],
                        real_v_clean,
                        t["mark_url_val"]
                    )

                if plat == "Android" and t.get("channel") == "官方":
                    has_android = True
                    decompile_apk_url = t.get("official_url") or t["url"]
                    android_info = {
                        "url": decompile_apk_url,
                        "version": clean_ver,
                        "is_beta": "true" if is_beta_flag else "false"
                    }
        finally:
            shutil.rmtree(TEMP_DL, ignore_errors=True)
            shutil.rmtree(TEMP_EXTRACT, ignore_errors=True)

    if not has_subid_extracted:
        print("[状态] 本批次无有效新 SubID，取消落盘。")
        return

    # 6. 原子落盘保存主数据库与派生字典
    save_database_and_runtime(subid_map)

    # 7. 更新各渠道独立水位线文件
    for ver_path, (ch_name, ver_val, url_val) in versions_to_record.items():
        ver_path.parent.mkdir(parents=True, exist_ok=True)
        ver_path.write_text(f"{ver_val.strip()}\n{url_val.strip()}\n", encoding="utf-8")
        print(f"[版本记录] {ch_name}已更新: 版本={ver_val} | 直链已记录")

        if "telegram_android" in ver_path.name:
            compat_file = Path("data/telegram_latest_version.txt")
            compat_file.write_text(f"{ver_val.strip()}\n{url_val.strip()}\n", encoding="utf-8")

    print("\n[更新完毕] 所有新版本数据已追加写入 data/qq_subid_map.json")

    # 8. GitHub Actions 联动输出
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write("has_changes=true\n")
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
