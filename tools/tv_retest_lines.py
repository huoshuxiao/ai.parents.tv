#!/usr/bin/env python3
"""电视真机逐条线路回测：不靠抽帧、不靠截图，只认这台电视自己报出来的「第一帧」。

为什么要这一把尺（用户 10-26 连着两次点名「电视真机实测」「咪咕体育 全线路 真机回测」）：
  · 主持机 VLC 抽到帧 ≠ 这台电视能播（容器、鉴权、CDN 绑定都可能只在电视这一侧过不了）；
  · `screencap` 拍不到这台小米电视的视频层（播放时整屏纯黑），`screenrecord` 只拍得到悬浮层
    且 3–4 秒就断 ⇒ 视觉证据这条路在这台机上拿不到东西；
  · 能拿到的硬证据是应用自己打的日志：
      `ZapFix   : firstFrame channel=<id> line=N t=<elapsedRealtime>`  ← 真的出画了
      `QualityWatch: channel=<id> line=N WxH t=...`                    ← 出的是多大尺寸的画
      `StallWatch : stall|recover channel=<id> line=N ...`             ← 出画之后卡不卡
    三行都带 channel id 与 `t=`（毫秒级单调时钟），所以**不用清日志缓冲**，
    按 `t` 大于上一格读数取数即可——别的窗口在同一台电视上看东西也不会被我们抹掉证据。

跳台用的是遥控器数字键：停手 2.5 秒后那串数字＝**全频道台号**（`PlayerScreen.kt:757` 那段），
所以要先从目录算出台号；数字键只在播放器里生效（首页按数字只移焦点、不报错），
所以起跑前先确认自己真的在播放器里。

用法：
  python3 -u tools/tv_retest_lines.py --group migu --rounds 1
  python3 -u tools/tv_retest_lines.py --group cctv --rounds 3 --limit 5   # 先量单价
产物（都在 --out 下，不建子目录）：
  lines.jsonl   每格一行：轮次/台/台号/线路号/地址/首帧/起播耗时/分辨率/卡顿
  summary.json  按台汇总（任一轮出过首帧＝这台电视真播过）
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "app/src/main/assets/channels.json"
ADB_DEFAULT = str(ROOT.parent / "Android/Sdk/platform-tools/adb") if (ROOT.parent / "Android/Sdk").exists() else "adb"
# 模块级默认值：`main()` 里 `global ADB` 会覆盖它；少了这一行，**import 这把尺的脚本**（`prefs_restore_lines.py`）
# 一调 `device_ms` 就抛 NameError（10-26 23:34 棒 5 落盘就是这么崩的）。
ADB = ADB_DEFAULT

GROUPS = {
    # 咪咕体育：台名带「咪咕」的每一台（当前每台 1 条线 ⇒ "全线路"就是这些台各一条）
    "migu": lambda ch: "咪咕" in ch["name"],
    # 央视·卫视：目录里 category=="cctv" 那一组
    "cctv": lambda ch: ch.get("category") == "cctv",
    "sports": lambda ch: ch.get("category") == "sports",
}

# 标签与消息之间隔着什么（`I StallWatch:`、`I/StallWatch( 7210):`）不写进锚点，
# 只认消息本体——10-26 在电视上实测：`-v time` 与 `-v threadtime` 两种格式下标签前缀不一样，
# 按 `StallWatch:` 去匹配会一条都读不到，看起来就像"这台电视从没出过画面"。
RE_FIRST = re.compile(r"firstFrame channel=(\S+) line=(\d+).*?t=(\d+)")
RE_RES = re.compile(r"QualityWatch.*?channel=(\S+) line=(\d+)\s+(\d+)x(\d+).*?t=(\d+)")
RE_STALL = re.compile(r"stall channel=(\S+) line=(\d+).*?t=(\d+)")
RE_RECOVER = re.compile(r"recover channel=(\S+) line=(\d+) stall=(\d+)ms.*?t=(\d+)")
RE_SELECT = re.compile(r"select channel=(\S+) line=(\d+).*?t=(\d+)")
RE_WATCH = re.compile(r"watch channel=(\S+) line=(\d+) playing=(\S+) 卡顿(\d+)次/(\d+)ms.*?t=(\d+)")
# 10-27 新加：这一行答的是「拿到的是直播还是一枚定长片子」。`8.138.7.223` 那族 302 到同一支广告片，
# firstFrame 会绿、`真机播过` 会记成成立，播的却不是本台节目。`duration` 未知时是 Long.MIN_VALUE（负数）。
# 没有这一行＝装的是不带这格读数的旧包，按「没测到」念，不许折成 live=false。
RE_LIVE = re.compile(r"LiveWatch\b.*?channel=(\S+) line=(\d+) live=(\S+) duration=(-?\d+)")

# v1 的窗口从"按下"起算、把 0x0 当分辨率、把 playing 混进 first_frame；这三条在真机上都被读数否掉了
# （咪咕 16 格里 6 格读成"没首帧"，其中 4K-1 在窗口结束后 1 秒就报了 3840x2160）。
# 版本写进每一行，两把尺的台账不许并成一份来比。
# v5→v6 只换了挪线那一套（`nav_press_plan`：顶到第 1 格再数 want-1 下，不再按 |Δ|），
# 读数口径没动 ⇒ `tools/.cache/tv_retest_cctv_v5` 那份 v5 台账（16:19–…）与 v6 不并比，
# 但同一批"到位/没到位"的判据在 v6 更严，v5 里那几格 `nav_failed` 在 v6 未必还失败。
TOOLV = 8  # 10-27：absorb 多读一格 LiveWatch（live／duration_ms）⇒ 台账行的字段集变了，另起版本
           # （上一版 7 是 10-26 19:4x 换「落地阶段按线号过滤」时起的那一版）。
           # v7 那份（10-26 夜 94 格）照旧用 `--toolv 7` 数：读侧支持，写侧被 refuse_mixed_toolv 拦着，
           # 不许把两版行混在一份台账里比（#131 那一族的复发口）。


def sh(args, serial=None, timeout=60):
    cmd = [args[0]] + (["-s", serial] if serial and args[0].endswith("adb") else []) + args[1:]
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


LOG_TAGS = ("-s", "StallWatch:*", "ZapFix:*", "QualityWatch:*", "LineProbe:*", "FreezeWatch:*")
LIVE_LOG = None          # --live 指过来：读本地这份持续写入的过滤日志，比每次拖缓冲快一个量级


def app_log(serial) -> str:
    """这把尺唯一允许的读日志入口：优先读本地实时文件，退路是**按标签过滤**的 `logcat -d`。

    为什么（10-26 17:1x 现量，同一台电视同一时刻）：整份缓冲 `logcat -d` 是 **80,355 行 / 9,077,970 字节 / 5.91 秒**，
    而按标签过滤只有 **213 行 / 25,317 字节 / 2.05 秒**——一把测量里这种读要发生几十次，
    拖整份缓冲就是"222 条要跑 6-8 小时"里最贵的那一块。`--live` 之后本地读文件是毫秒级。
    """
    if LIVE_LOG:
        f = Path(LIVE_LOG)
        if f.exists():
            txt = f.read_text(encoding="utf-8", errors="replace")
            return txt[-400_000:]
    return adb_out(serial, "logcat", "-d", "-v", "threadtime", *LOG_TAGS)


def adb_out(serial, *args, timeout=60) -> str:
    r = sh([ADB, *args], serial=serial, timeout=timeout)
    return (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")


def keyevents(serial, codes, gap=0.12):
    """一台电视上批量 `input keyevent A B C` 只会落地第一个（10-24 踩过），所以一次一条。"""
    for c in codes:
        adb_out(serial, "shell", "input", "keyevent", str(c))
        time.sleep(gap)


def targets(group, cat):
    """要测的线：全部按**设备那份目录**数（线路次序与台号都以它为准）。"""
    keep = GROUPS[group]
    rows = []
    order = {cid: i + 1 for i, cid in enumerate(numbered_ids(cat))}
    for ch in cat["channels"]:
        if not keep(ch):
            continue
        number = order.get(ch["id"])
        if not number:
            continue                      # 可播线路为空的台进不了台号表，跳过并如实报
        for i, url in enumerate(ch.get("streams") or []):
            rows.append({"number": number, "channel_id": ch["id"], "name": ch["name"],
                         "line_index": i, "url": url})
    return rows


def park_keep_waiting(cur_channel, park_id, waited_s, bound_s):
    """停车的等待循环还要不要再转一轮（True＝再等；False＝可以往下走）。

    为什么加这道判据（10-26 22:1x 现读把 #125 那一格点到了 CCTV-2 的**第一条线**）：
    这一格的 5 行读数签名完全一致——`land_wait_s=None`、`nav_from=None`、`nav_presses=0`、
    `presses=1`、`seen=False`，也就是阶段 A 一行都没读到、`landed_at` 为 None，后面整段观测被跳过；
    而同一台的线 2..9 全部 `seen=True`（17:53 那格 `presses=1 land_wait=9.4`）。两条路唯一的差别：
    线1 紧跟在 `--park 1` 之后，而旧写法只要 `--park-wait`（默认 12 秒）一到就**盲按目标台号**——
    这台上排队时前一枚台号可能十几秒才生效，两枚台号挨太近会被应用吃成一枚或并成一个多位数。
    判据只认三条：已到车位→不等；没到但没到点→等；没到且已到点→不等（由调用方改判"没落地"）。
    """
    if cur_channel == park_id:
        return False
    return waited_s < bound_s


def park_target(ids, park_number, target_cid):
    """停车台该停哪一号：目标台正好占着默认停车号时，换下一号——**键号要跟着换**。

    为什么单独抽出来（10-26 17:4x 现读）：原来这一支只换了 `park_id`，按下去的还是
    `args.park` 那个键号 ⇒ 测台号 1 那一台时"停车"停的就是它自己，
    应用根本没重新起播，那一格读出来是 `seen=False`（v6 第 1 格 CCTV-1 线 1 就是这样）。
    """
    if park_number is None:
        return None, None
    if ids and target_cid is not None and len(ids) > park_number:
        nxt = ids[park_number]              # ids 是 0 基列表：台号 k 存在 ids[k-1]
        if ids[park_number - 1] == target_cid:
            return nxt, park_number + 1     # 目标台占了这一号 ⇒ 停车台与键号一起挪到下一号
    return ids[park_number - 1] if len(ids) >= park_number else None, park_number


def press_number(serial, number):
    keyevents(serial, [7 + int(d) for d in str(number)])   # KEYCODE_0=7 … KEYCODE_9=16


def device_ms(serial) -> int:
    """设备单调时钟（毫秒）：日志里那些 `t=` 用的就是它，跨端相减必须是同一个钟。"""
    up = adb_out(serial, "shell", "cat", "/proc/uptime")
    try:
        return int(float(up.split()[0]) * 1000)
    except (ValueError, IndexError):
        return 0


def device_catalog(serial, pkg) -> dict:
    """把设备那份**正在用**的目录拉回来：台号是按它编的，不是在仓库那份上编的。

    10-26 真机踩点：仓库里 CCTV-6 是第 6 台，可设备上按 6 落在 CCTV-6 电影、按 61 才落在
    咪咕直播 4K-1——设备那份目录经过「立即更新线路」合并，顺序与仓库那份不必相同。
    所以台号必须现拉现算，不能拿仓库下标 +1 顶替。
    """
    r = sh([ADB, "-s", serial, "shell", "run-as", pkg, "cat", "files/channels_live.json"])
    text = (r.stdout or b"").decode("utf-8", "replace")
    if not text.lstrip().startswith("{"):
        raise SystemExit(f"[拒绝] 读不到设备侧 files/channels_live.json（拿到 {len(text)} 字节）")
    return json.loads(text)


def numbered_ids(cat: dict) -> list:
    """复刻 ChannelNumbers.numbered(playableChannels())：央视 1–17 显式排前，5+ 第 18，其余按目录原序。"""
    playable = [c for c in cat["channels"] if c.get("streams")]

    def cctv_number(name):
        m = re.match(r"^CCTV-(\d{1,2})(?![\dA-Za-z+])", name.strip())
        return int(m.group(1)) if m else None

    head = [next((c for c in playable if cctv_number(c["name"]) == k), None) for k in range(1, 18)]
    head = [c for c in head if c]
    five = next((c for c in playable if c["name"].strip().startswith("CCTV-5+")), None)
    reserved = {c["id"] for c in head} | ({five["id"]} if five else set())
    tail = [c for c in playable if c["id"] not in reserved]
    ordered = head + ([five] if five else []) + tail
    return [c["id"] for c in ordered]


SELFTEST = [("CCTV-6 电影", "cctv-638cd7b045"), ("咪咕直播 4K-1", "sports-7cfbe253ed")]


def selfcheck_numbers(ids: list, serial: str | None = None, pkg: str = "com.sun.tv.parents",
                       live: bool = True) -> None:
    """自证"复刻的台号表"没歪：按复刻表算出的键，**在设备上真按一次**，看应用落在哪台。

    早期这里是写死「号→台」的读数（10-26 小米电视按 6 落 CCTV-6、按 61 落咪咕直播 4K-1）。
    10-29 一天内目录动了几次（重入四台主机、删地方频道、cctv 组改序），写死的号每动一次目录就
    过期一次、把整跑拒在门外——所以改成现读现按：号从本次要跑的那份表里算，落点由设备回答。
    判据没变（复刻表歪了照样拒跑），只是不再依赖任何过期常量。
    """
    table = {cid: i + 1 for i, cid in enumerate(ids)}
    for name, cid in SELFTEST:
        got = table.get(cid)
        if got is None:
            raise SystemExit(f"[拒绝] 复刻台号表里没有 {name}({cid}) ⇒ 不跑")
        if not serial or not live:
            # 干跑（--dry-run）也一律不按键：干跑动一下设备，就会把正在跑的那一场污染掉
            #（10-29 20:20 实测：第 1 轮跑到 CCTV-1 线 6 时我干跑了一次自证，按 6 把应用带去了 CCTV-6）。
            print(f"[自证] {name} = 键 {got}（干跑/未连设备：只念号，不实按）", flush=True)
            continue
        if not ensure_player(serial, pkg, print):
            raise SystemExit("[拒绝] 进不了播放器，按不出自证读数 ⇒ 不跑")
        press_number(serial, got)
        # 按完要**轮询**等落点：这台应用在慢线/卡顿里时数字键会排队几秒到几十秒，
        # 固定等 6 秒会把「还在上一个台」读成「复刻表歪了」（10-29 12:5x 就误拒过一次）。
        seen, age = None, None
        for _ in range(12):
            time.sleep(2.5)
            seen, age = current_channel(serial)
            if seen == cid:
                break
        if seen != cid:
            raise SystemExit(
                f"[拒绝] 按 {got} 落在 {seen}（{age} ms 前），复刻表说应是 {name}({cid}) ⇒ 不跑")
        print(f"[自证] 按 {got} 落在 {name}（设备现读，复刻表与设备一致）", flush=True)


def in_player(serial, fresh_ms: int = 15_000) -> bool:
    """在播放器里的证据：**最近 15 秒内**打过一行 `StallWatch: watch channel=…`（首页没有这行）。

    必须带新鲜度。10-26 14:0x 实测：装完新包后应用是死的，日志缓冲里还留着装机前那一批
    `watch` 行（最后一次 14:02:34、设备已经走到 12660 秒），只按"有没有这行"判就会把
    "应用根本没在跑"读成"在播放器里"，接下来整批按键全打在空气上、39 条线一律记成没首帧。
    """
    now = device_ms(serial)
    for line in app_log(serial).splitlines():
        if "watch channel=" not in line:
            continue
        m = re.search(r"t=(\d+)", line)
        if m and now - int(m.group(1)) <= fresh_ms:
            return True
    return False


def app_alive(serial, pkg) -> bool:
    return bool(adb_out(serial, "shell", "pidof", pkg).strip())


def ensure_player(serial, pkg, log) -> bool:
    if in_player(serial):
        return True
    log(f"[player] 不在播放器里（进程活着吗：{app_alive(serial, pkg)}），拉起应用\n")
    adb_out(serial, "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LEANBACK_LAUNCHER", "1")
    for _ in range(3):
        time.sleep(6)
        if in_player(serial):
            return True
        # 主页那一层按 OK 只是"选中左边那一组"，进不了播放器（10-26 15:3x 现读：连按 OK
        # 只是把左边分组高亮从央视·卫视挪到体育频道）。要先 右 把焦点请进频道卡片，再 OK。
        keyevents(serial, [22, 23])
        time.sleep(4)
        if in_player(serial):
            return True
    print("[warn] 三次都没能确认在播放器里，这一轮的读数会整批缺首帧", flush=True)
    return False


def timing(cell, switch_ms):
    """(遥控器排队多久, 这条线起播多久)。两个数分开算，谁读不到就写 None，不许互相顶替。

    nav_lag = 应用为这条线打的第一行 − 我按"确定"之前那一刻；zap = 第一帧 − 那一行。
    """
    seen, first = cell.get("first_seen"), cell.get("first")
    nav_lag = (seen - switch_ms) if (seen and switch_ms) else None
    zap = (first - seen) if (first and seen) else None
    return nav_lag, zap, cell.get("anchor")


def new_cell():
    return {"seen": False, "first": None, "first_line": None, "first_seen": None, "anchor": None,
            "res": None, "res_zero": 0, "stalls": 0, "stall_ms": 0, "playing": None,
            "live": None, "duration_ms": None, "cum": (0, 0)}


def refuse_mixed_toolv(toolv_arg, dry_run):
    """要往台账写行的那一档，`--toolv` 必须与这把尺自己的版本一致，否则拒绝（返回一句话）。

    为什么要这道闸（10-26 19:4x 现读代码）：`--toolv` 只作用于**读侧**（`rows_from_ledger`／
    `failure_keys` 的过滤器），而写进台账的每一枚 `toolv=` 永远取当前常量 `TOOLV`（`:641`）。
    尺升到 7 之后，链子里那些写死的 `--toolv 6`（`tv_closeout_chain.sh:87/108/114/179/195/201/212`）
    就会变成"数 v6 的行、往同一份目录里写 v7 的行"——下一次 `--audit`／读数板按 6 再数一遍，
    新写的行**一枚都读不到**，报出来的就是「没跑全 222 格」这种假故障。
    空跑（`--dry-run`）不落行⇒放行；没给旗子（0＝用当前 TOOLV）⇒放行。
    """
    if dry_run or not toolv_arg or toolv_arg == TOOLV:
        return None
    return (f"[拒绝] --toolv {toolv_arg} 与这把尺当前版本 TOOLV={TOOLV} 不一致，而这一档会往台账写行"
            f"（`--toolv` 是读侧过滤器，写的行永远盖 {TOOLV}）。"
            f"要么把旗子改成 --toolv {TOOLV}＋换新的台账目录，要么用 --dry-run/--audit/--rescore "
            f"这类不落按键的档去数旧台账。")


def absorb(cell, item, text, last_t, want_line=None):
    """把**属于本台**且比 `last_t` 新的日志行读进这一格，返回新的 `last_t`。

    `want_line` 非空时只认那一行的 `line=`（10-26 15:1x 现读：连着要 CCTV-1 的第 1、2 条线，
    两格读数一模一样、`line=` 都是 1 ⇒ 不按线号过滤就会把一条线记成四条）。

    抽成纯函数是为了让这把尺能被喂真日志（`tools/tests/test_tv_retest_lines.py` 用的是 10-26
    从这台小米电视 logcat 里逐字切下来的行），也让长跑中途改判据时能拿存量行重算 verdict。
    """
    for line in text.splitlines():
        if f"channel={item['channel_id']}" not in line:
            continue
        m = re.search(r"t=(\d+)", line)
        if not m or int(m.group(1)) <= last_t:
            continue
        # `seen` 只回答"应用有没有为这个台打过行"＝落地闸，跟线号无关，所以必须排在线号过滤**之前**
        # （10-26 现读：把它挪到过滤之后，落地那一圈永远读不到行 ⇒ 整格判成「按键没落地（不作数）」）。
        cell["seen"] = True
        if want_line is not None:
            m3 = re.search(r"line=(\d+)", line)
            if m3 and int(m3.group(1)) != want_line:
                last_t = max(last_t, int(m.group(1)))   # 游标照样推过去：这行已经读过了，只是不计分
                continue
        m4 = RE_SELECT.search(line)
        if (m4 and (want_line is None or int(m4.group(2)) == want_line)
                and cell["first"] is None):
            # 应用自己说"这次选线我接住了"——这是唯一能分清"按键排队"与"这条线起播"的锚点：
            # QualityWatch 与 firstFrame 都在出帧那一瞬才打（真读：16:00:08.618 与 .621 只差 3ms），
            # 拿它们当起点会把 150 秒的按键排队算进"起播耗时"。
            # 只认"出帧之前的那一枚选线行"：出帧之后再来的选线属于下一次切换，
            # 拿它当锚点会算出负起播（10-26 16:1x 现读：线 1 那一格 -38,236ms）。
            cell["first_seen"] = int(m4.group(3))
            cell["anchor"] = "select"
        elif cell["first_seen"] is None:
            # 没有选线行（这一格是靠换台落进来的，或装的是不带这行日志的旧包）：退回"本线第一行"，
            # 锚点如实写 firstlog，读数引用时必须连这个锚点一起念。
            cell["first_seen"] = int(m.group(1))
            cell["anchor"] = "firstlog"
        m2 = RE_FIRST.search(line)
        if m2 and cell["first"] is None:
            cell["first"] = int(m2.group(3))
            cell["first_line"] = int(m2.group(2))
        m2 = RE_RES.search(line)
        if m2:
            # 0x0 是 QualityWatch 第一拍还没量到画面尺寸，不是黑屏（10-26 真机 6 格读出 0x0，
            # 其中 4K-1 那一格在 1 秒后就报了 3840x2160）。
            if (m2.group(3), m2.group(4)) == ("0", "0"):
                cell["res_zero"] += 1
            else:
                cell["res"] = f"{m2.group(3)}x{m2.group(4)}"
        if RE_STALL.search(line):
            cell["stalls"] += 1
        m2 = RE_RECOVER.search(line)
        if m2:
            cell["stall_ms"] += int(m2.group(3))
        m2 = RE_WATCH.search(line)
        if m2:
            cell["playing"] = m2.group(3) == "true"
            cell["cum"] = (int(m2.group(4)), int(m2.group(5)))
        m2 = RE_LIVE.search(line)
        if m2:
            # 只存读数，不参与判分：这一格要拿它做删线依据时先看清楚它在说什么（见上面那三行注释）。
            cell["live"] = m2.group(3) == "true"
            cell["duration_ms"] = int(m2.group(4))
        last_t = max(last_t, int(m.group(1)))
    return last_t


def newest_line(serial, cid):
    """这个台**此刻**在第几条线（日志里 `line=` 是 1 起的）：取本台最新一行里的数。"""
    best_t, best_line = 0, None
    for line in app_log(serial).splitlines():
        if f"channel={cid}" not in line:
            continue
        m = re.search(r"line=(\d+).*?t=(\d+)", line)
        if m and int(m.group(2)) > best_t:
            best_t, best_line = int(m.group(2)), int(m.group(1))
    return best_line



def nav_press_plan(total, want):
    """把"该按哪几下"抽成纯函数：先按上键顶到第 1 格（撞顶不会再动＝可数），再按 want-1 次下键。

    为什么不再用 `|want-cur|`：`cur` 是从 logcat 里 `line=` 最新那行推的，而面板的初焦是应用自己的
    `selectedIndex`（`PlayerScreen.kt:1933`），两者可以不是同一格——10-26 16:2x 现读：要第 7 条、
    日志说在 6、按 1 次下键，应用打出来的是 `select line=4 from=6`（选下去的是计划外那条）。
    为什么不能"按之前先读高亮"：这台电视的 `uiautomator dump` 里 `focused`／`selected` **全是 false**
    （两份真 dump 各数一次：`tools/.cache/cctv7/pick_v115.xml`、`tools/.cache/probe_check/o.xml`
    都是 0 枚），读不到焦点在哪一格 ⇒ 只能靠"顶到第 1 格"这个不依赖读数的起点。
    多按 `+3` 下是把底部那颗「重新检测」「完成」两步也算进去（焦点若落在按钮上，先要跳回列表）。
    """
    if total < 1 or not (1 <= want <= total):
        raise ValueError(f"要第 {want} 条线，可这台只有 {total} 条：先把参数对清楚")
    return [19] * (total + 3) + [20] * (want - 1)


def line_totals(cat):
    """每台几条线（面板行数）：以设备那份目录为准，`nav_press_plan` 要用它定顶格步数。"""
    return {ch["id"]: len(ch.get("streams") or []) for ch in cat["channels"]}


def press_gate(cur, target, cur_age, first_press, since_press, repress_after, idle_ms=25_000):
    """这一轮该不该按台号。第一次＝无条件；补按才要"队列排空"那三道判据。

    为什么第一次不许设闸（10-26 16:5x 现读，这把尺把整遍跑成空转的真元凶）：
    原先第一次按也走"日志已老 ≥25 秒"那一支，可 `cur_age` 量的是**最新一行 `watch` 有多旧**，
    而一条健康在播的线每 10 秒就打一行 ⇒ 停车台播得越正常，`cur_age` 越新，
    第一次那一下永远按不出去（v5 那一遍 CCTV-2 连 8 格 `seen=False presses=0`、
    v6 冒烟第 1 格也是 `presses=0`；把进程停掉后单按一次台号 2，25 秒内就落地
    ⇒ 键是通的，拦路的是这道闸）。
    补按为什么还要闸：这台电视排队时应用会晚一两分钟才跳台，它还在走队列时补按只会把队列堆更深
    （10-26 14:2x 连按 61/62/63/64 实测）。
    """
    if first_press:
        return True
    if since_press is None or since_press < repress_after:
        return False
    return cur is None or cur == target or (cur_age or 0) >= idle_ms


def panel_opened(serial, cid, mark, wait=6.0) -> bool:
    """菜单键按下去之后，应用有没有真开出选线面板（认它自己打的 `LineProbe: panel` 那一行）。

    为什么必须现读这一行（10-26 17:2x–17:3x 三层探针）：这台电视上**菜单键会被吃掉一枚**——
    现读序列：面板开着 → 按返回关掉它 → 立刻按菜单键 ⇒ `panel` 行 0 枚（没开）；
    再按一次返回 → 菜单键 ⇒ 开了。也就是说"返回键之后的头一枚菜单键"不落地，
    而 v6b 冒烟第 3 格那 14 下按键全打在没开的面板上（`nav_failed=True` 其实是"面板没开"）。
    """
    stop = time.monotonic() + wait
    while time.monotonic() < stop:
        for line in app_log(serial).splitlines():
            if "LineProbe: panel " in line and f"channel={cid}" in line:
                m = re.search(r"t=(\d+)", line)
                if m and int(m.group(1)) > mark:
                    return True
        time.sleep(0.5)
    return False


def nav_via_panel(serial, cid, want, total, timeout=25.0):
    """用选线面板把正在用的那条线挪到第 `want` 条；返回 (读到的线号, 按 OK 前那一刻, 按了几下)。

    为什么不用左右键：10-26 15:2x 现读，一条线挂住（`QualityWatch line=3 0x0`、
    `playing=false`）之后连按两次左键 `line=` 纹丝不动——控制栏/错误层把那两下按走了
    （`PlayerScreen.kt:942` 那一支要求 `!controlsVisible`）。
    起点为什么是"顶到第 1 格"：面板刚开时高亮在哪一格**不总等于**「使用中」那格
    （17:31 现读：应用在第 2 条，开面板一枚键都不按直接 OK ⇒ `select line=1`；
    而 17:36 那三格又是原样落回第 4／7／2 条）⇒ 起点不可读，只能自己造一个可数的起点。
    收尾为什么**不**按返回：OK 自己就会关掉面板（17:36 三格连着开面板都成功），
    而补一枚返回反而会让下一格的菜单键被吃掉（`panel_opened` 那段）。
    """
    opened = False
    for _attempt in (1, 2):
        base = device_ms(serial)
        adb_out(serial, "shell", "input", "keyevent", "82")        # MENU：开选线面板
        if panel_opened(serial, cid, base):
            opened = True
            break
        adb_out(serial, "shell", "input", "keyevent", "4")          # 让出被吃掉的那一枚
        time.sleep(1.2)
    if not opened:
        return None, 0, 0
    plan = nav_press_plan(total, want)
    keyevents(serial, plan, gap=0.4)
    ok_ms = device_ms(serial)          # 起播读数的基线：按 OK **之前**那一刻
    adb_out(serial, "shell", "input", "keyevent", "23")             # OK：选定这一格
    stop = time.monotonic() + timeout
    got = None
    while time.monotonic() < stop:
        got = newest_line(serial, cid)
        if got == want:
            break
        time.sleep(0.7)
    return got, ok_ms, len(plan)

def current_channel(serial) -> tuple:
    """应用**此刻**在哪个台：取整份缓冲里最新的 `StallWatch: watch channel=` 那一行的台与 `t=`。

    为什么要这一格（10-26 14:2x–14:4x 在真机上量出来的）：这台电视被一条正在缓冲的线拖住时，
    数字键会在输入队列里**排队几十秒到两分钟**——台账写完了、应用还在上一个台上。
    那时按下去的读数会记到错的格子上，`没首帧` 其实是 `还没轮到`。
    """
    now = device_ms(serial)
    best = (None, 0)
    for line in app_log(serial).splitlines():
        m = RE_WATCH.search(line)
        if m and int(m.group(6)) > best[1]:
            best = (m.group(1), int(m.group(6)))
    if not best[1]:
        return None, None
    return best[0], now - best[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default="192.168.1.4:5555")
    ap.add_argument("--adb", default=ADB_DEFAULT)
    ap.add_argument("--pkg", default="com.sun.tv.parents")
    ap.add_argument("--group", default="migu", choices=sorted(GROUPS))
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--out", default="")
    ap.add_argument("--wait", type=int, default=180,
                    help="阶段 A：按完台号后最多等多久（秒）让应用**真的跳到**这个台；"
                         "这台电视在排队时按键会迟到几十秒到两分钟")
    ap.add_argument("--only-shipped", action="store_true",
                    help="只测随包打进去的那份目录里的线路：设备目录被「立即更新线路」合并过，"
                         "央视·卫视这一组在设备上是 351 条、在包里是 223 条，全测要 5 小时以上")
    ap.add_argument("--after", type=float, default=30.0,
                    help="阶段 B：确认应用已经落到这个台之后，再收这么久的读数（拿分辨率与卡顿）")
    ap.add_argument("--repress-after", type=float, default=60.0, dest="repress_after",
                    help="等了这么多秒还没落到目标台，且应用在自己的台上已经稳定 ≥25 秒（队列排空），"
                         "就补按一次台号")
    ap.add_argument("--park", type=int, default=1, help="测每条线之前先跳去这个台号（跟目标台同号时自动改用下一号）")
    ap.add_argument("--park-wait", type=float, default=12.0, dest="park_wait",
                    help="等停车台落地的最多秒数；不落也继续，不拿这一步卡整场")
    ap.add_argument("--no-park", action="store_true", dest="no_park",
                    help="不先跳去别的台（省时间，但同一个台的第二条线就读不到新的起播事件）")
    ap.add_argument("--nav-wait", type=float, default=90.0, dest="nav_wait",
                    help="阶段 A2：最多花这么多秒把线路挪到这一格要的那一条（方向键换线）")
    ap.add_argument("--line-wait", type=float, default=25.0, dest="line_wait",
                    help="按一下方向键后等 `line=` 变化的秒数；不变就当这条线按不动，再看情况按下一次")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-cid", default="",
                    help="逗号分隔的频道 id：只测这些台。第 1 遍里失败的那些要单独复跑 3 遍定案，"
                         "不重测整组（咪咕 39 条一遍约 40 分钟，全组×3 遍就成两小时）")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="只打印这一跑会测哪些格，不开台账、不拉应用、不按任何键")
    ap.add_argument("--retest-failures", default="", dest="retest_failures",
                    help="只复跑这份台账里「真机播过」以外的格子（含两种「不作数」）："
                         "定案那几遍要按格子筛，按台筛会把已经播过的线一起重测")
    ap.add_argument("--settle", type=float, default=1.5, help="按完数字键到开始读日志之间的额外等待")
    ap.add_argument("--toolv", type=int, default=0,
                    help="只数这个尺版本写过的行；0＝用当前 TOOLV。尺换版后复盘旧台账要用它")
    ap.add_argument("--live", default="",
                    help="指向一份持续写入的过滤日志（另开一个 `adb logcat -v threadtime -s …` 在写）："
                         "读它比每次拖整份缓冲快一个量级，一把测量能省掉几十次 5 秒级的往返")
    ap.add_argument("--audit", default="",
                    help="对表「计划要测的每一格」与「台账里真出现的格」：给台账路径，"
                         "零按键零 adb；没跑全／有重复／有计划外的格都返回码 1")
    ap.add_argument("--rescore", default="",
                    help="只重算读数：给一份 lines.jsonl 的路径，不碰 adb、不按任何键、不写台账")
    args = ap.parse_args()

    global ADB, LIVE_LOG
    ADB = args.adb
    if args.live:
        lf = Path(args.live)
        if not lf.is_absolute():
            lf = ROOT / lf
        if not lf.exists():
            raise SystemExit(f"[拒绝] --live 那份文件不在：{lf}（先起那条 logcat 再跑）")
        LIVE_LOG = str(lf)
        print(f"[live] 读数走本地 {lf.name}，不再每次拖设备缓冲", flush=True)
    if args.audit:
        # 「全线路回测」这句结论必须能被现查，而且是零按键、零 adb：计划那一侧直接拿仓库那份目录数，
        # 台账那一侧拿这把尺写过的行。身份按（台 id＋地址）算——设备那份的**线序**会因"这台真播过置顶"
        # 而与仓库不同，按线号比会把同一格判成"缺一格多一格"（次序差异单独念，不算没跑全）。
        src = Path(args.audit)
        if not src.is_absolute():
            src = ROOT / src
        if not src.exists():
            raise SystemExit(f"[拒绝] 台账不在：{src}")
        plan = targets(args.group, json.loads(CATALOG.read_text(encoding="utf-8")))
        got = rows_from_ledger(src, toolv=(args.toolv or None))
        a = audit_plan(plan, got)
        who = {r["channel_id"]: r["name"] for r in plan}
        print(f"[audit] 尺版本 toolv={args.toolv or TOOLV}｜仓库 {args.group} 组计划 {a['plan']} 格"
              f"（{len(plan)} 条线）｜台账 {a['rows']} 行、覆盖 {a['covered']} 格", flush=True)
        print(f"[audit] 没跑全 {len(a['missing'])} 格｜在数的行重复 {len(a['dup'])} 处｜"
              f"不作数之后重测的格 {len(a['retest'])} 处｜计划外 {len(a['extra'])} 格｜"
              f"线序与仓库那份不一致 {a['order_diff']} 行"
              f"（次序不同不算没测到，只说明设备那份被置顶规则换过序）", flush=True)
        for cid, url in a["missing"][:12]:
            print(f"    缺：{who.get(cid, cid)} {url[:74]}", flush=True)
        for cid, url in a["dup"][:6]:
            print(f"    重复：{who.get(cid, cid)} {url[:74]}", flush=True)
        bad = bool(a["missing"] or a["dup"] or a["extra"])
        print("[audit] 结论：" + ("还没跑全——这一遍不能写成「全线路回测完成」" if bad
                                else "跑全了：计划每一格都有台账行，且没有重复、没有计划外的格"), flush=True)
        return 1 if bad else 0
    if args.rescore:
        # 读数用途一律走 no-write 档：这一条路上一个按键都不发，别的窗口正在用这台电视时也安全。
        src = Path(args.rescore)
        if not src.is_absolute():
            src = ROOT / src
        if not src.exists():
            raise SystemExit(f"[拒绝] 台账不在：{src}")
        dst = Path(args.out) if args.out else ROOT / "tools/.cache/rescore_out"
        if not dst.is_absolute():
            dst = ROOT / dst
        dst.mkdir(parents=True, exist_ok=True)
        print(f"[rescore] 尺版本 {args.toolv or TOOLV}，台账 {src.name}，"
              f"不落按键（no-write 档）", flush=True)
        summarize(src, dst / f"summary-toolv{args.toolv or TOOLV}.json", args.rounds,
                  toolv=(args.toolv or None))
        return 0
    why = refuse_mixed_toolv(args.toolv, args.dry_run)
    if why:
        print(why, flush=True)
        return 6
    dev = device_catalog(args.serial, args.pkg)
    out = Path(args.out or f"tools/.cache/tv_retest_{args.group}")
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / "lines.jsonl"

    rows = targets(args.group, dev)
    totals = line_totals(dev)          # 挪线要知道这台有几格（面板行数＝设备那份目录）
    ids = numbered_ids(dev)
    selfcheck_numbers(ids, args.serial, args.pkg, live=not args.dry_run)
    # 停车台：测每一条线之前先跳去的那个台（默认台号 1，测它自己时用台号 2）。
    park_cid = ids[args.park - 1] if len(ids) >= args.park else None
    park_cid_number = args.park
    if args.no_park:
        park_cid = None
        park_cid_number = None
    if args.only_shipped:
        shipped = {(ch["id"], url) for ch in json.loads(CATALOG.read_text(encoding="utf-8"))["channels"]
                   for url in (ch.get("streams") or [])}
        before = len(rows)
        rows = [r for r in rows if (r["channel_id"], r["url"]) in shipped]
        print(f"[filter] --only-shipped：设备目录 {before} 条 → 随包那份目录里的 {len(rows)} 条"
              f"（差出来的 {before - len(rows)} 条是设备端自动更新合并进来的，不在包里）", flush=True)
    if args.only_cid:
        wanted = [c.strip() for c in args.only_cid.split(",") if c.strip()]
        before = len(rows)
        rows = [r for r in rows if r["channel_id"] in wanted]
        # 点名的台在这一跑里找不到＝台号表/目录换底了或者 id 抄错，必须当场念出来，
        # 不能让它静默变成"这一遍少测了几台、结果却看着齐全"。
        miss = [c for c in wanted if c not in {r["channel_id"] for r in rows}]
        print(f"[filter] --only-cid：{before} 条 → {len(rows)} 条（点名 {len(wanted)} 台）"
              + (f"  找不到 {len(miss)} 台：{','.join(miss)}" if miss else ""), flush=True)
        if miss:
            raise SystemExit("[拒绝] 有点名的台在这一跑的目录里不存在，先把 id 对清楚再起")
    if args.retest_failures:
        # 「第 1 遍里失败的那些再各跑 N 遍定案」＝按**格子**筛，不是按台筛：
        # 只给 --only-cid 会把这一台里已经播过的线一起重测（CCTV-1 那 7 条就是 7 格白跑）。
        src = Path(args.retest_failures)
        if not src.is_absolute():
            src = ROOT / src
        if not src.exists():
            raise SystemExit(f"[拒绝] 台账不在：{src}")
        keys = set(failure_keys(src, toolv=(args.toolv or None), rounds=1))
        before = len(rows)
        rows = [r for r in rows if (r["channel_id"], r["line_index"], r["url"]) in keys]
        # 台账里有点名的格子在这一跑的目录里找不到＝地址换了／线被摘了，必须当场念出来，
        # 不能让它静默变成"这一遍少测了几格、结果却看着齐全"（与 --only-cid 同一口径）。
        miss = len(keys) - len({(r["channel_id"], r["line_index"], r["url"]) for r in rows})
        print(f"[filter] --retest-failures：{before} 条 → {len(rows)} 条"
              f"（第 1 遍里没成的 {len(keys)} 格，其中 {miss} 格在这一跑的目录里已不存在）", flush=True)
        if miss:
            raise SystemExit("[拒绝] 有失败格子在现目录里找不到（源换址或线被摘），先对清楚再起")
        if not rows:
            print("[plan] 第 1 遍里没有失败的格子，这一遍不用起场", flush=True)
            return 0
    if args.limit:
        rows = rows[: args.limit]
    print(f"[plan] {args.group} 组 {len(rows)} 条线 × {args.rounds} 轮，真机 {args.serial}，"
          f"读数落 {out.relative_to(ROOT)}（台号与线路次序按设备那份目录）", flush=True)
    if args.dry_run:
        # 只看"这一跑会测哪些格"，不开台账、不拉应用、不按一下键——起场前对表用，
        # 也防住"两个进程写同一份台账"那种事故（10-26 17:4x 真踩过一次）。
        names = collections.Counter(r["name"] for r in rows)
        head = "，".join(f"{r['name']} 线{r['line_index'] + 1}" for r in rows[:8])
        print(f"[dry-run] 不起场。会测 {len(rows)} 格，覆盖 {len(names)} 台；前 8 格：{head}",
              flush=True)
        return 0

    done = set()
    if ledger.exists():
        for raw in ledger.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            # 只认同一把尺（v2）写过的格子：v1 的窗口是从"按下"起算的，按键排队那几十秒被算成了
            # 这条线的表现，两种读数控不能混在一份台账里比。
            if r.get("toolv") != TOOLV:
                continue
            if r.get("seen") is False:
                continue        # 按键没落地那一格不算测过，下一跑重测
            done.add((int(r["round"]), r["channel_id"], int(r["line_index"]), r["url"]))

    last_t = 0
    with ledger.open("a", encoding="utf-8") as log:
        if not ensure_player(args.serial, args.pkg, print):   # 提示走 stdout，别混进 JSONL 台账
            return 3
        # 起点：拿设备当前的单调时钟，只认比它新的行（不清缓冲，别的窗口在这台电视上的证据留着）
        last_t = device_ms(args.serial)

        for rnd in range(1, args.rounds + 1):
            todo = [r for r in rows if (rnd, r["channel_id"], r["line_index"], r["url"]) not in done]
            print(f"[round {rnd}] 待测 {len(todo)} 条", flush=True)
            ok = 0
            t0 = time.monotonic()
            prev_channel = None
            for n, item in enumerate(todo, 1):
                row = dict(round=rnd, toolv=TOOLV, **item, ts=time.strftime("%H:%M:%S"))
                # 起播耗时只能拿**设备自己的**单调时钟量：日志里的 t= 是设备 uptime 毫秒，
                # 和这台机器的 time.monotonic() 不是一个起点，两者相减会得到没意义的数。
                cell = new_cell()
                left_early = False
                # 这三个必须**每格**清零：写在 `if not stay` 里面，连续测同一个台的几条线时 stay 为真、
                # 这一整段被跳过，读出来的就是上一格的残留（10-26 17:5x 现读：CCTV-1 线1→7 的
                # `land_wait_s` 是 3.1/24.2/45.2/85.2/105.8/148.6/193.8，一路单调递增＝进程累计时间，
                # 而 `nav_lag_ms` 更坏——69,563ms 会被读成"遥控器排队 69 秒"，那正是父母手上的量）。
                presses = 0
                pressed_at = None
                press_ms = 0

                # 上一格如果就停在这个台上，就别再按一次台号（重按同一个台号应用什么都不做，
                # 白等 20 秒）。只有换台才走"停车→跳回来→等落地"这一整段。
                stay = (item["channel_id"] == prev_channel
                        and current_channel(args.serial)[0] == item["channel_id"])
                if not stay:
                    # 阶段 A：按下去，等到**应用真的走到这个台**（以它自己的日志行为准）。
                    # v1 从"按下"起算一个固定窗口，而这台电视在排队时会两分钟后才跳到这一台，
                    # 那一格记的其实是"上一个台的余波"——咪咕 16 格里 6 格"没首帧"就是这么来的。
                    # 阶段 A0：先停到另一个台上去。
                    # 已经在这个台上再按一次台号＝应用什么都不做（10-26 15:1x 现读：手动停在 CCTV-1 之后
                    # 连测它两条线，两格只有 `watch` 行、一条 `QualityWatch/firstFrame` 都没有，
                    # 读起来像"这条线没画面"，其实是"根本没重新起播"）。先跳去别的台，再跳回来，
                    # 每一格才是一次真的起播——这也才是父母按遥控器的样子。
                    park_id, park_number = park_target(ids, park_cid_number, item["channel_id"])
                    park_landed = None          # None＝这一格没停车；True/False＝停了、到没到车位
                    if park_id and park_id != item["channel_id"] and not args.no_park:
                        park_ms = device_ms(args.serial)
                        press_number(args.serial, park_number)   # 键号必须跟着 park_id 走
                        # 车位没到就别急着按目标台号（#125 那条：盲按会把两枚台号吃成一枚/并成多位数）。
                        # 上限取两倍 --park-wait：到了车位当场跳出去，只有真停不下来才多花这点时间。
                        park_t0 = time.monotonic()
                        park_landed = False
                        while True:
                            cur, _age = current_channel(args.serial)
                            if cur == park_id:
                                park_landed = True
                                break
                            if not park_keep_waiting(cur, park_id, time.monotonic() - park_t0,
                                                     args.park_wait * 2):
                                break
                            time.sleep(1.5)
                        row["park_landed"] = park_landed
                    land_deadline = time.monotonic() + args.wait
                    while time.monotonic() < land_deadline:
                        cur, cur_age = current_channel(args.serial)
                        # 车位没到位时，第一枚目标台号要再等应用把手上的那一枚吃完（在某台上静置 3 秒），
                        # 否则就是"盲按"：这一格 CCTV-2 线1 五遍零观测，正是这么来的。
                        queue_idle = park_landed is not False or (cur is not None and (cur_age or 0) >= 3000)
                        if press_gate(cur, item["channel_id"], cur_age,
                                      pressed_at is None,
                                      None if pressed_at is None else time.monotonic() - pressed_at,
                                      args.repress_after) and queue_idle:
                            press_ms = device_ms(args.serial)
                            last_t = max(last_t, press_ms)   # 只认按下去之后打的行
                            press_number(args.serial, item["number"])
                            presses += 1
                            pressed_at = time.monotonic()
                            row["press_ms"] = press_ms
                            row["repressed"] = presses > 1
                        time.sleep(1.5)
                        last_t = absorb(cell, item, app_log(args.serial), last_t,
                                       want_line=item["line_index"] + 1)
                        if cell["seen"]:
                            break
                    landed_at = time.monotonic() if cell["seen"] else None
                else:
                    landed_at = time.monotonic()
                    cell["seen"] = True
                # 阶段 A2：把线挪到这一格要的那一条。
                # 按台号只会落到"这台此刻记着的那条线"，不会落到第 N 条——10-26 15:1x 现读：
                # 连着要 CCTV-1 的第 1、2 条线，两格读数一字不差、`line=` 都是 1。
                # 用户要的是「从线路 1 开始」逐条测，所以这一格必须真的把线挪到位（走选线面板）。
                want_line = item["line_index"] + 1
                nav_from = None
                nav_presses = 0
                nav_failed = False
                switch_ms = 0
                line_moved = False
                if landed_at:
                    # 按台号只会落到"这台此刻记着的那条线"，不会落到第 N 条——10-26 15:1x 现读：
                    # 连着要 CCTV-1 的第 1、2 条线，两格读数一字不差、`line=` 都是 1。
                    # 用户要的是「从线路 1 开始」逐条测，所以每一格都要真的把线挪到位。
                    nav_from = newest_line(args.serial, item["channel_id"])
                    switch_ms = press_ms
                    total_lines = totals.get(item["channel_id"], 0)
                    if nav_from != want_line and total_lines >= want_line:
                        arrived, ok_ms, navi = nav_via_panel(
                            args.serial, item["channel_id"], want_line,
                            total_lines, timeout=args.line_wait)
                        nav_presses = navi
                        nav_from = arrived
                        if ok_ms:
                            switch_ms = ok_ms
                    elif nav_from != want_line:
                        # 设备目录里这台没有这么条线（多半是别的窗口刚改过设备那份缓存）：
                        # 这一格不许硬按，如实记"挪不到那条线"，留给下一遍。
                        nav_presses = 0
                        nav_from = None
                    nav_failed = nav_from != want_line
                    # 基线取"按 OK 之前"那一刻：firstFrame 往往在选定后 0.1 秒内就到，
                    # 拿到位之后再读时钟当基线会把它当成旧行丢掉（10-26 15:4x 现读：
                    # 面板 15:41:48 开、line=1 的 1920x1080 与 firstFrame 都在 15:42:00.65，
                    # 而基线是 15:42:01 之后取的 ⇒ 那一格被记成"没出画"）。
                    last_t = max(last_t, switch_ms)
                # 阶段 B：落地（且挪到位）之后再收 `--after` 秒；窗口起点不是"按下台号"那一刻。
                if landed_at:
                    # 窗口从"挪到位"之后起算：挪线本身要十几秒，用落地那一刻起算会把整段
                    # 测量窗吃掉（读出来就是"一格都没数"）。
                    win_deadline = time.monotonic() + args.after
                    while time.monotonic() < win_deadline:
                        time.sleep(1.5)
                        last_t = absorb(cell, item, app_log(args.serial),
                                       last_t, want_line=want_line)
                        cur, cur_age = current_channel(args.serial)
                        if cur and cur != item["channel_id"] and (cur_age or 0) < 20_000:
                            left_early = True
                        if not nav_failed and newest_line(args.serial, item["channel_id"]) != want_line:
                            # 窗口里应用自己把线换走了＝这条线被判挂走容错路（那正是"这条线不行"的证据）
                            line_moved = True
                if cell["first"] or cell["playing"]:
                    ok += 1
                # seen：这一趟里应用有没有为这个台打过任何一行。一行都没有＝按键没落地/应用不在
                # 前台 ⇒ 这一格不许读成「这条线坏了」，也不许当成已完成（上面 done 那侧同口径）。
                row.update(first_frame=bool(cell["first"]), played=bool(cell["playing"]),
                           res=cell["res"], res_zero=cell["res_zero"],
                           stalls=cell["stalls"], stall_ms=cell["stall_ms"], playing=cell["playing"],
                           live=cell["live"], duration_ms=cell["duration_ms"],
                           cum_stalls=cell["cum"][0], cum_stall_ms=cell["cum"][1], seen=cell["seen"],
                           first_line=cell["first_line"],
                           want_line=want_line, nav_from=nav_from, nav_presses=nav_presses,
                           nav_failed=nav_failed, line_moved=line_moved,
                           presses=presses, left_early=left_early,
                           land_wait_s=round(landed_at - pressed_at, 1)
                           if (landed_at and pressed_at) else None)
                # 两个数分开报，别并成一个"起播耗时"：
                #   nav_lag_ms＝按下"确定"到应用真的开始放这条线＝遥控器排队的时间（父母手上的那一段）
                #   zap_ms    ＝这条线开始被放到出第一帧＝流本身起播的时间
                # 10-26 15:5x 现读：CCTV-1 线 4 两数分别是 148,058ms 与 5,330ms——并成一个就会报成 153 秒。
                row["nav_lag_ms"], row["zap_ms"], row["anchor"] = timing(cell, switch_ms)
                prev_channel = item["channel_id"]
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
                log.flush()
                if n % 5 == 0 or n == len(todo):
                    el = time.monotonic() - t0
                    print(f"[r{rnd} {n}/{len(todo)}] {item['name']} 出首帧 {ok} 用时 {el:.0f}s"
                          f"（外推 {(el / n) * len(todo):.0f}s）", flush=True)
            print(f"[round {rnd}] 出首帧 {ok}/{len(todo)}", flush=True)
            time.sleep(2)

    summarize(ledger, out / "summary.json", args.rounds)
    return 0


def credited_first(rs):
    """这一格里「首帧能算作**这条线自己**打出来」的那几行。

    剔掉的是**借来的**：`first_line` 记了、且与本格线号对不上——落地等待那一处曾经不带 `want_line`，
    同台别的线的 `firstFrame` 会被记进这一格（#140）。那枚借来的首帧会让一条从没出过画的线被判成
    「真机播过」，而这一句同时是复跑排除、清晰度置顶与"这台播过就别动"的依据。写侧已在 TOOLV 7 修掉，
    这里补判据侧：旧台账（v6 那 336 行里的 4 枚）照样不能当证据。

    ⚠️ 作用域只到这里：`first_line` **没记**的旧行（v5/v6 之前那几代）仍按原口径算首帧——
    把它们一并打掉会改写历史判据（实测会让夹具 H3/V1/W1/W2/W7/W8 六格翻红），那不是本缺陷的范围。
    这类行的缺口由 `score_rows` 的 `hits_no_line_field` 单列报数，不许悄悄折成"已验证"。
    """
    out = []
    for r in rs:
        if not r.get("first_frame"):
            continue
        fl, li = r.get("first_line"), r.get("line_index")
        if fl is not None and li is not None and fl != li + 1:
            continue                      # 借来的
        out.append(r)
    return out


def verdict_for(rs, rounds):
    """同一格（同一台同一条线的若干轮）的判据。**只有这一处写**：
    记分板、复跑清单、卡顿读数板都调它——两处各写一遍就会打架（10-24 那两份手写名单的教训）。

    10-27 收紧（#140）：借来的首帧单独成态，不再折进「真机播过」，也不折成「全轮没首帧」
    （后者是把"量到了但当时不是这条线"读成"什么都没出"）。"""
    hits = credited_first(rs)
    played = [r for r in rs if r.get("played")]
    if hits:
        return "真机播过"
    frames = [r for r in rs if r.get("first_frame")]
    if frames:
        return "出过画但线号对不上（不作数）"
    if len(rs) < rounds:
        return "轮次不全"
    if not any(r.get("seen") for r in rs):
        return "按键没落地（不作数）"
    if any(r.get("nav_failed") for r in rs) and not any(r.get("first_frame") for r in rs):
        return "挪不到那条线（不作数）"
    if any(r.get("line_moved") for r in rs):
        # 窗口里应用自己把这条线换走了＝容错路判定这条线挂了
        return "中途被换线（这条线挂）"
    if played:
        # 应用自己说在播、可这台电视到窗口结束都没报出一帧 —— 屏幕上就是「正在连接/缓冲中」。
        return "落到台但没出画"
    return "全轮没首帧"


def score_rows(rows, rounds):
    """按（台 id, 线号, 地址）把若干轮读数并成一格，每格带 verdict 与读数——记分板与复跑清单共用。"""
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["channel_id"], r["line_index"], r["url"])].append(r)
    items = []
    for (cid, li, url), rs in sorted(by.items()):
        hits = credited_first(rs)
        verdict = verdict_for(rs, rounds)
        items.append({
            "channel_id": cid, "channel": rs[0]["name"], "number": rs[0]["number"],
            "line_index": li, "url": url, "rounds": len(rs), "hits": len(hits),
            "hits_all_frames": sum(1 for r in rs if r.get("first_frame")),
            "hits_no_line_field": sum(1 for r in rs if r.get("first_frame") and r.get("first_line") is None),
            "hits_borrowed": sum(1 for r in rs if r.get("first_frame")
                                 and r.get("first_line") is not None
                                 and li is not None and r["first_line"] != li + 1),
            "res": sorted({r["res"] for r in rs if r.get("res")}),
            "res_zero": sum(int(r.get("res_zero") or 0) for r in rs),
            # 锚点必须跟着读数一起落进记分板：`起播` 那一列的含义完全取决于它是从 `select` 还是
            # 从 `firstlog`（本线第一行）起算的，只报数不报锚点就会把 2 毫秒读成"这条线真快"。
            "anchor": sorted({r["anchor"] for r in rs if r.get("anchor")}),
            "nav_failed": any(r.get("nav_failed") for r in rs),
            "nav_presses": sorted({int(r["nav_presses"]) for r in rs if r.get("nav_presses")}),
            "zap_ms": sorted(r["zap_ms"] for r in rs if r.get("zap_ms")),
            "nav_lag_ms": sorted(r["nav_lag_ms"] for r in rs if r.get("nav_lag_ms")),
            "land_wait_s": sorted(r.get("land_wait_s") for r in rs if r.get("land_wait_s")),
            "presses": sum(int(r.get("presses") or 0) for r in rs),
            "left_early": any(r.get("left_early") for r in rs),
            "stalls": sum(int(r.get("stalls") or 0) for r in rs),
            "stall_ms": sum(int(r.get("stall_ms") or 0) for r in rs),
            "verdict": verdict,
        })
    return items


def audit_plan(plan_rows, ledger_rows):
    """「全线路回测」这句话要能被现查：把**计划要测的格**与**台账里真出现的格**对表。
    身份按（台 id＋地址）算——设备那份的**线序**会因"这台真播过就置顶"而与仓库那份不同，
    按线号比会把同一格判成"缺一格、多一格"，所以线序差异单独数一个数（`order_diff`），不算没跑全。
    缺＝这一遍没测到（不许写成"跑完了"）；重复＝同一格被写了两遍（多半是两个进程抢同一份台账，
    10-26 17:4x 真踩过）；多＝台账里出现了计划之外的格（目录中途换底）。"""
    plan = {(r["channel_id"], r["url"]) for r in plan_rows}
    got_keys = [(r["channel_id"], r["url"]) for r in ledger_rows]
    seen = set(got_keys)
    # 「同一格两行」有两种完全不同的来历，必须分开数：
    # 一种是这一格第一次没落地/没挪到位（`seen=False`，按规矩不作数、下一跑重测）⇒ 正常的重测行；
    # 一种是**两个都在数的行** ⇒ 多半是两个进程抢同一份台账（10-26 17:4x 真踩过），那才是事故。
    counted_keys = [(r["channel_id"], r["url"]) for r in ledger_rows if r.get("seen") is not False]
    by_url = {(r["channel_id"], r["url"]): r["line_index"] for r in plan_rows}
    off = sum(1 for r in ledger_rows
              if (r["channel_id"], r["url"]) in by_url
              and by_url[(r["channel_id"], r["url"])] != r["line_index"])
    counted_c = collections.Counter(counted_keys)
    return {"plan": len(plan), "rows": len(got_keys), "covered": len(seen & plan),
            "missing": sorted(plan - seen),
            "dup": sorted(k for k, c in counted_c.items() if c > 1),
            "retest": sorted(k for k, c in collections.Counter(got_keys).items()
                             if c > 1 and counted_c.get(k, 0) <= 1),
            "extra": sorted(seen - plan), "order_diff": off}


def rows_from_ledger(ledger, toolv=None):
    """读台账并只留同一把尺写过的行。默认只数**当前这一把尺**；`toolv=` 是给"尺子已经换版、
    旧台账还要能复盘"留的口（10-26 现读：尺升到 6 之后，正在跑的 v5 那份台账会被整批滤成
    0 条＝8 小时读数没法复盘）。v1 的 first_frame 里混过 playing（"应用说在播"被当成"电视出了画"），
    两种读数不能并成一个结论。"""
    want_v = TOOLV if toolv is None else toolv
    all_rows = [json.loads(l) for l in
                Path(ledger).read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in all_rows if r.get("toolv") == want_v]


def failure_keys(ledger, toolv=None, rounds=1):
    """第 1 遍里"没成"的那些格子＝「真机播过」以外的全部，**含两种「不作数」**——那两格压根没测到
    东西，不复跑就永远没有读数。复跑清单要从台账直接算出来，不靠人手抄。
    返回 `[(channel_id, line_index, url)]`，可直接喂给 `--retest-failures`。"""
    items = score_rows(rows_from_ledger(ledger, toolv), rounds)
    return [(i["channel_id"], i["line_index"], i["url"])
            for i in items if i["verdict"] != "真机播过"]


def summarize(ledger, path, rounds, toolv=None):
    items = score_rows(rows_from_ledger(ledger, toolv), rounds)
    c = collections.Counter(i["verdict"] for i in items)
    path.write_text(json.dumps({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "rounds": rounds, "counts": dict(c), "items": items},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("[tv] " + " · ".join(f"{k} {v}" for k, v in c.items()), flush=True)


if __name__ == "__main__":
    sys.exit(main())
