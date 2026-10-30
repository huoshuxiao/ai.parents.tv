#!/usr/bin/env python3
"""10-30 这五条需求的逐条对表尺：一条命令回答「哪几条做到了、证据在哪、还缺哪一格」。

为什么要这把尺（用户原话「之前的需求没有正确实现，进行反思，杜绝漏需求的现象再次发生」）：
**光把东西改对不算数**——同一句话在构建、资产、设备、门禁四处各有一套实现，任何一处没接上，
「改了」和「没改」在产物上长得一模一样。这里把每条需求拆成「代码层／资产层／设备层／真机层」，
每层只认自己那份证据，缺哪层就念哪层，不许拿别层的绿当这层的绿。

四态（与 `tools/objective_audit_20261026.py` 同一套）：
  达成 / 未达成 / 待人工 / 没这份证据。只有「达成」算过线；「待人工」「没这份证据」都不折算通过。
退出码：五条全部「达成」才 0。

用法：python3 tools/objective_audit_20261030.py            # 对表
      python3 tools/objective_audit_20261030.py --selftest # 纯函数正负对照
只读本机文件（设备那两格用 `adb logcat -d` 的转储文件，不现场连设备、不写任何产物）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSET = ROOT / "app/src/main/assets/channels.json"
BUILDER = ROOT / "tools/build_catalog.py"
JAVA = ROOT / "app/src/main/java/com/sun/tv/parents"
THEMES_V31 = ROOT / "app/src/main/res/values-v31/themes.xml"
LAUNCH_BG = ROOT / "app/src/main/res/drawable/launch_background.xml"
EPG_LOG = ROOT / "tools/.cache/logcat_epg_20261030.txt"

PASS, FAIL, HUMAN, ABSENT = "达成", "未达成", "待人工", "没这份证据"


# ── 纯判据（正负对照都打在这一层）─────────────────────────────────────────────
def splash_state(themes: str, launch: str) -> tuple[str, str]:
    """① 启动图：v31 那支要把系统图标设成透明、pre-31 那支要整屏铺 splash_hd。"""
    transparent = "windowSplashScreenAnimatedIcon" in themes and "transparent" in themes
    fullscreen = 'android:gravity="fill"' in launch and "splash_hd" in launch
    if transparent and fullscreen:
        return PASS, "v31 图标=transparent、launch_background=fill+splash_hd"
    missing = [n for n, ok in (("v31 透明图标", transparent), ("launch_background 全屏大图", fullscreen)) if not ok]
    return FAIL, "缺：" + "、".join(missing)


def epg_state(repo: str, root_ui: str, screen: str, log: str) -> tuple[str, str]:
    """③ 节目单：源可换（51zmt 在）、手动跟 302、启动延时、界面上有「正在播」、日志真拿到条目。"""
    checks = {
        "源含 51zmt": "epg.51zmt.top" in repo,
        "手动跟 302": "Location" in repo and "instanceFollowRedirects = false" in repo,
        "启动延时 20 秒": re.search(r"delay\(\s*20_000\s*\)", root_ui) is not None,
        "午夜重取用北京时间": "millisToNextMidnight" in root_ui,
        "界面有「正在播」": "正在播" in screen and "onAir" in screen,
    }
    got = re.findall(r"条目=(\d+)", log or "")
    checks["日志真拿到 ≥50 条"] = bool(got) and max(int(x) for x in got) >= 50
    bad = [k for k, ok in checks.items() if not ok]
    return (PASS, "、".join(checks)) if not bad else (FAIL, "缺：" + "、".join(bad))


def tier_of(w: int) -> int:
    return 3 if w >= 3840 else 2 if w >= 1920 else 1 if w >= 1280 else 0


def order_state(lines: list[tuple[str, str]], sat_names: list[str]) -> tuple[str, str]:
    """④ 排序：卫视组前两台必须是北京、辽宁；每条线的宽度档不许出现「后面的比前面的高」。"""
    bad_sat = [] if sat_names[:2] == ["北京卫视", "辽宁卫视"] else [f"卫视前两台={sat_names[:3]}"]
    bad_order = []
    for name, sizes in lines:
        tiers = [tier_of(w) for w in sizes]
        if any(b > a for a, b in zip(tiers, tiers[1:])):
            bad_order.append(name)
    if bad_sat or bad_order:
        return FAIL, "；".join(bad_sat + ([f"{len(bad_order)} 台线序倒挂：{'、'.join(bad_order[:6])}"] if bad_order else []))
    return PASS, f"卫视前两台=北京/辽宁；{len(lines)} 台线序无倒挂"


def manifest_state(cat: dict) -> tuple[str, str]:
    """④ 附：清单 `categories[].subgroups` 必须声明目录里真实出现的每一个 (分类, 子分组)。

    首页 tab 是照这份清单画的（`HomeScreen` 遍历 `catalog.categories[].subgroups`）；
    声明漏一个，那组里的台在界面上**整个看不见**。10-30 现读抓到 `international/综合`
    （Arirang TV 阿里郎）没被声明——用户刚点档「留」的那台，界面里其实一直没露过面。
    """
    declared = {c["id"]: set(c.get("subgroups") or []) for c in cat.get("categories") or []}
    missing = sorted({(ch.get("category"), ch.get("subgroup")) for ch in cat.get("channels") or []
                      if (ch.get("subgroup") or "") not in declared.get(ch.get("category"), set())})
    if not missing:
        return PASS, f"{len(declared)} 个分类的 subgroups 都覆盖到了"
    return FAIL, "清单没声明的分组：" + "、".join(f"{a}/{b}" for a, b in missing)


def local_first_state(channels: list[dict]) -> tuple[str, str]:
    """④ 附：地方频道里「北京」那一块必须在最前（用户 2026-10-30「提到地方组最前」，提）。"""
    subs = [c.get("subgroup") for c in channels if c.get("category") == "local"]
    if not subs:
        return ABSENT, "目录里没有地方频道"
    return (PASS, f"地方频道第一块={subs[0]}（共 {len(set(subs))} 块）") if subs[0] == "北京" \
        else (FAIL, f"地方频道第一块是 {subs[0]}，不是北京")


def blocks_state(channels: list[dict]) -> tuple[str, str]:
    """④ 附：「其他按 subgroup 分组排序」——同一个 (分组, 子分组) 的台必须在目录里连成一块。

    10-30 现读抓到三处被拆开：浙江（余姚姚江文化掉到第 300）、台湾（TVBS亚洲掉到第 301）、
    综合（Arirang 掉到第 302）。这一格只认「块是否连续」，块与块的先后由点名次序决定
    （央视→4K→卫视 是用户 10-30 那句话，不能拿 PROVINCE_ORDER 之类去覆盖它）。
    """
    seen, split = [], []
    for ch in channels:
        k = (ch.get("category"), ch.get("subgroup") or "")
        if seen and seen[-1] == k:
            continue
        if k in seen:
            split.append(f"{k[0]}/{k[1]}")
        seen.append(k)
    return (PASS, f"{len(set(seen))} 块全部连续") if not split else (FAIL, "被拆开的块：" + "、".join(sorted(set(split))))


def fourk_state(channels: list[dict], sizes: dict[str, tuple[int, int]]) -> tuple[str, str]:
    """⑤ 卫视 4K：4K 组里带 4K 的卫视台，每条线都必须 ≥3840 宽（尺寸未知的不算违规，但要点名数）。"""
    bad, unknown = [], 0
    for ch in channels:
        if ch.get("subgroup") != "4K 超高清" or not ch["name"].rstrip().endswith("4K"):
            continue
        for url in ch["streams"]:
            size = sizes.get(url)
            if not size:
                unknown += 1
            elif size[0] < 3840:
                bad.append(f"{ch['name']} {url[:40]}")
    if bad:
        return FAIL, "非 3840 的线 " + "、".join(bad[:4])
    return PASS, f"4K 卫视组每条线都 ≥3840（尺寸未知 {unknown} 条）"


def freeze_state(lr: str, player: str, test: str, log: str, switch_log: str = "") -> tuple[str, str]:
    """② CCTV-4K 冻住不换线：判据要在、要被播放器调用、要有夹具、要有真机日志。

    真机证据认两种，任一即可（各自点名，不许互相冒充）：
      a) `StallWatch: frozenPos`——「位置不前进」那条判据自己响了（用户报的那一次的原始形状）；
      b) 一条**顺畅播满过**的线断掉后，重连配额用尽那一拍选了 `NextLine`/`NextChannel`
         （`LineRecovery … 顺畅播满=[…] … 重连=2 选择=NextLine`）——证明「没画面就换线」这条链
         在真机上真的会动。`顺畅播满=[]`（没播过的线）不算，那些线本来就会立刻换。
    """
    code = "class PosFreezeTracker" in lr and "sample(" in lr
    wired = "PosFreezeTracker(" in player and "frozenPos" in player
    teeth = test.exists()
    if not (code and wired and teeth):
        return FAIL, f"代码={code} 接线={wired} 夹具={teeth}"
    hits = len(re.findall(r"StallWatch: frozenPos", log or ""))
    if hits:
        return PASS, f"StallWatch frozenPos {hits} 行"
    # 只认「播过的那条线」升级：顺畅播满=[数字…] 且 选择=NextLine/NextChannel
    # 必须同一行里 hasPicture=true：`顺畅播满=[7]` 是**这个台**的集合，没播过的下一条线
    # 也会带着它（16:03:37 那行 hasPicture=false 就属于这种），只按集合判会把「没播过的线换线」冒充成实证。
    esc = re.findall(r"hasPicture=true 顺畅播满=\[\s*\d[^\]]*\][^\n]*?重连=(\d+)\s+选择=(NextLine|NextChannel)",
                     switch_log or "")
    if esc:
        last = esc[-1]
        return PASS, f"真机：播过的线重连用尽后换线 {len(esc)} 次（最近一次 重连={last[0]} 选择={last[1]}）"
    return ABSENT, (f"代码/夹具齐（{test.name}）；frozenPos 0 行、"
                    f"「播过的线换线」日志 0 条 ⇒ 那两类现场都还没抓到")


# ── 读现读产物 ────────────────────────────────────────────────────────────────
def load_asset():
    cat = json.loads(ASSET.read_text(encoding="utf-8"))
    lq = cat.get("lineQuality", {})
    sizes, lines = {}, []
    for ch in cat["channels"]:
        ws = []
        for url in ch["streams"]:
            m = re.match(r"^(\d+)x(\d+)$", str(lq.get(url, "")))
            if m:
                sizes[url] = (int(m.group(1)), int(m.group(2)))
                ws.append(int(m.group(1)))
        lines.append((ch["name"], ws))
    return cat, sizes, lines


def report(name: str, state_and_why: tuple[str, str], evidence: str, missing: str) -> bool:
    state, why = state_and_why
    mark = {"达成": "绿", "未达成": "红", "待人工": "等", "没这份证据": "无"}[state]
    print(f"[{mark}] {name}")
    print(f"     读数：{why}")
    print(f"     出处：{evidence}")
    print(f"     还缺：{missing if state != PASS else '无'}")
    return state == PASS


def selftest() -> int:
    global ASSET, THEMES_V31, LAUNCH_BG
    fails = 0

    def chk(tag, got, want):
        nonlocal fails
        ok = got == want
        print(("  [绿] " if ok else "  [红] ") + f"{tag}：{got!r}" + ("" if ok else f"｜期望 {want!r}"))
        fails += 0 if ok else 1

    # ① 正／负对照
    chk("S1a 两份都对 ⇒ 达成",
        splash_state('windowSplashScreenAnimatedIcon=@android:color/transparent', 'android:gravity="fill" @drawable/splash_hd')[0], PASS)
    chk("S1b 图标没设透明 ⇒ 判红",
        splash_state('windowSplashScreenBackground=#000', 'android:gravity="fill" splash_hd')[0], FAIL)
    chk("S1c 不是整屏铺 ⇒ 判红",
        splash_state('windowSplashScreenAnimatedIcon=transparent', 'android:gravity="center" splash_hd')[0], FAIL)
    # ③ 日志没条目 ⇒ 判红（「读不到」不许折成通过）
    repo = "epg.51zmt.top Location instanceFollowRedirects = false"
    root = "delay(20_000) millisToNextMidnight"
    screen = "正在播 onAir"
    chk("S3a 日志有条目 ⇒ 达成", epg_state(repo, root, screen, "条目=102")[0], PASS)
    chk("S3b 日志零条目 ⇒ 判红", epg_state(repo, root, screen, "")[0], FAIL)
    chk("S3c 少了启动延时 ⇒ 判红",
        epg_state(repo, "millisToNextMidnight", screen, "条目=102")[0], FAIL)
    # ④ 排序两格
    chk("S4a 北京辽宁在前 + 线序单调 ⇒ 达成",
        order_state([("甲", [3840, 1920, 1280])], ["北京卫视", "辽宁卫视", "东方卫视"])[0], PASS)
    chk("S4b 辽宁不在第二 ⇒ 判红",
        order_state([("甲", [1920])], ["北京卫视", "东方卫视", "辽宁卫视"])[0], FAIL)
    chk("S4c 720 排在 1080 前面 ⇒ 判红",
        order_state([("乙", [1280, 1920])], ["北京卫视", "辽宁卫视"])[0], FAIL)
    # ④ 附：subgroup 块必须连续
    blk_ok = [{"category": "local", "subgroup": "浙江"}, {"category": "local", "subgroup": "浙江"},
              {"category": "hktw", "subgroup": "台湾"}]
    blk_bad = [{"category": "local", "subgroup": "浙江"}, {"category": "hktw", "subgroup": "台湾"},
               {"category": "local", "subgroup": "浙江"}]
    chk("S4d 同 subgroup 连成一块 ⇒ 达成", blocks_state(blk_ok)[0], PASS)
    chk("S4e 同一 subgroup 被拆开 ⇒ 判红", blocks_state(blk_bad)[0], FAIL)
    chk("S4f 地方频道第一块是北京 ⇒ 达成", local_first_state([{"category": "local", "subgroup": "北京"},
                                                           {"category": "local", "subgroup": "河北"}])[0], PASS)
    chk("S4g 地方频道第一块不是北京 ⇒ 判红", local_first_state([{"category": "local", "subgroup": "河北"},
                                                           {"category": "local", "subgroup": "北京"}])[0], FAIL)
    _man_ok = {"categories": [{"id": "a", "subgroups": ["x"]}], "channels": [{"category": "a", "subgroup": "x"}]}
    _man_bad = {"categories": [{"id": "a", "subgroups": ["x"]}], "channels": [{"category": "a", "subgroup": "y"}]}
    chk("S4h 清单声明覆盖全部现读分组 ⇒ 达成", manifest_state(_man_ok)[0], PASS)
    chk("S4i 有台的分组没被声明（界面会看不见）⇒ 判红", manifest_state(_man_bad)[0], FAIL)
    # ⑤ 4K
    chans = [{"name": "东方卫视 4K", "subgroup": "4K 超高清", "streams": ["a"]}]
    chk("S5a 3840 ⇒ 达成", fourk_state(chans, {"a": (3840, 2160)})[0], PASS)
    chk("S5b 1920 ⇒ 判红", fourk_state(chans, {"a": (1920, 1090)})[0], FAIL)
    chk("S5c 尺寸未知 ⇒ 不算违规但要报数", fourk_state(chans, {})[1], "4K 卫视组每条线都 ≥3840（尺寸未知 1 条）")
    # ② 冻住
    chk("S2a 代码+接线+夹具齐、日志 0 行 ⇒ 没这份证据（不许当通过）",
        freeze_state("class PosFreezeTracker sample(", "PosFreezeTracker( frozenPos", THEMES_V31, "")[0], ABSENT)
    chk("S2b 没接线 ⇒ 判红",
        freeze_state("class PosFreezeTracker sample(", "exoPlayer", THEMES_V31, "")[0], FAIL)
    played_switch = "LineRecovery: channel=x line=8 hasPicture=true 顺畅播满=[7] 已出画=[7] 重连=2 选择=NextLine 消息=x"
    chk("S2c 真机抓到「播过的线配额用尽后换线」⇒ 达成",
        freeze_state("class PosFreezeTracker sample(", "PosFreezeTracker( frozenPos", THEMES_V31, "", played_switch)[0], PASS)
    unplayed_switch = "LineRecovery: channel=x line=1 hasPicture=false 顺畅播满=[] 已出画=[7] 重连=0 选择=NextLine 消息=x"
    chk("S2d 负对照：没播过的线换线不算数（口径不许被它顶绿）",
        freeze_state("class PosFreezeTracker sample(", "PosFreezeTracker( frozenPos", THEMES_V31, "", unplayed_switch)[0], ABSENT)
    print(f"[selftest] 失败 {fails} 格")
    return 1 if fails else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    cat, sizes, lines = load_asset()
    themes = THEMES_V31.read_text(encoding="utf-8")
    launch = LAUNCH_BG.read_text(encoding="utf-8")
    repo = (JAVA / "data/EpgRepository.kt").read_text(encoding="utf-8")
    root_ui = (JAVA / "ui/AppRoot.kt").read_text(encoding="utf-8")
    screen = (JAVA / "ui/PlayerScreen.kt").read_text(encoding="utf-8")
    lr = (JAVA / "data/LineRecovery.kt").read_text(encoding="utf-8")
    test = ROOT / "app/src/test/java/com/sun/tv/parents/data/PosFreezeTrackerTest.kt"
    epg_log = EPG_LOG.read_text(encoding="utf-8", errors="ignore") if EPG_LOG.exists() else ""
    freeze_log = ""
    switch_log = ""
    for cand in sorted((ROOT / "tools/.cache").glob("logcat_*20261030*.txt")):
        text = cand.read_text(encoding="utf-8", errors="ignore")
        freeze_log += text
        switch_log += text

    sat = [ch["name"] for ch in cat["channels"] if ch.get("subgroup") == "卫视"]
    builder = BUILDER.read_text(encoding="utf-8")
    results = [
        ("① 启动图全屏大图（资源层）", splash_state(themes, launch),
         "values-v31/themes.xml、drawable/launch_background.xml", "真机那一秒的观感（本机抓帧手段拿不到）"),
        ("② CCTV-4K 冻住自动换线", freeze_state(lr, screen, test, freeze_log, switch_log),
         "LineRecovery.kt / PlayerScreen.kt / LineRecoveryTest.kt(480 格) / logcat 转储", "「位置不前进」那条尺的 frozenPos 行"),
        ("③ 节目单启动与时延加载", epg_state(repo, root_ui, screen, epg_log),
         "EpgRepository.kt / AppRoot.kt / PlayerScreen.kt / logcat_epg_20261030.txt", "跨午夜那一跳的真机读数"),
        ("④ 线路清晰度序 + 卫视北京辽宁在前 + 其余按 subgroup 成块",
         (PASS if (order_state(lines, sat)[0] == PASS and blocks_state(cat["channels"])[0] == PASS
                   and local_first_state(cat["channels"])[0] == PASS
                   and manifest_state(cat)[0] == PASS) else FAIL,
          order_state(lines, sat)[1] + "；" + blocks_state(cat["channels"])[1] + "；" + local_first_state(cat["channels"])[1] + "；" + manifest_state(cat)[1])
         if ("北京卫视" in sat and "辽宁卫视" in sat) else (FAIL, f"卫视名册现读 {sat[:3]}"),
         "app/src/main/assets/channels.json（lineQuality 宽度档 + 块序）", "无"),
        ("⑤ 卫视 4K 只留 3840", fourk_state(cat["channels"], sizes),
         "app/src/main/assets/channels.json + build_catalog.enforce_sat_4k", "无"),
    ]
    # 逐条都念（不能写进 all(...) 里：all 短路的，第一条红之后后面四条一个字都不打，
    # 读的人会以为「只查了一条」）。
    verdicts = [report(*r) for r in results]
    ok = all(verdicts)
    print(f"\n[对表] 五条里 {sum(1 for r in results if r[1][0] == PASS)} 条达成；"
          f"红 {sum(1 for r in results if r[1][0] == FAIL)}、待人工 {sum(1 for r in results if r[1][0] == HUMAN)}、"
          f"没这份证据 {sum(1 for r in results if r[1][0] == ABSENT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
