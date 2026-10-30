"""把回测台账变成一份人看的名单（用户 10-29「回测＝漂移/卡顿」）。

用法：
  python3 tools/retest60_report.py --ledger tools/.cache/emu60_cctv_r1b_2020.jsonl/lines.jsonl \
      --out dist/retest60-20261029.md [--rounds 1] [--title ...]

判定与计数**复用** tv_retest_lines 里的 score_rows / verdict_for，不另造第二把尺：
同一份台账用工具和用脚本必须给出同一串数字，否则报告就成了自说自话。
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import tv_retest_lines as T  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", action="append", required=True, help="lines.jsonl（可重复给多份）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", type=int, default=1, help="这一份台账里有几轮读数（与跑的时候一致）")
    ap.add_argument("--title", default="央视·卫视 全线路回测（60 秒/条）")
    a = ap.parse_args(argv)

    rows = []
    for p in a.ledger:
        rows += T.rows_from_ledger(Path(p))
    if not rows:
        raise SystemExit("[拒绝] 台账里没有当前这把尺的行，报告会是空的")
    items = T.score_rows(rows, a.rounds)
    counts = collections.Counter(i["verdict"] for i in items)

    L = [f"# {a.title}\n",
         f"- 台账：{'、'.join('`%s`' % p for p in a.ledger)}",
         f"- 格子数：**{len(items)}**（台 {len({i['channel_id'] for i in items})} 个）",
         "- 判定分布：" + " · ".join(f"{k} **{v}**" for k, v in counts.most_common()),
         "",
         "## 一、有问题的格子（「真机播过」以外全部）\n",
         "| 台号 | 频道 | 线 | 判定 | 首帧/轮次 | 卡顿次数 | 卡顿毫秒 | 起播 ms |",
         "|---|---|---|---|---|---|---|---|"]
    bad = [i for i in items if i["verdict"] != "真机播过"]
    bad.sort(key=lambda i: (i["number"] or 0, i["line_index"] or 0))
    for i in bad:
        L.append("| %s | %s | %d | %s | %d/%d | %d | %d | %s |" % (
            i["number"], i["channel"], (i["line_index"] or 0) + 1,
            i["verdict"], i["hits"], i["rounds"], i["stalls"], i["stall_ms"],
            (i["zap_ms"][0] if i["zap_ms"] else "—")))
    L.append("")
    L.append("## 二、按台汇总\n")
    L.append("| 台号 | 频道 | 线数 | 播过 | 有问题 | 结论 |")
    L.append("|---|---|---|---|---|---|")
    by = collections.defaultdict(list)
    for i in items:
        by[(i["number"], i["channel"])].append(i)
    for (num, name), cells in sorted(by.items(), key=lambda kv: (kv[0][0] or 0)):
        ok = sum(1 for c in cells if c["verdict"] == "真机播过")
        nbad = len(cells) - ok
        verdict = "全线路播过" if nbad == 0 else ("全部没播过" if ok == 0 else "部分播过")
        L.append("| %s | %s | %d | %d | %d | %s |" % (num, name, len(cells), ok, nbad, verdict))
    L.append("")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[写盘] {out}  {len(items)} 格 / 问题 {len(bad)} 格")
    print("[判定分布] " + " · ".join(f"{k} {v}" for k, v in counts.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
