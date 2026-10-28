#!/usr/bin/env python3
"""`tools/deletion_cost_report.py` 的牙：每一格写明它挡的是哪一种谎。

  K1 同一份目录自己比自己 ⇒ 所有分档必须为 0（挡住"恒有输出"）
  K2 摘掉某台的第一条 ⇒ 必须同时进「少线的台」与「首条变了」（挡住只报总数、漏掉父母落点变了）
  K3 一台被摘光 ⇒ 进「整台消失」，且**分项之和 == 线差**要仍然成立
     （少了这一支就会把整台的线漏在求和外面，10-22 那类"分项之和≠总数"的形状）
  K4 消失那台是不是必看台要按传进来的名单答，名单读不到时不许折成「没有必看台」
  K5 前面删掉一台 ⇒ 后面每台台号 -1，位移的方向与台数都得如实
  K6 线已经不在了但 lineQuality 还留着它的分数 ⇒ 孤儿键那一格必须数得到
  K7 **真产物正对照**（样本在 `.gitignore` 的 tools/.cache 里，不在就明写"没跑成"，不许当通过）
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))

spec = importlib.util.spec_from_file_location("dcr", BASE / "tools/deletion_cost_report.py")
dcr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dcr)

fails = []


def check(tag, got, want):
    ok = got == want
    print(("  [绿] " if ok else "  [红] ") + f"{tag}：实得 {got!r}" + ("" if ok else f"｜期望 {want!r}"))
    if not ok:
        fails.append(tag)


def cat(channels, quality=None):
    return {"generatedAt": "T", "blockedLines": {}, "lineQuality": quality or {},
            "lineMoves": {}, "categories": {}, "schemaVersion": 1,
            "channels": [{"id": cid, "name": name, "category": "cctv", "subgroup": "央视",
                          "logo": "", "streams": list(urls)} for cid, name, urls in channels]}


CH = [("c1", "CCTV-1 综合", ["http://h/a1", "http://h/a2", "http://h/a3"]),
      ("c2", "CCTV-2 财经", ["http://h/b1", "http://h/b2"]),
      ("c3", "辽宁体育", ["http://h/l1"])]

d0 = dcr.diff(cat(CH), cat(CH))
check("K1 自己比自己：台/线差都是 0", (d0["totals"]["channels"], d0["totals"]["streams"]), ((3, 3), (6, 6)))
check("K1b 所有分档为空", (len(d0["vanished"]), len(d0["lost"]), len(d0["first_changed"]),
                          len(d0["shifts"]), len(d0["orphan_quality"])), (0, 0, 0, 0, 0))

old = cat(CH)
new = cat([("c1", "CCTV-1 综合", ["http://h/a1", "http://h/a2", "http://h/a3"]),
           ("c2", "CCTV-2 财经", ["http://h/b2"]),          # 摘掉第一条
           ("c3", "辽宁体育", ["http://h/l1"])])
d1 = dcr.diff(old, new)
# 摘掉的是**第一枚** b1（我第一版把期望写成留下的那枚 b2＝期望值错、工具没错）
check("K2 少线的台 1 台、摘的就是被删那枚", (len(d1["lost"]), d1["lost"][0]["name"], d1["lost"][0]["urls"],
                            d1["lost"][0]["before"], d1["lost"][0]["after"]),
      (1, "CCTV-2 财经", ["http://h/b1"], 2, 1))
check("K2b 首条变了也必须被点名（这就是父母那一格）",
      [(x["name"], x["old"], x["new"]) for x in d1["first_changed"]],
      [("CCTV-2 财经", "http://h/b1", "http://h/b2")])
check("K2c 台号没变", len(d1["shifts"]), 0)

new2 = cat([("c1", "CCTV-1 综合", ["http://h/a1", "http://h/a2", "http://h/a3"]),
            ("c2", "CCTV-2 财经", ["http://h/b1", "http://h/b2"])])   # 只让辽宁体育整台没了（K2b）
d2 = dcr.diff(old, new2)
check("K3 整台消失 1 台", (len(d2["vanished"]), d2["vanished"][0]["name"], d2["vanished"][0]["lines"]),
      (1, "辽宁体育", 1))
r2 = dcr.render(d2)
check("K3b 整台那条线要算进求和（漏掉就永远不等）",
      (d2["totals"]["streams"], d2["totals"]["channels"],
       "｜被摘的地址 1 枚（含整台消失那 1 条）｜补进来的 0 枚（含整台新增那 0 条）｜等式 被摘 1 − 补进 0 == 线差 1 ✓" in r2,
       "✗" in r2),
      ((6, 5), (3, 2), True, False))
check("K4 必看台按名单答", dcr.diff(old, new2, must_names={"辽宁体育"})["vanished"][0]["must"], True)
check("K4b 名单是空集时如实答 False（读不到名单不许折成『没有必看台』）",
      dcr.diff(old, new2, must_names=set())["vanished"][0]["must"], False)
check("K4c 这台电视播过要单独一档", dcr.diff(old, new2, played_ids={"c3"})["vanished"][0]["played"], True)

new3 = cat([("c2", "CCTV-2 财经", ["http://h/b1", "http://h/b2"]),
            ("c3", "辽宁体育", ["http://h/l1"])])          # 删掉排在最前的 CCTV-1
d3 = dcr.diff(old, new3)
check("K5 后面每台台号 -1", sorted((x["name"], x["old"], x["new"]) for x in d3["shifts"]),
      [("CCTV-2 财经", 2, 1), ("辽宁体育", 3, 2)])
check("K5b 整台消失那台不进位移表（它没有新台号）",
      [x["name"] for x in d3["shifts"] if x["name"] == "CCTV-1 综合"], [])
check("K5c 辽宁体育的台号：从 3 到 2（这一格就是要报给父母的那句话）",
      next(x["new"] for x in d3["shifts"] if x["name"] == "辽宁体育"), 2)

d4 = dcr.diff(cat(CH, {"http://h/l1": 0.9}), new2)
check("K6 线没了但分数还在 ⇒ 孤儿键数得到", len(d4["orphan_quality"]), 1)

# K7 那四格原来钉的是「`.cache` 里按小时命名的落盘前备份」对「此刻的在库资产」：
# 每落一次盘这两份就一起变，期望值会一片红（10-27 现读到过三轮：-1 → -2 → 十五枚孤儿键），
# 而红的从来不是判据、是基线。现在钉的是**冻结进版本库的那一对**（甲批落盘前后各一份），
# 上面两格 K7a 先验这两份的 sha16，对不上就直接报"钉的那一对被换了"，不许让下面四格绿着漂。
sample = BASE / "tools/fixtures/asset_before_placeholder_batch.json"
live = BASE / "tools/fixtures/asset_after_placeholder_batch.json"
PAIR_SHA = {"before": "a2db0d619e745b14", "after": "ffb7562fc9ca062a"}


def _sha16(p):
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


if sample.exists() and live.exists():
    check("K7a 钉住的那一对没被换（10-27 13:40 甲批：276 台/485 条 → 276 台/472 条）",
          (_sha16(sample), _sha16(live)), (PAIR_SHA["before"], PAIR_SHA["after"]))
    d5 = dcr.diff(dcr._load(sample), dcr._load(live))
    lost = [u for x in d5["lost"] for u in x["urls"]]
    check("K7 真产物：台数没变、线少 13 枚、黑名单多 13 枚",
          (d5["totals"]["channels"], d5["totals"]["streams"][1] - d5["totals"]["streams"][0],
           d5["totals"]["blocked"][1] - d5["totals"]["blocked"][0]),
          ((276, 276), -13, 13))
    check("K7b 真产物：摘掉的 13 枚全是这一族（12 枚 8.138.7.223 ＋ 1 枚 kwimgs 定长片）",
          (len(lost), sum(1 for u in lost if "8.138.7.223" in u),
           sum(1 for u in lost if "kwimgs" in u)), (13, 12, 1))
    check("K7c 真产物：首条变了的正好是这 8 台",
          sorted(x["name"] for x in d5["first_changed"]),
          sorted(["CCTV-2 财经", "CCTV-7 国防军事", "CCTV-9 纪录", "CCTV-10 科教",
                  "CCTV-12 社会与法", "CCTV-13 新闻", "CCTV-17 农业农村", "广东卫视"]))
    check("K7d 真产物：lineQuality 里 13 枚已摘地址成了孤儿键、lineMoves 0 枚、台号位移 0 台",
          (len(d5["orphan_quality"]), len(d5.get("orphan_moves") or []), len(d5["shifts"])), (13, 0, 0))
else:
    print("  [跳] K7 真产物样本不在（tools/.cache 被 gitignore；这一格是『没跑成』，不是『通过』）")

# K8「整台换组」不许被算成整台消失（10-27 16:3x 现读抓出来的缺陷：旧版按 id 配对，
# 山东教育卫视 从 cctv/卫视 挪到 local/山东 时 id 一定变 ⇒ 它被报成「整台消失」，
# 它那 2 条线被报成「被摘」，[少线的台] 那行自己打了 25 != 23 ✗，而真正消失的那台反倒没人念）。
def mkcat(rows):
    return {"generatedAt": "T", "blockedLines": {}, "lineQuality": {}, "lineMoves": {},
            "categories": {}, "schemaVersion": 1,
            "channels": [{"id": i, "name": n, "category": c, "subgroup": sg, "logo": "",
                          "streams": list(u)} for i, n, c, sg, u in rows]}


SAME = ["http://h/e1", "http://h/e2"]
BASE_ROWS = [("c1", "CCTV-1 综合", "cctv", "央视", ["http://h/a1"]),
             ("cctv-educ", "山东教育卫视", "cctv", "卫视", SAME),
             ("c3", "辽宁体育", "sports", "国内体育", ["http://h/l1"])]
MOVED = [("c1", "CCTV-1 综合", "cctv", "央视", ["http://h/a1"]),
         ("local-educ", "山东教育卫视", "local", "山东", SAME),
         ("c3", "辽宁体育", "sports", "国内体育", ["http://h/l1"])]
d8 = dcr.diff(mkcat(BASE_ROWS), mkcat(MOVED))
check("K8 换组那台既不算消失也不算新增，只进换组那一栏",
      (len(d8["vanished"]), len(d8["added"]), len(d8["regrouped"])), (0, 0, 1))
check("K8a 换组不许顺手摘线：被摘 0 枚、线差 0（分项之和与总数仍然自洽）",
      (len([u for x in d8["lost"] for u in x["urls"]]),
       d8["totals"]["streams"][1] - d8["totals"]["streams"][0]), (0, 0))
check("K8b 换组读数把离开/到达的组与「地址一字未换」都答出来",
      (d8["regrouped"][0]["old"], d8["regrouped"][0]["new"], d8["regrouped"][0]["same_urls"]),
      ("cctv/卫视", "local/山东", True))
# 换组同时把这台挪到队尾：台号位移必须看得见（数字键就是位次）
TAIL = [MOVED[0], MOVED[2], MOVED[1]]
d8b = dcr.diff(mkcat(BASE_ROWS), mkcat(TAIL))
check("K8c 换组还挪了位次 ⇒ 位移那一格要指名它",
      [(x["name"], x["old"], x["new"]) for x in d8b["shifts"] if x["name"] == "山东教育卫视"],
      [("山东教育卫视", 2, 3)])
# 负对照：台名也换了才是真消失，不许被"按台名配对"吞掉
RENAMED = [MOVED[0], ("local-educ", "山东教育频道", "local", "山东", SAME), MOVED[2]]
d8c = dcr.diff(mkcat(BASE_ROWS), mkcat(RENAMED))
check("K8d 负对照：台名真变了要报成消失 1 ＋新增 1（换组那一栏不能吞掉它）",
      (len(d8c["vanished"]), len(d8c["added"]), len(d8c["regrouped"])), (1, 1, 0))
check("K8e 负对照：这一台在这台电视播过要按台名也认得到（只按 id 认会读成没播过）",
      (dcr.diff(mkcat(BASE_ROWS), mkcat(RENAMED), played_ids={"cctv-educ"})["vanished"][0]["played"],
       dcr.diff(mkcat(BASE_ROWS), mkcat(RENAMED), played_names={"山东教育卫视"})["vanished"][0]["played"],
       dcr.diff(mkcat(BASE_ROWS), mkcat(RENAMED))["vanished"][0]["played"]),
      (True, True, False))

# K9 目录**变大**时旧等式自己报假故障：10-27 17:29 钉回 Al Jazeera 那一批线 449→450、被摘 0 枚，
# 旧代码只数被摘那一侧 ⇒ 打「分项之和 0 == 线差 -1 ✗」。补进那一侧必须一起记账。
d9 = dcr.diff(cat(CH[:2]), cat(CH))
r9 = dcr.render(d9)
check("K9 只新增：被摘 0 − 补进 1 == 线差 -1 要成立，且整张报告不许有 ✗",
      (len(d9["added"]), d9["totals"]["streams"],
       "等式 被摘 0 − 补进 1 == 线差 -1 ✓" in r9, "✗" in r9),
      (1, (5, 6), True, False))
mixed_new = cat([("c1", "CCTV-1 综合", ["http://h/a1", "http://h/a2"]),
                 ("c2", "CCTV-2 财经", ["http://h/b1", "http://h/b2"]),
                 ("c3", "辽宁体育", ["http://h/l1"]),
                 ("c4", "新增台", ["http://h/n1", "http://h/n2"])])
d9b = dcr.diff(cat(CH), mixed_new)
check("K9b 负对照：同一批里摘 1 枚＋整台新增 2 枚，两侧各自记账后求和才等于线差",
      (sum(len(x["urls"]) for x in d9b["lost"]),
       sum(len(x["urls"]) for x in d9b["gained"]),
       sum(x["lines"] for x in d9b["added"]), d9b["totals"]["streams"],
       "等式 被摘 1 − 补进 2 == 线差 -1 ✓" in dcr.render(d9b)),
      (1, 0, 2, (6, 7), True))
check("K9c 牙齿：把补进那一侧摘掉（退回旧写法）等式就不成立——这一格是来守方向的",
      sum(len(x["urls"]) for x in d9b["lost"]) == d9b["totals"]["streams"][0] - d9b["totals"]["streams"][1],
      False)

print(f"[deletion_cost] 判红 {len(fails)} 格")
for f in fails:
    print("  红:", f)
sys.exit(1 if fails else 0)
