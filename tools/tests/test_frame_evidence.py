#!/usr/bin/env python3
"""验一件事：抽帧抽到占位页的线路，在目录生成侧必须算「已确认坏」，不算「可用证据」。

这是「线路可用性优于频道数量」最容易被糊过去的一格——占位页也是画面，
只看「有没有抽到帧」会把测试卡顶到第一条。夹具是合成的，不联网：
正对照＝带 placeholder 字段的行必须被判坏且被 usable() 拒掉；
负对照＝同一行去掉 placeholder 字段必须判好（证明起作用的是那一格，不是别处的规则）。

后半批（10-23）验的是这条判据改软之后的**边界**：用户 10-23 02:14「CCTV-4K、CCTV 8K 有台标
就算真出画面」，报告侧 `prepack_report.card_is_picture` 必须与构建侧同一口径——
名单外的台、没人写过台标结论的帧、二维码推广页那三类都不许被顺手放行。

跑法：python3 tools/tests/test_frame_evidence.py    （退出码 0 才算过）
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402

CLEAN = "http://1.2.3.4:9901/tsfile/live/0101_1.m3u8"
PLACEHOLDER = "http://5.6.7.8:9901/tsfile/live/0102_1.m3u8"
NAME = "某某卫视"


def rows(with_placeholder: bool):
    out = [{"channel": NAME, "line_index": 0, "url": CLEAN,
            "width": 1920, "height": 1080, "bytes": 200_000, "frame": "/tmp/a.jpg",
            "placeholder": ""}]
    if with_placeholder:
        out.append({"channel": NAME, "line_index": 1, "url": PLACEHOLDER,
                    "width": 1280, "height": 720, "bytes": 90_000, "frame": "/tmp/b.jpg",
                    "placeholder": "占位页：CCTV 测试卡"})
    else:
        out.append({"channel": NAME, "line_index": 1, "url": PLACEHOLDER,
                    "width": 1280, "height": 720, "bytes": 90_000, "frame": "/tmp/b.jpg"})
    return out


def load(with_placeholder: bool) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
        for row in rows(with_placeholder):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        path = Path(fh.name)
    try:
        bc.FRAME_RES, bc.FRAME_PLACEHOLDER, bc.FRAME_STALLED = bc.load_frames(path)
    finally:
        path.unlink()


def main() -> int:
    failures = []
    bc.HEALTH, bc.IDENTITY, bc.DROP_DEAD = {}, {}, True

    load(True)
    if bc.evidence(PLACEHOLDER, NAME)[0] != bc.BAD_TIER:
        failures.append(f"占位页线路判成 {bc.evidence(PLACEHOLDER, NAME)}，应为档 {bc.BAD_TIER}")
    if bc.usable(NAME, PLACEHOLDER):
        failures.append("占位页线路还被 usable() 放行 ⇒ 它会带着测试卡留在目录里")
    if bc.evidence(CLEAN, NAME)[0] != 2:
        failures.append(f"正常帧线路被判成 {bc.evidence(CLEAN, NAME)}，应为「没测过」档 2")
    if not bc.usable(NAME, CLEAN):
        failures.append("正常帧线路被拒 ⇒ 判据过牙")
    if bc.resolution_rank(PLACEHOLDER) == 0:
        failures.append("占位页的尺寸被丢了 ⇒ 负对照与正对照混在了一起")

    load(False)
    if bc.stream_rules.canon(PLACEHOLDER) in bc.FRAME_PLACEHOLDER:
        failures.append(f"没给 placeholder 字段却读出了占位页结论 {bc.FRAME_PLACEHOLDER}")
    if bc.FRAME_PLACEHOLDER.get(bc.stream_rules.canon(CLEAN)) != "":
        failures.append("空串＝「看过、画面正常」这条明确意见没被登记 ⇒ "
                        "多份证据累加时清不掉早先的判坏结论，会漏回坏线")
    if bc.evidence(PLACEHOLDER, NAME)[0] == bc.BAD_TIER:
        failures.append("去掉 placeholder 字段后仍判坏 ⇒ 抓到的不是那一格，是别处的规则")
    if not bc.usable(NAME, PLACEHOLDER):
        failures.append("去掉 placeholder 字段后被拒 ⇒ 这一格不是唯一变量")

    # ── 报告侧的同一把尺：prepack_report.card_is_picture（用户 10-23 02:14「有台标就算真出画面」）
    # 构建侧放宽、报告侧不放宽 = 同一件事两层读数互相打架（10-22 踩过）。这四格盯的是报告那一层。
    import prepack_report as pr  # noqa: E402
    OK4K = "http://gslb.example.com/cctv4k.m3u8"
    CARD = "占位页：CCTV 4K 超高清 测试卡（色条 +「3840×2160/50/P」）"
    AD = "占位页：epg.pw 二维码推广页（「Scan the QR code…download our latest APP」）"
    logo_ok = {OK4K: {"logo": "台标相符（左上角「CCTV 4K 超高清」）", "picture": "测试卡"}}
    note = pr.card_is_picture("CCTV-4K 超高清", CARD, OK4K, logo_ok)
    if not note:
        failures.append("报告侧没放行：必看台＋测试卡＋人工台标相符 ⇒ 它还会被 G2/G4 判成没画面")
    if pr.card_is_picture("河南卫视", CARD, OK4K, logo_ok):
        failures.append("报告侧放宽外溢到名单外的台了（用户那句只点 4K/8K，加必看台是我按新判据收的）")
    if pr.card_is_picture("CCTV-4K 超高清", CARD, OK4K,
                          {OK4K: {"logo": pr.PENDING, "picture": pr.PENDING}}):
        failures.append("没人写过台标结论也放行 ⇒ 这一格变成「有身份栏就通过」，判据没牙")
    if pr.card_is_picture("CCTV-4K 超高清", AD, OK4K, logo_ok):
        failures.append("推广页那一类被一起放行了 ⇒ 用户 10-22 那句「占位页赢」被顺手改软")

    for line in failures:
        print(f"[frame-evidence] 不通过 {line}")
    print(f"[frame-evidence] 结论：{'通过' if not failures else f'{len(failures)} 格不通过'}"
          f"（正对照 6 格 + 负对照 7 格）")
    return 1 if failures else 0


if __name__ == "__main__":
    importlib.reload(bc)
    raise SystemExit(main())
