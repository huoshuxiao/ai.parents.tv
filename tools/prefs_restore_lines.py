#!/usr/bin/env python3
"""把设备侧「记住的线路」恢复成**这一遍真机播过**的那一条（task #126 点档＝甲）。

为什么默认是空跑（不写）：这一支要 `force-stop` 应用再改它的 `shared_prefs/parents_tv.xml`。
应用活着的时候写＝它内存里那一份会把你刚写的盖掉（或读不到），而 `force-stop` 会让正在跑的
自动回测整批读成「按键没落地」。所以：**链子跑完之后**才能加 `--write`，其余时候只许看计划。

键的形制（现读源码与设备文件，不是猜的）：文件 `parents_tv.xml`，键 `line_<频道 id>`，
**值是那条线的 URL 本身**（`TvPrefs.kt:134 setLine(channelId, url)`）。URL 里有 `&` 时，
SharedPreferences 写出来是 `&amp;` ⇒ 读要解回来、写要转出去，回读必须逐字对表。
"""
import argparse
import collections
import hashlib
import html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tv_retest_lines as tvrl          # noqa: E402  六态判据与按格聚合共用那把尺

PREFS_FILE = "shared_prefs/parents_tv.xml"
KEY_RE = re.compile(r'<string name="line_([^"]+)">((?:[^<]|&(?!amp;)*)*?)</string>')


def parse_prefs(xml):
    """把 `line_*` 那批键读成 {频道 id: URL}（`&amp;` 解回 `&`）。"""
    out = {}
    for cid, raw in KEY_RE.findall(xml):
        out[cid] = html.unescape(raw)
    return out


def escape_value(url):
    return url.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def restore_plan(proven, current, blocked, catalog_urls):
    """proven: {cid: [(line_index, url, zap_ms), ...]} 这一遍判「真机播过」的那些线
    current: {cid: url} 设备此刻记住的线
    blocked: 集合，黑名单里的 URL
    catalog_urls: {cid: set(url)} 设备那份目录里这一台真有哪些地址

    四类分开且分项之和要等于台数：`already`（已经指在对的那条）／`change`（要改指）／
    `no_proven`（这一遍没有一条真播过 ⇒ **不动它**，也不许把键删掉——删了就是把"记住"清空，
    那是比指错更硬的动作）／`refuse`（想改但目标地址不在这台设备的目录里，或它自己在黑名单里
    ⇒ 拒绝，这是"改了会立刻坏"那一类）。
    挑哪一条：线号最小的那条（父母打开就落在它上面最快）；线号相同按起播最快。"""
    plan = {"already": [], "change": [], "no_proven": [], "refuse": []}
    for cid in sorted(proven):
        picks = sorted(proven[cid], key=lambda x: (x[0], x[2] if x[2] is not None else 1 << 40))
        want_line, want_url, want_zap = picks[0]
        now = current.get(cid)
        if now == want_url:
            plan["already"].append({"cid": cid, "line": want_line, "url": want_url})
            continue
        if not want_url or want_url in blocked:
            plan["refuse"].append({"cid": cid, "line": want_line, "url": want_url,
                                   "why": "目标地址自己在黑名单里"})
            continue
        if want_url not in catalog_urls.get(cid, set()):
            plan["refuse"].append({"cid": cid, "line": want_line, "url": want_url,
                                   "why": "目标地址不在设备那份目录的这一台里（写了就是条悬空起点）"})
            continue
        plan["change"].append({"cid": cid, "from": now, "line": want_line, "url": want_url,
                               "zap_ms": want_zap})
    for cid in current:
        if cid not in proven:
            plan["no_proven"].append({"cid": cid, "kept": current[cid]})
    plan["channels"] = len(plan["already"]) + len(plan["change"]) + len(plan["no_proven"]) + len(plan["refuse"])
    return plan


def proven_from_ledgers(ledgers, toolv):
    """按台账顺序合并：越靠后的那份（＝更晚跑的复跑定案）优先覆盖同一格。"""
    by_cid = collections.defaultdict(dict)      # cid -> url -> (line, zap)
    order = []
    for path in ledgers:
        p = Path(path)
        if not p.exists():
            print(f"[跳过] 没有这份台账：{path}")
            continue
        rows = tvrl.rows_from_ledger(str(p), toolv)
        if not rows:
            print(f"[跳过] 这份台账里没有 toolv={toolv} 的行：{path}")
            continue
        cells = tvrl.score_rows(rows, rounds=1)
        for c in cells:
            if c["verdict"] != "真机播过":
                continue
            cid, url = c["channel_id"], c["url"]
            zap = min(c["zap_ms"]) if c["zap_ms"] else None
            by_cid[cid][url] = (c["line_index"] + 1, zap)
            if cid not in order:
                order.append(cid)
    return {cid: [(li, u, zap) for u, (li, zap) in by_cid[cid].items()] for cid in by_cid}


def adb(serial, *args, stdin=None, timeout=60):
    return subprocess.run(["/usr/bin/adb", "-s", serial, *args], capture_output=True,
                          text=True, input=stdin, timeout=timeout)


def read_device_prefs(serial, pkg):
    r = adb(serial, "shell", "run-as", pkg, "cat", PREFS_FILE)
    if r.returncode != 0 or "<map" not in r.stdout:
        return None, (r.stderr or "").strip() or "run-as 没读到那份 xml"
    return r.stdout, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", action="append", default=[],
                    help="可多次；越靠后的越优先（复跑定案那份要放在最后）")
    ap.add_argument("--toolv", type=int, default=6)
    ap.add_argument("--serial", default="192.168.1.4:5555")
    ap.add_argument("--pkg", default="com.parentstv.live")
    ap.add_argument("--blocked", default="app/src/main/assets/channels.json")
    ap.add_argument("--out", default=None, help="把计划落一份 jsonl（只在 --write 时写）")
    ap.add_argument("--write", action="store_true", help="**真改设备**：链子跑完之前不许加")
    args = ap.parse_args()

    if not args.ledger:
        print("[拒绝] 至少给一份 --ledger；没有台账就没有「这一遍真播过」的依据")
        return 2
    # 10-26 现读设备那份目录的真实形制（不是我猜的）：顶层
    # `generatedAt/sourceNote/blockedLines/lineQuality/categories/channels`，
    # 每台 `streams` 是**一串 URL 字符串**（不是对象）——按 `{s["url"]}` 去读会把整台解析成异常。
    cat_r = adb(args.serial, "shell", "run-as", args.pkg, "cat", "files/channels_live.json")
    urls_by_cid, blocked, blocked_from = {}, set(), "无"
    if cat_r.returncode == 0 and cat_r.stdout.lstrip().startswith("{"):
        d = json.loads(cat_r.stdout)
        for ch in d.get("channels", []):
            urls_by_cid[ch["id"]] = {s for s in ch.get("streams", []) if isinstance(s, str)}
        blocked = set(d.get("blockedLines", []))
        blocked_from = "设备那份 files/channels_live.json"
    else:
        print("[警告] 没读到设备那份目录（`files/channels_live.json`）⇒ 只能出计划，`--write` 会被拒绝")
        bp = Path(args.blocked)
        if bp.exists():
            blocked = set(json.loads(bp.read_text()).get("blockedLines", []))
            blocked_from = f"仓库那份 {args.blocked}（设备没读到才用它）"
    proven = proven_from_ledgers(args.ledger, args.toolv)
    xml, err = read_device_prefs(args.serial, args.pkg)
    if xml is None:
        print(f"[拒绝] 读不到设备 prefs：{err}")
        return 2
    current = parse_prefs(xml)
    plan = restore_plan(proven, current, blocked, urls_by_cid)
    n_ok = sum(1 for x in plan["change"] if x["url"] in urls_by_cid.get(x["cid"], set()) or not urls_by_cid)
    print(f"[对表] 台账里判「真机播过」的台 {len(proven)}｜设备现在记着 {len(current)} 枚 line_ 键｜设备目录里 {len(urls_by_cid)} 台有地址表｜黑名单 {len(blocked)} 条（出处：{blocked_from}）")
    print(f"[计划] 已经对 {len(plan['already'])}｜要改 {len(plan['change'])}｜"
          f"这一遍没有真播过的线（不动）{len(plan['no_proven'])}｜拒绝 {len(plan['refuse'])}"
          f"｜分项之和 {len(plan['already']) + len(plan['change']) + len(plan['no_proven']) + len(plan['refuse'])} "
          f"== 台数 {plan['channels']} {'✓' if plan['channels'] == len(plan['already']) + len(plan['change']) + len(plan['no_proven']) + len(plan['refuse']) else '✗ 对不上，别看下面'}")
    for x in plan["change"][:40]:
        print(f"  改指：{x['cid']} 线{x['line']} 起播 {x['zap_ms']}ms\n"
              f"        从 {str(x['from'])[:72]}\n        到 {x['url'][:72]}")
    for x in plan["refuse"][:20]:
        print(f"  拒绝：{x['cid']} → {x['url'][:60]}（{x['why']}）")

    if not args.write:
        print("[空跑] 没加 --write ⇒ 一条写盘指令都没发；要真恢复请等链子跑完后加 --write")
        return 0
    if not urls_by_cid:
        print("[拒绝] 设备目录没读到，无法证明目标地址在这一台里 ⇒ 不写")
        return 3
    if not plan["change"]:
        print("[无事可做] 计划里没有一条要改 ⇒ 不 force-stop、不写盘")
        return 0

    stamp = tvrl.device_ms(args.serial)
    bak = Path(f"tools/.cache/prefs_before_restore_{stamp}.xml")
    bak.write_text(xml)
    sha_before = hashlib.sha256(xml.encode()).hexdigest()[:16]
    print(f"[备份] {bak.name} sha256 前 16 位 {sha_before}")
    r = adb(args.serial, "shell", "am", "force-stop", args.pkg)
    print(f"[force-stop] rc={r.returncode}（应用活着写 prefs 会被它自己盖掉）")
    # force-stop 只是"发出杀死指令"：`apply()` 是后台线程写的，进程在断气前还会把内存里那一份
    # 刷回 parents_tv.xml。23:34 那一次就是写完立刻回读，读到的是应用临死前盖回来的旧值
    # ⇒ 40 枚全对不上，而"没该动却动了的键"是 0（因为它盖回来的本来就是原样）。
    # 所以：先确认 pid 没了，再等一个落盘余量，然后才动笔。
    gone_at = None
    for _ in range(20):
        p = adb(args.serial, "shell", "pidof", args.pkg)
        if not (p.stdout or "").strip():
            gone_at = tvrl.device_ms(args.serial)
            break
        time.sleep(0.5)
    print(f"[等进程死] {'pid 已空（设备钟 ' + str(gone_at) + '）' if gone_at else '20 次还没空——照样等 2 秒再写，但这一格要在报告里念成未证'}")
    time.sleep(2.0)
    new_xml = xml
    for x in plan["change"]:
        pat = f'<string name="line_{x["cid"]}">'
        i = new_xml.find(pat)
        if i < 0:
            print(f"  [跳过] {x['cid']} 现在根本没有这一枚键（新建键不是这一档要干的事）")
            continue
        j = new_xml.find("</string>", i)
        new_xml = new_xml[:i] + pat + escape_value(x["url"]) + new_xml[j:]
    # 00:35 现读到的死因：`run-as pkg sh -c "cat > 相对路径"` 里那一层 sh 的工作目录不是应用数据目录
    # （读的时候 `run-as pkg cat 相对路径` 是 run-as 自己解析的，所以能读到；换成了 sh 就落在 `/` 上），
    # 于是 `/system/bin/sh: can't create shared_prefs/parents_tv.xml: No such file or directory`、rc=1，
    # 而 23:34 那一次把 rc 丢了没念 ⇒ 只看到「真读到 0 枚」。写绝对路径，并先写临时名再 mv，避免半份文件。
    abs_prefs = f"/data/data/{args.pkg}/{PREFS_FILE}"
    w = adb(args.serial, "shell", "run-as", args.pkg, "sh", "-c",
            f"cat > {abs_prefs}.tmp && mv {abs_prefs}.tmp {abs_prefs}")
    print(f"[写盘] rc={w.returncode} {w.stderr.strip()[:120]}")
    if w.returncode != 0:
        print("[拒绝] 写盘这一步自己报了错 ⇒ 不把它当成「写完了只是没对上」，停在这里（设备那份还是备份那一份）")
        return 4
    time.sleep(1.0)
    after, err2 = read_device_prefs(args.serial, args.pkg)
    if after is None:
        print(f"[拒绝] 写完回读失败：{err2} ⇒ 这一批按未落地处理")
        return 4
    # 字节层先对一次表：rc=0 只说明 `cat` 没报错，不说明设备里那一份就是我写进去的。
    # 换行会被 adb 转成 CRLF，所以这里只报数不判红；判红仍按下面那把键层尺。
    print(f"[字节对表] 要写的 sha256 前 16 {hashlib.sha256(new_xml.encode()).hexdigest()[:16]}"
          f"｜回读 sha256 前 16 {hashlib.sha256(after.encode()).hexdigest()[:16]}"
          f"｜字节数 {len(new_xml)} vs {len(after)}"
          f"{'（相同＝逐字节落地）' if new_xml == after else ''}")
    got = parse_prefs(after)
    bad, ok = [], 0
    for x in plan["change"]:
        if got.get(x["cid"]) == x["url"]:
            ok += 1
        else:
            bad.append((x["cid"], got.get(x["cid"])))
    same_others = [k for k in current if current[k] != got.get(k) and k not in {x["cid"] for x in plan["change"]}]
    print(f"[回读对表] 要改 {len(plan['change'])} 枚 ⇒ 真读到 {ok} 枚｜没该动却动了的键 {len(same_others)} 枚"
          f"{'' if not same_others else '（' + ', '.join(same_others[:6]) + '）'}")
    if bad:
        for cid, v in bad[:10]:
            print(f"  [红] {cid} 回读是 {str(v)[:70]}")
    if args.out:
        Path(args.out).write_text("".join(json.dumps(e, ensure_ascii=False) + "\n"
                                          for e in plan["change"]))
    print(f"[终值] 备份在 {bak.name}；应用没重新起（下一次你打开电视才生效）")
    return 1 if (bad or same_others) else 0


if __name__ == "__main__":
    raise SystemExit(main())
