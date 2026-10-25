#!/usr/bin/env python3
"""验的是「卫视频道，台标与频道不一致的，根据台标移入对应卫视频道」（用户 10-25 指令第 8 条）。

盯住七种会说谎的情形：
  A) 搬运只搬一半 —— from 名下摘了、to 名下没接住（或反过来），那条线当场从目录里消失。
  B) 同一地址在 to 名下已有一种写法 —— 追加后一台两条同址线，选线面板出现两条一模一样的线路。
  C) `[拒绝]` 那三条不生效：台名现读不存在／不是卫视组内部／地址根本不在 from 名下，
     却照样改了目录 —— 凭一条错结论凭空造台名，或把线挂进地方台打乱分组顺序。
  D) 拒绝时把被试那台的线路顺手改了（回滚没做干净）。
  E) 改挂结果没有下游消费者：构建算完就丢 ⇒ 设备端合并公开列表时按旧台名又把它捞回来
     （10-22 那次泄漏就是「内置目录改了、lineMoves 这一格没人挡」的形状）。
  F) 两名册打架：目录那份与设备端手抄的 RelabeledStreams 给同一地址写了不同主人，
     构建不选边、只报 [check]（#94 那一枚的处置）。
  G) 清单文件读不动就整片崩、或缺字段的行被静默丢掉（「读不到」会被读成「没有这件事」）。

每格都配负对照：换掉被试那一格的输入，结论必须翻面。

跑法：python3 tools/tests/test_logo_move.py    （退出码 0 才算过）
"""
from __future__ import annotations

import io
import contextlib
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402
import stream_rules  # noqa: E402

canon = stream_rules.canon
MOVED_URL = "http://10.0.0.7:8080/live/hljws.m3u8"
KEPT_URL = "http://10.0.0.8:8080/live/other.m3u8"
# 归一键相同的另一种写法：`#` 后那半截是公开列表粘上来的串，canon 会剥掉（10-24 现读到的形状）。
SAME_ADDR_OTHER_WRITING = "http://10.0.0.7:8080/live/hljws.m3u8#fromlist"

failures: list[str] = []
ran = 0


def check(label: str, got, want) -> None:
    global ran
    ran += 1
    if got != want:
        failures.append(f"{label}: 得到 {got!r}，期望 {want!r}")


def row(name, urls, subgroup="卫视", category="cctv"):
    return {"id": f"{category}-{name}", "name": name, "category": category,
            "subgroup": subgroup, "logo": "", "streams": list(urls)}


def channels():
    return [row("黑龙江卫视", [MOVED_URL, KEPT_URL]),
            row("辽宁卫视", [KEPT_URL]),
            row("某某地方台", [KEPT_URL], subgroup="地方", category="local")]


def moves(frm="黑龙江卫视", to="辽宁卫视", url=MOVED_URL, evidence="跨模板打分 0.51／0.12＋单帧人工复核"):
    return {canon(url): {"from": frm, "to": to, "evidence": evidence}}


def run(chans, mv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got = bc.remount_by_logo(chans, mv)
    return got, buf.getvalue()


# ── A/B：搬得干净 ────────────────────────────────────────────────────────────────
chans = channels()
result, out = run(chans, moves())
by_name = {c["name"]: c["streams"] for c in chans}
check("A1 from 名下这条已摘掉", canon(MOVED_URL) in [canon(s) for s in by_name["黑龙江卫视"]], False)
check("A1b from 名下另一条不许被牵连", by_name["黑龙江卫视"], [KEPT_URL])
check("A2 to 名下接住了这条", canon(MOVED_URL) in [canon(s) for s in by_name["辽宁卫视"]], True)
check("A2b 接住的位置在末位（不动目标台原来的首条）", by_name["辽宁卫视"], [KEPT_URL, MOVED_URL])
check("A3 返回记录按规范地址建键", sorted(result), [canon(MOVED_URL)])
check("A4 记录里带着 from/to/证据", (result[canon(MOVED_URL)]["from"],
                                    result[canon(MOVED_URL)]["to"] != "",
                                    result[canon(MOVED_URL)]["evidence"] != ""),
      ("黑龙江卫视", True, True))
check("A5 打了 [logo_move] 那一行", out.startswith("[logo_move]"), True)

# B：同一地址已有另一种写法 ⇒ 不许再追加一条
chans_b = [row("黑龙江卫视", [MOVED_URL]), row("辽宁卫视", [SAME_ADDR_OTHER_WRITING])]
result_b, _ = run(chans_b, moves())
check("B1 to 名下仍只有一条（写法不同也算同一条）",
      len(chans_b[1]["streams"]), 1)
check("B2 一条都不许凭空多出来", sorted(canon(s) for s in chans_b[1]["streams"]), [canon(MOVED_URL)])
check("B3 搬运照样记了这笔账", len(result_b), 1)

# ── C/D：三条拒绝面 ─────────────────────────────────────────────────────────────
result_c, out_c = run(channels(), moves(to="没有这个台"))
check("C1 台名现读不存在 ⇒ 拒绝", "[拒绝]" in out_c and "没有这个台" in out_c, True)
check("C1b 拒绝时一条都不许搬", result_c, {})

chans_d = channels()
result_d, out_d = run(chans_d, moves(to="某某地方台"))
check("C2 目标不在卫视组内部 ⇒ 拒绝", "不是卫视组内部" in out_d, True)
check("C2b 目录一字未动", [c["streams"] for c in chans_d],
      [[MOVED_URL, KEPT_URL], [KEPT_URL], [KEPT_URL]])

chans_e = channels()
result_e, out_e = run(chans_e, moves(url="http://10.0.0.9:8080/live/不在名下.m3u8"))
check("C3 地址不在 from 名下 ⇒ 拒绝", "名下现读没有这条地址" in out_e, True)
check("C3b 拒绝时也不许动线路", chans_e[0]["streams"], [MOVED_URL, KEPT_URL])

# 源头不在卫视组也要拒（这条指令只说「卫视频道」）
chans_f = channels()
_, out_f = run(chans_f, moves(frm="某某地方台", to="辽宁卫视", url=KEPT_URL))
check("C4 源头不是卫视组 ⇒ 同样拒绝", "不是卫视组内部" in out_f, True)

# ── E：下游消费者（判据不能只有夹具在守）──────────────────────────────────────
source = (BASE / "tools/build_catalog.py").read_text(encoding="utf-8")
call_site = re.search(r"logo_moves = remount_by_logo\(", source)
check("E1 build() 里真有一处在调 remount_by_logo", call_site is not None, True)
check("E2 调它的那一站在标清闸门之前", source.index("logo_moves = remount_by_logo(")
      < source.index("channels, sd_empty = drop_sd_lines(channels)"), True)
check("E3 结果落进目录顶层的 lineMoves", '"lineMoves": logo_moves' in source, True)
check("E4 命令行有 --wshi-moves 这一路输入", '--wshi-moves' in source, True)
kt = (BASE / "app/src/main/java/com/parentstv/live/data/Models.kt").read_text(encoding="utf-8")
check("E5 设备端读的字段名与 JSON 键名一致（不是构建写了没人读）",
      re.search(r"val lineMoves: Map<String, LineMove>", kt) is not None, True)
merger = (BASE / "app/src/main/java/com/parentstv/live/data/CatalogUpdater.kt").read_text(encoding="utf-8")
check("E6 合并那一步真把 lineMoves 传了进去", "blocked, moves)" in merger, True)
check("E7 --wshi-moves 默认值指向随仓库走的那份（不是 tools/.cache/）",
      'BASE / "tools" / "wshi_logo_moves.json"' in source, True)

# ── F：两名册对表 ───────────────────────────────────────────────────────────────
handwritten = canon("http://112.27.5.218:9901/tsfile/live/0111_1.m3u8?key=txiptv&playlive=1&authid=0")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bc.cross_check_relabels({handwritten: {"from": "西藏卫视", "to": "重庆卫视", "evidence": "人造冲突"}})
clash_out = buf.getvalue()
check("F1 同一地址两个主人要报出来", "同一地址两个主人 1 条" in clash_out, True)
check("F2 报的是「主人不同」这一件，两边名字都点名",
      "设备端四川卫视 / 目录重庆卫视" in clash_out, True)
buf2 = io.StringIO()
with contextlib.redirect_stdout(buf2):
    bc.cross_check_relabels({})
check("F3 负对照：没有改挂记录时报「无冲突」，且照样数得出设备端那 4 条",
      ("无冲突" in buf2.getvalue(), "RelabeledStreams 4 条" in buf2.getvalue()), (True, True))

# ── G：清单本身（在库那份）读得动、且默认是空的 ────────────────────────────────
loaded = bc.load_wshi_moves(BASE / "tools/wshi_logo_moves.json")
check("G1 在库那份清单读得动", isinstance(loaded, dict), True)
check("G2 现在还没有确认的改挂（本批只做能力，不凭空搬线）", loaded, {})
check("G3 清单缺文件时返回空表而不是崩", bc.load_wshi_moves(BASE / "tools/不存在.json"), {})
check("G3b 没给路径（None）也返回空表", bc.load_wshi_moves(None), {})

junk = BASE / "tools/.cache/fixture_wshi_moves_junk.json"
junk.write_text('{"moves": [{"from": "甲卫视", "to": "乙卫视"}, {"url": "http://x.example/1.m3u8"}]}',
                encoding="utf-8")
buf3, buf4 = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(buf3):
    junk_loaded = bc.load_wshi_moves(junk)
check("G4 缺 from/to 的那条不进表", junk_loaded, {})
check("G4b 缺 from/to 的要当场点名", "[warn]" in buf3.getvalue() and "缺 from/to" in buf3.getvalue(), True)
with contextlib.redirect_stdout(buf4):
    bc.load_wshi_moves(junk)
check("G5 负对照：缺地址那条也是同样点名（两条都各点一次名）",
      buf4.getvalue().count("[warn]"), 2)
junk.unlink()

print(f"[logo-move] 跑了 {ran} 格，失败 {len(failures)} 格")
for f in failures:
    print("  [判红] " + f)
raise SystemExit(1 if failures else 0)
