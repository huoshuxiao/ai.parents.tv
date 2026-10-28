#!/usr/bin/env python3
"""把真机回测台账读成一张「哪条线在电视上卡、卡多久、起播多慢」的读数板。

为什么单独做这一张（目标里那句「优化稳定性减少卡顿 央视·卫视，咪咕体育 全线路 真机回测」）：
`tools/tv_retest_lines.py` 的 `summarize()` 只回答"这条线有没有画面"（六态判据），
而卡顿那一列（`StallWatch` 数出来的 `stalls`/`stall_ms`）在六态里不参与判据——
判据不看它≠它没数据。这一张就是把那两列单独摆出来，供"要不要动参数"由用户点档用。

跑法（只读，不发任何按键）：
    python3 tools/retest_stall_board.py --ledger tools/.cache/tv_retest_cctv_v6/lines.jsonl --toolv 6
"""
from __future__ import annotations

import argparse
import collections
import statistics
from pathlib import Path
from urllib.parse import urlparse

import tv_retest_lines as tvrl

STATE_ORDER = ("真机播过", "出过画但线号对不上（不作数）",
               "落到台但没出画", "中途被换线（这条线挂）", "挪不到那条线（不作数）",
               "按键没落地（不作数）", "轮次不全", "全轮没首帧")


def first_line_board(cells):
    """按台摆「父母打开这一台时那第一条线怎么样」——目标里那句「从线路 1 开始回测」与
    「缓冲中／正在连接」问的都是这一条：线号 1 才是他们每天真按到的那一格，
    第 2、3 条能不能看对他们不重要。
    四类必须分开数（分项之和要等于台数）：首条真播过／首条不合格／首条这一遍还没测／这台压根没行。
    「还没测」不许混进"不合格"——那是把进度读成结论。"""
    by_ch = collections.defaultdict(dict)
    for x in cells:
        by_ch[x["cid"]][x["line"]] = x
    ok, bad, unmeasured = [], [], []
    for cid, lines in sorted(by_ch.items()):
        name = lines[1]["name"] if 1 in lines else lines[min(lines)]["name"]
        first = lines.get(1)
        if first is None:
            unmeasured.append((name, "这一遍还没测到线 1"))
        elif first["verdict"] == "真机播过":
            ok.append(name)
        else:
            bad.append((name, first["verdict"], first["url"]))
    return {"channels": len(by_ch), "ok": ok, "bad": bad, "unmeasured": unmeasured}


def playing_frame_board(rows):
    """数「应用自己在日志里说在播、可这一格从头到尾没出过一帧」——#123 那一族，也就是父母看到的
    「永远转圈的正在连接」。原来六态口径把这一族混进「落到台但没出画」，那两个数是不同的病：
    前者是应用以为好了（所以 10 秒看门狗只数卡顿、不数"没画面"，就永远不换线），后者是压根没连上。
    四档按**行**分，互斥且之和要等于行数；`playing` 为 None（这一窗没读到 StallWatch 那一行）单独归"没读数"，
    不许折成 False 判红——那是把"没量到"读成"没在播"。
    ⚠️口径警告（写进正文，不许省）：这一遍每格只开约 20 秒，"挂着几分钟不动"那种形态本来就落在窗外，
    所以这里数到 0 **不等于**症状消失，只能说"这一遍的窗口里没抓到"。"""
    buckets = {"played_and_frame": [], "frame_not_playing": [], "no_frame_not_playing": [],
               "no_reading": [], "claimed_playing_no_frame": []}
    for r in rows:
        p, f = r.get("playing"), bool(r.get("first_frame"))
        if p is None:
            buckets["no_reading"].append(r)
        elif p and f:
            buckets["played_and_frame"].append(r)
        elif p and not f:
            buckets["claimed_playing_no_frame"].append(r)
        elif f:
            buckets["frame_not_playing"].append(r)
        else:
            buckets["no_frame_not_playing"].append(r)
    bad = buckets["claimed_playing_no_frame"]
    # 格级要分两种，混成一个数就会打脸：整格每一轮都"说在播却没出画"＝这条线从来没给过画面；
    # 只有一轮如此、别的轮出过画的格子，判据那边会算「真机播过」，两个数并列念时必须对得上。
    by_cell = collections.defaultdict(list)
    for r in rows:
        by_cell[(r["channel_id"], r["line_index"], r["url"])].append(r)
    n_bad = collections.Counter((r["channel_id"], r["line_index"], r["url"]) for r in bad)
    all_bad = [k for k, rs in by_cell.items() if n_bad[k] == len(rs)]
    some_bad = [k for k in n_bad if k not in all_bad]
    return {"rows": len(rows), "buckets": {k: len(v) for k, v in buckets.items()},
            "sum_buckets": sum(len(v) for v in buckets.values()),
            "bad_rows": len(bad), "bad_cells_all": len(all_bad), "bad_cells_some": len(some_bad),
            "bad_channels": len({r["channel_id"] for r in bad}),
            "items": [(r["name"], r["line_index"], r["url"], r.get("res"))
                      for k in all_bad for r in by_cell[k]]}


def borrowed_frame_board(rows):
    """数「这一格 credited 的首帧其实属于另一条线」——`absorb()` 在落地等待那一处（`:692`）没带
    `want_line`，所以同台的**别的线**的 `firstFrame` 能被记进这一格；台账里 `first_line` 存的就是
    那一行自己的线号，拿它跟计划线号一比就露出来。
    为什么要单独数：这一枚会让一条**从没出过画的线**被判成「真机播过」，而「真机播过」既是
    复跑清单的排除条件、也是清晰度置顶与"这台播过就别动"的依据——借来的首帧会一路传到删除名单。
    这一张**只报数、不改判据**：改判据要升 toolv 并另起台账（正在跑的这一遍用的是它起场那一刻的代码）。
    """
    # 三档，不许把"这一行没带 first_line 字段"折成"一致"（那是把没量到读成通过）
    borrowed = [r for r in rows if r.get("first_frame") and r.get("first_line") is not None
                and r["first_line"] != r["line_index"] + 1]
    unknown = [r for r in rows if r.get("first_frame") and r.get("first_line") is None]
    ok = [r for r in rows if r.get("first_frame") and r.get("first_line") is not None
          and r["first_line"] == r["line_index"] + 1]
    return {"frame_rows": sum(1 for r in rows if r.get("first_frame")),
            "credited_ok": len(ok), "borrowed": len(borrowed),
            "no_line_field": len(unknown),
            "cells": len({(r["channel_id"], r["line_index"], r["url"]) for r in borrowed}),
            "items": [(r["name"], r["line_index"] + 1, r["first_line"], r.get("anchor"),
                       r.get("zap_ms"), r["ts"], r["url"]) for r in borrowed]}


def host_stall_board(cell_list, min_cells=5):
    """把卡顿那一列按**主机**摊开，并把分母一起打出来。

    为什么要这一档（目标那句「优化稳定性减少卡顿」）：`stalls/stall_ms` 不参与六态判据，
    以前只报得出"110/222 格有卡顿、合计 173 次"——这一句既说不出"哪家源在卡"，
    也说不出"是不是只是格数多的那几家看着多"。所以每一档带三个数：这一族共有几格（分母）、
    其中几格有卡顿、卡顿合计多少毫秒。`cells < min_cells` 的标成**样本太小**，
    不许被读成结论（3 格的主机哪怕 1 格卡 20 秒，比例也只有 33%，拿它删线就是冤枉）。
    取主机一律 `urlparse().netloc`（10-24 用 `Path.parts` 把主机读成路径首段，代价是一份假分组表）。
    """
    by_host = collections.defaultdict(list)
    for c in cell_list:
        by_host[urlparse(c["url"]).netloc or "?"].append(c)
    out = []
    for host, cs in by_host.items():
        stalling = [c for c in cs if (c.get("stalls") or 0) > 0]
        worst = max(stalling, key=lambda c: c.get("stall_ms") or 0, default=None)
        out.append({
            "host": host, "cells": len(cs), "stalling": len(stalling),
            "total_ms": sum(c.get("stall_ms") or 0 for c in cs),
            "median_ms": int(statistics.median([c["stall_ms"] for c in stalling])) if stalling else 0,
            "worst": None if worst is None else (worst["channel"], worst["line_index"] + 1,
                                                 worst["stalls"], worst["stall_ms"]),
            "small": len(cs) < min_cells,
        })
    out.sort(key=lambda r: (-r["total_ms"], -r["stalling"], r["host"]))
    return {"rows": out, "cells": sum(r["cells"] for r in out),
            "stalling": sum(r["stalling"] for r in out),
            "total_ms": sum(r["total_ms"] for r in out)}


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--toolv", type=int, default=6, help="只数这个尺版本写过的行")
    ap.add_argument("--slow-ms", type=int, default=5000, help="起播超过这么多毫秒算慢（默认 5 秒）")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    # 六态判据与"按格子聚合"都直接调尺子那两份函数——这一张自己不再写第二套，
    # 否则两处口径迟早分叉（读数板和记分板给出两个不同的"没出画"条数就没人敢信了）。
    rows = tvrl.rows_from_ledger(args.ledger, args.toolv)
    cells = tvrl.score_rows(rows, rounds=1)
    for x in cells:
        x["line"] = x["line_index"] + 1
        x["cid"] = x["channel_id"]
        x["name"] = x["channel"]
        x["hit"] = x["hits"] > 0
        x["verdict_no_frame"] = (x["verdict"] == "落到台但没出画")
        # `score_rows` 把多轮的 `zap_ms` 收成升序列表（记分板那一列要逐轮念），这张板每格只取一个数：
        # 取最快那一轮，与旧版"这一格里最快的一次"口径一致。
        x["zap_ms"] = min(x["zap_ms"]) if x["zap_ms"] else None

    print(f"[读数面] 台账 {Path(args.ledger).name}｜尺版本 toolv={args.toolv}｜"
          f"格数 {len(cells)}（台数 {len({c['cid'] for c in cells})}）")

    c = collections.Counter(x["verdict"] for x in cells)
    print("\n[六态分档] 分项之和 " + str(sum(c.values())) + " == 格数 " + str(len(cells))
          + ("  ✓" if sum(c.values()) == len(cells) else "  ✗ 对不上，别看下面"))
    for k in STATE_ORDER:
        if c.get(k):
            print(f"  {k:<22} {c[k]:>4}  ({c[k] / len(cells):.0%})")

    played = [x for x in cells if x["hit"]]
    zaps = [x["zap_ms"] for x in played if x["zap_ms"]]
    print("\n[起播耗时] 只数真出过画的那些格，且锚点要连着念")
    if zaps:
        print(f"  中位 {statistics.median(zaps):.0f}ms｜P95 {pct(zaps, 0.95)}ms｜最大 {max(zaps)}ms"
              f"｜样本 {len(zaps)} 格")
        slow = sorted((x for x in played if (x["zap_ms"] or 0) > args.slow_ms),
                      key=lambda x: -(x["zap_ms"] or 0))
        anchors = collections.Counter(a for x in slow for a in x["anchor"])
        print(f"  起播 >{args.slow_ms}ms 的 {len(slow)} 格｜这些格的锚点分布 {dict(anchors)}"
              f"（firstlog 那几格的『起播』是从本线第一行起算的，不等于这条线快慢）")
        for x in slow[:args.top]:
            print(f"    {x['name']} 线{x['line']} 起播 {x['zap_ms']}ms 锚 {'/'.join(x['anchor'])} "
                  f"res {'/'.join(x['res']) or '-'}")
    else:
        print("  还没有一格出过首帧（这一遍刚开始）")

    stalled = sorted((x for x in cells if x["stalls"] or x["stall_ms"]),
                     key=lambda x: (-x["stall_ms"], -x["stalls"]))
    print(f"\n[卡顿] 数到卡顿的格 {len(stalled)} / {len(cells)}｜合计 {sum(x['stalls'] for x in stalled)} 次"
          f" / {sum(x['stall_ms'] for x in stalled)}ms")
    for x in stalled[:args.top]:
        print(f"    {x['name']} 线{x['line']} 卡顿{x['stalls']}次/{x['stall_ms']}ms "
              f"出画={x['hit']} 判据={x['verdict']}")

    hsb = host_stall_board(cells)
    print(f"\n[卡顿按主机] 主机 {len(hsb['rows'])} 家｜分母（格）合计 {hsb['cells']}｜"
          f"有卡顿的格 {hsb['stalling']}｜卡顿毫秒合计 {hsb['total_ms']}"
          f"｜分项之和 {hsb['cells']} == 格数 {len(cells)} "
          f"{'✓' if hsb['cells'] == len(cells) else '✗ 有格没归进主机，别拿这张表定案'}")
    for r in hsb["rows"][:args.top]:
        w = r["worst"]
        print(f"    {r['host']:<26} 卡顿格 {r['stalling']:>3}/{r['cells']:<3}"
              f"＝{r['stalling'] / r['cells']:>4.0%}｜合计 {r['total_ms']:>7}ms"
              f"｜有卡顿那几格的中位 {r['median_ms']:>6}ms"
              + (f"｜最坏 {w[0]} 线{w[1]} {w[2]}次/{w[3]}ms" if w else "")
              + ("｜样本太小，别当结论" if r["small"] else ""))

    pfb = playing_frame_board(rows)
    b = pfb["buckets"]
    print(f"\n[「说在播却没出画」那一类] 按**行**分四档（这是台账里真有的 `playing` 字段，不是判据推出来的）："
          f"在播且出过画 {b['played_and_frame']}｜出过画但那一刻没在播 {b['frame_not_playing']}｜"
          f"没出画也没在播 {b['no_frame_not_playing']}｜这一窗没读到那一行（不作数）{b['no_reading']}｜"
          f"**在播却没出画 {b['claimed_playing_no_frame']}**"
          f"｜分项之和 {pfb['sum_buckets']} == 行数 {pfb['rows']} "
          f"{'✓' if pfb['sum_buckets'] == pfb['rows'] else '✗ 对不上，别看下面'}")
    print(f"  行 {pfb['bad_rows']}｜整格每一轮都这样 {pfb['bad_cells_all']} 格／台 {pfb['bad_channels']}"
          f"｜只有一轮这样、别的轮出过画 {pfb['bad_cells_some']} 格"
          f"｜⚠️这一遍每格只开约 20 秒，『挂着几分钟不动』那种形态本来就落在窗外 ⇒ 这里数到 0 **不等于**症状消失，"
          f"只能说这一遍的窗口里没抓到（原始 logcat 层的证据见 CHANGELOG 追加三十三那一节）")
    for name, li, url, res in pfb["items"][:args.top]:
        print(f"    {name} 线{li + 1} res {res or '-'} url {url[:64]}")

    conn = [x for x in cells if x["verdict_no_frame"]]
    print(f"\n[落到台但没出画]（六态里那一态，按判据算的，**不**等于应用说过在播）：{len(conn)}")
    for x in conn[:args.top]:
        print(f"    {x['name']} 线{x['line']} res {'/'.join(x['res']) or '-'} 判据={x['verdict']}")

    nohit = [x for x in cells if not x["hit"] and x["verdict"] not in
             ("挪不到那条线（不作数）", "按键没落地（不作数）")]
    print(f"[没出画的格]（已排除两类『不作数』）：{len(nohit)}")
    for x in nohit[:args.top]:
        print(f"    {x['name']} 线{x['line']} 判据={x['verdict']} url {x['url'][:70]}")
    bf = borrowed_frame_board(rows)
    print(f"\n[借来的首帧] 有首帧的行 {bf['frame_rows']} 枚｜行号与计划线号一致 {bf['credited_ok']} 枚｜"
          f"**不一致（同台的别的线的首帧被记进这一格）{bf['borrowed']} 枚**｜涉及格 {bf['cells']} 枚"
          f"｜没带 first_line 字段的 {bf['no_line_field']} 枚"
          f"｜分项之和 {bf['credited_ok'] + bf['borrowed'] + bf['no_line_field']} == {bf['frame_rows']} "
          f"{'✓' if bf['credited_ok'] + bf['borrowed'] + bf['no_line_field'] == bf['frame_rows'] else '✗ 对不上'}")
    for name, want, got_ln, anchor, zap, ts, url in bf["items"][:10]:
        print(f"    {name} 计划线{want} 首帧其实来自线{got_ln}｜锚 {anchor} 起播 {zap}ms ts {ts} url {url[:58]}")
    print("    （这一张只报数：判据没改、toolv 没升——改判据要升版本并另起台账，正在跑的这一遍用的是它起场那一刻的代码）")
    fl = first_line_board(cells)
    tot = len(fl["ok"]) + len(fl["bad"]) + len(fl["unmeasured"])
    print(f"\n[每台第一条] 台账里出现过的台 {fl['channels']}｜首条真机播过 {len(fl['ok'])}｜"
          f"首条不合格 {len(fl['bad'])}｜首条这一遍还没测 {len(fl['unmeasured'])}"
          f"｜分项之和 {tot} == 台数 {fl['channels']} {'✓' if tot == fl['channels'] else '✗ 对不上，别看下面'}")
    for name, v, url in fl["bad"][:args.top]:
        print(f"    父母打开这一台就是这一格：{name} 线1 判据={v} url {url[:64]}")
    for name, why in fl["unmeasured"][:args.top]:
        print(f"    没测到、不许算成不合格：{name}（{why}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
