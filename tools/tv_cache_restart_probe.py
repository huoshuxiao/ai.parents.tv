#!/usr/bin/env python3
# 「线路清晰度算一次就好」这句里还剩一格没量过：**应用被杀掉重开之后**，那份缓存还在不在。
#
# 为什么现成日志答不了这一格：`tools/.cache/live_log.txt` 里 104 枚 `LineProbe: panel` 行
# 全来自同一个 pid（17796，16:45→18:59 两小时十四分钟）⇒ 这段时间应用进程一号都没换过，
# 所以"跨重启"在真机上是**未证**，不是通过。要证它只能真把应用杀一次再开一次。
#
# 为什么必须排在电视空闲之后跑：这一支会发按键、开面板，可能拉几条分片；
# 自动回测正在测某一格的时候做这些＝把那一格的读数弄脏（本机就是父母家那张网）。
#
# 硬边界：不写目录、不删线、不打包、不装机、不 commit；只按数字键与菜单键，不碰「重新检测」那颗
# （面板里焦点读不到，盲按会把线换成计划外那一条——那正是 task #125 拦着的事）。
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tv_retest_lines as tvrl      # noqa: E402  按键、落地与开面板的判定共用那把尺的助手

PANEL = re.compile(r"^(..-.. ..:..:..)\.\d+\s+(\d+)\s+\d+ I LineProbe: panel channel=(\S+) "
                   r"共(\d+)条 要探(\d+)条 缓存命中(\d+)条 force=(\w+)")


def panel_rows(live_path):
    out = []
    for line in open(live_path, errors="replace"):
        m = PANEL.match(line.strip())
        if m:
            out.append({"ts": m.group(1), "pid": m.group(2), "cid": m.group(3),
                        "total": int(m.group(4)), "probe": int(m.group(5)),
                        "hit": int(m.group(6)), "force": m.group(7)})
    return out


def decide(before, after, cid):
    """对表「重启前这一台最后一次开面板」与「重启后第一次开面板」。
    放行只认一件事：重启后第一枚 `要探0条`。`要探>0` ⇒ 这一格**没做到**，如实报数
    （不许把"缓存命中若干条"当成成功——那是重启后仍然重算了一遍）。拿不到行 ⇒ 未证。"""
    b = [r for r in before if r["cid"] == cid]
    a = [r for r in after if r["cid"] == cid]
    if not a:
        return {"verdict": "未证", "why": "重启后没读到这一台的 panel 行（面板没开成或日志断了）",
                "before": b[-1] if b else None, "after": None}
    first = a[0]
    if first["probe"] == 0:
        return {"verdict": "跨重启保住",
                "why": f"重启后第一枚 panel 行 `要探0条 缓存命中{first['hit']}条`",
                "before": b[-1] if b else None, "after": first}
    return {"verdict": "跨重启没保住",
            "why": f"重启后第一枚 panel 行 `要探{first['probe']}条`＝又重算了一遍 ⇒ "
                   f"目标那句「算一次就好」在杀进程之后不成立，要改的是缓存的存放位置（不是探测次数）",
            "before": b[-1] if b else None, "after": first}


def open_panel_once(serial, cid, mark, log):
    """按菜单键开一次选线面板，并认应用自己打的那一行；没开成就照尺子的办法补一次（返回键之后的头一枚
    菜单键会被吃掉——10-26 17:2x 现读出来的坑）。返回 (开没开成, 说明)。"""
    tvrl.keyevents(serial, [82])
    if tvrl.panel_opened(serial, cid, mark, wait=6.0):
        return True, "第一次菜单键就开成"
    log("  [重试] 菜单键没落地（这把尺知道的那颗被吃掉的键）：按一次返回再按菜单键\n")
    tvrl.keyevents(serial, [4])
    time.sleep(1.0)
    mark2 = tvrl.device_ms(serial)
    tvrl.keyevents(serial, [82])
    if tvrl.panel_opened(serial, cid, mark2, wait=6.0):
        return True, "补一次之后开成"
    return False, "两次都没开出面板 ⇒ 这一格不下结论"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adb", default=os.environ.get("ADB", "/usr/bin/adb"))
    ap.add_argument("--serial", default="192.168.1.4:5555")
    ap.add_argument("--pkg", default="com.sun.tv.parents")
    ap.add_argument("--live", default="tools/.cache/live_log.txt")
    ap.add_argument("--cid", default=None, help="默认取日志里最近一次 `要探>0` 的那一台")
    ap.add_argument("--boot-wait", type=float, default=14.0)
    ap.add_argument("--dry-run", action="store_true", help="只念它会做什么，一条 adb 都不发")
    args = ap.parse_args()

    def log(msg):
        print(msg, end="" if msg.endswith("\n") else "\n", flush=True)

    if not Path(args.live).exists():
        log(f"[拒绝] 读数流那份文件不在：{args.live}")
        return 2
    rows = panel_rows(args.live)
    if not rows:
        log(f"[拒绝] 这份日志里一行 panel 都没有：{args.live}（先确认 `--live` 那条 logcat 在写）")
        return 2
    pids = sorted({r["pid"] for r in rows})
    cid = args.cid
    if not cid:
        probed = [r for r in rows if r["probe"] > 0]
        if not probed:
            log("[拒绝] 日志里没有『要探>0』那一次＝找不到「今天真探过某一台」的起点，不硬挑一台")
            return 2
        cid = probed[-1]["cid"]
    mine = [r for r in rows if r["cid"] == cid]
    last = mine[-1]
    log(f"[挑台] cid={cid}｜日志里该台 {len(mine)} 枚 panel 行｜最近一枚 {last['ts']} "
        f"共{last['total']}条 要探{last['probe']}条 缓存命中{last['hit']}条")
    log(f"[现场] 这份日志的 pid 集合＝{pids}（{'一号都没换过＝跨重启这一格现未证' if len(pids) == 1 else '已经换过 ' + str(len(pids)) + ' 号，但仍未按跨重启口径对表'}）")

    if args.dry_run:
        log("[dry-run] 正式档只做这四步，且只在电视空闲时排：")
        log(f"  1) {args.adb} -s {args.serial} shell am force-stop {args.pkg}    # 只杀本应用")
        log(f"  2) monkey -p {args.pkg} -c android.intent.category.LEANBACK_LAUNCHER 1，等 {args.boot_wait:.0f} 秒后 ensure_player")
        log(f"  3) 从设备目录取 {cid} 的台号 → press_number（数字键只在播放器生效）→ 菜单键开面板（没落地就按『返回＋菜单』补一次）")
        log("  4) 读重启后新 pid 的第一枚 panel 行，只认 `要探` 是不是 0；不按「重新检测」那颗")
        log("[dry-run] 没碰设备：一条 adb 都没发")
        return 0

    def run(*a):
        return subprocess.run([args.adb, "-s", args.serial, *a], capture_output=True, text=True, timeout=60)

    log("[动作] force-stop 本应用（不动电视上别的应用）")
    run("shell", "am", "force-stop", args.pkg)
    time.sleep(2.0)
    log("[动作] 冷启动")
    run("shell", "monkey", "-p", args.pkg, "-c", "android.intent.category.LEANBACK_LAUNCHER", "1")
    time.sleep(args.boot_wait)
    if not tvrl.ensure_player(args.serial, args.pkg, lambda m: log(str(m).rstrip())):
        log("[未证] 冷启动后没确认落到播放器 ⇒ 不硬发按键（键送给别的应用就是白测）")
        return 3
    ids = tvrl.numbered_ids(tvrl.device_catalog(args.serial, args.pkg))
    if cid not in ids:
        log(f"[未证] 这一台不在设备那份的台号表里（共 {len(ids)} 个 id）⇒ 按不了它")
        return 3
    num = ids.index(cid) + 1
    log(f"[动作] 按台号 {num} 跳到 cid={cid}（设备台号表共 {len(ids)} 台），等首帧")
    tvrl.press_number(args.serial, num)
    time.sleep(9.0)
    mark = tvrl.device_ms(args.serial)
    opened, why = open_panel_once(args.serial, cid, mark, log)
    log(f"[面板] {why}")
    time.sleep(3.0)
    after = [r for r in panel_rows(args.live) if r["pid"] not in pids and r["force"] == "false"]
    d = decide(rows, after, cid)
    b, a = d["before"], d["after"]
    log(f"[对表] 重启前最后一枚：{('共%d条 要探%d条 缓存命中%d条' % (b['total'], b['probe'], b['hit'])) if b else '无'}")
    log(f"       重启后第一枚：{('pid %s %s 共%d条 要探%d条 缓存命中%d条' % (a['pid'], a['ts'], a['total'], a['probe'], a['hit'])) if a else '无'}")
    log(f"[结论] {d['verdict']}｜{d['why']}")
    log("[作用域] 只证到：这台电视＋这枚包＋这一台频道，冷启动后**第一次开面板**那一刻有没有重算。"
        "面板那颗「重新检测」(force=true) 不在这一格作用域里。")
    return 0 if d["verdict"] == "跨重启保住" else 4


if __name__ == "__main__":
    raise SystemExit(main())
