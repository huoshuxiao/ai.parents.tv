#!/usr/bin/env python3
"""验的是「序号型线路族识别工具」（用户 10-27 要的：`{0}` 从 1 试到异常，出台标＋清晰度）。

尺在 `tools/line_id_probe.py`，它自己不判删除：只把三把现成的尺（health_probe 的清单探针、
probe_channels 的原生抽帧、logo_check 的角标模板）串成一条流水线。本夹具盯六类会说谎的情形：

  P) 停止规则：「直到异常」到底停在哪一枚，尾部连续异常数要如实写进那句话；
     试满 --max-index 与「中途断一枚又活了」两种情形不许共用一句话。
  Q) 清晰度分档：0×0 是**没量到**，不许折成「标清」（折错了就等于给一条线判了构建里那道标清闸）。
  R) 台标分档：两枚以上同时过 T_HI ⇒ 只能说「认到族、认不到台」；一枚都没有模板可比时
     不许读成「没认出任何台标」（那是把尺子没眼睛说成源没台标）。
  S) 跨模板聚合：同一台的多角与人工附加单帧取**最大值**；角位不在左上/右上的模板不参与排名。
  E) 汇总数：分项之和必须等于「抽到帧」那一格；死线（没有台标字段）不许被数进「台标认出」——
     这一格钉的是我上一版写成的 `str(r.get('logo','').startswith(...))`：`str(False)`＝"False"
     也是非空串，会把每一枚都数进去（当场把 1 枚数成 2 枚）。
  T) 真帧正对照：拿人工判过「台标相符」的真帧（挑**不同台**的三张）走一遍本工具的识别路，
     top-1 必须是那一台，且每台自己的模板必须参评（漏了自己的模板＝静默少比，分数再高也没意义）。
     证据文件不在时打「未跑」并判红，不折成通过。

跑法：python3 tools/tests/test_line_id_probe.py    （退出码 0 才算过）
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import line_id_probe as LP  # noqa: E402
import logo_check as LC  # noqa: E402

failures: list[str] = []
ran = 0


def check(label: str, got, want) -> None:
    global ran
    ran += 1
    if got != want:
        failures.append(f"{label}: 得到 {got!r}，期望 {want!r}")


def alive(idx: int, ok: bool) -> dict:
    return {"index": idx, "alive": ok, "probe_verdict": "可取到分片" if ok else "清单404"}


def worker_from(seq: dict, default: bool = False):
    def w(template, idx):
        return alive(idx, seq.get(idx, default))
    return w


def part_p() -> None:
    rows, stop_at, why = LP.scan_family("t{0}", 1, 40, 1, worker_from({}, default=True))
    check("P1 全活⇒试满 max-index 而不是说「异常收口」", (len(rows), stop_at), (40, 41))
    check("P1b 那句话必须是「试满」口径", why.startswith("试满 --max-index 40"), True)

    rows, stop_at, why = LP.scan_family("t{0}", 1, 40, 1, worker_from({}, default=False))
    check("P1c 第 1 枚就异常⇒只试 1 枚就收口（不是空跑到底）", (len(rows), stop_at), (1, 1))
    check("P1d 那句话要点名第 1 枚", why.startswith("第 1 枚起连续 1 枚异常"), True)

    rows, stop_at, why = LP.scan_family("t{0}", 1, 40, 1, worker_from({1: True, 2: True, 3: False}))
    check("P2 第 3 枚异常即停（用户口径「直到异常」）", (len(rows), stop_at), (3, 3))
    check("P2b 那句话要点名第几枚与判词",
          why.startswith("第 3 枚起连续 1 枚异常（末枚 清单404）"), True)

    seq = {1: True, 2: False, 3: True, 4: True, 5: False, 6: False}
    rows, stop_at, why = LP.scan_family("t{0}", 1, 40, 2, worker_from(seq))
    check("P3 断一枚又活⇒不算收口（stop-after=2 要连续两枚）", (len(rows), stop_at), (6, 6))
    check("P3b 连续数如实写成 2", "连续 2 枚异常" in why, True)

    rows, stop_at, why = LP.scan_family("t{0}", 30, 31, 1, worker_from({30: True, 31: False}))
    check("P4 从 --start 起数（不是从 1）", (len(rows), stop_at), (2, 31))


def part_q() -> None:
    check("Q1 1920×1090 是高清档", LP.hd_grade(1920, 1090), "高清档")
    check("Q2 720×578 是标清", LP.hd_grade(720, 578), "标清")
    check("Q3 1280×720 落在闸上（长边≥1280 算高清）", LP.hd_grade(1280, 720), "高清档")
    check("Q4 0×0 是没量到，不许折成标清", LP.hd_grade(0, 0), "未证（抽不到帧）")


def part_r() -> None:
    hi, mid, lo = LC.T_HI, (LC.T_HI + LC.T_LO) / 2, LC.T_LO / 2
    check("R1 单枚过线⇒报出台名",
          LP.logo_readout({"湖北卫视": hi + 0.02, "贵州卫视": lo})[0], "最像 湖北卫视")
    check("R2 两枚过线⇒只认到族",
          LP.logo_readout({"A": hi + 0.1, "B": hi + 0.05})[0], "认到族认不到台（2 枚过线）")
    check("R3 一枚没过线但过 T_LO⇒不确定",
          LP.logo_readout({"A": mid, "B": lo})[0], "不确定（没有单枚过线）")
    check("R4 全在 T_LO 下⇒没认出任何台标",
          LP.logo_readout({"A": lo, "B": lo - 0.05})[0], "没认出任何台标（广告/黑屏/无角标）")
    check("R5 没有模板可比≠源没有台标", LP.logo_readout({})[0], "无模板可比")
    check("R6 分数明细按高低排到前三",
          LP.logo_readout({"A": 0.9, "B": 0.8, "C": 0.7, "D": 0.6})[1], "A 0.900、B 0.800、C 0.700")


def part_s() -> None:
    """跨模板聚合：把 LC.score_edges 换成确定性桩，只看「取最大／角位筛选／extras 只追加」这三件事。"""
    real = LC.score_edges
    LC.score_edges = lambda corner, tpl: float(tpl[0])        # noqa: E731
    try:
        corners = {"tl": 1, "tr": 1}
        templates = {
            "甲台": {"loc": "tl", "template": [0.40],
                     "templates": {"tl": [0.40], "tr": [0.55]}},
            "乙台": {"loc": "tl", "template": [0.70], "templates": {"tl": [0.70]}},
            "丙台": {"loc": "br", "template": [0.99], "templates": {"br": [0.99]}},
        }
        scores = LP.score_against(corners, templates)
        check("S1 同台多角取最大值", scores["甲台"], 0.55)
        check("S2 只挂在右下角的模板不参与排名（角位不在左/右上）", "丙台" in scores, False)
        check("S3 排名里最高的是乙台", max(scores, key=scores.get), "乙台")
        templates["乙台"]["extras"] = [{"loc": "tl", "template": [0.85]}]
        check("S4 人工附加单帧只追加、取最大（不会把已得的分冲掉）",
              LP.score_against(corners, templates)["乙台"], 0.85)
        templates["乙台"]["extras"] = [{"loc": "tl", "template": [0.10]}]
        check("S5 附加帧分低也不许拉低本台分",
              LP.score_against(corners, templates)["乙台"], 0.70)
    finally:
        LC.score_edges = real


def part_e() -> None:
    rows = [
        {"alive": True, "frame": "a.jpg", "grade": "高清档", "logo": "最像 湖北卫视"},
        {"alive": True, "frame": "b.jpg", "grade": "标清", "logo": "认到族认不到台（3 枚过线）"},
        {"alive": True, "frame": "c.jpg", "grade": "高清档", "logo": "不确定（没有单枚过线）"},
        {"alive": False, "probe_verdict": "清单404"},
        {"alive": True, "frame": "d.jpg", "grade": "未证（抽不到帧）"},
    ]
    s = LP.summarize(rows)
    check("E1 扫到的枚数（含死线）", s["scanned"], 5)
    check("E2 死线不许被数进「台标认出」", s["logo"], 1)
    check("E3 认到族单独一格", s["clash"], 1)
    check("E4 抽到帧只数有帧的", s["frames"], 4)
    check("E5 分项之和：高清2＋标清1＋没档位1＝抽到帧4", s["grade_sum_ok"], True)
    check("E6 没档位那一格必须被看见（不许静默少算）", s["ungraded"], 1)
    check("E7 空表不炸", LP.summarize([])["scanned"], 0)


def part_t() -> None:
    """真帧正对照：人工判过「台标相符」的帧，本工具的识别路要把它认成那一台。"""
    cand = []
    for p in sorted(glob.glob(str(BASE / "tools/.cache/gate/round_*/review.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for r in d.get("reviews") or []:
            f = r.get("frame") or ""
            name = r.get("channel") or r.get("name") or ""
            if name and str(r.get("logo", "")).startswith("台标相符") and Path(f).is_file():
                cand.append((name, f))
    if not cand:
        ran_note = "未跑（读不到任何「台标相符」的真帧证据）⇒ 这一格不算通过"
        print(f"  [T] {ran_note}")
        failures.append("T1 真帧正对照没跑成：" + ran_note)
        return
    reviews = LC.load_reviews(["tools/.cache/gate/round_*/review.json"])
    templates = LC.build_templates(reviews)
    for name, rows_e in LC.extra_templates(reviews).items():
        templates.setdefault(name, {"loc": rows_e[0]["loc"], "template": rows_e[0]["template"],
                                    "templates": {}, "sources": [], "n_sources": 0,
                                    "locs_seen": {}})
        templates[name]["extras"] = rows_e
    if not templates:
        failures.append("T2 模板库为空 ⇒ 真帧正对照无从谈起")
        return
    hit = 0
    tried = 0
    own_present = 0
    seen_names = set()
    picks = []
    for name, frame in cand:          # 尽量挑不同的台，别三张都是同一家的相邻帧
        if name in seen_names:
            continue
        seen_names.add(name)
        picks.append((name, frame))
        if len(picks) >= 3:
            break
    for name, frame in picks:
        tried += 1
        scores = LP.score_against(LP.corners_of(Path(frame)), templates)
        top = max(scores, key=scores.get) if scores else None
        hit += 1 if top == name else 0
        own_present += 1 if name in scores else 0
        print(f"  [T] {name} → 本工具最像 {top}（本台分 {scores.get(name, float('nan')):.3f}，"
              f"共 {len(scores)} 枚模板参评）")
    check(f"T3 {tried} 张不同台的真帧里至少 2 张认回本台（跨模板识别有牙）", hit >= 2, True)
    check("T4 每张真帧的本台模板都必须参评（漏了自己的模板＝静默少比）",
          (own_present, tried), (tried, tried))


def main() -> int:
    for fn in (part_p, part_q, part_r, part_s, part_e, part_t):
        fn()
    for line in failures:
        print(f"[红] {line}")
    print(f"[line_id_probe 夹具] {ran - len(failures)}/{ran} 过 · 红 {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
