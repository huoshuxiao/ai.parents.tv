#!/usr/bin/env python3
"""审「台标相符」这批证据本身有没有问题：一台的画面被别的台名认领、结论没有可复核的帧、两份台账互相打脸。

为什么要有这个文件：10-24「保留有台标的线路」这条目标里，三轮审计都是在 `tools/.cache/` 下现写的脚本
（扫同址共用帧、扫无机器登记、台账 vs 人工看图），跑完就留在缓存里、换台机器就没了，下一批没人重跑。
这里把三把尺收进仓库，`--strict` 直接给退出码，落盘那一步可以把它当一道闸。

三把尺各挡一种说谎：
  A 同一枚帧被 ≥2 个台名认领 —— 一枚画面只可能是一个台的台标，共用即至少一枚是假证据（安多卫视／
    嘉兴新闻综合／沐川综合 三对就是这么抓出来的；同体重名条目单独计数，不算配错）。
  B 「台标相符」却没有任何机器帧登记过的地址 —— 「保留」只靠一句手抄结论，是最薄的一层。
  C 台账与人工看图对同一 (台名,地址) 判得不一样 —— 台账那一枚在构建侧排在前面，人工那句会被无声吃掉。
只读：不写台账、不写资产、不写判据。
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as B  # noqa: E402

LEDGER = BASE / "tools/.cache/linecheck/identity_review.jsonl"
REVIEW_GLOB = "tools/.cache/gate/round_*/review.json"
SHIPPED = BASE / "app/src/main/assets/channels.json"


def _md5(path: str) -> str:
    return hashlib.md5(Path(path).read_bytes()).hexdigest()[:12]


def machine_frames() -> tuple[dict, dict]:
    """(canon 地址 -> {台名: {帧 md5}})，(canon 地址 -> 有没有被登记过)。"""
    per = collections.defaultdict(lambda: collections.defaultdict(set))
    for f in glob.glob(str(BASE / "tools/.cache/**/line_frames.jsonl"), recursive=True):
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            row = json.loads(line)
            url = B.stream_rules.canon(row.get("url") or "")
            fr = (row.get("frame") or "").strip()
            if not url or not fr or not Path(fr).is_file():
                continue
            per[url][row.get("channel") or row.get("name") or ""].add(_md5(fr))
    return per, {u: True for u in per}


def human_reviews() -> dict:
    """(台名, canon) -> [(轮次, logo 文字)]，只收写得下结论的行。"""
    out = collections.defaultdict(list)
    for p in sorted(glob.glob(str(BASE / REVIEW_GLOB))):
        round_name = Path(p).parent.name
        for row in json.loads(Path(p).read_text(encoding="utf-8")).get("reviews", []):
            logo = (row.get("logo") or "").strip()
            name = row.get("name") or row.get("channel") or ""
            url = B.stream_rules.canon(row.get("url") or "")
            if logo and name and url:
                out[(name, url)].append((round_name, logo))
    return out


def shipped_pairs() -> set:
    data = json.loads(Path(SHIPPED).read_text(encoding="utf-8"))
    return {(c["name"], B.stream_rules.canon(s)) for c in data["channels"] for s in c["streams"]}


_LEDGER_EVIDENCE: dict[str, str] = {}


def _evidence_has_frame(key: str) -> bool:
    """台账那一行的 evidence 里是不是写着一枚现存在盘上的帧。"""
    import re
    text = _LEDGER_EVIDENCE.get(key) or ""
    for m in re.finditer(r"(\S+\.(?:jpg|jpeg|png))", text):
        path = Path(m.group(1))
        if not path.is_absolute():
            path = BASE / path
        if path.is_file():
            return True
    return False


def analyse(identity: dict, per_url: dict, human: dict, shipped: set) -> dict:
    good = {k: v for k, v in identity.items() if v in B.IDENTITY_GOOD}

    shared = []
    for url, names in per_url.items():
        uniq = sorted(n for n in names if n)
        if len(uniq) < 2:
            continue
        common = set.intersection(*[names[n] for n in uniq])
        if common:
            # 「同体重名条目」（永城新闻/永城综合、浙江钱江/浙江钱江都市）共用同一枚帧是正常的；
            # 判据只看两个台名前两个字是否相同——够区分"同一个台的两个名字"与"两个不同的台"，
            # 误判的代价只是把它列进"同体重名"，人工扫一眼清单就能看出。
            same_body = all(n[:2] == uniq[0][:2] for n in uniq)
            shared.append((url, uniq, sorted(common), same_body))

    # B 问的是"这条结论还能不能复核"：机器登记过帧算有；台账 evidence 里写着一枚**还在盘上**的帧也算有
    # （10-24 17:1x 那批就是这么补的）。两样都没有才是只剩一句话。
    no_machine = []
    for k in good:
        url = k.split("|", 1)[1]
        if url in per_url:
            continue
        ev = ""
        try:
            ev = str(good.get(k) or "")
        except AttributeError:
            pass
        if _evidence_has_frame(k):
            continue
        no_machine.append((k, (k in shipped)))

    conflicts = []
    # 注意键的形状：台账是 "台名|地址" 字符串，人工看图那份是 (台名, 地址) 元组。
    # 直接拿两个集合求交集永远是空 ⇒ C 会恒为 0（10-24 第一版就是这么空转的，selfcheck 第一臂当场抓到）。
    for key in identity:
        name, url = key.split("|", 1)
        if (name, url) not in human:
            continue
        logos = [lg for _r, lg in human[(name, url)]]
        agrees = [lg for lg in logos if lg.startswith("台标相符")]
        against = [lg for lg in logos if "别的台" in lg]
        if identity[key] in B.IDENTITY_GOOD and against and not agrees:
            conflicts.append(("台账相符·人工说别的台", key))
        elif identity[key] not in B.IDENTITY_GOOD and agrees:
            conflicts.append(("台账说不符·人工说相符", key))
    return {"good": good, "shared": shared, "no_machine": no_machine, "conflicts": conflicts}


def item_keys(res: dict) -> list[str]:
    """把三格读数压成一组可比对的键：「A|地址」「B|台名|地址」「C|台名|地址」。"""
    keys = []
    for url, _names, _common, same_body in res["shared"]:
        if not same_body:
            keys.append(f"A|{url}")
    for key, _in_ship in res["no_machine"]:
        keys.append(f"B|{key}")
    for _kind, key in res["conflicts"]:
        keys.append(f"C|{key}")
    return keys


def report(res: dict) -> int:
    real_bad = [s for s in res["shared"] if not s[3]]
    print(f"A 同址共用同一枚帧：{len(res['shared'])} 处（跨台 {len(real_bad)} / 同体重名 {len(res['shared']) - len(real_bad)}）")
    for url, names, common, same_body in res["shared"]:
        tag = "同体重名" if same_body else "跨台"
        print(f"   [{tag}] {names} 共用帧 {common} | {url[:70]}")
    in_cat = sum(1 for _k, s in res["no_machine"] if s)
    print(f"B 「台标相符」没有机器帧登记：{len(res['no_machine'])} 枚（其中此刻在目录里 {in_cat}）")
    for key, in_ship in res["no_machine"]:
        print(("   [在库] " if in_ship else "   [不在] ") + key.split("|", 1)[0] + " | " + key.split("|", 1)[1][:64])
    print(f"C 台账与人工看图互相打脸：{len(res['conflicts'])} 处")
    for kind, key in res["conflicts"]:
        print(f"   [{kind}] {key.split('|', 1)[0]} | {key.split('|', 1)[1][:64]}")
    return len(real_bad) + len(res["conflicts"])


def selfcheck(identity: dict, per_url: dict, human: dict, shipped: set) -> int:
    """负对照：往一份**副本**里造两种假证据，真函数必须各自抓到一个，否则这把尺在空转。"""
    base = analyse(identity, per_url, human, shipped)
    injected = dict(identity)
    # ① 造一枚「台账相符、人工说别的台」：挑一对人工写着别的台的 (台名,地址)
    pair = next((k for k, rows in human.items()
                 if any("别的台" in lg for _r, lg in rows) and k not in shipped), None)
    if pair is None:
        print("[selfcheck] 没有可用的「别的台」人工结论，无法造反例 —— 如实报，不假装通过")
        return 1
    injected[pair[0] + "|" + pair[1]] = "台标相符"
    got = analyse(injected, per_url, human, shipped)
    caught_c = len(got["conflicts"]) > len(base["conflicts"])
    print(f"[selfcheck] 注入「台账相符 vs 人工别的台」{pair[0]} ⇒ C 冲突 {len(base['conflicts'])} → {len(got['conflicts'])}，"
          f"抓到={caught_c}")
    # ② 造一枚「台标相符、从没被机器抽过帧」：挑一个机器登记里不存在的假地址
    fake = ("自检用假台", "http://audit.selfcheck.invalid/live/fake.m3u8")
    injected2 = dict(identity)
    injected2[f"{fake[0]}|{fake[1]}"] = "台标相符"
    got2 = analyse(injected2, per_url, human, shipped)
    caught_b = len(got2["no_machine"]) > len(base["no_machine"])
    print(f"[selfcheck] 注入「台标相符但无机器帧」{fake[0]} ⇒ B 计数 {len(base['no_machine'])} → {len(got2['no_machine'])}，"
          f"抓到={caught_b}")
    return 0 if (caught_c and caught_b) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", type=Path, default=LEDGER)
    ap.add_argument("--strict", action="store_true", help="有跨台共用或台账/人工冲突就退出 1")
    ap.add_argument("--baseline", type=Path, default=None,
                    help="已裁定条目清单（名单式基线）；只有基线之外的新条目才让 --strict 退出 1")
    ap.add_argument("--write-baseline", type=Path, default=None)
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    global _LEDGER_EVIDENCE
    identity = B.load_identity(args.identity)
    for line in args.identity.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            key = f"{row.get('name', '')}|{B.stream_rules.canon(row.get('url') or '') or row.get('canonical') or ''}"
            if row.get("evidence"):
                _LEDGER_EVIDENCE[key] = row["evidence"]
    per_url, _registered = machine_frames()
    human = human_reviews()
    shipped = shipped_pairs()
    print(f"[audit] 台账 {len(identity)} 键（台标相符 {sum(1 for v in identity.values() if v in B.IDENTITY_GOOD)}）"
          f"｜机器帧地址 {len(per_url)} 枚｜人工看图键 {len(human)}｜在库线 {len(shipped)}")
    if args.selfcheck:
        return selfcheck(identity, per_url, human, shipped)
    res = analyse(identity, per_url, human, shipped)
    keys = item_keys(res)
    if args.write_baseline:
        args.write_baseline.write_text(
            json.dumps({"note": "已逐条裁定过的条目；新出现的一条都不许静默放过",
                        "items": [{"key": k, "why": "见 CHANGELOG 对应节点"} for k in sorted(keys)]},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[audit] 基线已写 {args.write_baseline}（{len(keys)} 条）")
        return 0
    bad = report(res)
    baseline = set()
    if args.baseline and args.baseline.exists():
        baseline = {it["key"] for it in json.loads(args.baseline.read_text(encoding="utf-8")).get("items", [])}
        print(f"[audit] 基线 {args.baseline}：{len(baseline)} 条已裁定")
    new = [k for k in keys if k not in baseline]
    if new:
        print(f"[audit] 基线之外新出现 {len(new)} 条：")
        for k in new:
            print("   [新]", k[:120])
    if args.strict and new:
        print(f"[audit] --strict：{len(new)} 条新问题 ⇒ 退出 1")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
