#!/usr/bin/env python3
"""线路识别工具：给一族「序号型」地址，从 {0}=1 逐个试到异常，报每条的**台标＋清晰度**。

用法（两条族一次扫）：
  python3 tools/line_id_probe.py \
      --template 'http://36.110.54.118:866/hls/{0}/index.m3u8' \
      --template 'http://61.138.82.196:808/hls/{0}/index.m3u8' \
      --out tools/.cache/line_id_20261027

三件事各用一把现成的尺（不在这里再造第二把）：
  · 有没有这条线 —— `health_probe.probe_one`（清单状态＋取一枚分片，404／连不上一眼可分）；
  · 清晰度 —— `probe_channels.capture_frames(native=True)`，jpg 的宽高就是这条流的真实尺寸；
  · 台标 —— `logo_check` 的角标模板跨台打分，报「最像哪几台＋分数」，
    并按 `logo_check` 的 T_HI／T_LO 分「有台标／不确定／疑似无台标」。

⚠️ 台标那一栏只挑候选、不定案：CCTV 与各省台角标长得像，两枚以上同时过线时这里写
「认到族认不到台」；每帧都落盘（`<out>/frames/`），定案要人眼看那一张。

停止规则：用户口径「从 1 开始，直到异常」⇒ 默认连续 1 个异常就停这一族（`--stop-after` 可调；
有些族中间断一两枚，要全扫就 `--stop-after 999 --max-index 60`）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import health_probe as hp  # noqa: E402
import logo_check as LC  # noqa: E402
import probe_channels as pc  # noqa: E402
import stream_rules  # noqa: E402
from PIL import Image  # noqa: E402

DEAD = {"清单404", "清单空", "清单无分片", "连不上", "连接超时", "响应超时", "拒绝访问",
        "服务端错误", "鉴权已过期", "证书错误", "容器不支持", "变体清单取不到", "清单是网页",
        "探测异常"}
HD_MIN_WIDTH = 1280   # 与构建里那条「只收原生 ≥1280 宽」同一把尺（build_catalog 的标清闸注释）


def hd_grade(width: int, height: int) -> str:
    """按帧的真实像素给档位；0×0 是「没量到」，不许读成标清。"""
    if not width or not height:
        return "未证（抽不到帧）"
    if max(width, height) >= HD_MIN_WIDTH:
        return "高清档"
    return "标清"


def corners_of(frame) -> dict:
    """一帧只解一次像素：左上／右上角的边图，后面跨模板打分都复用这两枚。"""
    try:
        edges = LC.edge_map(LC.load_gray(str(frame)))
    except Exception:  # noqa: BLE001 - 坏图交给「抽不到帧」那一格
        return {}
    return {loc: LC.corner(edges, loc) for loc in ("tl", "tr")}


def score_against(corners: dict, templates: dict) -> dict:
    """{频道名: 该台模板里的最高分}。模板可能带多个角位与人工附加单帧，取最大值。"""
    out = {}
    for name, tpl in templates.items():
        base = tpl.get("templates") or {tpl.get("loc"): tpl.get("template")}
        cands = [(loc, t) for loc, t in base.items() if t is not None]
        for e in tpl.get("extras") or []:
            if e.get("template") is not None:
                cands.append((e.get("loc", "tl"), e["template"]))
        best = None
        for loc, t in cands:
            corner = corners.get((loc or "tl")[:2])
            if corner is None:
                continue
            try:
                s = float(LC.score_edges(corner, t))
            except Exception:  # noqa: BLE001
                continue
            if best is None or s > best:
                best = s
        if best is not None:
            out[name] = best
    return out


def logo_readout(scores: dict) -> tuple[str, str]:
    """把跨模板分数写成两句人话：(台标那一栏, 分数明细)。

    分档口径与 `logo_check.verdict_logo` 一致：两枚以上同时 ≥T_HI ⇒ 认到族、认不到台。
    """
    if not scores:
        return "无模板可比", ""
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    over = [kv for kv in ranked if kv[1] >= LC.T_HI]
    tail = "、".join(f"{n} {s:.3f}" for n, s in ranked[:3])
    if len(over) >= 2:
        return f"认到族认不到台（{len(over)} 枚过线）", tail
    if len(over) == 1:
        return f"最像 {over[0][0]}", tail
    if ranked[0][1] >= LC.T_LO:
        return "不确定（没有单枚过线）", tail
    return "没认出任何台标（广告/黑屏/无角标）", tail


def frame_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(str(path)) as im:
            return int(im.width), int(im.height)
    except Exception:  # noqa: BLE001 - 读不出尺寸记 0，让下游按「未证」处理
        return 0, 0


def one_line(template: str, idx: int, out_dir: Path, templates: dict, catalog: dict,
             connect_timeout: float, read_timeout: float, grab_timeout: int) -> dict:
    url = template.format(idx)
    row = {"template": template, "index": idx, "url": url}
    try:
        probe = hp.probe_one(url, connect_timeout, read_timeout)
    except Exception as exc:  # noqa: BLE001 - 探针自己出错也算「异常」，但要写明是哪种
        row["probe_verdict"] = "探测异常"
        row["probe_detail"] = f"{type(exc).__name__}: {exc}"
        row["alive"] = False
        return row
    row["probe_verdict"] = probe.get("verdict")
    row["probe_detail"] = probe.get("detail")
    row["window"] = probe.get("asset")
    row["final_url"] = probe.get("final_url")
    row["alive"] = bool(probe.get("verdict")) and probe.get("verdict") not in DEAD
    if not row["alive"]:
        return row
    work = out_dir / "frames" / re.sub(r"[^0-9A-Za-z_.-]+", "_", url.split("://", 1)[-1])[:96]
    grab = pc.capture_frames(url, work, grab_timeout, native=True)
    frames = [f for f in (grab.get("frames") or []) if f.stat().st_size > 0]
    row["grab_ms"] = grab.get("elapsed_ms")
    if not frames:
        row["no_frame_reason"] = "抽不到帧（解不出容器／黑屏超时）" + ("（VLC 报超时）"
                                                                 if grab.get("timed_out") else "")
        return row
    frame = frames[len(frames) // 2]
    w, h = frame_size(frame)
    row["frame"] = str(frame)
    row["definition"] = f"{w}x{h}"
    row["grade"] = hd_grade(w, h)
    scores = score_against(corners_of(frame), templates)
    row["logo_top"] = {k: round(v, 3) for k, v in
                       sorted(scores.items(), key=lambda kv: -kv[1])[:5]}
    row["logo"], row["logo_scores"] = logo_readout(scores)
    hit = catalog.get(stream_rules.canon(url))
    row["in_catalog"] = f"{hit[0]} 线{hit[1]}" if hit else ""
    return row


def scan_family(template: str, start: int, max_index: int, stop_after: int, worker,
                on_row=None):
    """一族从 start 逐个试到异常。返回 (读数, 停下的序号, 为什么停)。纯逻辑，好单测。

    on_row 每枚调一次 ⇒ 一行十指分钟的工具能边跑边看，中途断了台账也留着已扫的那几枚。
    """
    rows = []
    dead_streak = 0
    idx = start
    while idx <= max_index:
        row = worker(template, idx)
        rows.append(row)
        if on_row:
            on_row(row)
        dead_streak = 0 if row.get("alive") else dead_streak + 1
        if dead_streak >= stop_after:
            return rows, idx, (f"第 {idx} 枚起连续 {dead_streak} 枚异常"
                               f"（末枚 {row.get('probe_verdict')}）")
        idx += 1
    tail = rows[-1].get("probe_verdict") if rows else "无"
    return rows, idx, (f"试满 --max-index {max_index} 没收口"
                       f"（末枚 {tail}，尾部已连续 {dead_streak} 枚异常；"
                       f"要接着扫抬 --max-index 或降 --stop-after）")


def summarize(rows: list[dict]) -> dict:
    """把扫完的读数汇总成六个数。分项之和必须能对上——
    上一版这里写成 `str(r.get('logo','').startswith(...))`，`str(False)` 也是非空串，
    当场把 2 枚数成 1 枚死线也计进去（夹具 E1/E2 就是钉这个的）。"""
    got_frame = [r for r in rows if r.get("frame")]
    out = {
        "scanned": len(rows),
        "alive": sum(1 for r in rows if r.get("alive")),
        "frames": len(got_frame),
        "logo": sum(1 for r in rows if str(r.get("logo") or "").startswith("最像")),
        "clash": sum(1 for r in rows if str(r.get("logo") or "").startswith("认到族")),
        "hd": sum(1 for r in got_frame if r.get("grade") == "高清档"),
        "sd": sum(1 for r in got_frame if r.get("grade") == "标清"),
        "ungraded": sum(1 for r in got_frame if r.get("grade") not in ("高清档", "标清")),
    }
    out["grade_sum_ok"] = out["hd"] + out["sd"] + out["ungraded"] == out["frames"]
    return out


def load_catalog_names(path: Path) -> dict:
    """canonical url -> (台名, 线号)：认出台标后要问一句「这条地址是不是已经在库里」。"""
    out = {}
    if not path or not path.exists():
        return out
    cat = json.loads(path.read_text(encoding="utf-8"))
    for ch in cat.get("channels") or []:
        for i, u in enumerate(ch.get("streams") or [], 1):
            out[stream_rules.canon(u)] = (ch["name"], i)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="序号型线路族：逐枚识别台标与清晰度，试到异常为止")
    ap.add_argument("--template", action="append", required=True, metavar="URL带{0}",
                    help="一族地址，{0} 换成序号；可重复给多个族")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--stop-after", type=int, default=1,
                    help="连续几个异常停这一族（默认 1＝「直到异常」）")
    ap.add_argument("--max-index", type=int, default=60, help="序号护栏，防止一直试下去")
    ap.add_argument("--out", type=Path, required=True,
                    help="落盘目录（必填：不给就拒绝起，防覆盖别的产品目录）")
    ap.add_argument("--connect-timeout", type=float, default=6.0)
    ap.add_argument("--read-timeout", type=float, default=8.0)
    ap.add_argument("--grab-timeout", type=int, default=25, help="VLC 抽帧等待秒数")
    ap.add_argument("--catalog", type=Path,
                    default=Path(__file__).resolve().parents[1] / "app/src/main/assets/channels.json")
    ap.add_argument("--review-glob", action="append",
                    help="台标模板来源（默认 tools/.cache/gate/round_*/review.json）")
    args = ap.parse_args(argv)

    bad = [t for t in args.template if "{0}" not in t]
    if bad:
        print("[拒绝] 这些模板里没有 {0}：" + "、".join(bad))
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    ledger = args.out / "line_id.jsonl"

    patterns = args.review_glob or ["tools/.cache/gate/round_*/review.json"]
    reviews = LC.load_reviews(patterns)
    templates = LC.build_templates(reviews)
    for name, rows_e in LC.extra_templates(reviews).items():
        templates.setdefault(name, {"loc": rows_e[0]["loc"], "template": rows_e[0]["template"],
                                    "templates": {}, "sources": [], "n_sources": 0,
                                    "locs_seen": {}})
        templates[name]["extras"] = rows_e
    print(f"[模板] review {len(reviews)} 行 → 频道模板 {len(templates)} 枚（来源 {patterns}）")
    if not templates:
        print("[警告] 一枚台标模板都没读到 ⇒ 台标那一栏会全空；"
              "这**不等于**这些线没有台标，是这把尺今天没眼睛")
    catalog = load_catalog_names(args.catalog)
    print(f"[库里] 现读 {len(catalog)} 枚地址（用来标这条地址是否已在目录里）")

    all_rows = []
    with ledger.open("w", encoding="utf-8") as fh:
        for template in args.template:
            print(f"\n[族] {template}")
            print("  序号 状态        清晰度      档     台标 / 分数")
            def emit(r: dict, fh=fh) -> None:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                fh.flush()
                all_rows.append(r)
                print(f"  {r['index']:>3} {(r['probe_verdict'] or '-'):<10} "
                      f"{(r.get('definition') or '-'):<11} {(r.get('grade') or '-'):<6} "
                      + (r.get("logo") or "—")
                      + (f"｜{r['logo_scores']}" if r.get("logo_scores") else "")
                      + (f"｜库里已有 {r['in_catalog']}" if r.get("in_catalog") else "")
                      + (f"｜{r['window']}" if r.get("window") else ""), flush=True)
                if r.get("no_frame_reason"):
                    print(f"        {r['no_frame_reason']}")

            rows, _, why = scan_family(
                template, args.start, args.max_index, args.stop_after,
                lambda tpl, idx: one_line(tpl, idx, args.out, templates, catalog,
                                          args.connect_timeout, args.read_timeout,
                                          args.grab_timeout), on_row=emit)
            alive = sum(1 for r in rows if r.get("alive"))
            print(f"[停] {why} ⇒ 这一族试了 {len(rows)} 枚（可取到分片 {alive} 枚）")

    s = summarize(all_rows)
    print(f"\n[汇总] 扫 {s['scanned']} 枚｜可取到分片 {s['alive']} 枚｜抽到帧 {s['frames']} 枚｜"
          f"台标认出 {s['logo']} 枚｜认到族认不到台 {s['clash']} 枚｜"
          f"分项之和 高清 {s['hd']} + 标清 {s['sd']} + 没档位 {s['ungraded']} = "
          f"{s['hd'] + s['sd'] + s['ungraded']}（要等于抽到帧 {s['frames']}）"
          + (" ✓" if s["grade_sum_ok"] else " ✗"))
    print("[口径] 台标那一栏是**候选**不是定案；「认到族认不到台」的那些要人眼看 "
          f"{args.out}/frames/ 里的原图。本工具只读不写目录、不碰黑名单")
    print(f"[落账] {ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
