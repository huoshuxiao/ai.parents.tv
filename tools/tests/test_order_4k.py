#!/usr/bin/env python3
"""验 2026-10-30 两条 AGENTS 口径在构建里真的生效：线路按清晰度排序、卫视 4K 只留 3840 宽。

用户原话（AGENTS.md）：
  「线路排序规则，清晰度由高到低排序，相同清晰度抖动小的排前」
  「4K频道清晰度全高清及以上」＋「卫视4K频道线路分辨率3840x2160」
  点档：「将卫视4K频道非3840线路移入对应的卫视频道，要与原有线路去重」

盯住的会说谎的形状：
  A) 拿面积比清晰度 —— 1920×1090 比 1920×1080 多的 10 行是填充，屏幕上没差别；
     按面积排会让首条线每换一次抽帧尺就翻一次（10-30 重建 45 台首条变化里就有这种）。
  B) 「相同清晰度抖动小的排前」只写在注释里、没人消费。
  C) 慢线降位跨了清晰度档 —— 1920 的慢线不许排到 1280 的快线后面。
  D) 卫视 4K 台里混进非 3840 的线（重建会再造出「东方卫视 4K」）。
  E) 搬线时不按 canon 去重 —— 基础台里同一条地址出现两次。
  F) 搬空台不删；或者找不到基础台就静默删线/凭空造台。
  G) 尺寸未知的线被当成「不是 4K」搬走（没读数不许判）。
  H) 整格恒绿：把被测函数换成空操作，结论必须翻面。

跑法：python3 tools/tests/test_order_4k.py    （退出码 0 才算过）
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402
import stream_rules  # noqa: E402

canon = stream_rules.canon

FHD_1080 = "http://10.1.0.1:8080/live/a.m3u8"   # 1920x1080
FHD_1090 = "http://10.1.0.2:8080/live/b.m3u8"   # 1920x1090（填充差 10 行，同一档）
UHD_4K = "http://10.1.0.3:8080/live/c.m3u8"     # 3840x2160
HD_720 = "http://10.1.0.4:8080/live/d.m3u8"     # 1280x720
UNKNOWN = "http://10.1.0.5:8080/live/e.m3u8"    # 没读数
JITTER_OK = "http://10.1.0.6:8080/live/f.m3u8"  # 1920x1080，抖动过闸
JITTER_BAD = "http://10.1.0.7:8080/live/g.m3u8"  # 1920x1080，抖动贴着码率

failures: list[str] = []
ran = 0


def check(label: str, got, want) -> None:
    global ran
    ran += 1
    ok = got == want
    print(("  [绿] " if ok else "  [红] ") + label + f"：实得 {got!r}" + ("" if ok else f"｜期望 {want!r}"))
    if not ok:
        failures.append(label)


def setup() -> None:
    bc.FRAME_RES = {canon(FHD_1080): [1920, 1080], canon(FHD_1090): [1920, 1090],
                    canon(UHD_4K): [3840, 2160], canon(HD_720): [1280, 720],
                    canon(JITTER_OK): [1920, 1080], canon(JITTER_BAD): [1920, 1080]}
    bc.HEALTH = {}
    bc.IDENTITY, bc.REVIEW_LOGO, bc.FRAME_PLACEHOLDER = {}, {}, {}
    bc.FROZEN, bc.RETEST_CUT, bc.CCTV3_CUT = {}, set(), {}
    bc.DEVICE_PROOF = {}
    bc.STREAM_RAW = {}
    bc.LINE_JITTER = {
        canon(JITTER_OK): {"median": 2.10, "min": 0.89, "rounds": 12},
        canon(JITTER_BAD): {"median": 1.05, "min": 0.68, "rounds": 12},
    }


def row(name, category, subgroup, urls):
    return {"id": f"{category}-{name}", "name": name, "category": category,
            "subgroup": subgroup, "logo": "", "streams": list(urls)}


# ── A：清晰度档 ────────────────────────────────────────────────────────────────
setup()
check("A1 1920×1090 与 1920×1080 是同一档", bc.resolution_rank(FHD_1090), bc.resolution_rank(FHD_1080))
check("A2 3840 比 1920 高一档", bc.resolution_rank(UHD_4K) > bc.resolution_rank(FHD_1080), True)
check("A3 1920 比 1280 高一档", bc.resolution_rank(FHD_1080) > bc.resolution_rank(HD_720), True)
check("A4 没读数是 0 档", bc.resolution_rank(UNKNOWN), 0)
key = bc.res_first_key("测试台")
# 面积那把尺会把 1090 顶到 1080 前面（2092800 > 2073600）；折成档后同档打平，
# 次序交给证据档 —— 1090 那条本机连不上就该沉到 1080 后面。
bc.HEALTH = {canon(FHD_1090): {"verdict": "连接超时"},
             canon(FHD_1080): {"verdict": "可取到分片"}}
check("A5 同档 1090 与 1080：连不上的那条排后面（不再被 10 行填充顶到第 1）",
      sorted([FHD_1090, FHD_1080], key=key)[0], FHD_1080)
bc.HEALTH = {}
check("A6 4K 线排在 1080 线前", sorted([FHD_1080, UHD_4K], key=key)[0], UHD_4K)
check("A7 1080 线排在 720 线前", sorted([HD_720, FHD_1080], key=key)[0], FHD_1080)

# ── B：同清晰度抖动小的排前 ────────────────────────────────────────────────────
setup()
check("B1 抖动过 1.2 倍闸的排在同档最前",
      sorted([JITTER_BAD, JITTER_OK], key=key), [JITTER_OK, JITTER_BAD])
check("B2 抖动也排在其他没测过的同档线前面",
      sorted([FHD_1080, JITTER_OK], key=key)[0], JITTER_OK)
# 负对照：把抖动表清空 ⇒ 同档内次序回到原序（证明 B1 真在吃 LINE_JITTER）
saved_jitter = bc.LINE_JITTER
bc.LINE_JITTER = {}
try:
    check("B1n 负对照：抖动表清空后两条同档线打平（次序交给后几段）",
          sorted([JITTER_BAD, JITTER_OK], key=key), [JITTER_BAD, JITTER_OK])
finally:
    bc.LINE_JITTER = saved_jitter

# ── C：慢线降位不跨档 ─────────────────────────────────────────────────────────
setup()
bc.HEALTH = {canon(FHD_1080): {"verdict": "可取到分片", "elapsed_ms": 30000}}
check("C1 1920 的慢线仍然排在 1280 快线前面（slow_penalty 只在同档内）",
      sorted([FHD_1080, HD_720], key=key)[0], FHD_1080)
check("C2 同档里慢线排到快线后面",
      sorted([FHD_1080, FHD_1090], key=key), [FHD_1090, FHD_1080])

# ── D/E/F/G：卫视 4K 只留 3840 ─────────────────────────────────────────────────
setup()
channels = [
    row("东方卫视", "cctv", "卫视", [FHD_1080]),
    row("东方卫视 4K", "cctv", "4K 超高清", [FHD_1090, FHD_1080, UHD_4K]),
    row("苏州 4K", "cctv", "4K 超高清", [UHD_4K]),
    row("无主 4K", "cctv", "4K 超高清", [FHD_1080]),
    row("未知台 4K", "cctv", "4K 超高清", [UNKNOWN]),
    row("CCTV-4K 超高清", "cctv", "央视", [FHD_1080]),
]
kept, moved, dropped = bc.enforce_sat_4k(channels)
by = {ch["name"]: ch for ch in kept}
check("D1 非 3840 线搬进基础台、按 canon 去重（1080 那条本来就有；同档同证据时保持原序）",
      by["东方卫视"]["streams"], [FHD_1080, FHD_1090])
check("D2 4K 台只留 3840 那条", by["东方卫视 4K"]["streams"], [UHD_4K])
check("D3 搬线记账：待搬 2 条、实搬 1 条", moved, [("东方卫视 4K", "东方卫视", 2, 1)])
check("E1 3840 那条线留在 4K 台（不搬）", kept[[ch["name"] for ch in kept].index("苏州 4K")]["streams"], [UHD_4K])
check("F1 搬空且找不到基础台的台原样保留、不删", "无主 4K" in by, True)
check("F2 搬空被删的台一个都没有", dropped, [])
check("G1 尺寸未知的线不搬（判不了 3840），台也留着", by["未知台 4K"]["streams"], [UNKNOWN])
check("G2 央视组的 CCTV-4K 超高清不受这条规则管", by["CCTV-4K 超高清"]["streams"], [FHD_1080])

# 搬空 → 删台那一格：基础台存在时 4K 台必须消失
channels2 = [
    row("东方卫视", "cctv", "卫视", [FHD_1080]),
    row("东方卫视 4K", "cctv", "4K 超高清", [FHD_1090]),
]
kept2, moved2, dropped2 = bc.enforce_sat_4k(channels2)
check("F3 搬空后整台删除", [ch["name"] for ch in kept2], ["东方卫视"])
check("F3b 删台名记在返回值里", dropped2, ["东方卫视 4K"])

# 负对照：把这一棒换成空操作 ⇒ 那个 4K 台照样活着（证明 F3 是这一行在管）
def _noop(chans):
    return chans, [], []
saved_enforce = bc.enforce_sat_4k
bc.enforce_sat_4k = _noop
try:
    kept3, _, _ = bc.enforce_sat_4k(channels2)
    check("F3n 负对照：去掉这一棒后 4K 台还在", [ch["name"] for ch in kept3], ["东方卫视", "东方卫视 4K"])
finally:
    bc.enforce_sat_4k = saved_enforce

print(f"[order-4k] 跑了 {ran} 格，失败 {len(failures)} 格")
for f in failures:
    print("  [判红] " + f)
raise SystemExit(1 if failures else 0)
