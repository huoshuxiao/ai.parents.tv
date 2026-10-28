#!/usr/bin/env python3
"""验的是「抽出『服务器超负载…微信扫码』告警页 ⇒ 这条线路删＋拉黑」这一格。

用户 10-26 真机点档原话：「央视·卫视 全线路回测 『服务器超负载所致…可以微信扫码支持一下』
此线路删除」。这一族比 10-25 那把「三轮都无台标才删」更硬，本夹具盯六种会说谎的情形：

  J) 命中即删：任一轮命中就删，**别的轮看到过台标也不留**（不钉这一格，代码会被
     顺手"优化"成「有台标就保留」，用户看到的那页就又回来了）。
  K) 只认这一枚指纹：测试卡／二维码推广页那些占位页不许冒充它（否则一条好线被别的
     画面判死）。
  L) 轮次不全时不判：只有两轮的线交人工，不按扫码页删（回测跑挂了不能当成源的错）。
  M) 人工「有台标」救不回这一族（10-26 比 10-25 硬，这条也要有牙）。
  N) 清单进构建：kind=overload_qr 要有自己的理由文案；不认识的 kind 不收。
  O) 删线必须同时进黑名单，档位是 4（对端坏那一侧），否则设备端「立即更新线路」捞回来。

每格都配负对照：把被试那一格摘掉，结论必须翻回原样。

跑法：python3 tools/tests/test_overload_qr.py    （退出码 0 才算过）
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402
import retest_flat_review as R  # noqa: E402
import stream_rules  # noqa: E402

QR = "占位页：源站「服务器超负载…微信扫码支持」告警页（右侧微信收款码，无任何台标）"
CARD = "占位页：色条测试卡"
NAME = "CCTV-2 财经"
QR_URL = "http://8.138.7.223/tv/cctv2.php"
OK_URL = "http://9.9.9.9:8080/live/cctv2hd.m3u8"
UNKNOWN_URL = "http://7.7.7.7/live/unknown.m3u8"
canon = stream_rules.canon

failures: list[str] = []
ran = 0


def check(label, got, want) -> None:
    global ran
    ran += 1
    if got != want:
        failures.append(f"{label}: 得到 {got!r}，期望 {want!r}")


def cell(rnd, chk, fp=None):
    return {"round": rnd, "check": chk, "score": 0.5, "fingerprint": fp}


def part_j() -> None:
    """命中即删，且走在「有台标就保留」前面。"""
    cells = [cell(1, "有台标"), cell(2, "有台标"), cell(3, "疑似无台标", QR)]
    qr, verdict = R.decide_verdict(cells)
    check("J1 第三轮命中扫码页 ⇒ 判删", verdict.startswith("超负载扫码页"), True)
    check("J2 命中轮次写进结论", qr, [3])
    check("J3 结论里带轮号", "第 3 轮命中" in verdict, True)
    # 负对照：摘掉那枚指纹，同三格必须变回「保留」
    qr0, v0 = R.decide_verdict([cell(1, "有台标"), cell(2, "有台标"), cell(3, "疑似无台标")])
    check("J4 负对照（摘掉指纹）", v0, "保留（至少一轮看到台标）")
    check("J5 负对照命中轮次为空", qr0, [])


def part_k() -> None:
    """只认这一枚指纹：别的占位页不算命中。"""
    _qr, verdict = R.decide_verdict([cell(1, "有台标"), cell(2, "有台标"), cell(3, "有台标", CARD)])
    check("K1 测试卡指纹不触发扫码页那一族", verdict, "保留（至少一轮看到台标）")


def part_l() -> None:
    """轮次不全优先交人工，不按扫码页判死。"""
    _qr, verdict = R.decide_verdict([cell(1, "有台标"), cell(2, "疑似无台标", QR)])
    check("L1 两格里命中也不删", verdict.startswith("轮次不全"), True)


def part_m() -> None:
    """人工写过「有台标」也救不回扫码页（10-26 比 10-25 硬）。"""
    cells = [cell(1, "疑似无台标"), cell(2, "疑似无台标"), cell(3, "疑似无台标", QR)]
    _qr, verdict = R.decide_verdict(cells, "有台标相符（CCTV-2）")
    check("M1 人工有台标不改扫码页的删", verdict.startswith("超负载扫码页"), True)
    # 负对照：同一份人工结论，摘掉指纹后必须走 10-25 那条「人工看图保留」
    _qr, v0 = R.decide_verdict([cell(1, "疑似无台标"), cell(2, "疑似无台标"), cell(3, "疑似无台标")],
                               "有台标相符（CCTV-2）")
    check("M2 负对照（摘指纹后人工结论生效）", v0, "保留（人工看图有台标）")


def part_n() -> None:
    """清单读入口径：两族各有文案，未知 kind 不收。"""
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "final_deletions.json"
        path.write_text(json.dumps({"cut": [
            {"url": QR_URL, "kind": "overload_qr", "evidence": "超负载扫码页（第 3 轮命中，删）"},
            {"url": OK_URL, "kind": "logo"},
            {"url": UNKNOWN_URL, "kind": "别的来路"},
        ]}, ensure_ascii=False), encoding="utf-8")
        got = bc.load_cctv3(path)
        check("N1 扫码页一族有自己的理由", "超负载" in got.get(canon(QR_URL), ""), True)
        check("N2 logo 一族文案不变", got.get(canon(OK_URL)), "三轮都无台标（程序＋人工复核）")
        check("N3 未知 kind 不收", canon(UNKNOWN_URL) in got, False)
        check("N4 文件不存在返回空表", bc.load_cctv3(Path(raw) / "nope.json"), {})


def part_o() -> None:
    """删线＋拉黑同批：档位 4、黑名单理由就是那族文案。"""
    saved = bc.CCTV3_CUT
    label = bc.CCTV3_KIND_LABEL["overload_qr"]
    bc.CCTV3_CUT = {canon(QR_URL): label}
    try:
        check("O1 档位=4", bc.evidence(QR_URL, NAME)[0], 4)
        blocked, _ = bc.blocked_lines()
        check("O2 进黑名单且理由同文案", blocked.get(canon(QR_URL)), label)
        check("O3 没在清单里的那条不进", canon(OK_URL) in blocked, False)
    finally:
        # 负对照放在还原之后：清空 CCTV3_CUT，同一条档位必须翻回非 4
        bc.CCTV3_CUT = {}
        check("O4 负对照（清空清单）", bc.evidence(QR_URL, NAME)[0] == 4, False)
        blocked, _ = bc.blocked_lines()
        check("O5 负对照（清空清单后不进黑名单）", canon(QR_URL) in blocked, False)
        bc.CCTV3_CUT = saved


def main() -> int:
    part_j()
    part_k()
    part_l()
    part_m()
    part_n()
    part_o()
    for line in failures:
        print(f"[红] {line}")
    print(f"[overload_qr 夹具] {ran - len(failures)}/{ran} 过 · 红 {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
