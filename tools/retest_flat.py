#!/usr/bin/env python3
"""央视·卫视 全线路 3 轮回测：每轮都从线路 1 开始，所有截图平铺在同一个目录。

用法：
    python3 tools/retest_flat.py                 # 3 轮、20 并发、每条 25 秒
    python3 tools/retest_flat.py --rounds 3 --workers 20
    python3 tools/retest_flat.py --limit 20      # 冒烟：只跑前 20 条线
    python3 tools/retest_flat.py --retry-no-frame --workers 8
                                                 # 只补台账里「这一轮没出帧」的格子；
                                                 # 已出过帧的一格不动（不覆写同名截图）

产物（全部在 tools/.cache/retest3_flat/，没有 frames/<channel>/line<k> 这种子目录）：
    {channel_id}__L{line}__R{round}.jpg   每条线每轮一张（取该轮最后一帧，画面已稳定）
        注意 {channel_id} 是目录里的 id，不是台号也不是台名：如
        cctv-1ffa84b5c3＝宁夏卫视、cctv-2e212df366＝CCTV-4K 超高清；
        L 从 01 起（=目录 streams 下标+1），R 是第 1..3 轮。
    lines.jsonl                          一行一条（轮次,台 id,台名,线路号,地址,平铺文件名,尺寸,md5…）
    summary.json                         三轮汇总（给程序复核 / 构建删线用）

为什么不再按「台/线路」建子目录：用户 10-25 明确要求「回测截图放统一的目录，不要子目录」，
人工复核时按文件名就能看出「哪一台的第几条线、第几轮」。
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import probe_channels  # noqa: E402

OUT = ROOT / "tools/.cache/retest3_flat"
WORK = ROOT / "tools/.cache/retest3_flat_work"


def md5(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


def safe(text):
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", text)


def targets():
    catalog = json.load(open(ROOT / "app/src/main/assets/channels.json", encoding="utf-8"))
    rows = []
    for ch in catalog["channels"]:
        # 央视·卫视这个分组在目录里就是 category == "cctv"（含 4K 超高清那两台）。
        if ch.get("category") != "cctv":
            continue
        for i, url in enumerate(ch.get("streams") or []):
            rows.append((ch["id"], ch["name"], i, url))
    return rows


def main():
    global OUT, WORK
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(OUT),
                    help="这一轮截图与台账写到哪个目录（默认沿用 retest3_flat；换新目录就不覆写旧证据）")
    ap.add_argument("--retry-no-frame", action="store_true",
                    help="只补台账里没出帧的（轮次, 线路）格子；出过帧的一律不动")
    ap.add_argument("--only-tv-failures", default="", dest="only_tv_failures",
                    help="只抽真机回测台账（tools/tv_retest_lines.py 的 lines.jsonl）里「没成」的那些格子："
                         "电视上判挂的地址才值得拿主持机再抽一帧看是不是那页扫码告警，不重抽全组")
    ap.add_argument("--tv-toolv", type=int, default=0, dest="tv_toolv",
                    help="那份真机台账的尺版本；0＝用 tv_retest_lines 当前的 TOOLV")
    ap.add_argument("--only-lines", default="", dest="only_lines",
                    help="只抽这份名单里的格子（json 数组或 jsonl，每行含 channel_id/line_index/url）："
                         "门禁 G3 判红成广告段的那几条要按时段复抽定案，不重抽全组")

    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="只打印这一跑会抽哪些线，不建目录、不起 VLC、不落任何文件")
    args = ap.parse_args()

    OUT = Path(args.out)
    if not OUT.is_absolute():
        # --out 允许写相对路径（相对仓库根），否则下面 OUT.relative_to(ROOT) 会当场炸。
        OUT = ROOT / OUT
    WORK = ROOT / "tools/.cache" / f"{OUT.name}_work"
    ledger = OUT / "lines.jsonl"

    all_targets = targets()
    if args.only_tv_failures:
        # 名单直接调真机那把尺的 failure_keys——两处各写一遍"什么算失败"迟早分叉。
        import tv_retest_lines as tvrl
        keys = set(tvrl.failure_keys(args.only_tv_failures,
                                     toolv=(args.tv_toolv or None), rounds=1))
        before = len(all_targets)
        all_targets = [t for t in all_targets if (t[0], t[2], t[3]) in keys]
        print(f"[filter] --only-tv-failures：{before} 条 → {len(all_targets)} 条"
              f"（真机台账里没成的 {len(keys)} 格；尺版本 toolv={args.tv_toolv or tvrl.TOOLV}）",
              flush=True)
        if not all_targets:
            print("[plan] 真机台账里没有失败的格子，这一遍不用抽帧", flush=True)
            return 0
    if args.only_lines:
        # 名单来自门禁报告本身（G3 判红成广告段的那几条），字段名与报告 JSON 对齐，不另立一套口径。
        raw = Path(args.only_lines).read_text(encoding="utf-8").strip()
        doc = json.loads(raw) if raw.startswith("[") else [json.loads(l) for l in raw.splitlines() if l.strip()]
        keys = {(e["channel_id"], int(e["line_index"]), e["url"]) for e in doc}
        before = len(all_targets)
        all_targets = [t for t in all_targets if (t[0], t[2], t[3]) in keys]
        print(f"[filter] --only-lines：{before} 条 → {len(all_targets)} 条（名单 {len(keys)} 格）", flush=True)
        if not all_targets:
            print("[plan] 名单里没有可抽的格（地址可能已不在当前目录里）", flush=True)
            return 0
    if args.limit:
        all_targets = all_targets[: args.limit]
    print(f"[plan] 央视·卫视 {len(all_targets)} 条线 × {args.rounds} 轮"
          f"（每轮从线路 1 开始，截图平铺到 {OUT.relative_to(ROOT)}）", flush=True)
    if args.dry_run:
        # 只念"会抽哪些线"：不建目录、不起 VLC、一个字节都不写（默认落点就是上一批的证据目录，
        # 拿它试跑会覆掉旧帧，所以这一档必须是真 no-write）。
        for t in all_targets[:20]:
            print(f"  {t[0]} L{t[2] + 1} {t[3]}", flush=True)
        print(f"[dry-run] 不起场，共 {len(all_targets)} 条线"
              f"（上面只列前 20 条），台账 {ledger.relative_to(ROOT)} 没动过", flush=True)
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    done = set()
    if ledger.exists():
        for raw in ledger.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if args.retry_no_frame and not row.get("frame"):
                # 没出帧的格子不算「已完成」：上一次可能只是并发/源抖动，
                # 补跑只认「这一次真拿到图」。
                continue
            # 键里必须带 url：同一个「台＋线路号」换过地址时（补新线常直接占到 L02），
            # 只按 (轮, 台, 线路号) 判「已跑过」会把新地址整条漏掉。
            done.add((int(row["round"]), row["channel_id"], int(row["line_index"]), row["url"]))

    for rnd in range(1, args.rounds + 1):
        todo = [t for t in all_targets if (rnd, t[0], t[2], t[3]) not in done]
        print(f"[round {rnd}] 待跑 {len(todo)} 条（已完成 {len(all_targets) - len(todo)}）", flush=True)
        if not todo:
            continue

        def one(item):
            cid, name, idx, url = item
            work = WORK / f"r{rnd}_{safe(cid)}_L{idx + 1}"
            t0 = time.monotonic()
            res = probe_channels.capture_frames(url, work, args.timeout, native=True)
            frames = res.get("frames") or []
            flat = None
            if frames:
                flat = OUT / f"{safe(cid)}__L{idx + 1:02d}__R{rnd}.jpg"
                # 取最后一帧：开头那两帧可能还停在解码器/播放器自己的占位画面上，
                # 台标还没出来；固定 7 秒窗口的尾巴才是这一轮真正看到的画面。
                shutil.copyfile(frames[-1], flat)
            row = {
                "round": rnd,
                "channel_id": cid,
                "channel": name,
                "line_index": idx,
                "url": url,
                "frame": flat.name if flat else None,
                "frame_count": len(frames),
                "timed_out": res.get("timed_out"),
                "exit_code": res.get("exit_code"),
                "elapsed_ms": res.get("elapsed_ms") or int((time.monotonic() - t0) * 1000),
            }
            shutil.rmtree(work, ignore_errors=True)
            if flat:
                try:
                    from PIL import Image
                    with Image.open(flat) as im:
                        row["width"], row["height"] = im.size
                    row["bytes"] = flat.stat().st_size
                    row["md5"] = md5(flat)
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
            return row

        ok = 0
        t_start = time.monotonic()
        with ledger.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(args.workers) as pool:
            futures = {pool.submit(one, t): t for t in todo}
            for n, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                if row.get("frame"):
                    ok += 1
                if n % 10 == 0 or n == len(todo):
                    el = time.monotonic() - t_start
                    print(f"[r{rnd} {n}/{len(todo)}] 出帧 {ok} 用时 {el:.0f}s", flush=True)
        print(f"[round {rnd}] 出帧 {ok}/{len(todo)}", flush=True)

    summarize(ledger, OUT / "summary.json", args.rounds)
    return 0


def summarize(ledger, out_path, rounds):
    """按（台, 线路号, 地址）汇总三轮：出没出帧、有没有图、供程序复核接着判台标。"""
    grouped = {}
    if ledger.exists():
        for raw in ledger.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            key = (row["channel_id"], int(row["line_index"]), row["url"])
            rec = grouped.setdefault(key, {
                "channel_id": row["channel_id"],
                "channel": row["channel"],
                "line_index": int(row["line_index"]),
                "url": row["url"],
                "rounds": {},
            })
            rec["rounds"][str(row["round"])] = {
                "frame": row.get("frame"),
                "width": row.get("width"),
                "height": row.get("height"),
                "md5": row.get("md5"),
                "timed_out": row.get("timed_out"),
                "elapsed_ms": row.get("elapsed_ms"),
            }
    items = []
    for key in sorted(grouped, key=lambda k: (k[0], k[1])):
        rec = grouped[key]
        have = [r for r in rec["rounds"].values() if r.get("frame")]
        rec["frames"] = len(have)
        rec["all_no_frame"] = len(have) == 0
        items.append(rec)
    summary = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rounds": rounds,
        "lines": len(items),
        "no_frame_all_rounds": sum(1 for r in items if r["all_no_frame"]),
        "items": items,
    }
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[summary] {len(items)} 条线；三轮都没出帧 {summary['no_frame_all_rounds']} 条"
          f" → {out_path.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
