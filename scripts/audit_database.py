import json
import re
from pathlib import Path

DATA_FILE = Path("data/qq_subid_map.json")

if not DATA_FILE.exists():
    print("[错误] 未找到 data/qq_subid_map.json！")
    exit(1)

data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
print(f"=== 开始全盘深度审计 (当前收录 SubID 主键: {len(data)} 个) ===")

total_entries = 0
platform_counts = {}
anomalies = []
timeline = []

for subid, val in data.items():
    items = val if isinstance(val, list) else [val]

    for item in items:
        total_entries += 1
        ver = str(item.get("version", ""))
        plat = str(item.get("platform", ""))
        arch = str(item.get("arch", ""))
        date = str(item.get("date", ""))
        url = str(item.get("source_url", ""))

        platform_counts[plat] = platform_counts.get(plat, 0) + 1

        reasons = []

        # 1. 结构与格式合规性检查
        if not subid.isdigit() or len(subid) != 9 or not subid.startswith("537"):
            reasons.append("SubID 非 537 开头的标准 9 位数字")

        if not ver or ver == "Unknown" or re.search(r"[\u4e00-\u9fa5]", ver):
            reasons.append("版本号包含汉字或为 Unknown")

        if arch.startswith("http") or not arch:
            reasons.append("架构字段残缺或被 URL 污染")

        if plat == "Android":
            pure_v = ver.replace("-Beta", "").strip()
            if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d{4,6})?", pure_v):
                reasons.append("Android 版本号格式不合法")

        if reasons:
            anomalies.append(
                {
                    "subid": subid,
                    "version": ver,
                    "platform": plat,
                    "arch": arch,
                    "date": date,
                    "reasons": reasons,
                    "url": url,
                }
            )

        # 收集时序数据供单调性检验
        if subid.isdigit() and date:
            timeline.append(
                {
                    "subid": int(subid),
                    "subid_str": subid,
                    "date": date,
                    "version": ver,
                    "platform": plat,
                    "arch": arch,
                }
            )

# 2. 时序单调性与离群突变检测
timeline.sort(key=lambda x: (x["date"], x["version"]))
for i in range(1, len(timeline)):
    prev = timeline[i - 1]
    curr = timeline[i]
    # 如果时间明显往后走，但 SubID 却出现超过 20000 以上的剧烈断崖式暴跌，标记为可疑离群点
    if curr["date"] > prev["date"] and (prev["subid"] - curr["subid"]) > 20000:
        anomalies.append(
            {
                "subid": curr["subid_str"],
                "version": curr["version"],
                "platform": curr["platform"],
                "arch": curr["arch"],
                "date": curr["date"],
                "reasons": [
                    f"时序异常突变: 较前一日 ({prev['date']}) 的 SubID {prev['subid_str']} 发生异常倒退"
                ],
                "url": "N/A",
            }
        )

# 3. 输出审计总结报告
print("\n--- 平台收录分布统计 ---")
for p, c in sorted(platform_counts.items()):
    print(f"- {p:<10}: {c} 条记录")
print(f"总计实体映射条数: {total_entries} 条")

print("\n--- 异常与风险项排查结果 ---")
if not anomalies:
    print(
        "[审计通过] 恭喜！未发现任何异常数据，所有 SubID 格式规范，时序单调平滑递增，无任何污染！"
    )
else:
    print(f"[发现风险] 共检测到 {len(anomalies)} 处需要核对的异常条目：")
    for a in anomalies:
        print(f"\n* SubID: {a['subid']} | {a['platform']} - {a['arch']} | 版本: {a['version']} | 日期: {a['date']}")
        print(f"  风险原因: {', '.join(a['reasons'])}")
        if a['url'] != "N/A":
            print(f"  来源链接: {a['url']}")

print("\n=== 审计流程结束 ===")
