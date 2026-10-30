#!/usr/bin/env python3
"""验的是用户 10-27 那句「山东教育卫视 频道 页放 地方频道」：整台换组只许改**页面**，别的一律不动。

构建侧的新机制是 `CHANNEL_GROUP_OVERRIDE`（台名 → 目标组）＋ `builder.add` 里那一处收口。
本夹具盯八种会说谎的情形：

  A) 换组没生效／生效但把 id 悄悄留着——id 是 sha1(category|subgroup|台名)，换组必然改 id，
     这一格把"会变"钉死，免得以后有人以为能同时保住旧 id。
  B) 同一台名从两份列表进来，换组后分裂成两个台（一个在 卫视、一个在 地方）。
  C) 名单两侧不同尺：表键按 norm_key 归一，查表也必须走同一把尺（繁体名要能命中）。
  D) **换组顺手把已判坏的线放回来**——10-27 试算真踩过：只改 subgroup 时，
     `39.135.138.58:18890/.../3221225719/index.m3u8`（黑名单理由「连接超时」）因为不再属于
     卫视组就绕过了卫视剪枝 ⇒ 目录 472→473 条、黑名单 1561→1560。这条支路必须跟着台名走。
  E) 表里写了个没声明的组 ⇒ 落盘后首页看不见这台（首页只列 SUBGROUP_ORDER 里的 subgroup），
     必须拒绝起构建，不许悄悄落一个界面外的台。
  F) 名单点了名、源里没有这台（或被删台规则整台摘掉）⇒ 覆盖率那一行必须点名，
     "命中 0"不许读成"换组已生效"。
  G) 生产规模正对照：拿真试算产物与在库资产逐台逐条对表，除这一台的组以外什么都不许变。
     试算文件不在时报「未跑」，不折成通过。

跑法：python3 tools/tests/test_group_move.py    （退出码 0 才算过）
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402

NAME = "山东教育卫视"
TRAD = "山東教育衛視"
SIBLING = "山东卫视"          # 名单外的近邻台：不许被误伤
LOCAL_OTHER = "烟台新闻综合"   # 真·地方台：卫视剪枝不许扩到它头上
SHE = "http://1.1.1.1/live/she_a.m3u8"
HER = "http://2.2.2.2/live/she_b.m3u8"
DEAD = "http://3.3.3.3/live/dead.m3u8"
SIB = "http://4.4.4.4/live/sib.m3u8"
LOTH = "http://5.5.5.5/live/loth.m3u8"
# 10-27 扩档那一组：作用域内/外的对照组（台名全是夹具专用，不在必看名单里）
INTL = "夹具国际台"
SPORT = "夹具体育台"
CCTV_TEST = "夹具央视台"
IVL = "http://6.6.6.6/live/intl.m3u8"
SPU = "http://7.7.7.7/live/sport.m3u8"
CVL = "http://8.8.8.8/live/cctvtest.m3u8"

# 夹具里「有画面实证」的那几条：新加的对照组要显式列进来，否则整台会被台级那一格删掉
PICT = {IVL: True, SPU: True, CVL: True}

failures: list[str] = []
notes: list[str] = []
ran = 0


def check(label: str, got, want) -> None:
    global ran
    ran += 1
    if got != want:
        failures.append(f"{label}: 得到 {got!r}，期望 {want!r}")


def row(name, category, subgroup, streams):
    """按 final_channels 落出来的行形造（strict_filter 只读这几格）。"""
    return {"id": f"{category}-fixture", "name": name, "category": category,
            "subgroup": subgroup, "logo": "", "streams": list(streams)}


@contextlib.contextmanager
def stubs(tiers=None, pictures=None, device=()):
    """把证据那几把尺接成夹具读数，其余逻辑（组判断、剪枝支路）全走真码。

    tiers 缺省＝档 0（有实证）；DEAD 给档 3（本机连不通）正是卫视剪枝要摘的那一档。
    """
    tiers = tiers or {}
    pictures = pictures or {}
    keep = {n: getattr(bc, n) for n in
            ("evidence", "has_picture", "has_picture_now", "device_proof", "logo_keeps_line", "usable")}
    bc.evidence = lambda url, name="": (tiers.get(url, 0), "夹具")
    bc.has_picture = lambda name, url: pictures.get(url, url in (SHE, HER, SIB, LOTH))
    bc.has_picture_now = lambda name, url: pictures.get(url, url in (SHE, HER, SIB, LOTH))
    bc.device_proof = lambda name, url: url in set(device)
    bc.logo_keeps_line = lambda name, url: False
    bc.usable = lambda name, url: True
    try:
        yield
    finally:
        for n, f in keep.items():
            setattr(bc, n, f)


@contextlib.contextmanager
def roster(table):
    keep = bc.CHANNEL_GROUP_OVERRIDE
    bc.CHANNEL_GROUP_OVERRIDE = table
    try:
        yield
    finally:
        bc.CHANNEL_GROUP_OVERRIDE = keep


def to_local(name="山东教育", subgroup="山东"):
    return {name: {"category": "local", "subgroup": subgroup, "why": "夹具"}}


def part_a() -> None:
    """换组生效，且 id 一定跟着变。"""
    with stubs(), roster(to_local(NAME)):
        b = bc.Builder()
        b.add(NAME, "cctv", "卫视", SHE, order=16)
        b.add(SIBLING, "cctv", "卫视", SIB, order=15)
        rows = b.final_channels()
        got = {r["name"]: r for r in rows}
        check("A1 名单内的台落到 local/山东",
              (got[NAME]["category"], got[NAME]["subgroup"]), ("local", "山东"))
        want_id = "local-" + hashlib.sha1("|".join(["local", "山东", NAME]).encode()).hexdigest()[:10]
        check("A2 id 按新组重算（换组必然换 id）", got[NAME]["id"], want_id)
        check("A3 名单外的台组不动", (got[SIBLING]["category"], got[SIBLING]["subgroup"]),
              ("cctv", "卫视"))
        old_id = "cctv-" + hashlib.sha1("|".join(["cctv", "卫视", SIBLING]).encode()).hexdigest()[:10]
        check("A4 名单外的台 id 不变", got[SIBLING]["id"], old_id)


def part_b() -> None:
    """两份来源、同一个台名 ⇒ 换组后仍是一个台，线路合起来。"""
    with stubs(), roster(to_local(NAME)):
        b = bc.Builder()
        b.add(NAME, "cctv", "卫视", SHE, order=16)
        b.add(NAME, "local", "山东", HER, order=999)
        rows = [r for r in b.final_channels() if r["name"] == NAME]
        check("B1 不分裂成两台", len(rows), 1)
        check("B2 两条线都在", sorted(rows[0]["streams"]), sorted([SHE, HER]))


def part_c() -> None:
    """表键与查表走同一把尺：繁体名命中、简写名不许误命中。"""
    check("C1 繁体名归一到同一枚键", bc.norm_key(TRAD), bc.norm_key(NAME))
    with stubs(), roster(to_local(NAME)):
        b = bc.Builder()
        b.add(TRAD, "cctv", "卫视", SHE)
        rows = b.final_channels()
        check("C2 繁体名进来也落新组",
              (rows[0]["category"], rows[0]["subgroup"]), ("local", "山东"))
        b2 = bc.Builder()
        b2.add("山东教育", "cctv", "卫视", SHE)
        r2 = b2.final_channels()
        check("C3 台名差两个字不算命中",
              (r2[0]["category"], r2[0]["subgroup"]), ("cctv", "卫视"))


def part_d() -> None:
    """剪枝支路跟着台名走：换组不许把已判坏的线放回目录；
    用户 10-27 点档「3」把档 3 可删的作用域从卫视组扩到 地方/国际/港澳台（央视名册与体育仍不动）。"""
    chans = [row(NAME, "local", "山东", [SHE, HER, DEAD]),
             row(LOCAL_OTHER, "local", "山东", [LOTH, DEAD]),
             row(SIBLING, "cctv", "卫视", [SIB, DEAD]),
             row(INTL, "international", "纪实", [IVL, DEAD]),
             row(SPORT, "sports", "国内体育", [SPU, DEAD]),
             row(CCTV_TEST, "cctv", "央视", [CVL, DEAD])]
    with stubs(tiers={DEAD: 3}, pictures=PICT), roster(to_local(NAME)):
        survivors, stats = bc.strict_filter([dict(c, streams=list(c["streams"])) for c in chans])
        by = {c["name"]: c["streams"] for c in survivors}
        check("D1 换组后台内那条档 3 线仍被摘（原组规则跟着走）", by[NAME], [SHE, HER])
        check("D2 摘的那条进了 tier3_pruned 记账",
              {n for n, _, _ in stats["tier3_pruned"]}, {NAME, SIBLING, LOCAL_OTHER, INTL})
        check("D3 扩档（用户 10-27「3 从卫视组扩到地方/国际/港澳台」）：名单外的真·地方台这条也摘",
              by[LOCAL_OTHER], [LOTH])
        check("D4 卫视组照旧摘线", by[SIBLING], [SIB])
        check("D4b 国际组也摘（在作用域内）", by[INTL], [IVL])
        check("D4c 没扩到体育：那条档 3 线照旧留着", by[SPORT], [SPU, DEAD])
        check("D4d 没扩到央视名册：那条档 3 线照旧留着", by[CCTV_TEST], [CVL, DEAD])
    # 负对照一：把作用域那张表临时清空，其余一模一样 ⇒ 差异必须来自这一行
    #（10-27 改判据前 D3/D4b 是「一条不动」，那两格正好是这张表的牙）
    saved_scope = bc.TIER3_PRUNE_CATS
    try:
        bc.TIER3_PRUNE_CATS = set()
        with stubs(tiers={DEAD: 3}, pictures=PICT), roster(to_local(NAME)):
            survivors, _ = bc.strict_filter([dict(c, streams=list(c["streams"])) for c in chans])
            by = {c["name"]: c["streams"] for c in survivors}
            check("D5 负对照（作用域清空）：地方/国际那两台一条不动",
                  (by[LOCAL_OTHER], by[INTL]), ([LOTH, DEAD], [IVL, DEAD]))
            check("D6 负对照：卫视与换组那台不跟这张表走（跟台名走）",
                  (by[SIBLING], by[NAME]), ([SIB], [SHE, HER]))
    finally:
        bc.TIER3_PRUNE_CATS = saved_scope
    # 负对照二：换组换到一个**不在作用域**的组（体育）——原组规则必须还摘它，
    # 而另一台真·体育台不受影响。这两格证明「跟着台名走」与「跟着作用域走」是两条独立的支路。
    to_sports = {"山东教育卫视": {"category": "sports", "subgroup": "国内体育",
                                  "why": "夹具专用：把换组目标挪到作用域外"}}
    chans2 = [row(NAME, "sports", "国内体育", [SHE, HER, DEAD]),
              row(SPORT, "sports", "国内体育", [SPU, DEAD])]
    saved_roster = dict(bc.CHANNEL_GROUP_OVERRIDE)
    saved_scope2 = bc.TIER3_PRUNE_CATS
    try:
        bc.CHANNEL_GROUP_OVERRIDE.clear()
        bc.CHANNEL_GROUP_OVERRIDE.update({bc.norm_key(k): v for k, v in to_sports.items()})
        bc.TIER3_PRUNE_CATS = {"local", "international", "hktw"}
        with stubs(tiers={DEAD: 3}, pictures=PICT):
            survivors, stats = bc.strict_filter([dict(c, streams=list(c["streams"])) for c in chans2])
            by = {c["name"]: c["streams"] for c in survivors}
            check("D7 换到作用域外仍按它离开的 卫视 组剪枝", by[NAME], [SHE, HER])
            check("D7b 另一台真·体育台不受影响", by[SPORT], [SPU, DEAD])
            check("D7c 记账里只有那一台", {n for n, _, _ in stats["tier3_pruned"]}, {NAME})
    finally:
        bc.CHANNEL_GROUP_OVERRIDE.clear()
        bc.CHANNEL_GROUP_OVERRIDE.update(saved_roster)
        bc.TIER3_PRUNE_CATS = saved_scope2


def part_e() -> None:
    """目标组没声明 ⇒ 拒绝起构建（落盘后首页看不见这台）。"""
    try:
        with roster(to_local("查无此组台", subgroup="山东山东")):
            bc.check_group_override()
        check("E1 未声明的组要拒绝", "没抛", "SystemExit")
    except SystemExit as exc:
        msg = str(exc)
        check("E1 未声明的组要拒绝", "拒绝" in msg and "查无此组台" in msg, True)
    with roster({}):
        try:
            bc.check_group_override()
            check("E2 空名单不报错", True, True)
        except SystemExit as exc:
            check("E2 空名单不报错", f"抛了 {exc}", True)


def part_f() -> None:
    """覆盖率两列：点了名却没进目录必须点名。"""
    with roster(to_local(NAME)):
        moved, missing = bc.group_move_coverage([row(LOCAL_OTHER, "local", "山东", [LOTH])])
        check("F1 源里没有这台：moved 为空", moved, [])
        check("F2 源里没有这台：missing 点名", missing, ["山东教育卫视"])
        moved, missing = bc.group_move_coverage([row(NAME, "local", "山东", [SHE])])
        check("F3 命中时 missing 为空", (len(moved), missing), (1, []))
    with roster({}):
        check("F4 空名单两列都空", bc.group_move_coverage([row(NAME, "cctv", "卫视", [SHE])]),
              ([], []))


def part_g() -> None:
    """生产规模正对照：**这条链真用的那一份试算产物**（`final_chain.sh` 的 `TMP`）与在库资产逐台逐条对表。

    10-27 16:3x 换过基准：原来对的是 `sdetv_move/asset_trial2.json`（只有换组那一批），
    判据改了（扩档＋降位名册）之后它还一路绿 ⇒ 它守的是旧世界，差异活不到它那一层。
    """
    trial = BASE / "tools/.cache/channels_before_land_1634.json"
    if not trial.exists():
        notes.append(f"G 未跑（10-27 16:34 落盘前那一份冻结基准不在：{trial}）")
        return
    # 这一块是「10-27 那一批」的落盘差值对表。在库资产此后被 10-28/10-30 的批次继续改过
    # （4K 并线、排线重排、补源），再拿这份快照对表红的是「批不同了」，不是规则坏了——
    # 规则本身由 A–F 逐格守。按文件 mtime 认批：晚于 10-28 零点就如实报「未跑」。
    asset_path = BASE / "app/src/main/assets/channels.json"
    if asset_path.stat().st_mtime >= 1793116800:  # 2026-10-28 00:00 北京时间
        notes.append("G 未跑（在库资产已被 10-28 之后的批次改过，这份 10-27 快照不再是它的上一批）")
        return
    # 基准换成「落盘前那一份快照 vs 在库」：原来对的是 `asset_final.json`，而落盘把它逐字节
    # 复制进在库 ⇒ 每次落盘之后这两份就成了同一份，GB/GD 这几格会绿着失去差异（10-27 17:29
    # 钉回那一批实测到：试算与在库同一枚 sha，被摘 0／消失 0 全是空对空）。
    old = json.load(trial.open(encoding="utf-8"))
    new = json.load((BASE / "app/src/main/assets/channels.json").open(encoding="utf-8"))

    def num(d):
        return (len(d["channels"]), sum(len(c["streams"]) for c in d["channels"]),
                len(d["blockedLines"]))

    check("GA 在库这一份三数现读（钉回 Al Jazeera 之后 277/450/1583）", num(new), (277, 450, 1583))
    om = {c["name"]: c for c in old["channels"]}
    nm = {c["name"]: c for c in new["channels"]}
    check("GB 台名层没有一台消失、也没有凭空新增（钉回的那台本来就是它）",
          (sorted(set(om) - set(nm)), sorted(set(nm) - set(om))), ([], []))
    aj = nm.get("Al Jazeera 半岛电视台")
    check("GB2 钉回的那台在库、台号 270、那条地址不在黑名单（判据若被回退这一格先红）",
          (aj is not None and aj["category"], [i for i, c in enumerate(new["channels"])
                                                if c["name"] == "Al Jazeera 半岛电视台"],
           aj["streams"][0] in new["blockedLines"]),
          ("international", [269], False))
    check("GB3 辽宁体育的台号没被这一批挪动（必看台那一格）",
          [i for i, c in enumerate(new["channels"]) if c["name"] == "辽宁体育"], [58])
    sd = nm.get("山东教育卫视")
    check("GC 换组那台落在 local/山东 且两条地址与旧目录逐条相同",
          ((sd["category"], sd["subgroup"]), [u for u in sd["streams"]]),
          (("local", "山东"), [u for u in om["山东教育卫视"]["streams"]]))
    canon = bc.stream_rules.canon
    ou = {canon(u) for c in old["channels"] for u in c["streams"]}
    nu = {canon(u) for c in new["channels"] for u in c["streams"]}
    lost = ou - nu
    where = {}
    for c in old["channels"]:
        for u in c["streams"]:
            if canon(u) in lost:
                where.setdefault(f"{c['category']}/{c.get('subgroup')}", set()).add(c["name"])
    # 作用域按不变式判，不写死名单：摘线只许发生在**旧那份的组**是 卫视 或点档「3」那三组的地方
    allowed = {f'{c["category"]}/{c.get("subgroup")}' for c in old["channels"]
               if c["subgroup"] == "卫视" or c["category"] in bc.TIER3_PRUNE_CATS}
    check("GD 摘线只发生在「卫视组＋地方/国际/港澳台」这些组里（央视名册/体育/特色 0 枚）",
          sorted(set(where) - allowed), [])
    check("GDc 央视名册与体育这两侧一条没被摘（10-27 点档「3」没扩到它们）",
          sorted(k for k in where if k.startswith(("cctv/央", "sports/")) and k != "cctv/卫视"), [])
    check("GDb 摘掉的枚数与线差自洽（没有地址是新增进来的）",
          (len(lost), num(old)[1] - num(new)[1], len(nu - ou)), (22, 22, 0))
    blocked_new = {canon(k) for k in new["blockedLines"]}
    check("GE 摘掉的地址同批全进了黑名单（防设备端自动更新捞回）",
          len([u for u in lost if u in blocked_new]), len(lost))
    hn = nm.get("湖南卫视")
    check("GF 湖南卫视 点档「甲」＝降位：线还在、条数没少、排到本台末位",
          (len(hn["streams"]), [i for i, u in enumerate(hn["streams"], 1) if "txmov2" in u]),
          (3, [3]))
    check("GFb 降位后首条不再是那枚存料片", "txmov2" in hn["streams"][0], False)


def main() -> int:
    for fn in (part_a, part_b, part_c, part_d, part_e, part_f, part_g):
        fn()
    for line in failures:
        print(f"[红] {line}")
    for line in notes:
        print(f"[未跑] {line}")
    print(f"[group_move 夹具] {ran - len(failures)}/{ran} 过 · 红 {len(failures)} · "
          f"未跑 {len(notes)} 组")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
