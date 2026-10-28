#!/usr/bin/env python3
"""G3 判定按 url 跨轮沿用：同一条线路不再每一轮都被重新问一遍。

为什么：门禁每跑一轮，`<out>/review.json` 只认**本轮**写过的结论；于是 r56 那 209 张里
绝大多数是前几轮人工已经看过、结论没变过的线（含 r13/r14/r55 判过「台标相符＋正常节目画面」
的那些），每一轮都要再看一遍。用户 10-26 点了「乙」：判定按 url 跨轮沿用。

沿用不是无条件的信任，三条闸（缺一条就不沿用、退回「待人工复核」）：
  ① **本轮帧是本轮真抽的**：队列里的 frame 必须存在（不拿上一轮的帧冒充本轮的）；
  ② **内容自证**：拿本轮帧对本台模板（左上/右上两套都试）重打分。
     - 上一轮判「好」（台标相符＋正常节目画面/测试卡）时：只有本台分 ≥ T_HI 才沿用。
       认不出本台台标（广告/黑屏/切换中）一律退回人工——**比「字面按 url 沿用」紧一档**：
       不这样卡，广告帧会被上一轮的好结论洗白（用户 10-26 选乙时只要求「别再重问」，
       没要求「广告也算过」；放宽带标那一格要单独点档）。
     - 上一轮判「红」时：本台分 < T_HI ⇒ 沿用旧红（保守，不重新问）；本台分 ≥ T_HI
       ⇒ **不沿用**，退回人工——线路自愈了要人写一句新结论，不能让旧红永远压着。
  ③ **坏指纹与非本台证据一票否决**：本轮帧命中广告/二维码/告警页指纹，或分档是别的台/
     台标家族撞车 ⇒ 不沿用（这是新消息，要人看）。

本轮 `review.json` 里已有的结论**永远优先**，这里只填空缺、不覆盖。

产出：`tools/.cache/gate/review_carry.json` 逐条记「沿用了什么／被什么挡住／本台分多少」，
门禁报告的 G3 那一格会带上「沿用 N 张（强 a／旧红 b）」，不静默改动判据。
"""
from __future__ import annotations

import glob
import json
import time
from pathlib import Path

GOOD_PICTURE_PREFIX = ("正常节目画面", "测试卡")
RED_BUCKETS = ("别的台", "台标家族撞车")


def _round_tag(path: Path) -> str:
    return path.parent.name


def load_prior_index(patterns, skip: Path | None = None) -> dict[str, dict]:
    """跨轮台账：url → 最近一次人工结论（按 reviewed_at，其次文件 mtime／行号）。"""
    skip = skip.resolve() if skip else None
    found = []
    for pat in patterns:
        for name in sorted(glob.glob(pat)):
            path = Path(name)
            if skip and path.resolve() == skip:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                mtime = path.stat().st_mtime
            except (OSError, ValueError):
                continue
            for pos, row in enumerate(data.get("reviews") or []):
                url = row.get("url")
                if not url:
                    continue
                found.append((row.get("reviewed_at") or "", mtime, pos, path, row))
    index = {}
    for at, mtime, pos, path, row in found:
        key = row["url"]
        cur = index.get(key)
        if cur is None or (at, mtime, pos) >= (cur["_at"], cur["_mtime"], cur["_pos"]):
            index[key] = {"url": key, "logo": row.get("logo") or "",
                          "picture": row.get("picture") or "",
                          "frame": row.get("frame") or "",
                          "reviewed_at": at, "round": _round_tag(path),
                          "name": row.get("name"), "_at": at, "_mtime": mtime, "_pos": pos}
    return index


def _templates():
    import logo_check as LC
    root = Path(__file__).resolve().parents[1]
    reviews = LC.load_reviews([str(root / "tools/.cache/gate/round_*/review.json")])
    return LC.build_templates(reviews), LC


_TEMPLATE_CACHE = []


def templates_cached():
    """模板只建一次：一进程里对同批 review 建两遍是纯浪费（夹具里要跑十几格）。"""
    if not _TEMPLATE_CACHE:
        _TEMPLATE_CACHE.append(_templates())
    return _TEMPLATE_CACHE[0]


def _own_score(frame: str, name: str, templates: dict, LC):
    tpl = templates.get(name)
    if not tpl:
        return None, ""
    best, best_loc, seen = -1.0, "", False
    for loc, tmpl in tpl["templates"].items():
        try:
            score = float(LC.score_frame(frame, tmpl, loc))
        except Exception:  # noqa: BLE001 - 坏图交给「抽不到帧」那一格
            continue
        seen = True
        if score > best:
            best, best_loc = score, loc
    # 注意：NCC 可以是负数（实测广告帧 -0.04），「没算出来」与「算出来是负分」必须分开，
    # 拿 -1.0 当哨兵会把负分误判成「没有模板」（10-26 夹具第一跑就撞上）。
    return (best if seen else None), best_loc


def _bucket(frame: str, name: str, templates: dict, LC):
    scores = {}
    for cand, tpl in templates.items():
        try:
            scores[cand] = float(LC.score_frame(frame, tpl["template"], tpl["loc"]))
        except Exception:  # noqa: BLE001
            continue
    got, other_name, other = LC.verdict_logo(scores, name)
    return got, other_name, (None if other is None else round(other, 3))


def apply_carry(review: dict, results: list[dict], current_review_path: Path,
                prior_globs=("tools/.cache/gate/round_*/review.json",),
                ledger_path: Path | None = None, log=print):
    """给「本轮待人工复核」的必看台线路补上沿用的结论。**不覆盖**本轮已有结论。

    返回 (新的 review, 汇总 dict)。汇总里 counts 的三个数与明细行数必须相等（调用方自检）。
    """
    pending = []
    for channel in results:
        if not channel.get("must_watch"):
            continue
        for line in channel["lines"]:
            url = line.get("url") or ""
            if not line.get("frame") or not url or url in review:
                continue
            if not Path(line["frame"]).exists():
                continue
            pending.append((channel["name"], line))
    summary = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "current_review": str(current_review_path), "pending": len(pending),
               "carried": 0, "counts": {"strong": 0, "red": 0},
               "blocked": {}, "ledger": str(ledger_path) if ledger_path else None}
    if not pending:
        return review, summary

    import prepack_report as PR  # 延迟导入：prepack_report 反过来 import 本模块

    prior = load_prior_index(prior_globs, skip=current_review_path)
    templates, LC = templates_cached()
    carried, blocked = [], []

    def block(name, line, prior_row, why, score=None, bucket="", fp=None):
        blocked.append({"name": name, "url": line["url"], "line_index": line["index"],
                        "frame": str(line["frame"]), "from_round": prior_row["round"],
                        "why": why, "own_score": None if score is None else round(score, 3),
                        "bucket": bucket, "fingerprint": fp})
        summary["blocked"][why] = summary["blocked"].get(why, 0) + 1

    for name, line in pending:
        # 探针给的是 Path、报告里是 str：台账要能落盘，统一按 str 记（Path 不是 JSON 可序列化）
        frame = str(line["frame"])
        prev = prior.get(line["url"])
        if not prev:
            block(name, line, {"round": ""}, "没有历史结论")
            continue
        logo_good = prev["logo"].startswith("台标相符")
        picture_good = prev["picture"].startswith(GOOD_PICTURE_PREFIX)
        own, loc = _own_score(frame, name, templates, LC)
        if own is not None and own >= LC.T_HI and not logo_good:
            # 线路自愈：旧红不再压着，退回人工写新结论
            block(name, line, prev, "旧红但本轮本台台标回来了，要人复核", own)
            continue
        if logo_good and picture_good:
            fp = PR.fingerprint_hit(frame)
            if fp and "测试卡" not in fp:
                block(name, line, prev, "本轮帧是已知广告/二维码/告警页", own, "", fp)
                continue
            if own is None:
                block(name, line, prev, "本台没有模板，证不了内容", None)
                continue
            whitelisted = name in PR.build_catalog.CARD_IS_PICTURE_CHANNELS
            if own >= LC.T_HI:
                kind = "strong"
            elif fp and "测试卡" in fp and prev["picture"].startswith("测试卡") and whitelisted:
                # 本台测试卡没有角标可对（名字在画面正中），只能靠「这一地址上一轮人工判过
                # 是这台 + 白名单里认它的卡算画面」两条一起放行；名单只认 build_catalog 那一份。
                kind = "strong"
                loc = "测试卡"
            else:
                bucket, other_name, other = _bucket(frame, name, templates, LC)
                if bucket in RED_BUCKETS:
                    block(name, line, prev, f"本轮帧更像{bucket}（{other_name} {other}）",
                          own, bucket)
                else:
                    # 认不出本台台标（广告/黑屏/切换中）就不借旧结论变绿：这一格退回人工。
                    # 比「字面按 url 沿用」紧一档——不这样做，广告帧会被上一轮的好结论洗白。
                    block(name, line, prev,
                          f"本轮帧认不出本台台标（本台分 {own:.2f}／{bucket}），退回人工",
                          own, bucket)
                continue
            review[line["url"]] = {
                "logo": f'{prev["logo"]}（沿用 {prev["round"]}）',
                "picture": f'{prev["picture"]}（沿用 {prev["round"]}；本轮帧本台分 {own:.2f}）'}
            summary["counts"][kind] += 1
            carried.append({"name": name, "url": line["url"], "line_index": line["index"],
                            "frame": frame, "from_round": prev["round"],
                            "from_reviewed_at": prev["reviewed_at"], "kind": kind,
                            "own_score": round(own, 3), "own_loc": loc,
                            "logo": review[line["url"]]["logo"],
                            "picture": review[line["url"]]["picture"]})
        else:
            if own is None:
                own = 0.0
            review[line["url"]] = {
                "logo": f'{prev["logo"]}（沿用 {prev["round"]}）',
                "picture": f'{prev["picture"]}（沿用 {prev["round"]}；本轮帧程序分 {own:.2f}）'}
            summary["counts"]["red"] += 1
            carried.append({"name": name, "url": line["url"], "line_index": line["index"],
                            "frame": frame, "from_round": prev["round"],
                            "from_reviewed_at": prev["reviewed_at"], "kind": "red",
                            "own_score": round(own, 3), "own_loc": loc,
                            "logo": review[line["url"]]["logo"],
                            "picture": review[line["url"]]["picture"]})

    summary["carried"] = len(carried)
    summary["carry_rows"], summary["blocked_rows"] = carried, blocked
    total = sum(summary["counts"].values())
    if total != len(carried):
        raise AssertionError(f"沿用计数自相矛盾：counts 之和 {total} ≠ 明细 {len(carried)} 行")
    log(f"[沿用] 待判 {len(pending)} 条：沿用 {len(carried)}"
        f"（强 {summary['counts']['strong']}／旧红 {summary['counts']['red']}）"
        f"，挡住 {len(blocked)} 条")
    for why, n in sorted(summary["blocked"].items(), key=lambda kv: -kv[1]):
        log(f"[沿用]   挡住 {n} 条：{why}")

    if ledger_path:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    return review, summary
