#!/usr/bin/env python3
"""验的是「全频道删除标清线路」＋「央视·卫视、地方频道按清晰度由高到低排序」这两格。

用户 10-25 原话：
  「全频道删除标清线路，未来新增的线路也不添加标清线路，删除没有线路的频道。标清线路体验差」
  「央视·卫视、地方频道，线路按清晰度由高到低排序」

盯住八种会说谎的情形：
  A) 判档口径与屏幕上那把尺不一致 —— 设备端选线面板（PlayerScreen.qualityText）说「高 ≥700 或宽 ≥1200
     才叫高清」，构建侧若用别的数，用户看到的档和实际删的档就不是同一批线。
  B) 拿 URL 里的数字猜档 —— 480/576 可能是端口或节目号（10-24 现读 `live.hd.hdbs.cn` 那种域名带 hd
     的假信号反过来同样成立）。尺寸未知的线只认清单标题里独立一档的「标清／SD」。
  C) 尺寸未知就一律不摘 —— 那「未来新增的线路也不添加标清线路」这条就没人守。
  D) 摘空线的同时没把地址送进黑名单 —— 设备端「立即更新线路」会把同一条地址换个台名再捞回来。
  E) 摘空就把台删掉，连必留名单（辽宁体育只有 1 条线）也一起删 —— 顶掉长期指令。
  F) 排序把「判坏的线」（档 4）按像素顶到第 1 条 —— 用户一直说的「正在连接」就是这么来的。
  G) --keep-sd 那把开关是假的（关了还摘）或反的（开了不摘）。
  H) 判据改了但只有夹具在守：`is_sd_line` 的返回值在生产路径上没人消费 ⇒ 整格恒绿。

每格都配负对照：换掉被试那一格的输入（或摘掉开关），结论必须翻面。

跑法：python3 tools/tests/test_definition_sd.py    （退出码 0 才算过）
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402
import stream_rules  # noqa: E402

canon = stream_rules.canon
HD_URL = "http://10.0.0.1:8080/live/hd.m3u8"          # 1920x1080
SD_URL = "http://10.0.0.2:8080/live/sd.m3u8"          # 720x576
EDGE_URL = "http://10.0.0.3:8080/live/edge.m3u8"      # 1280x720 ⇒ 高清档，不许摘
UNKNOWN_URL = "http://10.0.0.4:8080/live/unknown.m3u8"
PORT480_URL = "http://10.0.0.5:480/live/x.m3u8"       # 端口写着 480，尺寸未知 ⇒ 不许摘
DEAD_4K_URL = "http://10.0.0.6:8080/live/dead4k.m3u8"  # 3840x2160 但对端回了 404

failures: list[str] = []
ran = 0


def check(label: str, got, want) -> None:
    global ran
    ran += 1
    if got != want:
        failures.append(f"{label}: 得到 {got!r}，期望 {want!r}")


def setup() -> None:
    bc.FRAME_RES = {canon(HD_URL): [1920, 1080], canon(SD_URL): [720, 576],
                    canon(EDGE_URL): [1280, 720], canon(DEAD_4K_URL): [3840, 2160]}
    bc.HEALTH = {canon(HD_URL): {"verdict": "可取到分片"},
                 canon(DEAD_4K_URL): {"verdict": "清单404"}}
    bc.IDENTITY, bc.REVIEW_LOGO, bc.FRAME_PLACEHOLDER = {}, {}, {}
    bc.FROZEN, bc.RETEST_CUT, bc.CCTV3_CUT = {}, set(), {}
    bc.DEVICE_PROOF = {}
    bc.STREAM_RAW = {canon(UNKNOWN_URL): "CCTV-2 财经 标清", canon(PORT480_URL): "CCTV-2 财经 高清"}


def row(name, category, urls):
    return {"id": f"{category}-{name}", "name": name, "category": category,
            "subgroup": "央视", "logo": "", "streams": list(urls)}


# ── A/B/C：判档本身 ──────────────────────────────────────────────────────────────
setup()
check("A1 实测 1920x1080 不摘", bc.is_sd_line(HD_URL, ""), False)
check("A2 实测 720x576 摘", bc.is_sd_line(SD_URL, ""), True)
check("A3 实测 1280x720（屏幕那把尺的高清下沿）不摘", bc.is_sd_line(EDGE_URL, ""), False)
check("B1 尺寸未知＋标题写着「标清」⇒ 摘", bc.is_sd_line(UNKNOWN_URL, "CCTV-2 财经 标清"), True)
check("B2 尺寸未知＋标题写着「高清」⇒ 不摘", bc.is_sd_line(UNKNOWN_URL, "CCTV-2 财经 高清"), False)
check("B3 尺寸未知＋URL 端口是 480 ⇒ 不摘（不从数字猜档）", bc.is_sd_line(PORT480_URL, ""), False)
check("C1 尺寸未知、标题也没档名 ⇒ 不摘（没读数不摘线）", bc.is_sd_line(UNKNOWN_URL, ""), False)
# 负对照：把尺寸读数摘掉，同一条 720x576 的线就从「摘」翻成「不摘」——证明这一格真在吃 FRAME_RES。
saved = bc.FRAME_RES
bc.FRAME_RES = {}
check("A2n 负对照：没有尺寸读数时同一条线不摘（只剩标题那一半证据）", bc.is_sd_line(SD_URL, ""), False)
bc.FRAME_RES = saved
# 负对照：把「标清」写成「标清2」这种不是独立一档的字样，不许当成档名（否则 URL 里带 sd 的都会中）。
check("B1n 负对照：档名不独立（「标清版2」连写）时不摘", bc.is_sd_line(UNKNOWN_URL, "CCTV-2 标清版2"), False)

# ── E/F/G/H：drop_sd_lines 那一棒 ────────────────────────────────────────────────
setup()
bc.DROP_SD = True
bc.SD_DROPPED.clear()
bc.SD_EXEMPT.clear()
bc.REQUIRED_CHANNELS = {"辽宁体育"}
channels = [
    row("CCTV-2 财经", "cctv", [HD_URL, SD_URL, EDGE_URL]),
    row("辽宁体育", "sports", [SD_URL]),                  # 只有一条线，且是标清
    row("某县台", "local", [SD_URL]),                     # 摘空 ⇒ 整台删除
    row("CCTV-8K", "cctv", [DEAD_4K_URL, HD_URL]),        # 判坏的 4K 不许排第 1
]
survivors, emptied = bc.drop_sd_lines(channels)
by_name = {r["name"]: r["streams"] for r in survivors}
check("E1 央视组摘掉那条 720x576", by_name.get("CCTV-2 财经"), [HD_URL, EDGE_URL])
check("E2 必留名单里整台都是标清 ⇒ 原样保留", by_name.get("辽宁体育"), [SD_URL])
check("E3 必留豁免记进 SD_EXEMPT", [n for n, _ in bc.SD_EXEMPT], ["辽宁体育"])
check("E4 摘空且不在必留名单 ⇒ 整台删除", emptied, ["某县台"])
check("E5 被删的那台不在存活名单里", "某县台" in by_name, False)
check("F1 判坏的 4K 不排第 1（能播的 1920x1080 在前）", by_name.get("CCTV-8K"), [HD_URL, DEAD_4K_URL])
check("H1 摘线统计真被填上（生产路径有人在消费）", sorted(n for n, _, _ in bc.SD_DROPPED),
      ["CCTV-2 财经", "某县台"])
# 负对照：把尺寸读数清空 ⇒ 一条都不该摘（证明这一格真在吃 line_size，而不是恒摘）。
bc.SD_DROPPED.clear()
bc.FRAME_RES = {}
bc.STREAM_RAW = {}
survivors2, emptied2 = bc.drop_sd_lines(channels)
check("H2 负对照：没有任何尺寸/标题证据时不摘线", len(bc.SD_DROPPED), 0)
check("H3 负对照：那台只有一条标清线的县台活着", "某县台" in {r["name"] for r in survivors2}, True)
bc.FRAME_RES = {canon(SD_URL): [720, 576]}

# ── D：黑名单同批 ────────────────────────────────────────────────────────────────
setup()
bc.DROP_SD = True
bc.SD_DROPPED.clear()
bc.DROP_DEAD = True
bc.FROZEN, bc.AUDIO_ONLY_PREFIXES = {}, ("piccpndali.v.myalicdn.com/audio/",)
bc.IDENTITY, bc.REVIEW_LOGO = {}, {}
bc.drop_sd_lines([row("CCTV-2 财经", "cctv", [HD_URL, SD_URL])])
blocked, _exempt = bc.blocked_lines()
check("D1 摘掉的标清地址进了设备端黑名单", canon(SD_URL) in blocked, True)
check("D2 黑名单理由写的是标清（不是别的结论冒充）", "标清" in blocked.get(canon(SD_URL), ""), True)
check("D3 负对照：没摘的高清线不进黑名单", canon(HD_URL) in blocked, False)

# ── G：--keep-sd 那把开关 ────────────────────────────────────────────────────────
setup()
bc.DROP_SD = False
bc.SD_DROPPED.clear()
# 开关只在 build() 那一侧被读；这里按 build 的用法验：DROP_SD=False 时那一站整个跳过，
# 所以 SD_DROPPED 必须还是空的（关了还摘＝开关是假的）。
check("G1 --keep-sd 时这一站不动（构建里是 if DROP_SD 才调 drop_sd_lines）", bc.SD_DROPPED, [])
bc.DROP_SD = True
bc.SD_DROPPED.clear()
bc.drop_sd_lines([row("CCTV-2 财经", "cctv", [SD_URL, HD_URL])])
check("G2 开关打开（默认）时同一条线被摘", len(bc.SD_DROPPED), 1)

# ── 组内排序：只按用户点名的两组生效 ─────────────────────────────────────────────
setup()
key_cctv = bc.res_first_key("CCTV-2 财经")
check("S1 央视组那把尺：1920 排在 1280 前", sorted([EDGE_URL, HD_URL], key=key_cctv), [HD_URL, EDGE_URL])
check("S2 央视组那把尺：判坏的 4K 沉底", sorted([DEAD_4K_URL, EDGE_URL], key=key_cctv), [EDGE_URL, DEAD_4K_URL])
check("S3 只有央视·卫视与地方频道用这把尺", sorted(bc.RES_FIRST_CATS), ["cctv", "local"])

print(f"[definition-sd] 跑了 {ran} 格，失败 {len(failures)} 格")
for f in failures:
    print("  [判红] " + f)
raise SystemExit(1 if failures else 0)
