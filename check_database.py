# -*- coding: utf-8 -*-
"""
QQ SubID 数据库全维度深度体检与健康度审计脚本 (产品线全面解耦版)
运行方式：在项目根目录执行 `python check_database.py`
"""

import json
import os
import re
import sys
from pathlib import Path

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

DATA_FILE = Path("data/qq_subid_map.json")
RUNTIME_FILE = Path("data/subid.json")
WATERMARK_DIR = Path("data")

VALID_PLATFORMS = {"Android", "Windows", "macOS", "Linux", "TIM", "Watch"}

# 兼容硬件 CPU 架构与分发包体格式
VALID_ARCHES = {
    "Android": {"Phone", "Pad", "Package", "Apk"},
    "Windows": {"X86", "X64", "Arm", "Package", "Exe"},
    "macOS": {"Universal", "X64", "Arm", "Dmg", "Pkg", "Package"},
    "Linux": {"X64", "Arm", "LoongArch", "Mips", "Package", "Deb", "Rpm", "Appimage"},
    "TIM": {"Phone", "Pad", "Package", "Apk", "X86", "X64"},
    "Watch": {"Phone", "Package", "Watch", "Apk"},
}


def version_tuple(ver_str: str) -> tuple:
    """提取版本号中纯数字段供版本精确排序"""
    clean_v = ver_str.split("-")[0].strip()
    nums = [int(x) for x in re.findall(r"\d+", clean_v)]
    return tuple(nums)


def identify_product_line(item: dict) -> str:
    """
    精准识别独立产品线：彻底分离普通手机 QQ、Pad、手表版 (Watch)、TIM 与桌面端
    """
    plat = str(item.get("platform", "")).strip()
    arch = str(item.get("arch", "")).strip()
    url = str(item.get("source_url", "")).strip().lower()
    ver = str(item.get("version", "")).strip()
    ch = str(item.get("channel", "")).strip().lower()

    plat_low = plat.lower()
    arch_low = arch.lower()

    # 1. 识别 TIM
    if (
        "tim" in plat_low
        or "tim" in arch_low
        or "com.tencent.tim" in url
        or "/tim" in url
        or "tim" in ch
        or "tim" in ver.lower()
    ):
        return "TIM"

    # 2. 识别 QQ 手表版 (Watch)
    if (
        "watch" in plat_low
        or "watch" in arch_low
        or "手表" in ch
        or "watch" in url
        or "qqlite" in url
        or "com.tencent.qqlite" in url
        or "child" in url
    ):
        return "QQ(Watch)"

    # 3. 移动端细分
    if plat == "Android":
        v_t = version_tuple(ver)
        if v_t and v_t[0] < 5:
            if "tim" in url or "tim" in ch:
                return "TIM"
            return "QQ(Watch)"

        if arch_low == "pad":
            return "QQ(Android-Pad)"
        return "QQ(Android-Phone)"

    # 4. 桌面端
    if "win" in plat_low:
        return "QQ(Windows)"
    if "mac" in plat_low:
        return "QQ(macOS)"
    if "linux" in plat_low:
        return "QQ(Linux)"

    return plat or "Unknown"


def run_health_check():
    print(f"\n{BOLD}{CYAN}======================================================={RESET}")
    print(f"{BOLD}{CYAN}       🚀 QQ SubID 数据库全维度深度体检与一致性审计      {RESET}")
    print(f"{BOLD}{CYAN}======================================================={RESET}\n")

    if not DATA_FILE.exists():
        print(f"{RED}[致命错误] 未在当前目录下找到数据库文件: {DATA_FILE}{RESET}")
        sys.exit(1)

    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"{RED}[致命错误] 解析主数据库 JSON 失败: {e}{RESET}")
        sys.exit(1)

    total_keys = len(data)
    total_records = 0
    total_beta_records = 0
    anomalies = []
    warnings = []

    product_stats = {}
    channel_stats = {}
    timeline_by_product = {}

    # 1. 字段合规与平台架构匹配校验
    for subid, val in data.items():
        items = val if isinstance(val, list) else [val]

        if not str(subid).isdigit() or len(str(subid)) != 9 or not str(subid).startswith("537"):
            anomalies.append({
                "subid": subid,
                "type": "主键格式非法",
                "detail": f"SubID [{subid}] 必须是 537 开头的 9 位纯数字"
            })

        for it in items:
            total_records += 1
            ver = str(it.get("version", "")).strip()
            plat = str(it.get("platform", "")).strip()
            arch = str(it.get("arch", "")).strip()
            date = str(it.get("date", "")).strip()
            ch = str(it.get("channel", "未标注")).strip()
            url = str(it.get("source_url", "")).strip()
            is_beta = it.get("is_beta", ("-beta" in ver.lower() or "beta" in ver.lower()))

            if is_beta:
                total_beta_records += 1

            prod_line = identify_product_line(it)
            product_stats[prod_line] = product_stats.get(prod_line, 0) + 1
            channel_stats[ch] = channel_stats.get(ch, 0) + 1

            if not ver or ver == "Unknown" or "unknown" in ver.lower():
                anomalies.append({
                    "subid": subid, "platform": plat, "version": ver,
                    "type": "脏版本号", "detail": "版本号为 Unknown 或为空"
                })
            elif re.search(r"[\u4e00-\u9fa5]", ver):
                anomalies.append({
                    "subid": subid, "platform": plat, "version": ver,
                    "type": "版本号含汉字", "detail": f"版本号含中文字符: {ver}"
                })
            elif re.search(r"\(\d+\)", ver):
                warnings.append({
                    "subid": subid, "platform": plat, "version": ver,
                    "type": "版本号含括号构建号", "detail": f"未完全归一化清洗: {ver}"
                })

            valid_for_plat = {x.lower() for x in VALID_ARCHES.get(plat, set())}
            if valid_for_plat and arch.lower() not in valid_for_plat:
                anomalies.append({
                    "subid": subid, "platform": plat, "version": ver, "arch": arch,
                    "type": "平台架构错配", "detail": f"平台 [{plat}] 不应包含架构 [{arch}] (合法架构: {VALID_ARCHES.get(plat)})"
                })

            if not url or not (url.startswith("http://") or url.startswith("https://")):
                anomalies.append({
                    "subid": subid, "platform": plat, "version": ver,
                    "type": "非法下载URL", "detail": f"链接格式不合规: {url}"
                })

            if subid.isdigit() and ver and ver != "Unknown":
                timeline_by_product.setdefault(prod_line, []).append({
                    "subid": int(subid),
                    "subid_str": subid,
                    "version": ver,
                    "v_tuple": version_tuple(ver),
                    "date": date,
                    "channel": ch,
                    "arch": arch,
                    "is_beta": is_beta
                })

    # 2. 单调性审计 (各产品线独立排序比对)
    monotonic_errors = []
    monotonic_reversals = []

    for prod_line, entries in timeline_by_product.items():
        entries.sort(key=lambda x: (x["v_tuple"], x["date"], 0 if x["is_beta"] else 1))

        for i in range(1, len(entries)):
            prev = entries[i - 1]
            curr = entries[i]

            if curr["v_tuple"] > prev["v_tuple"]:
                diff = prev["subid"] - curr["subid"]
                if diff > 10000:
                    monotonic_errors.append({
                        "product": prod_line,
                        "type": "SubID 剧烈倒退反转 (断崖暴跌)",
                        "detail": f"产品 [{prod_line}] 版本递增 [{prev['version']}] -> [{curr['version']}]，SubID 暴跌: [{prev['subid_str']}] -> [{curr['subid_str']}] (倒退 {diff})"
                    })
                elif diff > 0:
                    monotonic_reversals.append({
                        "product": prod_line,
                        "prev_ver": prev["version"],
                        "curr_ver": curr["version"],
                        "prev_subid": prev["subid_str"],
                        "curr_subid": curr["subid_str"],
                        "diff": diff,
                        "prev_date": prev["date"],
                        "curr_date": curr["date"]
                    })

    # 3. 运行时字典一致性
    runtime_check_ok = True
    runtime_errors = []
    if not RUNTIME_FILE.exists():
        runtime_errors.append("未找到运行时编译字典 `data/subid.json`！")
        runtime_check_ok = False
    else:
        try:
            rt_dict = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
            if len(rt_dict) != total_keys:
                runtime_errors.append(f"运行时字典主键数 ({len(rt_dict)}) 与主数据库 ({total_keys}) 不一致！")
                runtime_check_ok = False
            else:
                missing_beta_key = sum(1 for v in rt_dict.values() if "is_beta" not in v)
                if missing_beta_key > 0:
                    runtime_errors.append(f"运行时字典中存在 {missing_beta_key} 项缺失 `is_beta` 属性！")
                    runtime_check_ok = False
        except Exception as e:
            runtime_errors.append(f"解析 `data/subid.json` 失败: {e}")
            runtime_check_ok = False

    # 4. 水位线核对与自动补齐
    watermark_report = {}
    for plat in ["Android", "Windows", "macOS", "Linux"]:
        wm_file = WATERMARK_DIR / f"telegram_{plat.lower()}_latest_version.txt"
        recorded_v = "未建立"
        if wm_file.exists():
            lines = wm_file.read_text(encoding="utf-8").strip().splitlines()
            if lines: recorded_v = lines[0]

        db_vers = [
            it.get("version", "").replace("-Beta", "").strip()
            for val in data.values()
            for it in (val if isinstance(val, list) else [val])
            if it.get("platform") == plat and it.get("version")
        ]
        db_max = sorted(db_vers, key=lambda v: version_tuple(v))[-1] if db_vers else "无数据"

        if recorded_v == "未建立" and db_max != "无数据":
            wm_file.write_text(f"{db_max}\n\n", encoding="utf-8")
            recorded_v = f"{db_max} (已自动补齐)"

        watermark_report[plat] = {"watermark": recorded_v, "db_max": db_max}

    # ========================================================
    # 输出综合体检报告
    # ========================================================
    print(f"{BOLD}[一] 数据库全盘概览与产品线解耦分布{RESET}")
    print(f"- 收录 SubID 主键总数 : {BOLD}{GREEN}{total_keys}{RESET} 个")
    print(f"- 全渠道实体映射条数 : {BOLD}{GREEN}{total_records}{RESET} 条 (正式版: {total_records - total_beta_records} 条, Beta 测试版: {total_beta_records} 条)")
    print(f"- 产品线解耦细分分布 :")
    for prod, c in sorted(product_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {prod:<18} : {c} 条记录")

    print(f"\n{BOLD}[二] 各平台最新水位线与数据库顶峰对齐校验{RESET}")
    for p, r in watermark_report.items():
        is_synced = (r["db_max"] in r["watermark"])
        status_tag = f"{GREEN}✔ 完美对齐{RESET}" if is_synced else f"{YELLOW}▲ 存在差值{RESET}"
        print(f"  • {p:<10} : 库内最高 [{r['db_max']}] | 独立水位线 [{r['watermark']}] -> {status_tag}")

    print(f"\n{BOLD}[三] 运行时轻量字典 (data/subid.json) 状态{RESET}")
    if runtime_check_ok:
        print(f"  {GREEN}✔ 完美同步！运行时字典收录键数与主库 100% 吻合，is_beta 标记完备。{RESET}")
    else:
        for err in runtime_errors:
            print(f"  {RED}✘ 字典异常: {err}{RESET}")

    print(f"\n{BOLD}[四] 数据库健康度与单调性审计判定{RESET}")
    all_fatal = anomalies + monotonic_errors
    if not all_fatal:
        print(f"\n{BOLD}{GREEN}🎉 [体检通过] 数据库健康度满分！无任何跨产品线错配或致命断崖倒退！{RESET}")
    else:
        print(f"\n{BOLD}{RED}🚨 检测到 {len(all_fatal)} 处【致命或违规条目】：{RESET}")
        for item in all_fatal:
            print(f"  {RED}✘ [{item['type']}]{RESET} {item['detail']}")

    print(f"\n{BOLD}[五] SubID 局部流水逆序（逆转）全量审计清单 (共 {len(monotonic_reversals)} 处){RESET}")
    if not monotonic_reversals:
        print(f"  {GREEN}✔ 没有任何逆序现象，所有版本完全单调严格递增！{RESET}")
    else:
        print(f"{CYAN}注：同主版本下并行构建分支流水存在微小交错属正常现象：{RESET}\n")
        for idx, rev in enumerate(monotonic_reversals, 1):
            print(
                f"  [{idx:02d}] 产品: {BOLD}{rev['product']:<12}{RESET} | "
                f"版本: {rev['prev_ver']} -> {rev['curr_ver']} | "
                f"SubID: {rev['prev_subid']} -> {rev['curr_subid']} | "
                f"倒退: {YELLOW}-{rev['diff']}{RESET} 位 | "
                f"日期: {rev['prev_date']} -> {rev['curr_date']}"
            )

    print(f"\n{BOLD}{CYAN}======================== 体检流程结束 ========================{RESET}\n")


if __name__ == "__main__":
    run_health_check()