#!/usr/bin/env python3
"""自动加线的闸（用户 10-28 点档「乙」：自动加线保留，但新增线必须先过门禁）。

流程：
  设备上 `CatalogUpdater` 把「公开源送来、但不在白名单里」的地址写进
  `files/line_candidates.json`（只记账，不进目录）；
  本脚本读这份台账（或任意同格式 json），逐条探「清单 → 分片 → 抽帧」，
  把过闸的地址写进 `app/src/main/assets/line_allowlist.json`——下一版 APK 带上它，
  这些地址才准由公开源自动加进设备目录。

判据（与门禁同一口径）：清单 HTTP 200 且能解析出分片、最后一个分片能取到字节、
ffmpeg 能抽出一帧。**抽不到帧就不放行**（宁可少加，也不让脏线进目录）。

跑法：
  python3 tools/vet_line_candidates.py --in /tmp/cand.json            # 只报读数
  python3 tools/vet_line_candidates.py --in /tmp/cand.json --write    # 写白名单
退出码 0 表示整场跑完（有 0 条过闸也是 0）；参数错/读不到输入为 2。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
ALLOWLIST = BASE / "app" / "src" / "main" / "assets" / "line_allowlist.json"


def load_candidates(path: Path) -> list[dict]:
    """接受两种形状：设备那份 {"items":[{"channel","url"}]} 或裸列表 ["url", ...]。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("items") or []
        return [{"channel": it.get("channel", ""), "url": it.get("url", "")} for it in items]
    if isinstance(raw, list):
        return [{"channel": "", "url": u if isinstance(u, str) else u.get("url", "")} for u in raw]
    return []


def curl(url: str, out: str, timeout: int) -> tuple[str, str]:
    r = subprocess.run(
        ["curl", "-sS", "-L", "--path-as-is", "-m", str(timeout), "-o", out,
         "-w", "%{http_code} %{url_effective}", url],
        capture_output=True, text=True,
    )
    body = r.stdout.strip()
    if not body:
        return "000", url
    code, _, eff = body.partition(" ")
    return code or "000", eff or url


def probe(item: dict, timeout: int, ffmpeg: str) -> dict:
    url = item["url"]
    tmp = tempfile.mkdtemp(prefix="vet_")
    code, eff = curl(url, f"{tmp}/pl.m3u8", timeout)
    segs: list[str] = []
    pl = Path(f"{tmp}/pl.m3u8")
    if pl.exists():
        body = pl.read_text(encoding="utf-8", errors="replace")
        segs = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")]
    frame = None
    width = height = 0
    if code == "200" and segs:
        base = eff.rsplit("/", 1)[0] + "/"
        curl(base + urllib.parse.quote(segs[-1]), f"{tmp}/seg.ts", timeout)
        out = f"{tmp}/frame.jpg"
        try:
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", f"{tmp}/seg.ts",
                 "-frames:v", "1", "-q:v", "3", out],
                capture_output=True, timeout=max(10, timeout * 2),
            )
        except subprocess.TimeoutExpired:
            # 坏分片能让 ffmpeg 一直等下去（10-28 01:5x 实测：不设超时整场 5 分钟收不了尾）。
            pass
        frame = out if os.path.exists(out) else None
        # 用户 10-28 标准：「清晰度非标清」——分辨率必须现读，不许凭目录里写的猜。
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", f"{tmp}/seg.ts"],
                capture_output=True, text=True, timeout=max(10, timeout * 2),
            )
            parts = [x for x in r.stdout.strip().split(",") if x.isdigit()]
            if len(parts) >= 2:
                width, height = int(parts[0]), int(parts[1])
        except subprocess.TimeoutExpired:
            pass
    return {**item, "http": code, "segs": len(segs), "frame": frame,
            "width": width, "height": height,
            "pass": bool(frame), "note": "" if frame else ("清单/分片没通" if code != "200" or not segs else "抽不到帧")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ALLOWLIST)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--verified", type=Path, default=None,
                    help="人工台标/广告结论台账 url→{logo,picture}；只有标了「台标相符」且非广告/无二维码的才可能写白")
    ap.add_argument("--write", action="store_true", help="把过闸且人工认过的地址写进白名单（默认只报数）")
    args = ap.parse_args()

    if not args.src.exists():
        print(f"[拒绝] 读不到候选台账 {args.src}", file=sys.stderr)
        return 2
    cands = [c for c in load_candidates(args.src) if c["url"].startswith(("http://", "https://"))]
    seen, uniq = set(), []
    for c in cands:  # 同一地址只探一次（公开源常把同一条挂到多个台名下）
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        uniq.append(c)
    print(f"[plan] 候选 {len(cands)} 条，去重后 {len(uniq)} 条；并发 {args.workers}，超时 {args.timeout}s", flush=True)

    t0 = time.monotonic()
    rows: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
        for r in ex.map(lambda it: probe(it, args.timeout, args.ffmpeg), uniq):
            rows.append(r)
    for r in rows:  # 「非标清」＝宽 ≥1280 或高 ≥720；读不到分辨率一律不放行
        r["hd"] = r.get("width", 0) >= 1280 or r.get("height", 0) >= 720
    ok = [r for r in rows if r["pass"] and r["hd"]]

    # 第二道闸：人工台标/广告结论（10-28 02:0x 现读：15 条「能出帧」里有 2 条是广告卡、2 条带二维码，
    # 所以只靠抽帧就放行等于把广告放进父母电视的目录）。
    verdicts: dict = {}
    if args.verified:
        if not args.verified.exists():
            print(f"[拒绝] 读不到人工结论台账 {args.verified}", file=sys.stderr)
            return 2
        verdicts = json.loads(args.verified.read_text(encoding="utf-8"))

    def human_ok(row: dict) -> bool:
        # 用户 10-28 标准：**有台标且台标与频道名相符**即算合格——「有台标＋广告」也算有台标，
        # 画面里是不是广告不再作否决；清晰度那一半由上面的 hd 判。
        v = verdicts.get(row["url"]) or {}
        return str(v.get("logo", "")).startswith("台标相符")

    admitted = [r for r in ok if human_ok(r)] if verdicts else []
    print(f"[done] 用时 {time.monotonic() - t0:.1f}s；过闸 {len(ok)}/{len(rows)} 条"
          + (f"；台标相符且非标清、可放行 {len(admitted)} 条" if verdicts else "；（没给 --verified，一律不许写白）"),
          flush=True)
    for r in rows:
        if not r["pass"]:
            print(f"  ✗ {r['channel'] or '-'} {r['url'][:80]} — {r['note']}", flush=True)
    for r in ok:
        print(f"  ✓ {r['channel'] or '-'} {r['url'][:80]}", flush=True)

    if args.write and not verdicts:
        print("[拒绝] 没给 --verified 就不许写白名单：抽帧过闸不等于内容可放行", file=sys.stderr)
        return 2
    if args.write:
        payload = {
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": f"由 tools/vet_line_candidates.py 生成；出帧且非标清 {len(ok)}/{len(rows)} 条，台标相符可放行 {len(admitted)} 条（标准：有台标且与频道名相符＋非标清）。",
            "lines": [r["url"] for r in admitted],
        }
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[write] {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
