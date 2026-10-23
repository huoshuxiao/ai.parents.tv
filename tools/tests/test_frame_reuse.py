#!/usr/bin/env python3
"""验「沿用旧帧必须先证这帧本来就是这条地址抽的」这一把尺。

帧目录是按线路号命名的（`frames/<频道id>/line<序号>`），所以目录一改——删线、重排、
两条线互换位置——同一个 `line0` 就装着别的地址的画面。`--rerender` 只换口径重出报告、
一行网络请求都不发，一旦跨着新目录跑，旧帧就会被挂到别的线上，
而门禁 G2 判的正是「必看台第 1 条线路必须有正常画面」——假的绿就是这么来的。
10-22 实测代价：拿 15:00 那一场（1048 台）的读数对 15:31 那份目录（1030 台）重出报告，
9 格位置换了地址，其中 **7 格旧帧会被记成本条线的「正常画面」，涉及 6 个台**，
含 CCTV-5 体育 与 CCTV-13 新闻 的 `line0`（两台正是必看组）。

六格：
 1 正对照：来源记录与本条地址相同 → 允许沿用
 2 来源记录是另一条地址 → 拒绝沿用，并写明是哪条
 3 旧帧没有来源记录 → 拒绝沿用（证不出归属的就不许当证据）
 4 真抽帧那一路会把来源记录写下来（键与黑名单同一把 stream_rules.canon）
 5 走生产入口：同一频道两条线互换位置 → 两格都拒，不许出现「一条线拿另一条线的画面算合格」
 6 负对照：位置没换时同一套夹具照样全部复用（证明摘掉帧的是「换了地址」那一格，不是开关本身）
跑法：python3 tools/tests/test_frame_reuse.py    （退出码 0 才算过）
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import prepack_report as pr  # noqa: E402
import stream_rules  # noqa: E402

FIX = BASE / "tools" / "fixtures"
failures: list[str] = []
CELLS = [0]


def check(cell: str, ok: bool, detail: str = "") -> None:
    CELLS[0] += 1
    print(f"{'通过' if ok else '判红'}  {cell}" + (f" —— {detail}" if detail else ""))
    if not ok:
        failures.append(cell)


A = "http://a.example.com/live/one.m3u8"
B = "http://b.example.com/live/two.m3u8"


def seed(work: Path, cid: str, index: int, url_for_provenance: str, with_note: bool = True) -> Path:
    """造一枚「上一轮抽好的帧」：frame_first.jpg + 一张 frame_*.jpg（只一张，卡流判据不介入）。"""
    d = work / "frames" / cid / f"line{index}"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIX / "frame_program.jpg", d / "frame_first.jpg")
    shutil.copyfile(FIX / "frame_program.jpg", d / "frame_00001.jpg")
    if with_note:
        (d / pr.SOURCE_NOTE).write_text(json.dumps(
            {"url": url_for_provenance, "canonical": stream_rules.canon(url_for_provenance)},
            ensure_ascii=False), encoding="utf-8")
    return d


with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp)

    # ── 1 正对照：地址没变，旧帧该沿用
    seed(work, "cctv-1", 0, A)
    r = pr.grab_frame(A, work / "frames" / "cctv-1" / "line0", 8, reuse=True, no_capture=True)
    check("1 来源记录与本条地址相同 → 允许沿用旧帧", bool(r["frame"]) and not r.get("reason"),
          f"帧 {Path(r['frame']).name if r['frame'] else None}")

    # ── 2 换地址：同一线路号现在放的是别的地址 → 必须拒
    seed(work, "cctv-2", 0, A)
    r2 = pr.grab_frame(B, work / "frames" / "cctv-2" / "line0", 8, reuse=True, no_capture=True)
    check("2 同一线路号换了地址 → 拒绝沿用，并写明旧帧来自哪条",
          r2["frame"] is None and "不是本条地址" in (r2.get("reason") or "")
          and stream_rules.canon(A) in (r2.get("reason") or ""),
          f"{(r2.get('reason') or '')[:76]}")

    # ── 3 没有来源记录（改这行代码之前抽的帧）→ 证不出归属就不许当证据
    seed(work, "cctv-3", 0, A, with_note=False)
    r3 = pr.grab_frame(A, work / "frames" / "cctv-3" / "line0", 8, reuse=True, no_capture=True)
    check("3 旧帧没有来源记录 → 拒绝沿用（线路号自己不构成归属证明）",
          r3["frame"] is None and "没有来源记录" in (r3.get("reason") or ""),
          f"{(r3.get('reason') or '')[:60]}")

    # ── 4 真抽帧那一路要把来源记下来（键与地址黑名单同一把尺）
    captured_dir = Path(tmp) / "raw"
    captured_dir.mkdir()
    real = captured_dir / "frame_00001.jpg"
    shutil.copyfile(FIX / "frame_program.jpg", real)

    def fake_capture(url, work_dir, timeout, native=False):  # noqa: ARG001
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / "frame_00001.jpg"
        shutil.copyfile(real, out)
        return {"frames": [out], "timed_out": False, "elapsed_ms": 1}

    pr.probe_channels.capture_frames = fake_capture
    d4 = work / "frames" / "cctv-4" / "line0"
    r4 = pr.grab_frame(B, d4, 8)
    note = json.loads((d4 / pr.SOURCE_NOTE).read_text(encoding="utf-8")) if (d4 / pr.SOURCE_NOTE).exists() else {}
    check("4 真抽一帧之后落下来源记录，且键与 stream_rules.canon 同一把尺",
          bool(r4["frame"]) and note.get("canonical") == stream_rules.canon(B)
          and note.get("url") == B, f"记录 {json.dumps(note, ensure_ascii=False)[:78]}")

    # ── 5/6 走生产入口：互换位置 vs 位置不动
    health = {A: {"verdict": "可取到分片", "http_status": 200, "seg_bytes": 262144, "resolution": None},
              B: {"verdict": "可取到分片", "http_status": 200, "seg_bytes": 262144, "resolution": None}}
    seed(work, "cctv-5", 0, A)
    seed(work, "cctv-5", 1, B)
    swapped = {"id": "cctv-5", "name": "CCTV-13 新闻", "subgroup": "央视", "category": "cctv",
               "streams": [B, A]}
    r5 = pr.probe_channel(swapped, health, work / "frames", 8, stop_at_first_frame=False,
                          reuse=True, no_capture=True)
    check("5 两条线互换位置 → 两格都拒，绝不出现「一条线拿另一条线的画面算合格」",
          r5["clean_frame_ok"] == 0 and r5["frame_ok"] == 0
          and all("拒绝沿用" in str(l.get("frame_skipped") or "") for l in r5["lines"]),
          f"抽帧 {r5['frame_ok']} 张、算数的 {r5['clean_frame_ok']} 张")

    intact = {"id": "cctv-5", "name": "CCTV-13 新闻", "subgroup": "央视", "category": "cctv",
              "streams": [A, B]}
    r6 = pr.probe_channel(intact, health, work / "frames", 8, stop_at_first_frame=False,
                          reuse=True, no_capture=True)
    check("6 负对照：位置一个字没动时同一套夹具照样两张都算画面（拒的是「换了地址」那一格，不是开关）",
          r6["clean_frame_ok"] == 2 and r6["frame_ok"] == 2,
          f"抽帧 {r6['frame_ok']} 张、算数的 {r6['clean_frame_ok']} 张")

print()
if failures:
    print(f"[verdict] {len(failures)}/{CELLS[0]} 格判红：{'、'.join(failures)}")
    sys.exit(1)
print(f"[verdict] {CELLS[0]}/{CELLS[0]} 格通过")
sys.exit(0)
