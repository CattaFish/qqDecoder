# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

REAL_HW_ARCHES = {
    "x86": "X86", "x64": "X64", "arm": "Arm", "arm64": "Arm64",
    "loongarch": "LoongArch", "mips": "Mips", "pad": "Pad"
}
PACKAGE_FORMATS = {"rpm", "deb", "appimage", "dmg", "pkg", "apk"}
STANDARD_RAW_KEYS = {"platform", "arch", "version", "date", "source_url", "is_beta"}


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
            if val not in hw_arches: hw_arches.append(val)
        elif low in PACKAGE_FORMATS:
            pkg = a.upper()
            if pkg not in packages: packages.append(pkg)

    versions = list(dict.fromkeys(it.get("version", "").strip() for it in items if it.get("version")))
    dates = list(dict.fromkeys(it.get("date", "").strip() for it in items if it.get("date")))

    arch_display = hw_arches[0] if len(hw_arches) == 1 else None

    ver_str = " / ".join(versions) if versions else "未知版本"
    plat_low = platform.lower()

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

    # ★ 绝对严苛：只有你在源库明确写了 is_beta == True，才输出该字段！绝不乱猜，绝不输出 false！
    if any(it.get("is_beta") is True for it in items):
        entry["is_beta"] = True

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


if __name__ == "__main__":
    map_file = Path("data/qq_subid_map.json")
    out_file = Path("data/subid.json")

    if not map_file.exists():
        print(f"[错误] 找不到: {map_file}")
        sys.exit(1)

    raw_map = json.loads(map_file.read_text(encoding="utf-8"))
    compiled = compile_subid_map_to_runtime_dict(raw_map)
    out_file.write_text(json.dumps(compiled, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[✔] 成功重新编译 {out_file}！共收录 {len(compiled)} 条")