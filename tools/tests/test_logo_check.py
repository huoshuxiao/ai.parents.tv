#!/usr/bin/env python3
"""logo_check 的夹具：合成三帧，证明这把尺「认台标」而不是「认背景」；再加 verdict_logo 的分档闸。

格子：
  A 正对照：同字样的台标贴在不同背景上 ⇒ 必须 ≥ T_HI
  B 负对照：没有台标的画面 ⇒ 必须 < T_LO
  C 牙齿：模板转 180° 再匹配同一帧 ⇒ 必须明显低于 A
  D 边界：台标挪到右上角、按 tl 裁 ⇒ 不许把「左上没台标」判成有台标
  V 组（9＋3 格，纯分数不碰像素）：verdict_logo 的六档分档、两格「不许把没证据说成认定」的牙齿、
     一档名单闸（分档名必须全在 LOGO_BUCKETS 里，且六档全被踩到）
  P 组（6 格）：台账那句「台标相符（左上…＋右上…）」只认第一个子句——后半句是别的屏上元素；
     两格牙齿（P2／P6）挡"把新解析改成一律返回左上"这类偷懒
  Q 组（5 格）：模板只吃说了同一角的那几帧；角标并列时按用户裁决优先左上（并列里没有左上才退回台账先后），
     且四个 PYTHONHASHSEED 下必须定同一个角（旧写法 max(set(...)) 由哈希随机化决定）
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import logo_check as lc  # noqa: E402

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
OUT = ROOT / "tools/.cache/cctv3/fixture"


def frame(path, bg, text_xy=None, text="CCTV 11", shape=False):
    im = Image.new("RGB", (1280, 720), bg)
    d = ImageDraw.Draw(im)
    if shape:
        d.ellipse((700, 300, 1100, 650), fill=(240, 210, 180))
        d.rectangle((100, 400, 500, 700), fill=(30, 120, 60))
    if text_xy:
        font = ImageFont.truetype(FONT, 64)
        d.text(text_xy, "CCTV", font=font, fill=(245, 245, 245))
        d.text((text_xy[0] + 190, text_xy[1] + 8), "11", font=font, fill=(245, 245, 245))
        d.text((text_xy[0] + 20, text_xy[1] + 76), "戏曲", font=ImageFont.truetype(FONT, 44),
               fill=(235, 235, 235))
    im.save(path, quality=94)
    return path


def verdict_cells():
    """verdict_logo 的六档夹具（纯分数，不碰像素）＋两格牙齿。

    这两格牙齿各挡一个 10-25 真犯过的错：
      V6 挡「本台没有模板也被报成别的台」——单边比较造出来的指控；
      V3 挡「四枚模板同时过线还被报成某一枚」——把"认不出是哪一台"冒充成"认出了是别一台"。
    V10 是尺子自身的牙：分档名必须落在 LOGO_BUCKETS 名单里，写错一个字就会多出一档没人数的读数。
    """
    HI, LO = lc.T_HI, lc.T_LO            # 0.45 / 0.30
    cases = [
        ("V1 本台过线", {"甲": HI + 0.05, "乙": 0.1}, "甲", "本台台标在"),
        ("V2 本台掉线＋恰好一枚别台过线", {"甲": LO - 0.2, "乙": HI + 0.05}, "甲", "别的台"),
        ("V3 两枚别台同时过线＝撞车", {"甲": LO - 0.2, "乙": 0.50, "丙": 0.46}, "甲", "台标家族撞车"),
        ("V4 全场都低", {"甲": 0.10, "乙": 0.05}, "甲", "没认出任何台标"),
        ("V5 本台在灰区（没掉到 T_LO 以下）", {"甲": (LO + HI) / 2, "乙": HI + 0.05}, "甲", "灰区"),
        ("V6 本台压根没模板", {"乙": 0.673}, "甲", "没有本台模板"),
        ("V7 只有本台一枚分数、别台全空", {"甲": 0.10}, "甲", "没认出任何台标"),
        ("V8 本台过线优先于别台更高", {"甲": HI + 0.01, "乙": 0.9}, "甲", "本台台标在"),
        ("V9 边界：别台恰好 == T_HI", {"甲": LO - 0.01, "乙": HI}, "甲", "别的台"),
    ]
    cells, bad_names = [], set()
    for name, scores, own, want in cases:
        got, other_name, other = lc.verdict_logo(scores, own)
        cells.append((f"{name} ⇒ {want}", got == want, f"判成「{got}」，最像 {other_name} {other}"))
        bad_names.add(got)
    cells.append(("V3 撞车那一格不许只报一枚台名",
                  "等 2 枚" in lc.verdict_logo({"甲": 0.1, "乙": 0.50, "丙": 0.46}, "甲")[1],
                  lc.verdict_logo({"甲": 0.1, "乙": 0.50, "丙": 0.46}, "甲")[1]))
    cells.append(("V6 没有本台模板那一格也不许报成认定",
                  "不算认定" in lc.verdict_logo({"乙": 0.673}, "甲")[1],
                  lc.verdict_logo({"乙": 0.673}, "甲")[1]))
    unknown = sorted(bad_names - set(lc.LOGO_BUCKETS))
    cells.append(("V10 分档名全在 LOGO_BUCKETS 名单里", not unknown,
                  f"名单 {len(lc.LOGO_BUCKETS)} 档／实出现 {sorted(bad_names)}"
                  + (f"／名单外 {unknown}" if unknown else "")))
    never = sorted(set(lc.LOGO_BUCKETS) - bad_names)
    cells.append((f"V11 名单里 {len(lc.LOGO_BUCKETS)} 档全被这九格踩到", not never,
                  "全踩到" if not never else f"没人踩过的档 {never}"))
    return cells


def parser_cells():
    """台账那句话里「＋右上／＋右下」说的是别的屏上元素，模板不许跟着它走。

    10-25 真事：浙江卫视三条台账被旧解析读成 tr／br／tl 三种，同一帧本台分数
    在 0.044~0.736 之间抖 ⇒ 一把不定音的尺报了 2 格「台标与频道不一致」，差点改挂。
    """
    cases = [
        ("P1 左上本台标＋右上新闻角标 ⇒ tl",
         lc.corner_of_verdict('台标相符（左上角「浙江卫视」＋右上「中国蓝新闻」）'), "tl"),
        ("P2 牙齿：真·右上角标不许被读成左上",
         lc.corner_of_verdict('台标相符（右上角「湖南卫视」）'), "tr"),
        ("P3 右下角标 ⇒ br",
         lc.corner_of_verdict('台标相符（右下角「CGTN الإخبار」）'), "br"),
        ("P4 只写「台标相符」⇒ 默认左上", lc.corner_of_verdict('台标相符'), "tl"),
        ("P5 左上＋右下剧名角标 ⇒ tl",
         lc.corner_of_verdict('台标相符（左上角蓝色「浙江卫视」＋右下剧名角标《掌心》）'), "tl"),
        ("P6 牙齿：角标位置写在后半句也不算",
         lc.corner_of_verdict('台标相符（「浙江卫视」，右上角那个是中国蓝新闻）'), "tl"),
    ]
    return [(n, got == want, f"读到 {got}，应为 {want}") for n, got, want in cases]


def template_cells(f_tl_a, f_tl_b, f_tr):
    """模板只吃「说了同一角」的那几帧；并列时按台账先后定角，且跨进程必须一字不差。"""
    def rows(*specs):
        return [{"name": "甲", "frame": str(f), "logo": lg, "url": None} for f, lg in specs]

    t = lc.build_templates(rows((f_tl_a, '台标相符（左上角「甲」）'),
                                (f_tl_b, '台标相符（左上角「甲」）'),
                                (f_tr, '台标相符（右上角「甲」）')))
    cells = [
        ("Q1 模板只取同一角的两帧（右上角那帧不许混进来）",
         t["甲"]["n_sources"] == 2 and str(f_tr) not in t["甲"]["sources"],
         f'n_sources={t["甲"]["n_sources"]}，来源 {[os.path.basename(s) for s in t["甲"]["sources"]]}'),
        ("Q2 多数角优先 ⇒ tl", t["甲"]["loc"] == "tl", t["甲"]["loc"]),
        ("Q3 locs_seen 如实记全部角", t["甲"]["locs_seen"] == {"tl": 2, "tr": 1}, str(t["甲"]["locs_seen"])),
    ]
    tie = rows((f_tr, '台标相符（右下角「甲」）'), (f_tr, '台标相符（右下角「甲」）'),
               (f_tl_a, '台标相符（左上角「甲」）'), (f_tl_b, '台标相符（左上角「甲」）'))
    cells.append(("Q4 并列时按用户裁决取左上（br 2 枚 vs tl 2 枚 ⇒ tl）", lc.dominant_loc(tie) == "tl", lc.dominant_loc(tie)))
    # Q5 跨进程确定性：旧写法 max(set(locs), key=locs.count) 的并列裁决受 PYTHONHASHSEED 控制。
    # 形状取浙江卫视台账的真形状（左上／右上／右下各一枚＝三档并列）：
    # 2 枚对 2 枚那种两档并列在实测里没抖出来（旧码 4 个 seed 都给 br）⇒ 牙齿要落在三档这一支。
    child = ("import sys;sys.path.insert(0,%r);import logo_check as lc;"
             "rows=[{'logo':'台标相符（右上角 x）'},{'logo':'台标相符（右下角 x）'},{'logo':'台标相符（左上 x）'}];"
             "print(lc.dominant_loc(rows))" % str(ROOT / "tools"))
    seen = []
    for seed in ("0", "1", "2", "7"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        seen.append(subprocess.run([sys.executable, "-c", child], env=env,
                                   capture_output=True, text=True).stdout.strip())
    cells.append(("Q5 四个 PYTHONHASHSEED 下三档并列定同一个角（左上）", len(set(seen)) == 1 and seen[0] == "tl",
                  f"seed 0/1/2/7 → {seen}"))
    return cells


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t1 = frame(OUT / "tpl_a.jpg", (90, 95, 100), (90, 60))
    t2 = frame(OUT / "tpl_b.jpg", (120, 80, 60), (110, 55))
    pos = frame(OUT / "pos.jpg", (40, 110, 150), (95, 62))
    neg = frame(OUT / "neg.jpg", (30, 90, 60), None, shape=True)
    tr = frame(OUT / "tr.jpg", (60, 60, 90), (940, 60))
    tr2 = frame(OUT / "tr2.jpg", (90, 40, 70), (936, 62))

    loc = "tl"
    tpl = np.median(np.stack([lc.corner(lc.edge_map(lc.load_gray(str(p))), loc) for p in (t1, t2)]), axis=0)
    s_pos = lc.score_frame(str(pos), tpl, loc)
    s_neg = lc.score_frame(str(neg), tpl, loc)
    s_flip = lc.score_edges(lc.corner(lc.edge_map(lc.load_gray(str(pos))), loc), np.rot90(tpl, 2))
    s_tr = lc.score_frame(str(tr), tpl, loc)

    # 右上角那一套模板：用 tr 建、拿 tr2 验（不同背景、位置差 4/2 像素），避免自匹配冒充泛化。
    tpl_tr = lc.corner(lc.edge_map(lc.load_gray(str(tr))), "tr")
    s_tr_ok = lc.score_frame(str(tr2), tpl_tr, "tr")
    s_tl_via_tr = lc.score_frame(str(pos), tpl_tr, "tr")
    s_neg_via_tr = lc.score_frame(str(neg), tpl_tr, "tr")

    frames_jsonl = OUT / "frames_multi_corner.jsonl"
    frames_jsonl.write_text("\n".join(json.dumps({"channel": "甲", "frame": str(p)}, ensure_ascii=False)
                                      for p in (pos, tr2, neg)) + "\n", encoding="utf-8")
    both = lc.build_templates([
        {"name": "甲", "frame": str(t1), "logo": '台标相符（左上角「甲」）', "url": None},
        {"name": "甲", "frame": str(t2), "logo": '台标相符（左上角「甲」）', "url": None},
        {"name": "甲", "frame": str(tr), "logo": '台标相符（右上角「甲」）', "url": None},
    ])["甲"]
    run_out = OUT / "program_multi_corner.jsonl"
    lc.run_frames({"甲": both}, frames_jsonl, run_out)
    run_rows = [json.loads(x) for x in run_out.read_text(encoding="utf-8").splitlines() if x.strip()]
    by_frame = {os.path.basename(r["frame"]): r for r in run_rows}

    # 附加单帧模板（人工看过的那一帧）只许加分、不许减分：基线只有左上模板时右上帧不算有，
    # 附上人工那枚右上模板后必须算有；同一张左上帧的分数不许被附加模板拉低。
    base_only = lc.build_templates([
        {"name": "甲", "frame": str(t1), "logo": '台标相符（左上角「甲」）', "url": None},
        {"name": "甲", "frame": str(t2), "logo": '台标相符（左上角「甲」）', "url": None},
    ])["甲"]
    with_extra = dict(base_only)
    with_extra["extras"] = lc.extra_templates(
        [{"name": "甲", "frame": str(tr), "logo": '台标相符（右上角「甲」）', "url": None}])["甲"]
    out_base, out_extra = OUT / "program_base.jsonl", OUT / "program_extra.jsonl"
    lc.run_frames({"甲": base_only}, frames_jsonl, out_base)
    lc.run_frames({"甲": with_extra}, frames_jsonl, out_extra)
    base_by = {os.path.basename(r["frame"]): r for r in
               (json.loads(x) for x in out_base.read_text(encoding="utf-8").splitlines() if x.strip())}
    extra_by = {os.path.basename(r["frame"]): r for r in
                (json.loads(x) for x in out_extra.read_text(encoding="utf-8").splitlines() if x.strip())}

    cells = [
        ("A 正对照 ≥ T_HI", s_pos >= lc.T_HI, f"{s_pos:.3f} (T_HI={lc.T_HI})"),
        ("B 负对照 < T_LO", s_neg < lc.T_LO, f"{s_neg:.3f} (T_LO={lc.T_LO})"),
        ("C 牙齿：旋转模板更低", s_flip < s_pos - 0.2, f"rot={s_flip:.3f} vs {s_pos:.3f}"),
        ("D1 右上模板对另一张右上帧仍判有", s_tr_ok >= lc.T_HI, f"{s_tr_ok:.3f}（背景不同、位置差 4/2 px）"),
        ("D2 左上模板对右上帧不判有（所以必须按角建模板）", s_tr < lc.T_HI, f"{s_tr:.3f}"),
        ("D3 右上模板对左上帧不判有（不许拿角标模板乱认）", s_tl_via_tr < lc.T_HI, f"{s_tl_via_tr:.3f}"),
        ("D4 右上模板对无台标帧 < T_LO", s_neg_via_tr < lc.T_LO, f"{s_neg_via_tr:.3f}"),
        ("E1 一个频道同时有左上/右上两套模板", set(both["templates"]) == {"tl", "tr"}, str(sorted(both["templates"]))),
        ("E2 run_frames：左上帧按 tl 判有", by_frame["pos.jpg"]["check"] == "有台标" and by_frame["pos.jpg"]["loc"] == "tl",
         f'{by_frame["pos.jpg"]["check"]} loc={by_frame["pos.jpg"]["loc"]} {by_frame["pos.jpg"]["score"]}'),
        ("E3 run_frames：右上帧按 tr 判有", by_frame["tr2.jpg"]["check"] == "有台标" and by_frame["tr2.jpg"]["loc"] == "tr",
         f'{by_frame["tr2.jpg"]["check"]} loc={by_frame["tr2.jpg"]["loc"]} {by_frame["tr2.jpg"]["score"]}'),
        ("E4 run_frames：无台标帧不许判有", by_frame["neg.jpg"]["check"] != "有台标",
         f'{by_frame["neg.jpg"]["check"]} {by_frame["neg.jpg"]["score"]}'),
        ("F1 对照组：只有左上模板时，右上帧不算有", base_by["tr2.jpg"]["check"] != "有台标",
         f'{base_by["tr2.jpg"]["check"]} {base_by["tr2.jpg"]["score"]}'),
        ("F2 附加一枚人工右上模板后，右上帧判有", extra_by["tr2.jpg"]["check"] == "有台标"
         and extra_by["tr2.jpg"]["loc"] == "tr",
         f'{extra_by["tr2.jpg"]["check"]} loc={extra_by["tr2.jpg"]["loc"]} {extra_by["tr2.jpg"]["score"]}'),
        ("F3 附加模板不许把原来判得的分数拉低（取最大值）",
         float(extra_by["pos.jpg"]["score"]) >= float(base_by["pos.jpg"]["score"]) - 1e-9,
         f'{base_by["pos.jpg"]["score"]} → {extra_by["pos.jpg"]["score"]}'),
        ("F4 附加模板对无台标帧仍不许判有", extra_by["neg.jpg"]["check"] != "有台标",
         f'{extra_by["neg.jpg"]["check"]} {extra_by["neg.jpg"]["score"]}'),
    ]
    cells += verdict_cells()
    cells += parser_cells()
    cells += template_cells(t1, t2, tr)
    bad = 0
    for name, ok, detail in cells:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")
        bad += 0 if ok else 1
    print(f"[logo 夹具] {len(cells) - bad}/{len(cells)} 过 · 红 {bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
