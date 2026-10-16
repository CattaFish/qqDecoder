# -*- coding: utf-8 -*-
"""
SubID 结构化运行时字典编译器 (方案 A 规范)
供 daily_monitor.py 与 build_subid_database.py 自动调用
summary 保持原始规范，独立透传保留 channel 等新增键
"""

REAL_HW_ARCHES = {
    "x86": "X86",
    "x64": "X64",
    "arm": "Arm",
    "arm64": "Arm64",
    "loongarch": "LoongArch",
    "mips": "Mips",
    "pad": "Pad"
}

PACKAGE_FORMATS = {"rpm", "deb", "appimage", "dmg", "pkg", "apk"}
STANDARD_RAW_KEYS = {"platform", "arch", "version", "date", "source_url"}


def build_single_entry(subid: str, items: list) -> dict:
    if not items:
        return None

    platforms = list(dict.fromkeys(it.get("platform", "").strip() for it in items if it.get("platform")))
    platform = platforms[0] if platforms else "QQ"

    raw_arches = [it.get("arch", "").strip() for it in items if it.get("arch")]
    hw_arches = []
    packages = []

    for a in raw_arches:
        low = a.lower()
        if low in REAL_HW_ARCHES:
            val = REAL_HW_ARCHES[low]
            if val not in hw_arches:
                hw_arches.append(val)
        elif low in PACKAGE_FORMATS:
            pkg = a.upper()
            if pkg not in packages:
                packages.append(pkg)

    versions = list(dict.fromkeys(it.get("version", "").strip() for it in items if it.get("version")))
    dates = list(dict.fromkeys(it.get("date", "").strip() for it in items if it.get("date")))

    arch_display = hw_arches[0] if len(hw_arches) == 1 else None

    ver_str = " / ".join(versions) if versions else "未知版本"
    plat_low = platform.lower()

    # 保持原版 summary 前缀逻辑，不做任何改动
    if "win" in plat_low:
        prefix = f"QQ(Win-{arch_display})" if arch_display else "QQ(Win)"
    elif "mac" in plat_low:
        prefix = "QQ(Mac)"
    elif "linux" in plat_low:
        prefix = f"QQ(Linux-{arch_display})" if arch_display else "QQ(Linux)"
    elif "android" in plat_low:
        prefix = "QQ(Pad)" if arch_display == "Pad" else "QQ"
    elif "watch" in plat_low:
        prefix = "QQ(Watch)"
    elif "tim" in plat_low:
        prefix = "TIM"
    else:
        prefix = f"{platform}({arch_display})" if arch_display else platform

    summary = f"{prefix} {ver_str}".strip()

    entry = {
        "summary": summary,
        "platform": platform,
        "version": ver_str
    }
    if arch_display:
        entry["arch"] = arch_display
    elif hw_arches:
        entry["arch"] = " / ".join(hw_arches)

    if packages:
        entry["packages"] = packages
    if len(versions) > 1:
        entry["versions"] = versions
    if dates:
        entry["date"] = " / ".join(dates)
        if len(dates) > 1:
            entry["dates"] = dates

    # -------------------------------------------------------------
    # 仅在此处保留新增键（例如 channel / 渠道 等扩展键）
    # -------------------------------------------------------------
    all_extra_keys = set()
    for it in items:
        all_extra_keys.update(k for k in it.keys() if k not in STANDARD_RAW_KEYS)

    for k in sorted(all_extra_keys):
        vals = list(dict.fromkeys(str(it[k]).strip() for it in items if it.get(k)))
        if vals:
            entry[k] = " / ".join(vals)
            if len(vals) > 1:
                entry[f"{k}s"] = vals

    return entry


def compile_subid_map_to_runtime_dict(raw_map: dict) -> dict:
    runtime_dict = {}
    for subid_str, items in raw_map.items():
        subid = str(subid_str).strip()
        if not subid or not subid.isdigit():
            continue
        item_list = items if isinstance(items, list) else [items]
        entry = build_single_entry(subid, item_list)
        if entry:
            runtime_dict[subid] = entry

    return dict(sorted(runtime_dict.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0, reverse=True))
