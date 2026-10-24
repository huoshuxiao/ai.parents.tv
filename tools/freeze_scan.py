#!/usr/bin/env python3
"""长窗冻帧扫描：门禁那把尺只抽 7 秒，分不出「镜头没动」和「源在喂旧画面」。

为什么非要另起一把尺：prepack_report 的 frames_stalled 拿一次抽帧里的几帧比字节，
窗口就是 --run-time=7 秒。实测 CCTV-8 那条 1080p 线在 20 秒窗口里出现「连续 4 帧字节
相同」，可那 4 帧是《枫桥警事》里两个警察站定的双人镜头——7 秒不动完全可能是真节目。
拿它删线会误伤。只有把窗口拉到分钟级，「源没在给新画面」才和「节目本身不动」分得开。

判据（一条线一个结论，按**解码帧数**判，不按秒判）：
  帧数 < 2                → 不作判（VLC 没解出帧＝没意见，不是判坏）
  连续字节相同段 ≥ --freeze-frames（默认 6 帧） → 冻帧
  全窗帧都相同、且整窗帧数 ≥ --freeze-frames      → 冻帧（从头就没动过）
  全窗帧都相同、但整窗只有 2~5 帧                 → 不足判冻（样本太短，与上一条同一口径）
  其余                                            → 画面正常推进

为什么按帧不按秒：VLC 帧号是解码帧计数，相邻帧号差就是 scene-ratio，
所以「连续 N 帧相同」= (N-1)×ratio 个解码帧没有新内容，这个量与本机解码快慢无关。
秒数只能反推（并发软解时 VLC 吞吐只有 8-12fps，比流的真实帧率低，按吞吐算出来的秒数虚高），
所以报告里另给一列 run_seconds_min = 解码帧数 / 50 —— 按 50fps 上界折的**下限**，
默认阈值 6 帧 × ratio 250 ⇒ ≥25 秒节目，远大于一个正常固定镜头，也远大于门禁那 7 秒窗口。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stream_rules
from probe_channels import UA, VLC

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "app" / "src" / "main" / "assets" / "channels.json"
DEFAULT_OUT = ROOT / "tools" / ".cache" / "freeze" / "line_freeze.jsonl"
DEFAULT_WORK = ROOT / "tools" / ".cache" / "freeze" / "shots"


def stream_urls(channel: dict) -> list[str]:
    urls = []
    for item in channel.get("streams", []):
        url = item if isinstance(item, str) else (item or {}).get("url")
        if url:
            urls.append(url)
    return urls


def pick_lines(catalog: dict, scope: str, names: tuple[str, ...]) -> list[tuple[str, str]]:
    """返回 [(频道名, url)]，按 scope 选要扫哪些线，同一地址只留一次。"""
    channels = catalog.get("channels", [])
    wanted: list[tuple[str, str]] = []
    for channel in channels:
        name = channel.get("name") or ""
        url_list = stream_urls(channel)
        if scope == "head":
            pairs = [(name, url) for url in url_list[:1]]
        elif scope == "cctv":
            pairs = [(name, url) for url in url_list if name.upper().startswith("CCTV")]
        elif scope == "named":
            pairs = [(name, url) for url in url_list if name in names]
        elif scope == "all":
            pairs = [(name, url) for url in url_list]
        else:
            raise SystemExit(f"未知 scope：{scope}")
        wanted.extend(pairs)
    seen, out = set(), []
    for name, url in wanted:
        key = stream_rules.canon(url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((name, url))
    return out


def kill_group(process) -> None:
    """与 probe_channels.kill_process_group 同一套：先 TERM 再 KILL。

    用 os.killpg 而不是 signal.killpg——后者是 Python 3.9 才有的，本机是 3.8，
    拿它写会让「超时收尾」这一步直接把整池线程打死（实测第三条线就是这么崩的）。
    """
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def capture(url: str, work_dir: Path, run_time: int, ratio: int, timeout: int) -> dict:
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(VLC), "-I", "dummy", "--ignore-config", "--no-audio", "--no-spu", "--no-osd",
        "--no-video-title-show", "--vout=vdummy", "--aout=dummy", "--avcodec-hw=none",
        "--quiet", "--http-user-agent", UA,
        "--video-filter=scene", "--scene-format=jpg",
        f"--scene-ratio={ratio}",
        "--scene-path", str(work_dir), "--scene-prefix=fr",
        f"--run-time={run_time}", "--play-and-exit", url,
    ]
    process = subprocess.Popen(
        command, cwd=str(ROOT), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    started = time.monotonic()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        if process.poll() is None:
            kill_group(process)   # 它内部已经 wait 过，这里别再 wait（会漏出 TimeoutExpired）
    return {
        "frames": sorted(work_dir.glob("fr*.jpg")),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "timed_out": timed_out,
        "exit_code": process.returncode,
    }


def frame_index(path: Path) -> int:
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    return int(digits) if digits else 0


def longest_run(digests: list[str]) -> tuple[int, int]:
    """返回 (最长连续相同段的长度, 该段起点下标)。"""
    best_len, best_start, cur_len, cur_start = 1, 0, 1, 0
    for i in range(1, len(digests)):
        if digests[i] == digests[i - 1]:
            cur_len += 1
        else:
            cur_start, cur_len = i, 1
        if cur_len > best_len:
            best_len, best_start = cur_len, cur_start
    return best_len, best_start


def verdict_for(frame_count: int, distinct: int, run_len: int, run_frames: int,
                freeze_frames: int) -> str:
    """判据本体：一条线一个结论。

    judge()（现场抽完帧就判）与 tools/.cache/freeze/recompute.py（把已落盘的读数按
    当前判据重算一遍）都只从这里出——两把尺各写一遍，迟早一边改了另一边忘。
    """
    if frame_count < 2:
        return f"帧数不足（{frame_count} 帧），不作判"
    if distinct == 1 and frame_count >= freeze_frames:
        return (f"冻帧：整窗 {frame_count} 帧字节全同"
                f"（≥{run_frames / 50.0:.0f} 秒节目无新画面）")
    if distinct == 1:
        # 只抽到两三帧、恰好字节相同：与「连续 N 帧相同」是同一件事，样本还不够长就不能判死。
        # 早先这一支走在 run 长度闸前面，2 帧全同就被写成冻帧（10-24 山西卫视那条撞上的就是这个）。
        return (f"整窗 {frame_count} 帧字节全同，但帧数少于判冻所需的 "
                f"{freeze_frames} 帧，不足判冻")
    if run_len >= freeze_frames:
        return (f"冻帧：连续 {run_len} 帧字节相同"
                f"（≥{run_frames / 50.0:.0f} 秒节目无新画面）")
    if run_len >= 2:
        return (f"短段相同 {run_len} 帧（{run_frames} 解码帧，"
                f"≥{run_frames / 50.0:.0f} 秒节目），不足判冻")
    return "画面正常推进"


def judge(url: str, name: str, shot: dict, ratio: int, freeze_frames: int) -> dict:
    frames = shot["frames"]
    row = {
        "url": url,
        "canonical": stream_rules.canon(url),
        "channel": name,
        "frames": len(frames),
        "elapsed_ms": shot["elapsed_ms"],
        "timed_out": shot["timed_out"],
        "exit_code": shot.get("exit_code"),
        "dir": str(frames[0].parent) if frames else "",
    }
    if len(frames) < 2:
        row["distinct"] = len(frames)
        row["verdict"] = verdict_for(len(frames), len(frames), 0, 0, freeze_frames)
        return row

    digests = []
    for path in frames:
        try:
            digests.append(hashlib.md5(path.read_bytes()).hexdigest())
        except OSError:
            digests.append("")
    distinct = len({d for d in digests if d})
    indexes = [frame_index(p) for p in frames]
    span_frames = max(indexes) - min(indexes)
    # fps 估算：VLC 在这场里解到末帧号 / 墙钟秒。取不到就按 25fps 兜底，
    # 兜底只会把秒数算准或算小一点，不会把「没动」说成「动了」。
    wall_s = max(shot["elapsed_ms"] / 1000.0, 1.0)
    fps = (max(indexes) / wall_s) if max(indexes) > 0 else 25.0
    fps = fps if 5.0 <= fps <= 120.0 else 25.0

    run_len, run_start = longest_run(digests)
    run_frames = (run_len - 1) * ratio
    # 秒数只当信息，不当判据。原因：这里的 fps 是「VLC 本场解码吞吐」反推的，
    # 四路并发软解时吞吐只有 8-12fps，比流的真实帧率低，算出来的秒数**虚高**
    # （实测 CCTV-3 那条按吞吐算 42 秒，按 25fps 的节目时间只有 20 秒）——
    # 拿它当删除阈值会朝「漏判」的方向偏。判据改用帧数：run_frames 是解码帧数，
    # 与并发无关；再按 50fps 上界折一个「至少多少秒节目」的下限报出来。
    run_seconds = run_frames / fps
    row.update({
        "distinct": distinct,
        "longest_run": run_len,
        "run_start_index": indexes[run_start],
        "run_end_index": indexes[min(run_start + run_len - 1, len(indexes) - 1)],
        "run_frames": run_frames,
        "run_seconds": round(run_seconds, 1),
        "run_seconds_min": round(run_frames / 50.0, 1),
        "span_frames": span_frames,
        "fps_est": round(fps, 1),
        "run_frame": str(frames[run_start]),
        "run_last_frame": str(frames[min(run_start + run_len - 1, len(frames) - 1)]),
    })
    row["verdict"] = verdict_for(len(frames), distinct, run_len, run_frames, freeze_frames)
    return row


def load_done(path: Path) -> dict[str, dict]:
    done = {}
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = row.get("canonical") or stream_rules.canon(row.get("url", ""))
            if key:
                done[key] = row
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description="长窗冻帧扫描")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--scope", choices=("head", "cctv", "named", "all"), default="head")
    parser.add_argument("--name", action="append", default=[], help="scope=named 时的频道名，可重复")
    parser.add_argument("--run-time", type=int, default=150)
    parser.add_argument("--scene-ratio", type=int, default=250)
    parser.add_argument("--freeze-frames", type=int, default=6,
                        help="连续多少帧字节相同就判冻（帧数按 scene-ratio 折算解码帧，与并发解码吞吐无关）")
    parser.add_argument("--timeout", type=int, default=None, help="单条硬超时，默认 run-time+70")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="已有读数的地址也重扫")
    parser.add_argument("--keep-frames", action="store_true")
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    targets = pick_lines(catalog, args.scope, tuple(args.name))
    if args.limit:
        targets = targets[:args.limit]

    done = {} if args.force else load_done(args.out)
    todo = [(name, url) for name, url in targets
            if (stream_rules.canon(url) or url) not in done]
    print(f"[freeze] 选中 {len(targets)} 条 · 已有读数 {len(targets) - len(todo)} 条 · 本场要扫 {len(todo)} 条",
          flush=True)
    if not todo:
        print("[freeze] 没有要扫的线路，收工。", flush=True)
        return 0

    timeout = args.timeout or (args.run_time + 70)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    work_root = args.work / time.strftime("%Y%m%d-%H%M%S")
    work_root.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    counter = {"n": 0}
    started_at = time.monotonic()

    def one(item):
        name, url = item
        key = "".join(ch if ch.isalnum() else "-" for ch in stream_rules.canon(url))[:80]
        with lock:
            counter["n"] += 1
            slot = counter["n"]
        work_dir = work_root / f"{slot:03d}-{key}"
        shot = capture(url, work_dir, args.run_time, args.scene_ratio, timeout)
        row = judge(url, name, shot, args.scene_ratio, args.freeze_frames)
        with lock:
            with args.out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n = counter["n"]
            rate = n / max(time.monotonic() - started_at, 1.0)
            eta = (len(todo) - n) / max(rate, 0.001)
            print(f"[{n}/{len(todo)}] {name} · {row['frames']} 帧 · {row['verdict']} "
                  f"· {rate * 60:.1f} 条/分钟 · 剩约 {eta / 60:.0f} 分钟", flush=True)
        if not args.keep_frames:
            shutil.rmtree(work_dir, ignore_errors=True)
        return row

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(one, todo))

    frozen = [r for r in rows if r["verdict"].startswith("冻帧")]
    undecided = [r for r in rows if "不作判" in r["verdict"]]
    print(f"\n[freeze] 本场 {len(rows)} 条：判冻 {len(frozen)} · 不作判 {len(undecided)} · "
          f"其余正常推进/短段相同", flush=True)
    for r in frozen:
        print(f"  冻帧 → {r['channel']} {r['url']} · {r['verdict']}", flush=True)
    print(f"[落盘] {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
