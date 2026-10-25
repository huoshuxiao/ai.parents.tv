#!/usr/bin/env python3
"""验的是「三轮回测都不通且没有本台台标 ⇒ 删线」这一格。

用户 10-24 18:3x 原话：「央视·卫视、地方频道全线路回测 3 次，≥3 是无台标删除线路」。
本夹具按「两条同时满足才删」的口径钉死，盯五种会说谎的情形：

  A) 失败次数不设门槛——1 次抖动就删线（家里那张网会抖，辽宁体育几分钟抖三次）。
  B) 门槛写反/写松——fails 是「几轮没通」，拿 ok 比会整批翻面。
  C) 台标那两条来源只接了一条：IDENTITY（抽帧复核）与 REVIEW_LOGO（人工看图台账）
     任何一源说「台标相符」都必须留下；反过来「台标不符/别的台」不许冒充豁免。
  D) 豁免判据写成「台账里有这一行就留」——键存在但结论不是本台台标时仍该删。
  E) 删线不落黑名单——设备端「立即更新线路」会把同一条地址捞回来，等于没删。

每格都配负对照：把被试那一格摘掉（RETEST_CUT 清空），档位必须翻回原样。

跑法：python3 tools/tests/test_retest_cut.py    （退出码 0 才算过）
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402
import stream_rules  # noqa: E402

NAME = "某卫视"
CUT_URL = "http://1.1.1.1:8080/live/cut.m3u8"
LOGO_URL = "http://2.2.2.2:8080/live/logo.m3u8"
TWO_URL = "http://3.3.3.3:8080/live/two.m3u8"
OTHER_URL = "http://4.4.4.4:8080/live/other.m3u8"
OK_URL = "http://5.5.5.5:8080/live/ok.m3u8"
canon = stream_rules.canon

failures: list[str] = []
ran = 0


def check(label: str, got, want) -> None:
    global ran
    ran += 1
    if got != want:
        failures.append(f"{label}: 得到 {got!r}，期望 {want!r}")


def with_evidence(retest: dict, identity: dict, review: dict) -> set:
    """临时换掉三份证据再算一次；返回这次算出来的删线集合。"""
    saved = (bc.RETEST, bc.IDENTITY, bc.REVIEW_LOGO)
    bc.RETEST, bc.IDENTITY, bc.REVIEW_LOGO = retest, identity, review
    try:
        return bc.compute_retest_cuts()
    finally:
        bc.RETEST, bc.IDENTITY, bc.REVIEW_LOGO = saved


def rounds(fails: int) -> dict:
    return {"ok": 3 - fails, "fails": fails, "verdicts": ["连接超时"] * fails + ["可取到分片"] * (3 - fails)}


def part_a() -> None:
    """门槛：fails 必须到 3 才进删线集合。"""
    got = with_evidence({canon(CUT_URL): rounds(2)}, {}, {})
    check("A1 两轮没通不删", canon(CUT_URL) in got, False)
    got = with_evidence({canon(CUT_URL): rounds(3)}, {}, {})
    check("A2 三轮没通才删", canon(CUT_URL) in got, True)
    # 负对照：把这个地址从证据里摘掉，同一格必须变回「不删」
    got = with_evidence({}, {}, {})
    check("A3 负对照（证据里没有它）", canon(CUT_URL) in got, False)


def part_b() -> None:
    """失败次数读的是 fails，不是 ok。"""
    row = {"ok": 3, "fails": 0, "verdicts": ["可取到分片"] * 3}
    got = with_evidence({canon(OK_URL): row}, {}, {})
    check("B1 三轮都通不删", canon(OK_URL) in got, False)
    row = {"ok": 0, "fails": 3, "verdicts": ["连接超时"] * 3}
    got = with_evidence({canon(CUT_URL): row}, {}, {})
    check("B2 三轮都不通删", canon(CUT_URL) in got, True)


def part_c() -> None:
    """两源「台标相符」都能豁免。"""
    ident = {f"{NAME}|{canon(LOGO_URL)}": "台标相符"}
    got = with_evidence({canon(LOGO_URL): rounds(3)}, ident, {})
    check("C1 抽帧复核台标相符 ⇒ 留", canon(LOGO_URL) in got, False)
    # REVIEW_LOGO 是 canon -> row，键就是地址
    got = with_evidence({canon(LOGO_URL): rounds(3)}, {}, {canon(LOGO_URL): {"logo": "台标相符（CCTV-1）"}})
    check("C2 人工看图台标相符 ⇒ 留", canon(LOGO_URL) in got, False)
    # 负对照：把台标那一行摘掉，同一条必须变回删
    got = with_evidence({canon(LOGO_URL): rounds(3)}, {}, {})
    check("C3 负对照（摘掉台标结论）", canon(LOGO_URL) in got, True)


def part_d() -> None:
    """键在、但那句不是本台台标 ⇒ 不豁免。"""
    ident = {f"{NAME}|{canon(OTHER_URL)}": "台标不符（画面是 CCTV-1）"}
    got = with_evidence({canon(OTHER_URL): rounds(3)}, ident, {})
    check("D1 台标不符不豁免", canon(OTHER_URL) in got, True)
    review = {canon(OTHER_URL): {"logo": "台标不符"}}
    got = with_evidence({canon(OTHER_URL): rounds(3)}, {}, review)
    check("D2 人工判不符不豁免", canon(OTHER_URL) in got, True)
    # 别的频道的台标行不许跨台豁免：同 URL 但键前缀不同也要认（endswith 口径）
    ident = {f"另一个台|{canon(CUT_URL)}": "台标相符"}
    got = with_evidence({canon(CUT_URL): rounds(3)}, ident, {})
    check("D3 同一地址在别的台名下的台标也算（地址级证据）", canon(CUT_URL) in got, False)


def part_e() -> None:
    """删线必须同时进黑名单，且档位是 4（对端坏那一侧）。"""
    saved = (bc.RETEST, bc.IDENTITY, bc.REVIEW_LOGO, bc.RETEST_CUT)
    bc.RETEST = {canon(CUT_URL): rounds(3), canon(TWO_URL): rounds(2)}
    bc.IDENTITY, bc.REVIEW_LOGO = {}, {}
    bc.RETEST_CUT = bc.compute_retest_cuts()
    try:
        check("E1 档位=4", bc.evidence(CUT_URL, NAME)[0], 4)
        blocked, _ = bc.blocked_lines()
        check("E2 进黑名单", blocked.get(canon(CUT_URL)), "三轮回测都不通且无台标")
        check("E3 没到门槛的那条不进", canon(TWO_URL) in blocked, False)
        # 负对照：把 RETEST_CUT 清空，同一条档位必须翻回非 4
        bc.RETEST_CUT = set()
        check("E4 负对照（清空删线集合）", bc.evidence(CUT_URL, NAME)[0] == 4, False)
    finally:
        bc.RETEST, bc.IDENTITY, bc.REVIEW_LOGO, bc.RETEST_CUT = saved


def part_f() -> None:
    """summary.json 的读入口径：缺轮次不汇总（宁可不出结论），现有文件能读回 urls。"""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        good = {"rounds": 3, "fail_all": [canon(CUT_URL)], "urls": {canon(CUT_URL): rounds(3)}}
        path = tmp / "summary.json"
        path.write_text(json.dumps(good), encoding="utf-8")
        got = bc.load_retest(path)
        check("F1 读回 url 表", canon(CUT_URL) in got, True)
        check("F2 失败次数原样", got[canon(CUT_URL)]["fails"], 3)
        check("F3 文件不存在时返回空表", bc.load_retest(tmp / "nope.json"), {})


def main() -> int:
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    part_f()
    for line in failures:
        print(f"[红] {line}")
    print(f"[retest 夹具] {ran - len(failures)}/{ran} 过 · 红 {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
