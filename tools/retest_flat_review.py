#!/usr/bin/env python3
"""对 tools/.cache/retest3_flat 的平铺截图做程序复核（人工复核之前那一道）。

口径（用户 10-25）：
  · 有效线路 = 画面左上或右上角有本台台标；整块没有台标的（广告/外景/黑屏）= 无效。
  · 三轮都判「疑似无台标」的线路进删除候选；任一轮看到台标就保留。
  · 「无模板 / 无帧 / 灰区 / 台标不在左·右上」一律不删，进人工复核队列。

口径（用户 10-26 真机点档，比上一条更硬）：
  · 任意一轮命中「服务器超负载所致…可以微信扫码支持一下」那枚告警页指纹 → 这条线路直接删，
    不等满三轮、也不因别轮看到过台标而保留（这页是源站拿来顶位的画面，不是本台内容）。

用法：
  python3 tools/retest_flat_review.py --out tools/.cache/retest3_flat
产物（都与截图同目录，仍不建子目录）：
  review.json              每台每条线三格的判读 + 结论 + 是否要人工复核
  human_queue.json         程序判不了、要人逐张看的线路
  deletion_candidates.json 三轮都无台标的线路（旧名，只含 logo 一族）
  final_deletions.json     喂给 build_catalog.py --cctv3 的删除清单（logo ＋ overload_qr 两族）
"""
import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import logo_check  # noqa: E402
import prepack_report as PR  # noqa: E402

OUT = ROOT / "tools/.cache/retest3_flat"
SUMMARY = OUT / "summary.json"
PROGRAM_FRAMES = OUT / "program_frames.jsonl"
PROGRAM_RESULT = OUT / "program_check.jsonl"
REVIEW = OUT / "review.json"
CUTS = OUT / "deletion_candidates.json"


def qr_rounds_of(cells):
    """这一族只看「服务器超负载…微信扫码」那一枚指纹；别的占位页（测试卡/二维码推广）不算命中。"""
    return [c["round"] for c in cells if "服务器超负载" in str(c.get("fingerprint") or "")]


def qr_doc_cut_count(rows):
    """扫码页那一族有几条（投影与计数共用同一个谓词，不许两处各写一遍 `kind ==`）。"""
    return [r for r in rows if r.get("kind") == "overload_qr"]


def qr_only_projection(rows, generated_at, source_path=None, held_logo=0):
    """从 final_deletions 的 cut 里按 kind 投影出「只删服务器超负载扫码页」这一族。

    为什么单独一份：`logo` 那一族（三轮都无台标）按 10-24 的口径与待点档 #106 **还没定案**，
    整份喂给构建会替用户把没点档的那批一起删掉。这一份只带 `kind == "overload_qr"` 的行，
    形状与构建的 `load_cctv3` 认的那三种 kind 对齐（不认识的 kind 它自己会 `[warn]` 不收）。
    """
    cut = [dict(r) for r in rows if r.get("kind") == "overload_qr"]
    doc = {"kind": "overload_qr", "cut": cut, "held_logo_candidates": int(held_logo)}
    if generated_at:
        doc["generatedAt"] = generated_at
    if source_path is not None:
        sp = Path(source_path)
        try:
            doc["source"] = str(sp.relative_to(ROOT))
        except ValueError:
            doc["source"] = str(sp)
    doc["note"] = ("本批只落「服务器超负载…微信扫码」这一族（用户 10-26 真机点档）。"
                   "logo 一族是程序候选、未定案：按 10-24「保留有台标的线路」与待点档 #106，"
                   "要人工逐张看过才删。这一份是从 final_deletions.json 按 kind 投影出来的，不是手抄第二份名单。")
    return doc


def decide_verdict(cells, human_logo=""):
    """一条线三格 → 结论。判据本体抽成纯函数：夹具跑的就是这一条路，不是另写一份。

    同一份文件里两条硬度不同的规则：
      · 用户 10-26 真机点档：任一轮抽出「服务器超负载…微信扫码」告警页 ⇒ 删，
        别的轮看到过台标也不留（这页是源站拿来顶位的画面，不是本台内容）。
      · 用户 10-25：无台标要三轮都无才删，任一轮有台标就留。
    """
    checks = [c["check"] for c in cells]
    qr = qr_rounds_of(cells)
    if len(cells) < 3:
        verdict = "轮次不全，人工复核"
    elif qr:
        verdict = f"超负载扫码页（第 {'/'.join(str(r) for r in qr)} 轮命中，删）"
    elif all(c.startswith("疑似无台标") for c in checks):
        verdict = "三轮都无台标（删除候选）"
    elif any(c.startswith("有台标") for c in checks):
        verdict = "保留（至少一轮看到台标）"
    elif "无模板" in checks:
        verdict = "无本台模板，人工复核"
    elif "无帧" in checks:
        verdict = "有轮次没帧，人工复核"
    else:
        verdict = "灰区/不确定，人工复核"
    # 人工已经在这条线上写过「有台标」的，不再退回队列二次问人；扫码页那一族除外（10-26 更硬）。
    if (human_logo.startswith("有台标")
            and not verdict.startswith("保留") and not verdict.startswith("超负载扫码页")):
        verdict = "保留（人工看图有台标）"
    return qr, verdict


def main():
    global OUT, SUMMARY, PROGRAM_FRAMES, PROGRAM_RESULT, REVIEW, CUTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT),
                    help="回测那一轮的目录（默认 tools/.cache/retest3_flat）")
    args = ap.parse_args()
    OUT = Path(args.out)
    if not OUT.is_absolute():
        OUT = ROOT / OUT
    SUMMARY = OUT / "summary.json"
    PROGRAM_FRAMES = OUT / "program_frames.jsonl"
    PROGRAM_RESULT = OUT / "program_check.jsonl"
    REVIEW = OUT / "review.json"
    CUTS = OUT / "deletion_candidates.json"
    print(f"[out] 这一批复核读的是 {OUT.relative_to(ROOT)}", flush=True)
    if not SUMMARY.exists():
        print(f"[拒绝] 没有 {SUMMARY.relative_to(ROOT)}，先跑 tools/retest_flat.py")
        return 2
    data = json.loads(SUMMARY.read_text(encoding="utf-8"))
    # 台账是追加写的：摘掉/换过地址的旧线路会留在 summary 里。判读只针对「现在目录里的线」，
    # 否则已删除的旧线会继续出现在删除候选里、也会把分母撑大。
    catalog = json.loads((ROOT / "app/src/main/assets/channels.json").read_text(encoding="utf-8"))
    current = {(ch["id"], i, url)
               for ch in catalog["channels"] if ch.get("category") == "cctv"
               for i, url in enumerate(ch.get("streams") or [])}
    items = [it for it in data["items"]
             if (it["channel_id"], it["line_index"], it["url"]) in current]
    stale = len(data["items"]) - len(items)
    no_row = sorted(current - {(it["channel_id"], it["line_index"], it["url"]) for it in items})
    print(f"[filter] 目录 {len(current)} 条线 · 台账里已不在目录的 {stale} 条跳过 · "
          f"目录里没有台账的 {len(no_row)} 条", flush=True)
    reviews = logo_check.load_reviews(["tools/.cache/gate/round_*/review.json"])
    human_path = OUT / "human_review.json"
    human_rows = []
    if human_path.exists():
        h0 = json.loads(human_path.read_text(encoding="utf-8"))
        for r in h0.get("reviews") or []:
            logo = str(r.get("logo") or "")
            if logo.startswith("有台标相符"):
                logo = logo[1:]
            frame = r.get("frame")
            if logo.startswith("台标相符") and frame and (OUT / frame).is_file():
                human_rows.append({"name": r.get("channel"), "frame": str(OUT / frame),
                                   "logo": logo, "url": r.get("url")})
    # 用户 10-25 的口径是「台标在左上或右上」。回测人工台账里那几帧是人看过的，
    # 但它们**不并进中位数**（会把原有模板稀释，湖南卫视实测就是这么被误判的），
    # 而是各当一枚单帧模板附加：分数只可能升，不会把原来判得的判没。
    templates = logo_check.build_templates(reviews)
    extras = logo_check.extra_templates(human_rows)
    for name, rows_e in extras.items():
        templates.setdefault(name, {"loc": rows_e[0]["loc"], "template": rows_e[0]["template"],
                                    "templates": {}, "sources": [], "n_sources": 0,
                                    "locs_seen": {}})
        templates[name]["extras"] = rows_e
    human = {}
    human_round = {}
    if human_path.exists():
        for r in h0.get("reviews") or []:
            key = (r["channel"], int(r["line_index"]))
            human[key] = r
            if r.get("round") is not None:
                human_round[key + (int(r["round"]),)] = r
    print(f"[load] 人工台账 {len(reviews)} 行 · 模板 {len(templates)} 个频道 · "
          f"附加单帧模板 {sum(len(v) for v in extras.values())} 枚（回测人工 {len(human_rows)} 行）",
          flush=True)

    rows = []
    with PROGRAM_FRAMES.open("w", encoding="utf-8") as fh:
        for item in items:
            for rnd, rec in sorted(item["rounds"].items()):
                if not rec.get("frame"):
                    continue
                frame = OUT / rec["frame"]
                row = {
                    "frame": str(frame),
                    "channel": item["channel"],
                    "url": item["url"],
                    "line_index": item["line_index"],
                    "round": int(rnd),
                }
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows.append(row)
    checked = logo_check.run_frames(templates, PROGRAM_FRAMES, PROGRAM_RESULT)
    by_key = {(r["channel"], r["line_index"], r["round"]): r for r in checked}

    verdicts = []
    for item in items:
        name = item["channel"]
        tpl = templates.get(name)
        loc = (tpl or {}).get("loc")
        cells = []
        for rnd in sorted(item["rounds"]):
            rec = item["rounds"][rnd]
            if not rec.get("frame"):
                cells.append({"round": int(rnd), "check": "无帧", "score": None})
                continue
            c = by_key.get((name, item["line_index"], int(rnd))) or {}
            check, score = c.get("check", "无帧"), c.get("score")
            matched = c.get("loc") or (tpl or {}).get("loc")
            # 占位页那把尺：门禁里那 14 枚 dHash 指纹也拿到回测用（用户 10-26 指令：
            # 「服务器超负载所致…可以微信扫码支持一下」这一页出现的线路直接删）。
            fp = PR.fingerprint_hit(OUT / rec["frame"]) if rec.get("frame") else None
            # 用户的口径只认左上/右上两个角；模板定在别的角时，这一台不按"没有台标"删，
            # 而是交给人工（可能是我们的模板角与真机台标位置不一致，不许机器一句话判死）。
            if matched in ("bl", "br") and check == "有台标":
                check = "角不在左/右上"
            # 轮级人工结论优先于程序（用户逐张看过的轮次）：写成「无台标」就是这一轮没台标，
            # 写成「有台标」就是这一轮有。线级结论仍走下面的 human 覆盖。
            hr = human_round.get((name, item["line_index"], int(rnd)))
            human_note = None
            if hr:
                hl = str(hr.get("logo") or "")
                if hl.startswith("有台标"):
                    check, human_note = "有台标（人工确认）", hl
                elif hl.startswith("无台标"):
                    check, human_note = "疑似无台标（人工确认）", hl
            cells.append({"round": int(rnd), "check": check, "score": score,
                          "loc": matched, "loc_scores": c.get("loc_scores"),
                          "frame": rec.get("frame"),
                          "fingerprint": fp, "human": human_note})
        h = human.get((name, item["line_index"]))
        qr_rounds, verdict = decide_verdict(cells, str((h or {}).get("logo") or ""))
        verdicts.append({
            "channel": name,
            "channel_id": item["channel_id"],
            "line_index": item["line_index"],
            "url": item["url"],
            "template_loc": loc,
            "rounds": cells,
            "overload_rounds": qr_rounds,
            "verdict": verdict,
            "needs_human": "人工复核" in verdict,
        })

    cut_rows = [v for v in verdicts if v["verdict"].startswith("三轮都无台标")]
    qr_cut = [v for v in verdicts if v["verdict"].startswith("超负载扫码页")]
    # 人工复核过的那几条以 human_review.json 为准：目视看到台标（左上或右上）的一律保留。
    # 10-25 程序漏判的 6 条里，4 条台标在右上（旧模板只裁左上），2 条模板尺寸不匹配但图上明明有台标。
    # 这些线已在上面改写成「保留（人工看图有台标）」，所以删除候选里不会再出现它们。
    human_kept = [v for v in verdicts if v["verdict"] == "保留（人工看图有台标）"]
    final_cut = [v for v in cut_rows if not (human.get((v["channel"], v["line_index"]))
                                             and str(human[(v["channel"], v["line_index"])].get("logo", "")).startswith("有台标"))]
    queue = [
        {
            "channel": v["channel"],
            "line_index": v["line_index"],
            "url": v["url"],
            "template_loc": v["template_loc"],
            "rounds": v["rounds"],
            "why": v["verdict"],
        }
        for v in verdicts if v["needs_human"]
    ]
    counts = collections.Counter(v["verdict"] for v in verdicts)
    human_round_hits = sum(1 for v in verdicts for c in v["rounds"] if c.get("human"))
    REVIEW.write_text(json.dumps({
        "generatedAt": data.get("generatedAt"),
        "rounds": data.get("rounds"),
        "filename_rule": "{channel_id}__L{line_index+1:02d}__R{round}.jpg"
                         "（channel_id 是目录里的 id，如 cctv-1ffa84b5c3＝宁夏卫视，不是台号）",
        "rule": "三轮都无台标才删；任一轮看到台标保留；无模板/无帧/灰区交人工。"
                "例外（用户 10-26）：任一轮命中「服务器超负载…微信扫码」告警页指纹 → 该线删，"
                "不看别的轮有没有台标。",
        "templates": {k: {"loc": v["loc"],
                          "corners": sorted((v.get("templates") or {}).keys()),
                          "extras": len(v.get("extras") or []),
                          "n_sources": v["n_sources"]} for k, v in templates.items()},
        "counts": dict(counts),
        "catalog_lines": len(current),
        "stale_lines_skipped": stale,
        "catalog_lines_without_evidence": [
            {"channel": next(ch["name"] for ch in catalog["channels"] if ch["id"] == cid),
             "channel_id": cid, "line_index": idx + 1, "url": url}
            for cid, idx, url in no_row
        ],
        "program_candidates": len(cut_rows),
        "overload_qr_candidates": len(qr_cut),
        "overload_qr_lines": [{"channel": v["channel"], "channel_id": v["channel_id"],
                               "line_index": v["line_index"], "url": v["url"],
                               "rounds_hit": v["overload_rounds"],
                               "frames": [c.get("frame") for c in v["rounds"]]}
                              for v in qr_cut],
        "kept_by_human": human_kept,
        "human_round_notes": human_round_hits,
        "final_cut": len(final_cut),
        "lines": verdicts,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "human_queue.json").write_text(json.dumps({
        "generatedAt": data.get("generatedAt"),
        "note": "程序判不了（无模板/无帧/灰区）的线路，截图文件名都在 rounds[].frame；请逐条看单张原帧。",
        "count": len(queue),
        "lines": queue,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    final_path = OUT / "final_deletions.json"
    # 用户 10-26 真机点档：「服务器超负载所致…可以微信扫码支持一下」这一页出现过的线路删。
    # 这一族不等「三轮都无台标」——它本身就是一个可机器识别的指纹，任一轮命中即进清单。
    final_rows = [
            {
                "url": v["url"],
                "kind": "logo",
                "channel": v["channel"],
                "line_index": v["line_index"],
                "evidence": "三轮都无台标（程序复核＋人工复核）",
                "scores": [c.get("score") for c in v["rounds"]],
            }
            for v in final_cut
        ] + [
            {
                "url": v["url"],
                "kind": "overload_qr",
                "channel": v["channel"],
                "line_index": v["line_index"],
                "evidence": v["verdict"],
                "hit_rounds": v["overload_rounds"],
                "hit_frames": [rr.get("frame") for rr in v["rounds"]
                               if "服务器超负载" in str(rr.get("fingerprint") or "")],
            }
            for v in qr_cut
        ]
    final_path.write_text(json.dumps({
        "generatedAt": data.get("generatedAt"),
        "kind": "logo+overload_qr",
        "cut": final_rows,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    # logo 那一族是**程序候选**、还没定案（10-24「保留有台标的线路」＋待点档 #106：历史上见过台标、
    # 最近几轮全是广告段的线要人工逐张看过才删）；只有扫码页这一族是用户已经点过档的。
    # 所以构建那边喂的是这份投影，不是整份 final_deletions.json——一份来源、两种去向，
    # 不手抄第二份名单（10-24 就为"两份名单打架"付过代价）。
    qr_path = OUT / "deletions_qr_only.json"
    qr_doc = qr_only_projection(final_rows, data.get("generatedAt"), final_path,
                                held_logo=len(final_rows) - len(qr_doc_cut_count(final_rows)))
    qr_path.write_text(json.dumps(qr_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[review] 扫码页一族单独投影 {len(qr_doc['cut'])} 条 → {qr_path.relative_to(ROOT)}"
          f"（logo 那 {len(final_rows) - len(qr_doc['cut'])} 条留在 {final_path.name} 等人工定案，不喂给构建）",
          flush=True)
    print("[review] " + " · ".join(f"{k} {v}" for k, v in counts.items()), flush=True)
    print(f"[review] 程序候选 {len(cut_rows)} 条；人工看图保留 {len(human_kept)} 条；"
          f"超负载扫码页 {len(qr_cut)} 条；最终删线 {len(final_cut) + len(qr_cut)} 条"
          f"（无台标 {len(final_cut)} ＋扫码页 {len(qr_cut)}）→ {final_path.relative_to(ROOT)}",
          flush=True)
    print(f"[review] 人工复核队列 {len(queue)} 条 → {(OUT / 'human_queue.json').relative_to(ROOT)}",
          flush=True)
    print(f"[review] 每帧判读 → {REVIEW.relative_to(ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
