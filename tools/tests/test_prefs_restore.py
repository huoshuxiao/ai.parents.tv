#!/usr/bin/env python3
"""`prefs_restore_lines.py` 的夹具：这一支要写设备文件，所以每一档都要说清它挡的是哪一种谎。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prefs_restore_lines as pr          # noqa: E402

fails, n = [], 0


def check(name, got, want):
    global n
    n += 1
    if got != want:
        fails.append(f"{name}: 期望 {want!r} 实得 {got!r}")


CAT = {"a": {"u1", "u2"}, "b": {"u1", "u9"}, "c": {"u5"}}
BLOCK = {"u9-bad"}

# P) 四类分开、分项之和==台数
plan = pr.restore_plan(
    proven={"a": [(2, "u2", 900), (1, "u1", 3000)],       # 已经是 u1？设备现在记 u2 ⇒ 要改成线 1 的 u1
            "b": [(1, "u1", 800)],                        # 设备已经记 u1 ⇒ already
            "d": [(3, "u3", 700)],                        # 目标地址不在设备目录（d 压根不在 CAT）⇒ refuse
            "e": [(1, "u9-bad", 500)]},                   # 目标在黑名单 ⇒ refuse
    current={"a": "u2", "b": "u1", "f": "uf"},            # f：这一遍没有真播过的线 ⇒ no_proven
    blocked=BLOCK, catalog_urls=CAT)
check("P1 要改只算真该改的那台", [x["cid"] for x in plan["change"]], ["a"])
check("P2 多条真播过时挑线号最小的那条", plan["change"][0]["line"], 1)
check("P3 已经对的不重复改", [x["cid"] for x in plan["already"]], ["b"])
check("P4 这一遍没有真播过＝不动它，也不删键", [x["cid"] for x in plan["no_proven"]], ["f"])
check("P5 目标地址不在设备目录＝拒绝（写了就是悬空起点）",
      [x["cid"] for x in plan["refuse"]], ["d", "e"])
check("P6 拒绝的理由分得开（一个悬空、一个在黑名单）",
      ["不在设备那份目录" in x["why"] for x in plan["refuse"]], [True, False])
check("P7 分项之和==台数",
      len(plan["already"]) + len(plan["change"]) + len(plan["no_proven"]) + len(plan["refuse"]),
      plan["channels"])

# S) 负对照：把 proven 掏空 ⇒ change/already 必须全空，但 no_proven 还得数得到（不许恒真）
empty = pr.restore_plan(proven={}, current={"a": "u2", "b": "u1"}, blocked=BLOCK, catalog_urls=CAT)
check("S1 没有台账依据时一条都不许改", len(empty["change"]), 0)
check("S2 没有台账依据时一条都不许当『已经对』", len(empty["already"]), 0)
check("S3 掏空之后设备那两枚键必须落在『不动』里（不是消失）",
      [x["cid"] for x in empty["no_proven"]], ["a", "b"])

# E) 转义往返：URL 里的 & 写出去必须是 &amp;，读回来要逐字等于目标
raw = "http://h/p?id=cctv12&t=1&k=2"
line = '<string name="line_a">' + pr.escape_value(raw) + "</string>"
check("E1 写出去真的转义了（不是原地塞 &）", pr.escape_value(raw), "http://h/p?id=cctv12&amp;t=1&amp;k=2")
check("E2 回读解回来逐字等于目标", pr.parse_prefs("<map>" + line + "</map>")["a"], raw)
# E3 解析器不许被 `&amp;` 里那个分号骗成提前结束
xml3 = '<map><string name="line_x">http://h?a=1&amp;b=2&amp;c=3</string></map>'
check("E3 含多个 &amp; 的键整枚读全", pr.parse_prefs(xml3)["x"], "http://h?a=1&b=2&c=3")
# E4 现读真设备的形状（两份快照之一）：拿一份真文件当被试，键数与文件里数出来的枚数要同
ROOT = Path(__file__).resolve().parents[2]
CANDS = [Path("tools/.cache/prefs_mid_cctv_v6_pass.xml"),
         ROOT / "tools/.cache/prefs_mid_cctv_v6_pass.xml"]
sample = next((q for q in CANDS if q.exists()), None)
if sample is not None:
    text = sample.read_text()
    got = pr.parse_prefs(text)
    check("E4 真文件里 <string name=\"line_…\"> 的枚数==解析到的台数",
          len(got), text.count('<string name="line_'))
    # E5 前缀撞车：`line_switch_seconds` 是另一枚设置（线路时长），不是"某台记住的线"。
    # 它混进恢复名单的话，写盘会把一个整数设置当成 URL 覆掉。
    check("E5 那枚撞前缀的设置键不许被当成频道", "switch_seconds" in got, False)
    check("E6 它确实以 int 形制存在于同一份文件里（所以只能靠解析器挡住）",
          'name="line_switch_seconds"' in text, True)
else:
    check("E4 真文件样本不在（这一格红＝没拿到被试）", 0, 1)

print(f"[prefs_restore] 夹具 {n} 格，判红 {len(fails)} 格")
for f in fails:
    print("  红:", f)
sys.exit(1 if fails else 0)
