#!/usr/bin/env python3
"""review_carry（乙：G3 判定按 url 跨轮沿用）的夹具。

格子（每格都要报「按了几条线／挡了几条」；任何一格判红 EXIT=1）：
  A 强沿用：上一轮判「台标相符＋正常节目画面」，本轮帧本台台标还在 ⇒ 沿用，并写「沿用 <轮次>」
  A2 本轮已有结论 ⇒ 一律不覆盖（连改写都不许）
  B 广告帧不借用：上一轮判好，本轮帧认不出本台台标（广告）⇒ 不沿用，退回人工
  C 旧红沿用：上一轮判红、本轮帧仍无本台台标 ⇒ 沿用旧红（不重问）
  D 自愈重问：上一轮判红、本轮帧本台台标回来了 ⇒ **不沿用**，退回人工
  E 别的台一票否决：上一轮判好，本轮帧更像别的台（或认不出本台）⇒ 不沿用
  F 坏指纹一票否决：上一轮判好，本轮帧命中广告/告警页指纹 ⇒ 不沿用
  F2 本台测试卡（白名单内）放行：只能靠「地址判过＋白名单」两条一起走
  F3 测试卡但不在白名单 ⇒ 不沿用（名单闸要有牙）
  G 没有本台模板 ⇒ 不沿用（证不了内容）
  H 最新的那条结论优先：旧轮判红、新轮判好 ⇒ 按「好」走
  I 旧轮判好、新轮判红 ⇒ 按「红」走
  Z 负对照：历史目录为空 ⇒ 一条都不沿用（证明 A~C 不是「什么都沿用」）
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import review_carry  # noqa: E402

GOOD = "tools/.cache/gate/round_r56/frames/cctv-79393328e8/line0/frame_first.jpg"     # CCTV-13 真画面
AD = "tools/.cache/gate/round_r56/frames/cctv-79393328e8/line1/frame_first.jpg"       # CCTV-13 广告帧
OTHER = "tools/.cache/gate/round_r56/frames/cctv-0959c89081/line0/frame_first.jpg"    # 东方卫视 真画面
BADFP = "tools/.cache/gate/round_r55/frames/cctv-6c71ae1298/line6/frame_first.jpg"    # 服务器超负载告警页
CARD = "tools/.cache/gate/round_r56/frames/cctv-2e212df366/line1/frame_first.jpg"     # CCTV-4K 色条卡
FX = ROOT / "tools/.cache/carry_fixture"

GOOD_ROW = ("台标相符（左上角「CCTV-13 新闻」）", "正常节目画面（《世界周刊》）")
RED_ROW = ("台标不符（整帧只有广告热线 400-179-9565）", "广告（化糖第一奇方，400-179-9565）")


def results(rs):
    out = {}
    for name, frame, url in rs:
        out.setdefault(name, []).append({"index": len(out[name]), "url": url,
                                         "frame": str(ROOT / frame)})
    return [{"name": n, "must_watch": True, "lines": ls} for n, ls in out.items()]


def write_prior():
    shutil.rmtree(FX, ignore_errors=True)
    FX.mkdir(parents=True)

    def row(url, name, logo_picture, at):
        return {"url": url, "name": name, "logo": logo_picture[0], "picture": logo_picture[1],
                "reviewed_at": at}

    old = {"reviews": [
        row("u-latest-good", "CCTV-13 新闻", RED_ROW, "2026-10-20 10:00"),
        row("u-latest-red", "CCTV-13 新闻", GOOD_ROW, "2026-10-21 10:00"),
    ]}
    new = {"reviews": [
        row("u-strong", "CCTV-13 新闻", GOOD_ROW, "2026-10-25 10:00"),
        row("u-weak", "CCTV-13 新闻", GOOD_ROW, "2026-10-25 10:00"),
        row("u-red", "CCTV-13 新闻", RED_ROW, "2026-10-25 10:00"),
        row("u-recover", "CCTV-13 新闻", RED_ROW, "2026-10-25 10:00"),
        row("u-other", "CCTV-13 新闻", GOOD_ROW, "2026-10-25 10:00"),
        row("u-badfp", "CCTV-4K 超高清",
            ("台标相符（左上角「CCTV-4K 超高清」）", "正常节目画面（纪录片）"), "2026-10-25 10:00"),
        row("u-card", "CCTV-4K 超高清",
            ("台标相符（左上角「CCTV-4K 超高清」）", "测试卡（本台色条卡）"), "2026-10-25 10:00"),
        row("u-card2", "CCTV-13 新闻", ("台标相符（左上角「CCTV-13 新闻」）", "测试卡（色条卡）"),
            "2026-10-25 10:00"),
        row("u-card3", "东方卫视", ("台标相符（左上角东方卫视）", "测试卡（色条卡）"),
            "2026-10-25 10:00"),
        row("u-notpl", "不存在的台", ("台标相符（左上角「不存在的台」）", "正常节目画面（某节目）"),
            "2026-10-25 10:00"),
        row("u-latest-good", "CCTV-13 新闻", GOOD_ROW, "2026-10-25 11:00"),
        row("u-latest-red", "CCTV-13 新闻", RED_ROW, "2026-10-25 11:00"),
    ]}
    for tag, doc in (("fx_old", old), ("fx_new", new)):
        d = FX / f"round_{tag}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "review.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return [str(FX / "round_fx_*/review.json")]


def case(tag, rs, current, globs, expect, fails):
    review = dict(current)
    got, summary = review_carry.apply_carry(
        review, results(rs), current_review_path=FX / "current_review.json",
        prior_globs=globs, log=lambda *_: None)
    detail = expect(review, summary)
    if detail is True:
        print(f"  [{tag}] 过：沿用 {summary['carried']}"
              f"（强 {summary['counts']['strong']}／旧红 {summary['counts']['red']}），"
              f"挡住 {len(summary.get('blocked_rows') or [])}")
    else:
        fails.append(f"{tag}：{detail}")
        print(f"  [{tag}] 判红：{detail}")
        print("    沿用：" + json.dumps(summary.get("carry_rows") or [], ensure_ascii=False)[:300])
        print("    挡住：" + json.dumps(summary.get("blocked_rows") or [], ensure_ascii=False)[:300])
    return review, summary


def main():
    fails = []
    globs = write_prior()

    case("A 强沿用", [("CCTV-13 新闻", GOOD, "u-strong")], {}, globs,
         lambda rv, sm: True if (sm["counts"]["strong"] == 1
                                 and rv["u-strong"]["logo"].startswith("台标相符")
                                 and "沿用 round_fx_new" in rv["u-strong"]["logo"])
         else "没按强证据沿用，或没写沿用轮次", fails)

    keep = {"logo": "本轮人工已判：台标相符（左上）", "picture": "正常节目画面（本轮）"}
    rv_a2, sm_a2 = case("A2 本轮结论不覆盖", [("CCTV-13 新闻", GOOD, "u-strong")],
                        {"u-strong": keep}, globs,
                        lambda rv, sm: True if (rv["u-strong"] == keep and sm["carried"] == 0)
                        else "本轮人工结论被沿用改写了", fails)

    case("B 广告帧不借用", [("CCTV-13 新闻", AD, "u-weak")], {}, globs,
         lambda rv, sm: True if ("u-weak" not in rv and sm["carried"] == 0
                                 and any("认不出本台台标" in k for k in (sm.get("blocked") or {})))
         else "广告帧被上一轮的好结论洗白了", fails)

    case("C 旧红沿用", [("CCTV-13 新闻", AD, "u-red")], {}, globs,
         lambda rv, sm: True if (sm["counts"]["red"] == 1
                                 and rv["u-red"]["picture"].startswith("广告")
                                 and "沿用" in rv["u-red"]["picture"])
         else "旧红没有沿用", fails)

    case("D 自愈重问", [("CCTV-13 新闻", GOOD, "u-recover")], {}, globs,
         lambda rv, sm: True if ("u-recover" not in rv
                                 and (sm.get("blocked") or {}).get(
                                     "旧红但本轮本台台标回来了，要人复核") == 1)
         else "本台台标回来了还被旧红压着", fails)

    case("E 别的台一票否决", [("CCTV-13 新闻", OTHER, "u-other")], {}, globs,
         lambda rv, sm: True if ("u-other" not in rv
                                 and any(("更像" in k or "认不出本台台标" in k)
                                         for k in (sm.get("blocked") or {})))
         else "别的台的画面被沿用成本台", fails)

    case("F 坏指纹一票否决", [("CCTV-4K 超高清", BADFP, "u-badfp")], {}, globs,
         lambda rv, sm: True if ("u-badfp" not in rv
                                 and (sm.get("blocked") or {}).get(
                                     "本轮帧是已知广告/二维码/告警页") == 1)
         else "已知告警页被沿用成正常画面", fails)

    case("F2 测试卡指纹放行", [("CCTV-4K 超高清", CARD, "u-card")], {}, globs,
         lambda rv, sm: True if (sm["carried"] == 1 and "测试卡" in rv["u-card"]["picture"])
         else "本台测试卡没沿用（白名单那一格会永远红着）", fails)

    case("F3 测试卡不在白名单", [("东方卫视", CARD, "u-card3")], {}, globs,
         lambda rv, sm: True if ("u-card3" not in rv and sm["carried"] == 0)
         else "白名单外的台靠测试卡混过了沿用", fails)

    case("G 没有本台模板", [("不存在的台", GOOD, "u-notpl")], {}, globs,
         lambda rv, sm: True if ("u-notpl" not in rv
                                 and (sm.get("blocked") or {}).get("本台没有模板，证不了内容") == 1)
         else "没有模板的台也被沿用了", fails)

    case("H 最新结论优先（旧红新好）", [("CCTV-13 新闻", GOOD, "u-latest-good")], {}, globs,
         lambda rv, sm: True if sm["counts"]["strong"] == 1
         else "没有按最新那一条（好）走", fails)

    case("I 最新结论优先（旧好新红）", [("CCTV-13 新闻", AD, "u-latest-red")], {}, globs,
         lambda rv, sm: True if (sm["counts"]["red"] == 1
                                 and rv["u-latest-red"]["picture"].startswith("广告"))
         else "没有按最新那一条（红）走", fails)

    case("Z 负对照（历史为空）",
         [("CCTV-13 新闻", GOOD, "u-strong"), ("CCTV-13 新闻", AD, "u-red")], {},
         [str(FX / "no_such_*.json")],
         lambda rv, sm: True if (sm["carried"] == 0 and len(rv) == 0 and
                                 (sm.get("blocked") or {}).get("没有历史结论") == 2)
         else "没有历史也照样沿用 ⇒ 这把尺恒真", fails)

    print(f"[判定] review_carry 夹具：{'过' if not fails else '判红'}（{13 - len(fails)}/13 格）")
    for f in fails:
        print("  [判红] " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
