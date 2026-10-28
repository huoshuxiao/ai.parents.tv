#!/usr/bin/env python3
"""验的是真机回测尺（`tools/tv_retest_lines.py`，落笔现读 TOOLV=8；本文件第一版锚在 v6，
#140 之后判据多了一道「首帧必须能证明是本线打的」）改掉的几种会说谎的读数。

为什么要换这把尺（10-26 在这台小米电视 192.168.1.4 上量出来的）：
  · v1 把 `QualityWatch` 第一拍的 `0x0` 当成分辨率写进台账 ⇒ 读出「这台电视放了个 0×0」；
    现读真行：`sports-7cfbe253ed` 在 14:27:25 打 `0x0`、14:27:26 就打 `3840x2160`，那一格的
    `res` 其实取早了。
  · v1 把 `first_frame` 定义成「出过第一帧 **或** 应用说在播」⇒ 「没画面」与「应用还在播上一个台」
    两种完全不同的事被并成一个数，而这正是用户一直在说的「正在连接」。
  · v1 的窗口从"按下台号"起算固定秒数；这台电视排队时应用会晚一两分钟才跳到目标台 ⇒
    那一格记的是上一个台的余波。咪咕那 16 格里 6 格被读成「没首帧」就是这么来的。

下面每一行日志都是从 `adb logcat -d -v threadtime` 的原始输出里逐字切下来的，不是我编的行形。

跑法：python3 tools/tests/test_tv_retest_lines.py    （退出码 0 才算过）
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
MODULE = Path(os.environ.get("TVRL_MODULE") or BASE / "tools" / "tv_retest_lines.py")
_spec = importlib.util.spec_from_file_location("tvrl", MODULE)
tvrl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tvrl)

CID_A = "sports-f432164f71"      # 这一台有完整三件套：0x0 → 1920x1080 → firstFrame
CID_B = "sports-8026757828"      # 这一台只有 watch＋stall/recover，全程没有 firstFrame
L_QW0_A = ("10-26 14:46:40.996 15445 15445 I QualityWatch: channel=sports-f432164f71 "
           "line=1 0x0 t=14852742")
L_QW1080_A = ("10-26 14:46:41.909 15445 15445 I QualityWatch: channel=sports-f432164f71 "
              "line=1 1920x1080 t=14853655")
L_FIRST_A = ("10-26 14:46:41.911 15445 15445 I ZapFix  : firstFrame channel=sports-f432164f71 "
             "line=1 t=14853657")
L_WATCH_A0 = ("10-26 14:46:50.956 15445 15445 I StallWatch: watch channel=sports-f432164f71 "
              "line=1 playing=true 卡顿0次/0ms t=14862703")
L_QW4K_B = ("10-26 14:27:26.669 15445 15445 I QualityWatch: channel=sports-7cfbe253ed "
            "line=1 3840x2160 t=13698416")
L_STALL_B = ("10-26 14:31:53.194 15445 15445 I StallWatch: stall channel=sports-8026757828 "
             "line=1 t=13964941")
L_RECOVER_B = ("10-26 14:31:56.760 15445 15445 I StallWatch: recover channel=sports-8026757828 "
               "line=1 stall=3566ms 卡顿1次/3566ms t=13968507")
L_WATCH_B1 = ("10-26 14:32:09.084 15445 15445 I StallWatch: watch channel=sports-8026757828 "
              "line=1 playing=true 卡顿1次/3566ms t=13980831")
L_QW0_B = ("10-26 14:30:39.113 15445 15445 I QualityWatch: channel=sports-8026757828 "
           "line=1 0x0 t=13890860")

ITEM_A = {"channel_id": CID_A, "line_index": 0, "url": "http://x/a.m3u8", "name": "甲台", "number": 61}
ITEM_B = {"channel_id": CID_B, "line_index": 0, "url": "http://x/b.m3u8", "name": "乙台", "number": 62}

fails = []
n_check = 0


def check(tag, got, want):
    global n_check
    n_check += 1
    if got != want:
        fails.append(f"{tag}: 期望 {want!r}，实得 {got!r}")


def run(item, lines, last_t=0, want_line=None):
    cell = tvrl.new_cell()
    for ln in lines:
        last_t = tvrl.absorb(cell, item, ln, last_t, want_line=want_line)
    return cell


# A) 0x0 不许当分辨率，后来的真尺寸必须胜出（真行：0x0 → 1920x1080）
c = run(ITEM_A, [L_QW0_A, L_QW1080_A])
check("A1 res", c["res"], "1920x1080")
check("A2 res_zero 计数", c["res_zero"], 1)
# A3/A4：这一格就是 v1 打脸的那一种——窗口里只等到 0x0（真行来自咪咕直播 4K-1 那 90 秒）。
# v1 会把 "0x0" 当分辨率写进台账，读起来像"这台电视放了个 0×0"；v2 必须留空并另记 res_zero。
c = run(ITEM_B, [L_QW0_B])
check("A3 只有 0x0 时 res 留空", c["res"], None)
check("A4 只有 0x0 时 res_zero=1", c["res_zero"], 1)

# B) 旧行不许算进这一格：last_t 抬到那一行之后再来一遍，格子必须还是空的
c = run(ITEM_A, [L_QW0_A, L_QW1080_A, L_FIRST_A], last_t=14853657)
check("B1 seen（全是旧行）", c["seen"], False)
check("B2 res（旧行不许补写）", c["res"], None)

# C) 别的台的行不许串进来（负对照：把甲台的行喂给乙台那一格）
c = run(ITEM_B, [L_QW0_A, L_FIRST_A, L_WATCH_A0])
check("C1 seen", c["seen"], False)
c = run(ITEM_B, [L_QW0_B, L_STALL_B, L_RECOVER_B, L_WATCH_B1])
check("C3 本台行照收", c["seen"], True)

# D) firstFrame 只认最早那一次，后面的重复行不许覆盖
c = run(ITEM_A, [L_FIRST_A, L_WATCH_A0])
check("D1 first", c["first"], 14853657)

# E) played 与 first_frame 分家（v1 打脸那一格）：只有 watch 行时不能读成"出过画面"
c = run(ITEM_B, [L_QW0_B, L_WATCH_B1])
check("E1 first_frame", bool(c["first"]), False)
check("E2 played", c["playing"], True)

# F) stall/recover 计数与毫秒都来自真行
c = run(ITEM_B, [L_QW0_B, L_STALL_B, L_RECOVER_B, L_WATCH_B1])
check("F1 stalls", c["stalls"], 1)
check("F2 stall_ms", c["stall_ms"], 3566)
check("F3 cum", c["cum"], (1, 3566))

# G) 跨台地址的读数不冲突：4K 那行属于第三台，喂进甲台的格子必须被丢掉
c = run(ITEM_A, [L_QW4K_B, L_FIRST_A])
check("G1 res（别台的 4K 不许顶替本台）", c["res"], None)
check("G2 firstFrame 仍在本台", c["first"], 14853657)

# K) v3 那一刀：只认这一条线的行。真行——line=1 的 firstFrame 与 line=2 的 QualityWatch
L_FIRST_L1 = ("10-26 15:50:54.519 15445 15445 I ZapFix  : firstFrame channel=sports-f432164f71 "
              "line=1 t=18706266")
L_QW_L2 = ("10-26 15:51:49.908 15445 15445 I QualityWatch: channel=sports-f432164f71 "
           "line=2 1920x1080 t=18761654")
c = run(ITEM_A, [L_FIRST_L1, L_QW_L2], want_line=2)
check("K1 要第 2 条线时，第 1 条线的 firstFrame 不许算进来", c["first"], None)
check("K2 本线的分辨率照收", c["res"], "1920x1080")
c = run(ITEM_A, [L_FIRST_L1, L_QW_L2])
check("K3 不传线号时两行都收（正对照）", c["first"], 18706266)


# L) #140：判据侧。首帧必须能证明是**这条线自己**打的，借来的不许判「真机播过」。
#    写侧（两处 absorb 都带 want_line）修在 TOOLV 7；这里守的是判据侧，旧台账那 4 枚借来的照样不算。
def cell(**kw):
    base = dict(channel_id=CID_A, name="甲台", number=1, line_index=0, url="http://x/a.m3u8",
                first_frame=True, first_line=1, played=False, seen=True, round=1)
    base.update(kw)
    return base


check("L1 首帧属于本线 ⇒ 真机播过", tvrl.verdict_for([cell()], 1), "真机播过")
check("L2 借来的首帧（计划线1、实际线2）⇒ 不作数",
      tvrl.verdict_for([cell(first_line=2)], 1), "出过画但线号对不上（不作数）")
check("L3 没记线号的旧行（v5/v6 之前那代）仍按原口径算首帧（作用域只剔借来的）",
      tvrl.verdict_for([cell(first_line=None)], 1), "真机播过")
check("L4 借来的一枚 + 本线真首帧同在 ⇒ 仍判真机播过（不许一枚脏行连坐）",
      tvrl.verdict_for([cell(first_line=2), cell()], 1), "真机播过")
check("L5 借来的首帧 + 应用说在播 ⇒ 仍是『线号对不上』，不许升格、也不许降成没出画",
      tvrl.verdict_for([cell(first_line=2, played=True)], 1), "出过画但线号对不上（不作数）")
check("L6 一行首帧都没有、应用只在播 ⇒ 落到台但没出画（旧口径的正对照）",
      tvrl.verdict_for([cell(first_frame=False, first_line=None, played=True)], 1), "落到台但没出画")

# M) 三个数分开报，且分项之和必须等于总首帧行（10-22 那次数错分档的教训）
it = tvrl.score_rows([cell(), cell(first_line=2)], rounds=1)[0]
check("M1 hits 只数本线的", it["hits"], 1)
check("M2 首帧总行数单独一列", it["hits_all_frames"], 2)
check("M3 借来的单独一列", it["hits_borrowed"], 1)
check("M4 分项之和==总首帧行", it["hits"] + it["hits_borrowed"], it["hits_all_frames"])
# N) 没记线号的那一代不能被悄悄当成"已验证"：缺口必须有一列看得见
it2 = tvrl.score_rows([cell(), cell(first_line=None)], rounds=1)[0]
check("N1 hits_no_line_field 单列报数（缺口可见）", it2["hits_no_line_field"], 1)
check("N2 借来的那列此时为 0", it2["hits_borrowed"], 0)
check("N3 这两行都还算首帧（总行数）", it2["hits_all_frames"], 2)
check("K4 不传线号时分辨率也收", c["res"], "1920x1080")

# K5–K8：v4 那一刀——"遥控器排队"与"这条线起播"必须分两个数（真行：15:50:50 打 0x0、15:50:54 出帧）
L_QW0_L1 = ("10-26 15:50:50.765 15445 15445 I QualityWatch: channel=sports-f432164f71 "
            "line=1 0x0 t=18702511")
c = run(ITEM_A, [L_QW0_L1, L_FIRST_L1], want_line=1)
check("K5 first_seen＝本线第一行的 t", c["first_seen"], 18702511)
lag, zap, anchor = tvrl.timing(c, 18700000)
check("K6 排队时长（按下载到开始放）", lag, 2511)
check("K7 起播时长（开始放到出第一帧）", zap, 3755)
c2 = run(ITEM_A, [L_QW0_L1])
check("K8 没出帧时起播写 None，不许拿排队时长顶", tvrl.timing(c2, 18700000)[:2], (2511, None))
check("K8b 没有选线行时锚点是 firstlog", tvrl.timing(c2, 18700000)[2], "firstlog")

# K9–K11：`LineProbe: select` 那一行才是分表的锚（行形与 PlayerScreen.kt:376 那条 Log.i 逐字同形，
# 装机后用真读数再核一次；这里只钉解析器认不认得它）。
L_SELECT_2 = ("10-26 16:10:02.100  4095  4095 I LineProbe : select channel=sports-f432164f71 "
              "line=2 from=1 t=18750000")
c3 = run(ITEM_A, [L_SELECT_2, L_QW0_L1, L_FIRST_L1], want_line=2)
check("K9 选线行当锚点", c3["first_seen"], 18750000)
check("K10 锚点类型", tvrl.timing(c3, 18700000)[2], "select")
check("K11 排队＝选线−按下载定", tvrl.timing(c3, 18700000)[0], 50000)

# K12–K14：v5 那一刀——**出帧之后**才来的选线行不许当锚点。
# 真读数出处：`tools/.cache/tv_smoke_v5/lines.jsonl` 第 1 行（10-26 16:10:45，CCTV-6 那条，
# 加上这道保护之前的第一跑）记的 `起播 -38236ms`——锚点被一枚迟到的选线行抬到首帧之后 38.2 秒。
# 下面这三行的 t 值是按那个真差反推的合成行形（前两行是真日志，第三行只是把迟到那枚的 t 摆成同量级），
# 为的是让"拔掉这道保护"必须被这几格抓住。
L_SELECT_LATE = ("10-26 16:10:45.100  4095  4095 I LineProbe : select channel=sports-f432164f71 "
                 "line=1 from=2 t=18744502")
c4 = run(ITEM_A, [L_QW0_L1, L_FIRST_L1, L_SELECT_LATE], want_line=1)
lag4, zap4, anchor4 = tvrl.timing(c4, 18700000)
check("K12 迟到的选线行不许改锚点", anchor4, "firstlog")
check("K13 迟到的选线行不许把起点抬高", c4["first_seen"], 18702511)
check("K14 起播时长不许变负", zap4, 3755)
check("K14b 起播时长为负＝判红（真读数那格就是负的）", zap4 < 0, False)

# L) v6 那一刀：挪线不再按 |Δ|（起点不可读），改成"顶到第 1 格再数 want-1 下"。
# 为什么不能"按之前先读高亮"：这台电视的 uiautomator dump 里 focused/selected 全是 false
# （两份真 dump 现数 `focused="true"` 各 0 枚），焦点在哪一格读不到 ⇒ 只能要一个不依赖读数的起点。
plan7 = tvrl.nav_press_plan(7, 7)
check("L1 要第 7 格＝先顶到第 1 格再数 6 下", plan7[:10], [19] * 10)
check("L2 顶格步数是 台内线数+3（含底部两颗按钮）", len(plan7[:10]), 7 + 3)
check("L3 后半段正好是 want-1 枚下键", plan7[10:], [20] * 6)
check("L4 要第 1 格时一枚下键都不许多按", tvrl.nav_press_plan(7, 1)[10:], [])
check("L5 单线台也只用顶格那几步", len(tvrl.nav_press_plan(1, 1)), 4)
# 负对照：真读数那一格（要第 7、日志说在 6、旧公式只按 1 下就落到了第 4 条）——
# 新公式按的次数必须与 |Δ| 那版不同，否则这刀等于没动。
check("L6 与旧 |Δ| 公式必须不同（旧版那一格只按 1 下）", len(plan7) != abs(7 - 6), True)
try:
    tvrl.nav_press_plan(7, 8)
    check("L7 越界要拒（不许硬按不存在的格子）", "没抛", "ValueError")
except ValueError:
    check("L7 越界要拒（不许硬按不存在的格子）", "ValueError", "ValueError")
check("L8 台内线数从目录取，streams 为 None 时算 0 条",
      tvrl.line_totals({"channels": [{"id": "a", "streams": ["u1", "u2"]}, {"id": "b"}]}),
      {"a": 2, "b": 0})

# N) v6 那一刀之二：`press_gate`——第一次按键不许设闸。
# 真读数出处：v5 那一遍 16:28–16:52 CCTV-2 连 8 格 `seen=False presses=0`（台账
# `tools/.cache/tv_retest_cctv_v5_lines_partial_1656.jsonl`），v6 冒烟第 1 格同样 `presses=0`
# （`tools/.cache/tv_smoke_v6_gate_blocked_row.jsonl`）；停掉进程后单按一次台号 2，25 秒内落地。
# 形状＝停车台正在**健康播放**（`watch` 每 10 秒一行 ⇒ cur_age 永远 < 25 秒），旧闸门因此永不放行第一次按。
G = dict(repress_after=60.0)
check("N1 第一次按：停在别的台、日志才 3 秒前，也必须按（旧闸在这一格永远拦死）",
      tvrl.press_gate("cctv-1", "cctv-2", 3_000, True, None, **G), True)
check("N2 第一次按：读不到在播哪个台，同样按",
      tvrl.press_gate(None, "cctv-2", None, True, None, **G), True)
check("N3 补按：离上次只过 10 秒 ⇒ 不许按（不堆队列）",
      tvrl.press_gate("cctv-1", "cctv-2", 3_000, False, 10.0, **G), False)
check("N4 补按：过 70 秒、还在别的台且日志新鲜 ⇒ 队列没排空，不许按",
      tvrl.press_gate("cctv-1", "cctv-2", 3_000, False, 70.0, **G), False)
check("N5 补按：过 70 秒、应用已经停在目标台却没数到行 ⇒ 按",
      tvrl.press_gate("cctv-2", "cctv-2", 3_000, False, 70.0, **G), True)
check("N6 补按：过 70 秒、别的台且日志已 40 秒没动 ⇒ 队列排空，按",
      tvrl.press_gate("cctv-1", "cctv-2", 40_000, False, 70.0, **G), True)

# P) v6 那一刀之三：停车台的**键号**必须跟着台号走。
# 真读数：v6 第 1 格（CCTV-1 线 1，17:37:55）`seen=False`——那一支只换了 park_id、
# 按下去的还是 `--park` 那个号，测台号 1 这一台时"停车"停的就是它自己 ⇒ 没重新起播。
IDS = ["cctv-1", "cctv-2", "cctv-3", "cctv-4"]
check("P1 目标占了默认停车号 ⇒ 台号与键号一起挪到下一号",
      tvrl.park_target(IDS, 1, "cctv-1"), ("cctv-2", 2))
check("P2 目标没占停车号 ⇒ 照原号", tvrl.park_target(IDS, 1, "cctv-3"), ("cctv-1", 1))
check("P3 目标是台号 2 那台 ⇒ 仍停台号 1", tvrl.park_target(IDS, 1, "cctv-2"), ("cctv-1", 1))
check("P4 --no-park（键号 None）⇒ 不停车", tvrl.park_target(IDS, None, "cctv-1"), (None, None))
check("P5 只有一台的组 ⇒ 读出来就是它自己，由调用方那步判成不停车",
      tvrl.park_target(["cctv-1"], 1, "cctv-1"), ("cctv-1", 1))

# H) 四态口径：真机播过 / 落到台但没出画 / 按键没落地（不作数）／轮次不全，外加 v1 行必须整批剔除
tmp = Path(os.environ.get("TVRL_FIXTURE_DIR") or BASE / "tools" / ".cache" / "tv_fixture")
if tmp.exists():
    shutil.rmtree(tmp)
tmp.mkdir(parents=True, exist_ok=True)
ledger = tmp / "lines.jsonl"
out = tmp / "summary.json"
rows = [
    # 真机播过（v2，有 firstFrame）
    dict(round=1, toolv=tvrl.TOOLV, **ITEM_A, ts="14:46:41", seen=True, first_frame=True,
         played=True, res="1920x1080", res_zero=1, stalls=0, stall_ms=0, playing=True,
         cum_stalls=0, cum_stall_ms=0, presses=1, left_early=False, land_wait_s=1.5,
         press_ms=14850000, zap_ms=14853657 - 14850000),
    # 落到台但没出画（v2，只有 watch 行）
    dict(round=1, toolv=tvrl.TOOLV, **ITEM_B, ts="14:32:09", seen=True, first_frame=False,
         played=True, res=None, res_zero=1, stalls=1, stall_ms=3566, playing=True,
         cum_stalls=1, cum_stall_ms=3566, presses=1, left_early=False, land_wait_s=2.0,
         press_ms=13960000, zap_ms=None),
    # 按键没落地（v2，一行都没读到）
    dict(round=1, toolv=tvrl.TOOLV, channel_id="sports-none", line_index=0, url="http://x/n.m3u8",
         name="丙台", number=63, ts="14:33:00", seen=False, first_frame=False, played=False,
         res=None, res_zero=0, stalls=0, stall_ms=0, playing=None, cum_stalls=0, cum_stall_ms=0,
         presses=2, left_early=False, land_wait_s=None, press_ms=14000000, zap_ms=None),
    # 挪不到那条线（v3：面板按不动，那一格不许读成"这条线没画面"）
    dict(round=1, toolv=tvrl.TOOLV, channel_id="sports-navfail", line_index=3, url="http://x/nf.m3u8",
         name="丁台", number=64, ts="15:52:36", seen=True, first_frame=False, played=False,
         res=None, res_zero=1, stalls=0, stall_ms=0, playing=False, cum_stalls=0, cum_stall_ms=0,
         presses=1, left_early=False, land_wait_s=12.0, press_ms=18000000, zap_ms=None,
         want_line=4, nav_from=3, nav_presses=0, nav_failed=True, line_moved=False),
    # 窗口里应用自己把线换走了＝这条线被判挂
    dict(round=1, toolv=tvrl.TOOLV, channel_id="sports-moved", line_index=2, url="http://x/mv.m3u8",
         name="戊台", number=65, ts="15:53:10", seen=True, first_frame=False, played=False,
         res=None, res_zero=1, stalls=0, stall_ms=0, playing=False, cum_stalls=0, cum_stall_ms=0,
         presses=1, left_early=False, land_wait_s=11.0, press_ms=18100000, zap_ms=None,
         want_line=3, nav_from=3, nav_presses=1, nav_failed=False, line_moved=True),
    # 上一把尺（toolv=5）写过的一格：尺换版之后默认数不到它，只有显式点名版本才回来
    dict(round=1, toolv=5, channel_id="sports-v5only", line_index=0, url="http://x/v5.m3u8",
         name="庚台", number=66, ts="16:10:45", seen=True, first_frame=True, played=True,
         res="1280x720", res_zero=2, stalls=1, stall_ms=2037, playing=True,
         cum_stalls=1, cum_stall_ms=2037, presses=1, left_early=False, land_wait_s=15.2,
         press_ms=20000000, want_line=1, nav_from=1, nav_presses=1, nav_failed=False,
         line_moved=False, anchor="select", nav_lag_ms=959, zap_ms=4115),
    # v1 那一格：同样的台，但 toolv 缺省——它会把"应用说在播"记成 first_frame=True，必须整行剔除
    dict(round=1, channel_id=CID_A, line_index=0, url=ITEM_A["url"], name="甲台", number=61,
         ts="14:25:45", seen=True, first_frame=True, res="0x0", stalls=9, stall_ms=13317,
         playing=True, cum_stalls=9, cum_stall_ms=13317, landed_after_s=73.2, zap_ms=None),
]
ledger.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
tvrl.summarize(ledger, out, rounds=1)
s = json.loads(out.read_text(encoding="utf-8"))
by = {i["channel_id"]: i for i in s["items"]}
check("H1 台账里 6 行 → 只数 v3 的 5 台", len(s["items"]), 5)
check("H2 v1 那行没被当证据", sum(1 for i in s["items"] if i["url"] == "http://x/a.m3u8"), 1)
check("H3 真机播过", by[CID_A]["verdict"], "真机播过")
check("H4 落到台但没出画", by[CID_B]["verdict"], "落到台但没出画")
check("H5 按键没落地（不作数）", by["sports-none"]["verdict"], "按键没落地（不作数）")
check("H6 甲台分辨率", by[CID_A]["res"], ["1920x1080"])
check("H7 甲台起播耗时（firstFrame-按下）", by[CID_A]["zap_ms"], [3657])
check("H8 乙台没有假分辨率", by[CID_B]["res"], [])
check("H9 丙台的按下次数", by["sports-none"]["presses"], 2)
check("H10 挪不到那条线", by["sports-navfail"]["verdict"], "挪不到那条线（不作数）")
check("H11 中途被换线（这条线挂）", by["sports-moved"]["verdict"], "中途被换线（这条线挂）")

# I) 负对照：v1 那一行单独成一份台账时，summary 必须报 0 条（尺子确实认得版本，不是恒真）
only_v1 = tmp / "v1.jsonl"
only_v1.write_text(json.dumps(rows[-1], ensure_ascii=False) + "\n", encoding="utf-8")
out1 = tmp / "v1_summary.json"
tvrl.summarize(only_v1, out1, rounds=1)
check("I1 只有 v1 行 ⇒ 条目 0", len(json.loads(out1.read_text(encoding="utf-8"))["items"]), 0)

# M) 尺换版之后旧台账还得能复盘：`summarize(..., toolv=N)` 那一道口
out5 = tmp / "s5.json"
tvrl.summarize(ledger, out5, rounds=1, toolv=5)
s5 = json.loads(out5.read_text(encoding="utf-8"))
check("M1 点名 toolv=5 ⇒ 只数出上一把尺那一格", [i["channel_id"] for i in s5["items"]], ["sports-v5only"])
check("M2 那一格的锚点与两个数都还在", (s5["items"][0]["anchor"], s5["items"][0]["zap_ms"]),
      (["select"], [4115]))
check("M2b 锚点必须跟着进记分板（不然 2 毫秒会被读成「这条线真快」）",
      "anchor" in s5["items"][0], True)
out99 = tmp / "s99.json"
tvrl.summarize(ledger, out99, rounds=1, toolv=99)
check("M3 没写过的版本 ⇒ 0 条（这一道口不是恒真）",
      len(json.loads(out99.read_text(encoding="utf-8"))["items"]), 0)
check("M4 不点名时按当前尺数（默认口径没被动过）", len(s["items"]), 5)

# V) 六态判据抽成一个纯函数：记分板、复跑清单、卡顿读数板三处共用同一份，不许各写一套
check("V1 出过首帧＝真机播过", tvrl.verdict_for([rows[0]], 1), "真机播过")
check("V2 只有 watch 行＝落到台但没出画", tvrl.verdict_for([rows[1]], 1), "落到台但没出画")
check("V3 一行都没读到＝按键没落地（不作数）", tvrl.verdict_for([rows[2]], 1), "按键没落地（不作数）")
check("V4 面板挪不到位＝挪不到那条线（不作数）", tvrl.verdict_for([rows[3]], 1), "挪不到那条线（不作数）")
check("V5 窗口里被应用自己换线＝这条线挂", tvrl.verdict_for([rows[4]], 1), "中途被换线（这条线挂）")
# 「轮次不全」必须排在"不作数"那些之前判：只跑了 1 轮、要 3 轮定案时这一格还不该有任何结论
check("V6 轮数不够时报「轮次不全」而不是「按键没落地」", tvrl.verdict_for([rows[2]], 3), "轮次不全")

# W) 复跑清单（--retest-failures 的那道口）：第 1 遍里"没成"的格子，含两种「不作数」
fk = sorted(tvrl.failure_keys(ledger, toolv=tvrl.TOOLV, rounds=1))
check("W1 台账 5 格里只有 1 格播过 ⇒ 复跑清单 4 格", len(fk), 4)
check("W2 播过那一格不进复跑清单",
      (rows[0]["channel_id"], rows[0]["line_index"], rows[0]["url"]) in fk, False)
for _i, _tag in ((1, "W3 落到台但没出画"), (2, "W4 按键没落地"),
                 (3, "W5 挪不到那条线"), (4, "W6 中途被换线")):
    check(_tag + " 那一格进清单",
          (rows[_i]["channel_id"], rows[_i]["line_index"], rows[_i]["url"]) in fk, True)
# 负对照：把「戊台」改成出过帧，清单必须当场少一格——证明这一道口读的是判据，不是写死的名单
flip = tmp / "flipped.jsonl"
flip.write_text("\n".join(json.dumps(dict(r, first_frame=True) if r.get("channel_id") == "sports-moved"
                                      else r, ensure_ascii=False) for r in rows if r.get("toolv")) + "\n",
                encoding="utf-8")
check("W7 负对照：戊台改成出过帧 ⇒ 清单从 4 格变 3 格",
      len(tvrl.failure_keys(flip, toolv=tvrl.TOOLV, rounds=1)), 3)
# 尺版本这一道口在复跑清单里也得是活的（不是只记分板认版本）
v1led = tmp / "v1led.jsonl"
v1led.write_text("\n".join(json.dumps(dict(r, toolv=1), ensure_ascii=False)
                            for r in (rows[0], rows[2])) + "\n", encoding="utf-8")
check("W8 点名 toolv=1 ⇒ 只剩那一台没播过的格",
      tvrl.failure_keys(v1led, toolv=1, rounds=1), [("sports-none", 0, "http://x/n.m3u8")])
check("W9 不点名时 v1 行整批剔除 ⇒ 清单空",
      tvrl.failure_keys(v1led, rounds=1), [])

# X) 读数板不再抄第二套判据（两处各写一遍迟早分叉出两个"没出画"条数）
board_src = Path(os.environ.get("TVRL_BOARD") or
                 BASE / "tools" / "retest_stall_board.py").read_text(encoding="utf-8")
tv_src = MODULE.read_text(encoding="utf-8")
check("X1 读数板里没有第二套六态判据", "def verdict_of" in board_src, False)
check("X2 读数板用的是尺子那份 score_rows", "tvrl.score_rows" in board_src, True)
check("X3 尺子里「落到台但没出画」这一态只 return 一次",
      tv_src.count('return "落到台但没出画"'), 1)

# Y) 连续测同一个台的几条线时，按下记录必须每格清零（残留会把上一格的数字读成这一格的）
check("Y1 每格先清零再判 stay（复位写在那个分支之前）",
      tv_src.index("pressed_at = None") < tv_src.index("stay = (item["), True)
check("Y2 没有按下那一刻时 nav_lag 写 None，不拿旧值冒充",
      tvrl.timing({"first_seen": 100, "first": 150, "anchor": "firstlog"}, 0)[0], None)
check("Y3 同一格有按下那一刻时 nav_lag 照旧算得出（这一道口不是恒真）",
      tvrl.timing({"first_seen": 100, "first": 150, "anchor": "firstlog"}, 40)[0], 60)

# Z) 「全线路回测」这句结论要能被现查：audit_plan 的四个数各挡哪一种谎
PLAN_Z = [dict(channel_id="a", line_index=0, url="u0", name="甲", number=1),
          dict(channel_id="a", line_index=1, url="u1", name="甲", number=1),
          dict(channel_id="b", line_index=0, url="u2", name="乙", number=2)]
GOT_Z = [dict(channel_id=r["channel_id"], line_index=r["line_index"], url=r["url"]) for r in PLAN_Z]
az = tvrl.audit_plan(PLAN_Z, GOT_Z)
check("Z1 每格都有台账行 ⇒ 缺／重复／计划外 全 0",
      (len(az["missing"]), len(az["dup"]), len(az["extra"])), (0, 0, 0))
check("Z1b 覆盖格数＝计划格数", (az["covered"], az["plan"]), (3, 3))
az2 = tvrl.audit_plan(PLAN_Z, GOT_Z[:2])
check("Z2 台账少一格 ⇒ 那一格被点名（「220/222 却写成跑完了」那一类）", az2["missing"], [("b", "u2")])
az3 = tvrl.audit_plan(PLAN_Z, GOT_Z + [dict(GOT_Z[0])])
check("Z3 同一格写了两行 ⇒ 重复 1 处（两个进程抢一份台账）", az3["dup"], [("a", "u0")])
check("Z3b 行数与覆盖格数分开报（只报行数就会把重复读成跑全）", (az3["rows"], az3["covered"]), (4, 3))
GOT_Z4 = [dict(r, line_index=r["line_index"] + 1 if r["url"] == "u0" else r["line_index"]) for r in GOT_Z]
az4 = tvrl.audit_plan(PLAN_Z, GOT_Z4)
check("Z4 同一格在线序不同的那一号 ⇒ 不算缺、算 order_diff",
      (len(az4["missing"]), len(az4["extra"]), az4["order_diff"]), (0, 0, 1))
az5 = tvrl.audit_plan(PLAN_Z, GOT_Z + [dict(channel_id="c", line_index=0, url="u9")])
check("Z5 台账里出现计划外的格（目录中途换底）", az5["extra"], [("c", "u9")])
# Z6 同一格两行要分清来历：先一格「不作数」（seen=False）再重测＝正常；两行都在数＝事故
GOT_Z6 = [dict(r, seen=True) for r in GOT_Z] + [dict(channel_id="a", line_index=0, url="u0", seen=False)]
az6 = tvrl.audit_plan(PLAN_Z, GOT_Z6)
check("Z6 不作数那一行不计入重复 ⇒ 重复 0 处", az6["dup"], [])
check("Z6b 重测的格单独数出来（不然 60 行／59 格这种数没人看得懂）", az6["retest"], [("a", "u0")])
check("Z6c 两种重复分开报之后，事故那一类仍然会被抓到",
      len(tvrl.audit_plan(PLAN_Z, GOT_Z + [dict(GOT_Z[0], seen=True)])["dup"]), 1)

# Q) 「每台第一条」那一节（读数板里那一块）：把"还没测到"读成"不合格"是这一族最容易犯的谎
# 读数板里写的是 `import tv_retest_lines`：先把上面按路径加载的那一份挂进 sys.modules，
# 这样它拿到的就是**本回合正在被测的那一份**（变异臂换的是 MODULE，不能让板子偷偷去导仓库那份）。
sys.modules.setdefault("tv_retest_lines", tvrl)
board_spec = importlib.util.spec_from_file_location(
    "rsb", Path(os.environ.get("TVRL_BOARD") or BASE / "tools" / "retest_stall_board.py"))
rsb = importlib.util.module_from_spec(board_spec)
board_spec.loader.exec_module(rsb)
CELLS_Q = [
    {"cid": "a", "name": "甲", "line": 1, "verdict": "真机播过", "url": "u0"},
    {"cid": "a", "name": "甲", "line": 2, "verdict": "全轮没首帧", "url": "u1"},
    {"cid": "b", "name": "乙", "line": 2, "verdict": "真机播过", "url": "u2"},
    {"cid": "c", "name": "丙", "line": 1, "verdict": "中途被换线（这条线挂）", "url": "u3"},
]
fq = rsb.first_line_board(CELLS_Q)
check("Q1 首条真播过的台", fq["ok"], ["甲"])
check("Q2 首条不合格只算首条那一格（甲的第 2 条不合格不算）", [x[0] for x in fq["bad"]], ["丙"])
check("Q3 首条还没测到＝算「没测」，不许算成不合格", [x[0] for x in fq["unmeasured"]], ["乙"])
check("Q4 分项之和==台数", len(fq["ok"]) + len(fq["bad"]) + len(fq["unmeasured"]), fq["channels"])

# R) 「说在播却没出画」那一节：读的是台账里真有的 `playing` 字段，不是六态判据推出来的那个数。
# 旧版这一节的名字写着"应用说在播"，可它背后是 `verdict == 落到台但没出画`——从没碰过 playing 字段，
# 那句标签是说谎的（10-26 18:5x 现读同一份台账：playing=True 且没出画 0 行，而那一态非零）。
def _r(cid, name, li, url, playing, ff, res=None):
    return {"channel_id": cid, "name": name, "line_index": li, "url": url,
            "playing": playing, "first_frame": ff, "res": res, "toolv": 6}

ROWS_R = [
    _r("a", "甲", 0, "u0", True, True, "1920x1080"),    # 在播且出过画
    _r("a", "甲", 1, "u1", True, False),                 # 说在播却没出画（整格只这一行⇒all_bad）
    _r("b", "乙", 0, "u2", False, True, "1280x720"),     # 出过画但那一刻没在播
    _r("b", "乙", 1, "u3", False, False),                # 没出画也没在播
    _r("c", "丙", 0, "u4", None, False),                 # 这一窗没读到 StallWatch 那一行
    _r("c", "丙", 0, "u4", None, True),                  # 同上（有首帧也不改变"没读数"这一档）
    _r("d", "丁", 0, "u5", True, False),                 # 丁线 1：两轮里这一轮没出画
    _r("d", "丁", 0, "u5", True, True, "1920x1080"),     # 同一格另一轮出过画⇒some_bad
]
pf = rsb.playing_frame_board(ROWS_R)
check("R1 四档互斥、分项之和==行数", pf["sum_buckets"], pf["rows"])
check("R2 各档条数（在播且出画／出画没在播／没画没在播／没读数／说在播却没画）",
      [pf["buckets"][k] for k in ("played_and_frame", "frame_not_playing",
                                  "no_frame_not_playing", "no_reading", "claimed_playing_no_frame")],
      [2, 1, 1, 2, 2])
check("R3 playing=None 不许折成 False（折了就会把两行算进坏档或没画档）",
      pf["buckets"]["no_reading"], 2)
check("R4 整格每一轮都说在播却没出画＝1 格", pf["bad_cells_all"], 1)
check("R5 只有一轮如此、别的轮出过画＝另一档（这一格判据那边算「真机播过」，两个数不许打脸）",
      pf["bad_cells_some"], 1)
check("R6 items 只列整格都这样的那些行", [(x[0], x[1]) for x in pf["items"]], [("甲", 1)])
check("R7 坏档台数按台去重（甲、丁各一格＝2 台）", pf["bad_channels"], 2)
_mR = [dict(x) for x in ROWS_R]
for x in _mR:
    if x["url"] in ("u1", "u5"):
        x["playing"] = False          # 变异臂：把"应用说在播"那一枚拔掉，坏档必须清零
check("R8 变异对照：拔掉 playing=True 后坏档==0（证明这一格真的在看 playing，不是恒真）",
      rsb.playing_frame_board(_mR)["buckets"]["claimed_playing_no_frame"], 0)

# T) 「借来的首帧」那一节：落地等待那一处 `absorb()` 没带 want_line ⇒ 同台**别条线**的 firstFrame
# 能被记进这一格，把一条从没出过画的线判成「真机播过」。这个数一路传到复跑清单、清晰度置顶与删除名单。
def _t(cid, name, li, url, first_line, ff=True):
    return {"channel_id": cid, "name": name, "line_index": li, "url": url,
            "first_frame": ff, "first_line": first_line, "anchor": "firstlog",
            "zap_ms": 3, "ts": "19:30:00", "toolv": 6, "playing": True}

ROWS_T = [
    _t("a", "甲", 0, "u0", 1),      # 计划线 1、首帧来自线 1 ⇒ 一致
    _t("b", "乙", 0, "u1", 2),      # 计划线 1、首帧来自线 2 ⇒ 借来的
    _t("c", "丙", 1, "u2", None),   # 老行没带 first_line 字段 ⇒ 未知，不许折成"一致"
    _t("d", "丁", 0, "u3", 1, ff=False),   # 没首帧的行根本不该进这三档
]
bf = rsb.borrowed_frame_board(ROWS_T)
check("T1 有首帧的行数只数 first_frame 为真的", bf["frame_rows"], 3)
check("T2 行号一致的算 credited_ok", bf["credited_ok"], 1)
check("T3 行号不一致才算借来（乙那格）", bf["borrowed"], 1)
check("T4 缺 first_line 字段单列一档，不折成通过", bf["no_line_field"], 1)
check("T5 三档分项之和==有首帧的行数",
      bf["credited_ok"] + bf["borrowed"] + bf["no_line_field"], bf["frame_rows"])
check("T6 items 里带上计划线与首帧实际来自哪条线",
      [(x[0], x[1], x[2]) for x in bf["items"]], [("乙", 1, 2)])
_mT = [dict(r) for r in ROWS_T]
for r in _mT:
    r["first_line"] = r["line_index"] + 1      # 变异臂：把行号全改成一致
check("T7 变异对照：行号全一致时借来的必须==0（证明这一格真在比行号）",
      rsb.borrowed_frame_board(_mT)["borrowed"], 0)
check("T8 空台账不虚增任何一档", rsb.borrowed_frame_board([])["frame_rows"], 0)

# ---- H 系列：卡顿按主机摊开（分母要一起打，样本太小要自己说出口） ----
def hcell(name, url, stalls=0, ms=0):
    return {"channel": name, "line_index": 0, "url": url, "stalls": stalls, "stall_ms": ms}


H = [hcell("A1", "http://h1/live/a.m3u8", 2, 5000), hcell("A2", "http://h1/live/b.m3u8", 1, 3000),
     hcell("B1", "http://h2/live/a.m3u8", 0, 0), hcell("B2", "http://h2/x/y.m3u8", 0, 0),
     hcell("C1", "http://h3:8080/a.m3u8", 4, 20000)]
hb = rsb.host_stall_board(H)
rows_h = {r["host"]: r for r in hb["rows"]}
check("H1 分组键是 netloc 不是路径首段（10-24 拿 Path.parts 把主机读成 'live' 那一族）",
      sorted(rows_h), ["h1", "h2", "h3:8080"])
check("H2 每家的分母与卡顿格数一起算", (rows_h["h1"]["cells"], rows_h["h1"]["stalling"],
                                       rows_h["h1"]["total_ms"]), (2, 2, 8000))
check("H2b 没卡顿的主机也要在场（否则『0 格』会被读成『这家没测过』）",
      (rows_h["h2"]["cells"], rows_h["h2"]["stalling"], rows_h["h2"]["total_ms"]), (2, 0, 0))
check("H3 最坏那一格指本主机内的，不是全局的", rows_h["h3:8080"]["worst"], ("C1", 1, 4, 20000))
check("H3b 有卡顿那几格的中位只数有卡顿的", rows_h["h1"]["median_ms"], 4000)
check("H4 求和：各家分母之和 == 输入格数", hb["cells"], len(H))
# 我第一版把期望写成 ["h1","h3:8080"]——h3:8080 那家 20000ms 比 h1 的 8000ms 大，降序它在前。
check("H4b 排序按卡顿毫秒合计降序", [r["host"] for r in hb["rows"]][:2], ["h3:8080", "h1"])
check("H5 样本太小说在明处（3 格以下自己标出来）",
      (rows_h["h3:8080"]["small"], rows_h["h1"]["small"]), (True, True))
check("H5b 边界：刚好凑到 min_cells 那家不再算太小（4 格太小、5 格不小）",
      [rsb.host_stall_board(H + [hcell(f"C{n2}", f"http://h3:8080/x{n2}.m3u8") for n2 in (2, 3, 4)],
                            min_cells=5)["rows"][0]["small"],
       rsb.host_stall_board(H + [hcell(f"C{n2}", f"http://h3:8080/x{n2}.m3u8") for n2 in (2, 3, 4, 5)],
                            min_cells=5)["rows"][0]["small"]],
      [True, False])
check("H6 空输入不崩、全零", (rsb.host_stall_board([])["cells"], rsb.host_stall_board([])["rows"]), (0, []))
H_neg = [dict(x, stalls=0, stall_ms=0) for x in H]      # 变异臂：把那两列摘掉
check("H7 变异对照：摘掉 stalls/stall_ms ⇒ 卡顿格与合计必须归零（证明是那一列在数）",
      (rsb.host_stall_board(H_neg)["stalling"], rsb.host_stall_board(H_neg)["total_ms"]), (0, 0))

# L) 10-27 新增那格读数「这条线拿到的是直播还是一枚定长片子」。行形照 app 侧
#    `PlayerScreen` 的 LiveWatch 那一条写（`tools/.cache/tv_retest_g2fix/lines.jsonl` 里
#    那 7 格 first_frame=true／playing=false 的占位片，就是这一格要分出来的东西）。
L_LIVE_AD = ("10-27 13:24:10.113 15445 15445 I LiveWatch: channel=sports-8026757828 "
             "line=1 live=false duration=15000 t=13990831")
L_LIVE_REAL = ("10-27 13:24:10.213 15445 15445 I LiveWatch: channel=sports-8026757828 "
               "line=1 live=true duration=-9223372036854775808 t=13990931")
L_LIVE_OTHER = ("10-27 13:24:11.113 15445 15445 I LiveWatch: channel=sports-9999999999 "
                "line=1 live=false duration=15000 t=13991831")
c = run(ITEM_B, [L_WATCH_B1, L_LIVE_AD])
check("L1 定长片读成 live=False", (c["live"], c["duration_ms"]), (False, 15000))
check("L2 出过画这一格没被它改动（正对照：只加读数不改判）", bool(c["first"]), False)
c = run(ITEM_B, [L_LIVE_AD, L_LIVE_REAL])
check("L3 后来的真直播行覆盖前一行（长数是 C.TIME_UNSET，负数原样存）",
      (c["live"], c["duration_ms"]), (True, -9223372036854775808))
c = run(ITEM_B, [L_WATCH_B1])
check("L4 没有 LiveWatch 行＝没测到，不许折成 live=False", c["live"], None)
c = run(ITEM_A, [L_LIVE_OTHER])
check("L5 别台的 LiveWatch 行不许顶替本台", c["live"], None)
c = run(ITEM_A, [L_LIVE_OTHER, L_WATCH_A0])
check("L6 本台的 StallWatch 行照收、live 仍空（证明 live 只来自 LiveWatch 那一行）",
      (c["playing"], c["live"]), (True, None))

shutil.rmtree(tmp)

print(f"[tv_retest_lines] 夹具 {n_check} 格，判红 {len(fails)} 格")
for f in fails:
    print("  红:", f)
sys.exit(1 if fails else 0)
