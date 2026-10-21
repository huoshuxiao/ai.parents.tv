#!/usr/bin/env python3
"""打包前线路检测报告：央视 / 卫视 / 辽宁体育。

「能看」按三级证据逐层记，不合并成一句「可用」：
  1) 清单可达 —— HTTP 2xx 且正文真是 #EXTM3U（302 跟随后仍要是清单）；
  2) 分片可取 —— 真的拉到 >1KB 媒体字节（复用 tools/health_probe.py 的判定）；
  3) 画面出帧 —— VLC 抽到 jpg（复用 tools/probe_channels.py 的 capture_frames）。
台标是不是这个台仍由人工看缩略图后写 `<out>/review.json`，脚本只标「待人工复核」。

    python3 tools/prepack_report.py --apk dist/ParentsTV-v1.1.5-debug.apk
    python3 tools/prepack_report.py --use-cache      # 沿用本次缓存里的清单/分片读数
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "tools"))
import health_probe  # noqa: E402
import probe_channels  # noqa: E402

DEFAULT_OUT = BASE / "tools" / ".cache" / "prepack"
DEFAULT_DIST = BASE / "dist"
UA = health_probe.UA
FRAME_OK_BYTES = 4_000
# 抽帧时一台最多试几条线：只试「分片可取」的线，第一条出帧就停。
MAX_FRAME_TRIES = 3


def log(message: str) -> None:
    print(message, flush=True)


def is_target(channel: dict, must: tuple[str, ...]) -> bool:
    if channel.get("subgroup") in ("央视", "卫视"):
        return True
    return channel.get("name") in must


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def apk_asset_sha(apk: Path, asset: str) -> str:
    with zipfile.ZipFile(apk) as zf:
        return hashlib.sha256(zf.read(asset)).hexdigest()


def run_health(catalog: Path, out_dir: Path, workers: int, use_cache: bool) -> dict[str, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "line_health.jsonl"
    if results.exists() and not use_cache:
        results.unlink()
    command = [
        sys.executable, str(BASE / "tools" / "health_probe.py"),
        "--catalog", str(catalog), "--out", str(out_dir), "--workers", str(workers),
    ]
    log("[health] " + " ".join(command))
    proc = subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if proc.returncode != 0 and not results.exists():
        raise SystemExit(f"health_probe 失败 EXIT={proc.returncode}，且没有产出读数文件")
    rows: dict[str, dict] = {}
    for line in results.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rows[row["url"]] = row
    return rows


def recheck_must_watch(targets: list[dict], health: dict[str, dict], results_path: Path,
                       rounds: int) -> dict[str, int]:
    """必看台若首轮一条都没取到分片，就串行复测几轮。

    首轮是 32 并发，慢源会被读成「连接超时」；实测 dassby 的辽宁体育首轮超时、
    单发 0.04 秒就 200。复测结果照样追加进 line_health.jsonl，读数可回溯。
    """
    tally = {"复测线路": 0, "复测后转好": 0}
    if rounds < 1:
        return tally
    for channel in targets:
        if any(health.get(u, {}).get("verdict") == "可取到分片" for u in channel["streams"]):
            continue
        for url in channel["streams"]:
            tally["复测线路"] += 1
            for _ in range(rounds):
                row = health_probe.probe_one(url, 12.0, 15.0)
                with results_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                health[url] = row
                if row["verdict"] == "可取到分片":
                    tally["复测后转好"] += 1
                    break
                time.sleep(2)
    return tally


def grab_frame(url: str, work_dir: Path, timeout: int) -> dict:
    """抽帧：出帧的 jpg 落到 work_dir/frame_first.jpg，返回路径与字节数。"""
    if work_dir.exists():
        shutil.rmtree(work_dir)
    captured = probe_channels.capture_frames(url, work_dir, timeout)
    frames = [f for f in captured["frames"] if f.stat().st_size > FRAME_OK_BYTES]
    if not frames:
        return {"frame": None, "bytes": 0, "timed_out": captured["timed_out"],
                "elapsed_ms": captured["elapsed_ms"]}
    keep = work_dir / "frame_first.jpg"
    shutil.copyfile(frames[0], keep)
    return {"frame": keep, "bytes": keep.stat().st_size, "timed_out": captured["timed_out"],
            "elapsed_ms": captured["elapsed_ms"]}


def probe_channel(channel: dict, health: dict[str, dict], frames_dir: Path,
                  timeout: int) -> dict:
    lines = []
    for index, url in enumerate(channel["streams"]):
        row = health.get(url)
        verdict = row["verdict"] if row else "未测"
        lines.append({
            "index": index, "url": url, "verdict": verdict,
            "http_status": (row or {}).get("http_status"),
            "seg_bytes": (row or {}).get("seg_bytes", 0),
            "detail": (row or {}).get("detail", ""),
        })
    segment_ok = [line for line in lines if line["verdict"] == "可取到分片"]
    frame = None
    frame_line = None
    for line in segment_ok[:MAX_FRAME_TRIES]:
        info = grab_frame(line["url"], frames_dir / channel["id"] / f"line{line['index']}", timeout)
        if info["frame"]:
            frame, frame_line = info, line
            break
    return {
        "id": channel["id"], "name": channel["name"], "subgroup": channel.get("subgroup"),
        "lines": lines, "segment_ok": len(segment_ok), "frame": frame, "frame_line": frame_line,
        "frame_tried": min(len(segment_ok), MAX_FRAME_TRIES),
    }


def load_review(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["url"]: row.get("logo", "待人工复核") for row in data.get("reviews", [])}


def render(report: dict) -> str:
    groups: dict[str, list[dict]] = {}
    for result in report["channels"]:
        groups.setdefault(result["subgroup"] or "其他", []).append(result)
    out = [
        f"# 打包前线路检测报告 · {report['title']}",
        "",
        f"- 生成时刻：{report['generated_at']}",
        f"- 目录文件：`{report['catalog']}`（{report['catalog_bytes']:,} B）",
        f"- 目录 sha256：`{report['catalog_sha256']}`",
        f"- 待打包 APK：{report['apk'] or '（本次未指定，报告只描述目录，不声明包体一致）'}",
        f"- APK 内 `assets/channels.json` sha256：`{report['apk_sha256'] or '未取'}`",
        f"- 覆盖：央视/卫视/辽宁体育 共 {len(report['channels'])} 台 / "
        f"{sum(len(r['lines']) for r in report['channels'])} 条线路",
        f"- 必看台串行复测：{report['recheck']['复测线路']} 条首轮全灭的线路被复测，"
        f"其中 {report['recheck']['复测后转好']} 条复测后取到分片"
        f"（并发探测会把慢源读成超时，这一层专门补它）",
        "",
        "> 三级证据各自独立成列：清单可达（HTTP 2xx + 正文 #EXTM3U）→ 分片可取（>1KB 媒体字节）"
        "→ 画面出帧（VLC 抽到 jpg）。出帧不等于台标相符，最后一列仍要人工看缩略图。",
        "",
    ]
    for group in ("央视", "卫视", "国内体育"):
        results = groups.get(group)
        if not results:
            continue
        frames = sum(1 for r in results if r["frame"])
        segs = sum(1 for r in results if r["segment_ok"])
        out += [
            f"## {group}：{len(results)} 台 · 分片可取 {segs} 台 · 出帧 {frames} 台",
            "",
            "| 频道 | 线路 | 清单 2xx | 分片可取 | 出帧 | 台标 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in sorted(results, key=lambda x: (x["subgroup"] or "", x["name"])):
            playlist_ok = sum(1 for line in r["lines"]
                              if isinstance(line["http_status"], int) and 200 <= line["http_status"] < 300)
            if r["frame"]:
                rel = Path("..") / r["frame"]["frame"].relative_to(BASE)
                which = r["frame_line"]["index"] + 1
                frame_cell = f"[{r['frame']['bytes']:,} B]({rel}) 第{which}条"
                logo = report["review"].get(r["frame_line"]["url"], "待人工复核")
            elif r["segment_ok"]:
                frame_cell = "—"
                logo = "抽帧未出画面"
            else:
                frame_cell = "—"
                logo = "无可抽帧线路"
            out.append(
                f"| {r['name']} | {len(r['lines'])} | {playlist_ok}/{len(r['lines'])} "
                f"| {r['segment_ok']}/{len(r['lines'])} | {frame_cell} | {logo} |"
            )
        out.append("")
    dead = [r for r in report["channels"] if not r["frame"]]
    out.append("## 未出帧的台（打包后按现状就是「点开可能黑屏」）")
    out.append("")
    if dead:
        out.append(f"- 共 {len(dead)} 台：" + "、".join(r["name"] for r in dead))
    else:
        out.append("- 无")
    out += [
        "",
        "## 判定",
        "",
    ]
    for gate in report["gates"]:
        out.append(f"- {gate['name']}：**{gate['status']}** —— {gate['evidence']}")
    out += [
        "",
        "## 复现",
        "",
        "```bash",
        report["command"],
        "```",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=BASE / "app/src/main/assets/channels.json")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--apk", type=Path, default=None)
    parser.add_argument("--title", default="v?")
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--frame-timeout", type=int, default=25)
    parser.add_argument("--use-cache", action="store_true", help="沿用上次清单/分片读数，不重新探测")
    parser.add_argument("--skip-frames", action="store_true", help="只出清单/分片两级证据")
    parser.add_argument("--must", default="辽宁体育", help="必看台，逗号分隔；首轮全灭会串行复测")
    parser.add_argument("--recheck", type=int, default=3, help="必看台每条线路的复测轮数，0=不复测")
    args = parser.parse_args()
    if args.apk and not args.apk.exists():
        raise SystemExit(f"--apk 指向的文件不存在：{args.apk}")

    catalog = args.catalog.resolve()
    must = tuple(name.strip() for name in args.must.split(",") if name.strip())
    data = json.loads(catalog.read_text(encoding="utf-8"))
    targets = [c for c in data["channels"] if is_target(c, must)]
    if not targets:
        raise SystemExit(f"目录里没匹配到央视/卫视/{args.must}：{catalog}")

    args.out.mkdir(parents=True, exist_ok=True)
    target_catalog = args.out / "target_catalog.json"
    target_catalog.write_text(json.dumps({"channels": targets}, ensure_ascii=False, indent=1),
                              encoding="utf-8")

    health = run_health(target_catalog, args.out, args.workers, args.use_cache)
    recheck = recheck_must_watch(
        [c for c in targets if c["name"] in must],
        health, args.out / "line_health.jsonl", args.recheck)
    if recheck["复测线路"]:
        log(f"[recheck] 必看台复测 {recheck['复测线路']} 条，转好 {recheck['复测后转好']} 条")
    frames_dir = args.out / "frames"
    results: list[dict] = []
    started = time.time()
    if args.skip_frames:
        for channel in targets:
            results.append({
                "id": channel["id"], "name": channel["name"], "subgroup": channel.get("subgroup"),
                "lines": [{"index": i, "url": u,
                           "verdict": health.get(u, {}).get("verdict", "未测"),
                           "http_status": health.get(u, {}).get("http_status"),
                           "seg_bytes": health.get(u, {}).get("seg_bytes", 0), "detail": ""}
                          for i, u in enumerate(channel["streams"])],
                "segment_ok": sum(1 for u in channel["streams"]
                                  if health.get(u, {}).get("verdict") == "可取到分片"),
                "frame": None, "frame_line": None, "frame_tried": 0,
            })
    else:
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(
                lambda c: probe_channel(c, health, frames_dir, args.frame_timeout), targets))
        log(f"[frames] {len(results)} 台，用时 {time.time() - started:.0f}s")

    order = {c["id"]: i for i, c in enumerate(targets)}
    results.sort(key=lambda r: (r["subgroup"] or "", order[r["id"]]))

    apk_path = args.apk.resolve() if args.apk else None
    apk_sha = None
    if apk_path:
        apk_sha = apk_asset_sha(apk_path, "assets/channels.json")
    review_path = args.out / "review.json"
    report = {
        "title": args.title,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "catalog": str(catalog.relative_to(BASE)),
        "catalog_bytes": catalog.stat().st_size,
        "catalog_sha256": sha256_file(catalog),
        "apk": str(apk_path.relative_to(BASE)) if apk_path else None,
        "apk_sha256": apk_sha,
        "channels": results,
        "recheck": recheck,
        "review": load_review(review_path),
        "dist_dir": args.dist,
        "command": "python3 tools/prepack_report.py " + " ".join(sys.argv[1:]),
    }

    by_group: dict[str, list[dict]] = {}
    for r in results:
        by_group.setdefault(r["subgroup"] or "其他", []).append(r)

    def gate(name: str, status: str, evidence: str) -> dict:
        return {"name": name, "status": status, "evidence": evidence,
                "ok": status == "通过", "checked": status != "未查"}

    gates = []
    lns = next((r for r in results if r["name"] == "辽宁体育"), None)
    if lns is None:
        gates.append(gate("G1 辽宁体育必须有出帧线路", "不通过", "目录里没找到「辽宁体育」这一台"))
    elif args.skip_frames:
        gates.append(gate("G1 辽宁体育必须有出帧线路", "未查",
                          f"--skip-frames 只出了清单/分片两级：分片可取 {lns['segment_ok']}/{len(lns['lines'])} 条，没抽帧"))
    elif lns["frame"]:
        gates.append(gate("G1 辽宁体育必须有出帧线路", "通过",
                          f"辽宁体育 {lns['segment_ok']}/{len(lns['lines'])} 条分片可取，"
                          f"第{lns['frame_line']['index'] + 1}条抽到 {lns['frame']['bytes']:,} B 画面"))
    else:
        gates.append(gate("G1 辽宁体育必须有出帧线路", "不通过",
                          f"辽宁体育 {len(lns['lines'])} 条线路，分片可取 {lns['segment_ok']} 条，抽帧 0 张"))
    for group in ("央视", "卫视"):
        rs = by_group.get(group, [])
        n = sum(1 for r in rs if r["frame"])
        if args.skip_frames:
            gates.append(gate(f"G2 {group} 至少有 1 台出帧", "未查",
                              f"--skip-frames 未抽帧；{group} {len(rs)} 台中 "
                              f"{sum(1 for r in rs if r['segment_ok'])} 台分片可取"))
        else:
            gates.append(gate(f"G2 {group} 至少有 1 台出帧", "通过" if n >= 1 else "不通过",
                              f"{group} {len(rs)} 台中 {n} 台出帧"))
    missing = [r for r in results if any(line["verdict"] == "未测" for line in r["lines"])]
    gates.append(gate(
        "G3 每一台每条线路都有本次读数", "通过" if not missing else "不通过",
        f"{sum(len(r['lines']) for r in results)} 条线路全部有本次读数" if not missing
        else f"{len(missing)} 台含「未测」线路：" + "、".join(r["name"] for r in missing)))
    if args.apk:
        gates.append(gate(
            "G4 报告描述的目录 == APK 内的目录",
            "通过" if apk_sha == report["catalog_sha256"] else "不通过",
            f"APK `{report['apk']}` 内 sha256 `{(apk_sha or '')[:16]}…` vs 目录 `{report['catalog_sha256'][:16]}…`"))
    else:
        gates.append(gate("G4 报告描述的目录 == APK 内的目录", "未查", "没传 --apk，这一格没查"))
    report["gates"] = gates

    (args.out / "prepack_report.json").write_text(
        json.dumps({**report, "dist_dir": str(report["dist_dir"])}, ensure_ascii=False, indent=1,
                   default=str), encoding="utf-8")

    args.dist.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^0-9A-Za-z._-]+", "-", args.title)
    md_path = args.dist / f"prepack_report-{slug}-{time.strftime('%Y%m%d-%H%M')}.md"
    md_path.write_text(render(report), encoding="utf-8")
    log(f"[report] {md_path}")
    failed = [g["name"] for g in gates if g["status"] == "不通过"]
    unchecked = [g["name"] for g in gates if g["status"] == "未查"]
    for gate_row in gates:
        log(f"[gate] {gate_row['status']:<4} {gate_row['name']} —— {gate_row['evidence']}")
    if failed:
        log(f"[verdict] {len(failed)} 格不通过：{'、'.join(failed)}"
            + (f"；另有 {len(unchecked)} 格未查" if unchecked else ""))
        return 1
    log(f"[verdict] 已查的 {len(gates) - len(unchecked)} 格全部通过"
        + (f"；{len(unchecked)} 格未查：{'、'.join(unchecked)}" if unchecked else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
