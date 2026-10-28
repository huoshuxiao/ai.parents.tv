#!/usr/bin/env python3
"""删线前后的**代价明细**：`[对表] 台/线/黑名单` 只给三个总数，答不了父母手上会感觉到什么。

这一把尺补的是那三个总数背后的四件事（都只读两份目录，不联网、不写盘）：
  · 哪几台**整台消失**（掉到 0 条线就被构建删台）——是不是必看台、这台电视有没有真播过；
  · 每一台少了哪几条地址、**首条（线路 1）换没换**——首条换了就是父母打开这一台落到的那一格变了；
  · **数字键位移**——台号是按目录次序编的，前面删掉一台，后面全都往前挪；
  · `lineQuality` / `lineMoves` 里留下的**孤儿键**——线已经不在目录里，分数与改挂还挂着它。

用法：python3 tools/deletion_cost_report.py --old <旧目录> --new <新目录>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "tools"))

import stream_rules  # noqa: E402


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _index(cat: dict) -> dict:
    """按**台名**给每台定位次与线路（数字键就是位次）。

    为什么不按 `id` 配（10-27 现读抓出来的缺陷）：`id` 是按 `category|subgroup|name` 哈希出来的，
    「整台换组」这一类改动会让同一台换一个 id ⇒ 按 id 配对时旧的那枚算「整台消失」、
    新的那枚没人念，被摘地址数也比线差多出这台的全部线路（实测：山东教育卫视 2 条被算成摘掉，
    [少线的台] 那行自己打了 25 != 23 ✗）。台名重名时键退回 `名#id`，不静默合并两台。
    """
    counts: dict = {}
    for c in cat["channels"]:
        counts[c["name"]] = counts.get(c["name"], 0) + 1
    out = {}
    for i, c in enumerate(cat["channels"], 1):
        key = c["name"] if counts[c["name"]] == 1 else f'{c["name"]}#{c["id"]}'
        out[key] = {"pos": i, "id": c["id"], "name": c["name"],
                    "category": c.get("category"), "subgroup": c.get("subgroup"),
                    "urls": [stream_rules.canon(s) for s in (c.get("streams") or [])]}
    return out


def diff(old: dict, new: dict, must_names=frozenset(), played_ids=frozenset(),
         played_names=frozenset()) -> dict:
    o_n, n_n = _index(old), _index(new)
    o_urls = {u for v in o_n.values() for u in v["urls"]}
    n_urls = {u for v in n_n.values() for u in v["urls"]}
    vanished, added, regrouped = [], [], []
    for key, c in o_n.items():
        if key not in n_n:
            vanished.append({"id": c["id"], "name": c["name"], "category": c.get("category"),
                             "lines": len(c["urls"]), "must": c["name"] in must_names,
                             "played": c["id"] in played_ids or c["name"] in played_names})
            continue
        if n_n[key]["id"] != c["id"]:
            regrouped.append({"name": c["name"], "old_lines": len(c["urls"]),
                              "new_lines": len(n_n[key]["urls"]),
                              "old": f'{c["category"]}/{c["subgroup"]}',
                              "new": f'{n_n[key]["category"]}/{n_n[key]["subgroup"]}',
                              "same_urls": c["urls"] == n_n[key]["urls"]})
    for key, c in n_n.items():
        if key not in o_n:
            added.append({"id": c["id"], "name": c["name"],
                          "category": f'{c["category"]}/{c["subgroup"]}', "lines": len(c["urls"])})
    lost = {k: [u for u in o_n[k]["urls"] if u not in set(n_n[k]["urls"])]
            for k in n_n if k in o_n}
    lost = {k: v for k, v in lost.items() if v}
    # 反方向也要数：只看"被摘"的那一等式在目录变大时会自己报 ✗
    # （10-27 17:29 钉回 Al Jazeera 那一批：线 449→450，被摘 0 枚，旧代码打「0 == 线差 -1 ✗」）。
    gained = {k: [u for u in n_n[k]["urls"] if u not in set(o_n[k]["urls"])]
              for k in n_n if k in o_n}
    gained = {k: v for k, v in gained.items() if v}
    first_changed = []
    for k in n_n:
        if k not in o_n:
            continue
        of = (o_n[k]["urls"] or [None])[0]
        nf = (n_n[k]["urls"] or [None])[0]
        if of != nf and nf is not None:
            first_changed.append({"name": n_n[k]["name"], "old": of, "new": nf})
    shifts = []
    for k in n_n:
        if k in o_n and o_n[k]["pos"] != n_n[k]["pos"]:
            shifts.append({"name": n_n[k]["name"], "old": o_n[k]["pos"], "new": n_n[k]["pos"]})
    orphan_q = [k for k in (old.get("lineQuality") or {}) if stream_rules.canon(k) not in n_urls]
    orphan_m = [k for k in (old.get("lineMoves") or {}) if stream_rules.canon(k) not in n_urls]
    return {
        "totals": {"channels": (len(old["channels"]), len(new["channels"])),
                   "streams": (sum(len(c["urls"]) for c in o_n.values()),
                               sum(len(c["urls"]) for c in n_n.values())),
                   "blocked": (len(old.get("blockedLines") or {}), len(new.get("blockedLines") or {})),
                   "urls": (len(o_urls), len(n_urls))},
        "vanished": sorted(vanished, key=lambda x: -x["lines"]),
        "added": sorted(added, key=lambda x: -x["lines"]),
        "regrouped": sorted(regrouped, key=lambda x: x["name"]),
        "lost": sorted(({"name": n_n[k]["name"], "urls": v,
                         "before": len(o_n[k]["urls"]), "after": len(n_n[k]["urls"])}
                        for k, v in lost.items()),
                       key=lambda x: (x["before"] - x["after"], x["name"]), reverse=True),
        "gained": sorted(({"name": n_n[k]["name"], "urls": v,
                           "before": len(o_n[k]["urls"]), "after": len(n_n[k]["urls"])}
                          for k, v in gained.items()),
                         key=lambda x: (x["after"] - x["before"], x["name"]), reverse=True),
        "first_changed": first_changed,
        "shifts": sorted(shifts, key=lambda x: abs(x["new"] - x["old"]), reverse=True),
        "orphan_quality": sorted(orphan_q), "orphan_moves": sorted(orphan_m),
    }


def render(d: dict, top: int = 12) -> str:
    t = d["totals"]
    lines = [f"[总数] 台 {t['channels'][0]}→{t['channels'][1]}（差 {t['channels'][1] - t['channels'][0]}）｜"
             f"线 {t['streams'][0]}→{t['streams'][1]}（差 {t['streams'][1] - t['streams'][0]}）｜"
             f"地址 {t['urls'][0]}→{t['urls'][1]}（差 {t['urls'][1] - t['urls'][0]}）｜"
             f"黑名单 {t['blocked'][0]}→{t['blocked'][1]}"]
    v = d["vanished"]
    both = [x for x in v if x["must"] and x["played"]]
    only_must = [x for x in v if x["must"] and not x["played"]]
    only_played = [x for x in v if x["played"] and not x["must"]]
    neither = [x for x in v if not x["must"] and not x["played"]]
    tot_v = len(both) + len(only_must) + len(only_played) + len(neither)
    lines.append(f"[整台消失] {len(v)} 台（必看台且播过 {len(both)}｜仅必看台 {len(only_must)}｜"
                 f"仅这台电视播过 {len(only_played)}｜都不是 {len(neither)}）"
                 f"｜分项之和 {tot_v} == {len(v)} {'✓' if tot_v == len(v) else '✗ 分类没盖全，别看下面'}")
    for x in v[:top]:
        lines.append(f"    掉到 0 条：{x['name']}（原 {x['lines']} 条，组 {x['category']}）"
                     + ("【必看台】" if x["must"] else "") + ("【电视播过】" if x["played"] else ""))
    n_lost_urls = sum(len(x["urls"]) for x in d["lost"]) + sum(x["lines"] for x in v)
    n_gain_urls = (sum(len(x["urls"]) for x in d.get("gained") or [])
                   + sum(x["lines"] for x in d["added"]))
    stream_delta = t["streams"][0] - t["streams"][1]
    lines.append(f"[整台新增] {len(d['added'])} 台"
                 + ("：" + "、".join(f"{x['name']}（{x['category']}，{x['lines']} 条）" for x in d["added"][:top])
                    if d["added"] else "（无）"))
    rg = d["regrouped"]
    lines.append(f"[换组] {len(rg)} 台台名没变、id 变了（整台换组这一类改动按台名配对，不许算进上面那两栏）")
    for x in rg[:top]:
        lines.append(f"    {x['name']}：{x['old']} → {x['new']}｜线路 {x['old_lines']}→{x['new_lines']} 条"
                     + ("（地址一字未换）" if x["same_urls"] else "（地址也变了，看少线那一栏）"))
    lines.append(f"[少线的台] {len(d['lost'])} 台｜被摘的地址 {n_lost_urls} 枚（含整台消失那 {sum(x['lines'] for x in v)} 条）"
                 f"｜补进来的 {n_gain_urls} 枚（含整台新增那 {sum(x['lines'] for x in d['added'])} 条）"
                 f"｜等式 被摘 {n_lost_urls} − 补进 {n_gain_urls} == 线差 {stream_delta} "
                 f"{'✓' if n_lost_urls - n_gain_urls == stream_delta else '✗ 有一批线是被换序/改名而不是被摘，逐项看'}")
    for x in d["lost"][:top]:
        lines.append(f"    {x['name']} 线 {x['before']}→{x['after']} 摘 {len(x['urls'])} 条："
                     + "、".join(u[:58] for u in x["urls"][:2]))
    lines.append(f"[首条（线路 1）变了] {len(d['first_changed'])} 台——父母打开这一台落到的那一格跟着变")
    for x in d["first_changed"][:top]:
        lines.append(f"    {x['name']}：{str(x['old'])[:52]} → {str(x['new'])[:52]}")
    s = d["shifts"]
    lines.append(f"[数字键位移] {len(s)} 台的台号变了｜最大位移 {max((abs(x['new'] - x['old']) for x in s), default=0)}"
                 f"｜方向 {sorted({(1 if x['new'] > x['old'] else -1) for x in s})}")
    for x in s[:3]:
        lines.append(f"    {x['name']} 台号 {x['old']}→{x['new']}")
    lines.append(f"[孤儿键] lineQuality {len(d['orphan_quality'])} 枚、lineMoves {len(d['orphan_moves'])} 枚"
                 f" 还指着已经不在这个目录里的地址（不清掉就是拿着已删线路的分数排序）")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--must-shots", default="tools/.cache/must_shots/ledger.jsonl",
                    help="模拟器截图台账：这些台在这台设备上真进去播过，掉台要单独点名")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    old, new = _load(Path(args.old)), _load(Path(args.new))
    must_names = {c["name"] for c in new["channels"]} & _must_names()
    played = set()
    played_names = set()
    led = BASE / args.must_shots
    if led.exists():
        for line in led.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("played") or row.get("entered_player"):
                played.add(row.get("id") or row.get("channel_id") or "")
                # 换过组的台 id 变了，只按 id 认会读成"这台电视没播过"⇒ 台名也认一份
                if row.get("name") or row.get("channel_name"):
                    played_names.add(row.get("name") or row.get("channel_name"))
    d = diff(old, new, must_names=must_names, played_ids=played, played_names=played_names)
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else render(d, args.top))
    return 0


def _must_names() -> set:
    """必看台名单只有一份：`build_catalog.MUST_PLAY_CHANNELS`（不许在这里再抄第二份）。"""
    try:
        import build_catalog as BC
    except Exception as exc:            # 名单读不到要让人看见，不许折成"没有必看台"
        print(f"[警告] 读不到 MUST_PLAY_CHANNELS（{type(exc).__name__}: {exc}）⇒ 必看台那一栏不作数",
              file=sys.stderr)
        return set()
    return set(getattr(BC, "MUST_PLAY_CHANNELS", set()) or set())


if __name__ == "__main__":
    raise SystemExit(main())
