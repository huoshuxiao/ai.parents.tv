#!/usr/bin/env python3
"""台标「有没有」的程序复核（人工复核之前的那一道）。

口径：只回答**这一帧左上/右上角有没有本频道那种台标**，不回答「是不是本台」。
做法：把人工判过「台标相符」的帧按频道取角标区，边图取中位数当模板；新帧同区域做
多尺度归一化互相关，取峰值。CCTV 各家角标长得像（都有「CCTV」字样），所以**跨频道的
高分不算错**——那些帧确实有台标；真正要找的是广告/黑屏/外景这种整块没有角标的帧。

人工复核只在这道之后做，且只复核本工具判「疑似无台标/不确定」的帧与一小撮「有台标」对照。

用法：
  python3 tools/logo_check.py --calibrate
  python3 tools/logo_check.py --frames tools/.cache/cctv3/r1/line_frames.jsonl \
      --out tools/.cache/cctv3/r1/program_check.jsonl
"""
import argparse
import collections
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.signal import fftconvolve

ROOT = Path(__file__).resolve().parents[1]
CANVAS = (1280, 720)
CROP_W, CROP_H = 0.30, 0.15
T_HI = 0.45   # ≥ 判「有台标」；已在已知无台标帧上校准（max 0.428）
T_LO = 0.30   # 低于此判「疑似无台标」
MAX_SHIFT = 12
SCALES = (0.94, 1.0, 1.06)

# 负对照只认**逐张看过**的帧（10-24 13:2x 看 tools/.cache/cctv3/negatives_sheet.jpg）：
# 六个里五个是广告/外景/烟花（整帧没有本台角标），第六个 CCTV-11 是用户点名的广告框。
# ⚠️ 不照抄 review.json 的「台标看不到」：其中 CGTN 西语 那条注释写「整帧黑」，但存的帧
# 明明是蓝底演播室、左上角「CGTN ESPAÑOL」——注释与帧对不上，照抄会把有台标的帧当负对照。
NEG_VERIFIED = [
    ("河北 4K", "tools/.cache/ev_out/prepack_1023c/frames/cctv-969f1e62f9/line0/frame_first.jpg"),
    ("CCTV-6 电影", "tools/.cache/ev_out/prepack_1023c/frames/cctv-638cd7b045/line0/frame_first.jpg"),
    ("河北卫视", "tools/.cache/gate/round_r11/frames/cctv-e54e6b5c0d/line0/frame_first.jpg"),
    ("CCTV-8 电视剧", "tools/.cache/gate/round_r13/frames/cctv-37ce6f88fe/line1/frame_first.jpg"),
    ("CCTV-7 国防军事", "tools/.cache/gate/round_r14/frames/cctv-36a66e8c4a/line5/frame_first.jpg"),
    ("CCTV-11 戏曲", "tools/.cache/gate/round_r43/frames/cctv-3949455821/line6/frame_first.jpg"),
]


def load_gray(path):
    im = Image.open(path).convert("L")
    return np.asarray(im.resize(CANVAS, Image.BILINEAR), dtype=np.float32)


def edge_map(a):
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = a[:, 2:] - a[:, :-2]
    gy[1:-1, :] = a[2:, :] - a[:-2, :]
    e = np.hypot(gx, gy)
    scale = np.percentile(e, 99) or 1.0
    return np.minimum(e / scale, 1.5)


def corner(a, loc):
    h, w = a.shape
    cw, ch = int(w * CROP_W), int(h * CROP_H)
    return {
        "tl": a[:ch, :cw],
        "tr": a[:ch, w - cw:],
        "br": a[h - ch:, w - cw:],
        "bl": a[h - ch:, :cw],
    }.get(loc, a[:ch, :cw])


CLAUSE_SEPS = ("＋", "+", "，", ",", "；", ";", "、", "/", "）", ")")


def corner_of_verdict(logo_text):
    """从人工那句「台标相符（…）」里取**本台台标所在的那一角**。

    只看第一个子句：台账 routinely 写成「台标相符（左上角「浙江卫视」＋右上「中国蓝新闻」）」，
    后半句说的是屏上另一个元素（新闻角标／剧名），跟着它走会把模板裁到错的那一角。
    10-25 实测：浙江卫视三条台账被读成 tr／br／tl 三种，同一帧的本台分数在 0.044~0.736 之间抖。
    """
    head = (logo_text or "")
    if head.startswith("台标相符"):
        head = head[len("台标相符"):]
    head = head.lstrip("（(")
    for sep in CLAUSE_SEPS:
        i = head.find(sep)
        if i >= 0:
            head = head[:i]
    head = head[:30]
    if "右上" in head:
        return "tr"
    if "右下" in head:
        return "br"
    if "左下" in head:
        return "bl"
    return "tl"


def dominant_loc(rows):
    """一个频道多条台账里出现最多的那一角；**并列时按用户的裁决优先左上**。

    旧写法 `max(set(locs), key=locs.count)` 在并列时把选择权交给 set 的遍历顺序，
    而字符串 set 的顺序受 PYTHONHASHSEED 控制 ⇒ 同一帧同一频道两次进程能差出 0.7 分
    （10-25 CGTN 阿语 2 枚右下＋2 枚左上＝并列，实测 seed 0~5 给出 br／tl／tr 三种模板）。
    用户 10-25 定档：并列按「台标在左上」——左上是本项目绝大多数台的角；并列里没有左上时，
    退回台账先后（同一份台账永远是同一个答案）。
    """
    locs = [corner_of_verdict(r["logo"]) for r in rows]
    counts = collections.Counter(locs)
    top = max(counts.values())
    winners = [k for k, v in counts.items() if v == top]
    if len(winners) == 1:
        return winners[0]
    if "tl" in winners:
        return "tl"
    return counts.most_common(1)[0][0]


def _ncc_peak(search, tpl):
    t = tpl - tpl.mean()
    tn = float(np.sqrt((t * t).sum())) + 1e-6
    num = fftconvolve(search, t[::-1, ::-1], mode="valid")
    k = np.ones_like(t)
    s1 = fftconvolve(search, k, mode="valid")
    s2 = fftconvolve(search * search, k, mode="valid")
    n = t.size
    den = np.sqrt(np.maximum(s2 - s1 * s1 / n, 1e-6)) * tn
    ncc = num / np.maximum(den, 1e-6)
    cy, cx = ncc.shape[0] // 2, ncc.shape[1] // 2
    sub = ncc[max(0, cy - MAX_SHIFT):cy + MAX_SHIFT + 1,
              max(0, cx - MAX_SHIFT):cx + MAX_SHIFT + 1]
    return float(np.nanmax(sub))


def score_edges(edges, tpl):
    best = -1.0
    for s in SCALES:
        t = tpl
        if s != 1.0:
            im = Image.fromarray(t.astype(np.float32))
            t = np.asarray(im.resize((max(8, int(t.shape[1] * s)), max(8, int(t.shape[0] * s))),
                                     Image.BILINEAR), dtype=np.float32)
        if t.shape[0] >= edges.shape[0] or t.shape[1] >= edges.shape[1]:
            continue
        best = max(best, _ncc_peak(np.pad(edges, ((MAX_SHIFT, MAX_SHIFT), (MAX_SHIFT, MAX_SHIFT))), t))
    return best


def score_frame(path, template, loc):
    return score_edges(corner(edge_map(load_gray(path)), loc), template)


def load_reviews(patterns):
    rows = []
    seen = set()
    for pat in patterns:
        for p in sorted(glob.glob(str(ROOT / pat))):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for r in d.get("reviews", []):
                f = r.get("frame") or ""
                path = f if os.path.isabs(f) else str(ROOT / f)
                if not os.path.isfile(path):
                    continue
                key = (r.get("name"), path)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"name": r.get("name"), "frame": path,
                             "logo": r.get("logo") or "", "url": r.get("url")})
    return rows


def build_templates(reviews):
    """按频道取人工判过「台标相符」的帧，角标区边图中位数当模板。

    只用**说了同一角**的那几帧：旧写法把该频道全部台账帧都按选中的那一角裁一遍，
    于是「右下角台标」那几帧的左上角（多半是背景）也被混进中位数里当模板（10-25）。
    """
    by = {}
    for r in reviews:
        if not r["name"] or not r["frame"] or not r["logo"].startswith("台标相符"):
            continue
        by.setdefault(r["name"], []).append(r)
    out = {}
    for name, all_rows in by.items():
        loc = dominant_loc(all_rows)
        locs_seen = dict(collections.Counter(corner_of_verdict(r["logo"]) for r in all_rows))
        rows = [r for r in all_rows if corner_of_verdict(r["logo"]) == loc]
        frames = []
        for r in rows[:6]:
            try:
                frames.append(corner(edge_map(load_gray(r["frame"])), loc))
            except Exception:
                continue
        if not frames:
            continue
        out[name] = {"loc": loc, "template": np.median(np.stack(frames), axis=0),
                     "sources": [r["frame"] for r in rows[:6]], "n_sources": len(frames),
                     "locs_seen": locs_seen}
    return out


def calibrate(templates, reviews):
    positives = []   # (channel, score) leave-one-out
    for name, rows in _group(reviews, "台标相符").items():
        loc = dominant_loc(rows)                      # 与 build_templates 同一个口径
        rows = [r for r in rows if corner_of_verdict(r["logo"]) == loc]
        frames = [r["frame"] for r in rows]
        if len(frames) < 2:
            continue
        edges = [corner(edge_map(load_gray(f)), loc) for f in frames]
        for i, e in enumerate(edges):
            tpl = np.median(np.stack([x for j, x in enumerate(edges) if j != i]), axis=0)
            positives.append((name, score_edges(e, tpl)))
    negatives = [(name, str(ROOT / rel)) for name, rel in NEG_VERIFIED if (ROOT / rel).is_file()]

    neg_scores = []
    for name, frame in negatives:
        best = -1.0
        for cname, t in templates.items():
            best = max(best, score_frame(frame, t["template"], t["loc"]))
        neg_scores.append((best, name, frame))

    pos_scores = [s for _, s in positives]
    neg_scores.sort(reverse=True)
    max_neg = neg_scores[0][0] if neg_scores else -1.0
    above = sum(1 for s in pos_scores if s >= T_HI)
    print(f"[calibrate] 正对照（留一法）{len(pos_scores)} 帧：min {min(pos_scores):.3f} "
          f"中位 {np.median(pos_scores):.3f} max {max(pos_scores):.3f}；≥{T_HI}: {above}/{len(pos_scores)}")
    print(f"[calibrate] 负对照 {len(neg_scores)} 帧：max {max_neg:.3f}（必须 < {T_HI}）")
    for s, name, frame in neg_scores[:5]:
        print(f"[calibrate]   负 {s:.3f} {name} {os.path.basename(os.path.dirname(os.path.dirname(frame)))}")
    # 牙齿：把模板转 180° 再匹配，分数必须显著掉下来 —— 否则这把 NCC 对什么都给高分。
    if positives:
        cands = [(n, [r for r in rs if corner_of_verdict(r["logo"]) == dominant_loc(rs)])
                 for n, rs in _group(reviews, "台标相符").items()]
        name, rows = next((n, rs) for n, rs in cands if len(rs) >= 2)
        loc = dominant_loc(rows)
        e = corner(edge_map(load_gray(rows[0]["frame"])), loc)
        tpl = np.median(np.stack([corner(edge_map(load_gray(r["frame"])), loc) for r in rows[1:4]]), axis=0)
        real = score_edges(e, tpl)
        flipped = score_edges(e, np.rot90(tpl, 2))
        print(f"[calibrate] 牙齿：真模板 {real:.3f} vs 旋转 180° 模板 {flipped:.3f}（真模板必须更高）")
        if flipped >= real:
            print("[calibrate] 判红：模板旋转后分数没掉 ⇒ 这把尺不可信")
            return 1
    if max_neg >= T_HI:
        print("[calibrate] 判红：已知无台标帧被程序判成「有台标」⇒ 会把该进人工的帧放过去")
        return 1
    return 0


def _group(reviews, prefix):
    by = {}
    for r in reviews:
        if r["logo"].startswith(prefix):
            by.setdefault(r["name"], []).append(r)
    return by


def classify(score):
    if score >= T_HI:
        return "有台标"
    if score < T_LO:
        return "疑似无台标"
    return "不确定"


# 「这一帧像不像它自称的那个台」的分档，只许这一处有：两把尺各写一遍，就会出现
# 「同一个地址一个说别的台、一个说灰区」那种没法交代的场面（10-25 已犯过一次正则数帧）。
LOGO_BUCKETS = ("别的台", "台标家族撞车", "没认出任何台标", "灰区", "本台台标在", "没有本台模板")


def verdict_logo(scores, own_name):
    """拿一帧（或一格里各帧取最高后的）跨模板分数，判它归谁。返回 (分档, 最像的别台, 那个分)。

    scores = {频道名: 分数}，own_name = 这条线自称的台。纯函数，不碰像素，所以两侧调用点
    （门禁的程序复核、卫视组的不一致探测）共用这一把尺。
    第二、三元素描述**分数最高的那一枚别台模板**；分档名不许被消费者忽略——
    「台标家族撞车」那一档给的名字带「等 N 枚」字样，就是不让它被当成"认出了是这一台"。

    分档的来由，两条都是 10-25 在真帧上量出来的、不是拍的：
      「台标家族撞车」＝≥2 枚别台模板同时 ≥ T_HI。延边卫视那条线的真帧上**四枚**模板同时过线
        （兵团 0.673／甘肃 0.569／青海 0.533／宁夏 0.494，都是"彩球＋白色一笔"那一族），
        而那一帧左上角写的就是「延边卫视」⇒ 分数只认得出"像哪一族"，认不出"是哪一台"。
        撞车时**不许**报成"是某某台"——那是把"认不出是哪一台"冒充成"认出了是别一台"。
      「没有本台模板」＝这一台从来没被人工认过标，没有 own 可比。单边比较无从谈起，
        旧写法把它连 own=None 一起塞进「别的台」，等于凭没有证据下一句指控。
    """
    others = sorted(((s, c) for c, s in scores.items() if c != own_name), reverse=True)
    if own_name not in scores:
        return ("没有本台模板",
                (f"{others[0][1]}（本台无模板，不算认定）" if others else ""),
                others[0][0] if others else None)
    own = scores[own_name]
    if own >= T_HI:
        return "本台台标在", "", (others[0][0] if others else None)
    over = [o for o in others if o[0] >= T_HI]
    if len(over) >= 2:
        return "台标家族撞车", f"{over[0][1]} 等 {len(over)} 枚（认到族、认不到台）", over[0][0]
    if own < T_LO and len(over) == 1:
        return "别的台", over[0][1], over[0][0]
    if not others:
        return "没认出任何台标", "", None
    if max(own, others[0][0]) < T_LO:
        return "没认出任何台标", others[0][1], others[0][0]
    return "灰区", others[0][1], others[0][0]


def run_frames(templates, frames_jsonl, out_path):
    rows = []
    for raw in open(frames_jsonl, encoding="utf-8"):
        raw = raw.strip()
        if not raw:
            continue
        report = json.loads(raw)
        frame = report.get("frame")
        if not frame or not os.path.isfile(frame):
            rows.append({**report, "check": "无帧", "score": None})
            continue
        t = templates.get(report.get("channel"))
        if not t:
            rows.append({**report, "check": "无模板", "score": None})
            continue
        s = score_frame(frame, t["template"], t["loc"])
        rows.append({**report, "check": classify(s), "score": round(s, 3)})
    with open(out_path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    print(f"[frames] {frames_jsonl} → {out_path}")
    print("[frames] 分布 " + " ".join(f"{k}:{v}" for k, v in Counter(r["check"] for r in rows).items()))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--frames")
    ap.add_argument("--out")
    ap.add_argument("--review-glob", action="append")
    args = ap.parse_args()
    patterns = args.review_glob or ["tools/.cache/gate/round_*/review.json"]
    reviews = load_reviews(patterns)
    templates = build_templates(reviews)
    print(f"[load] review 行 {len(reviews)}，模板 {len(templates)} 个频道")
    if args.calibrate:
        return calibrate(templates, reviews)
    if args.frames:
        if not args.out:
            print("[拒绝] --frames 必须带 --out（不默认覆盖任何文件）")
            return 2
        run_frames(templates, args.frames, args.out)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
