"""把「全线路回测 3 次」的几份 line_health.jsonl 汇总成一条判据输入。

用户 10-24 18:3x：央视·卫视、地方频道全线路回测 3 次，**三次都不通且没有本台台标**
才删线路。这个工具只算数：每条地址在几轮里通了、几轮没通、每轮各是什么结论。
「有没有台标」不在这一层判——那是构建侧 `logo_verified()` 的事，两边都读同一份台账。

    python3 tools/retest3_summary.py tools/.cache/retest3/r1 tools/.cache/retest3/r2 \
        tools/.cache/retest3/r3 --out tools/.cache/retest3/summary.json

输出形如：{"rounds": 3, "fail_all": [...], "urls": {"<canon>": {"ok": 0, "fails": 3, "verdicts": [...]}}}
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stream_rules  # noqa: E402

GOOD = "可取到分片"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rounds", nargs="+", type=Path, help="每轮一个目录（内含 line_health.jsonl）")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    tally = {}
    for index, folder in enumerate(args.rounds, 1):
        path = folder / "line_health.jsonl"
        if not path.exists():
            print(f"[缺件] 第 {index} 轮没有 {path} ⇒ 不汇总（宁可不出结论，也不拿两轮当三轮）")
            return 1
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            canon = stream_rules.canon(row.get("url") or "")
            if not canon:
                continue
            item = tally.setdefault(canon, {"ok": 0, "fails": 0, "verdicts": []})
            verdict = row.get("verdict") or "无结论"
            if verdict == GOOD:
                item["ok"] += 1
            else:
                item["fails"] += 1
            item["verdicts"].append(verdict)

    rounds = len(args.rounds)
    fail_all = sorted(k for k, v in tally.items() if v["fails"] >= rounds)
    result = {"rounds": rounds, "fail_all": fail_all, "urls": tally}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[汇总] {rounds} 轮、{len(tally)} 条地址；{rounds} 轮都没通 {len(fail_all)} 条 ⇒ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
