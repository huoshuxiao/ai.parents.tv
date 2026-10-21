#!/usr/bin/env python3
"""Probe every catalog stream and capture frames for visual logo review.

All writes stay under the project directory. The visual review is intentionally
separate from the network probe: a captured frame is not considered a valid
channel picture until a station logo is visible.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "app/src/main/assets/channels.json"
DEFAULT_OUT = ROOT / "tools/.cache/channel_probe"
VLC = Path("/snap/bin/vlc")
UA = (
    "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HTTP_HEADERS = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Range": "bytes=0-1023",
}
FRAME_WIDTH = 480
FRAME_HEIGHT = 270
PRINT_LOCK = threading.Lock()


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def load_catalog(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def thread_session() -> requests.Session:
    session = getattr(threading.current_thread(), "_probe_session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HTTP_HEADERS)
        threading.current_thread()._probe_session = session
    return session


def probe_http(url: str) -> dict:
    started = time.monotonic()
    response = None
    try:
        response = thread_session().get(
            url,
            timeout=(5, 7),
            stream=True,
            allow_redirects=True,
        )
        code = response.status_code
        content_type = response.headers.get("Content-Type", "")
        chunk = response.raw.read(512, decode_content=True) or b""
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if code not in (200, 206):
            return compact(url, elapsed_ms, error=f"HTTP {code}", status=code, content_type=content_type)
        if not chunk:
            return compact(url, elapsed_ms, error="无数据", status=code, content_type=content_type)
        beginning = chunk[:512]
        if beginning.startswith(b"\xef\xbb\xbf"):
            beginning = beginning[3:]
        beginning = beginning.lstrip()
        looks_like_hls = beginning.startswith(b"#EXTM3U")
        looks_like_html = beginning.startswith(b"<") or b"<html" in beginning.lower()
        if looks_like_html and not looks_like_hls:
            return compact(url, elapsed_ms, error="返回网页", status=code, content_type=content_type)
        return compact(url, elapsed_ms, status=code, content_type=content_type)
    except requests.Timeout:
        return compact(url, int((time.monotonic() - started) * 1000), error="响应超时")
    except requests.RequestException as error:
        return compact(
            url,
            int((time.monotonic() - started) * 1000),
            error=f"{type(error).__name__}",
        )
    except Exception as error:
        return compact(
            url,
            int((time.monotonic() - started) * 1000),
            error=f"{type(error).__name__}",
        )
    finally:
        if response is not None:
            response.close()


def compact(url: str, elapsed_ms: int, status: int | None = None, error: str | None = None, content_type: str = "") -> dict:
    return {
        "url": url,
        "status": status,
        "ok": error is None,
        "error": error,
        "elapsed_ms": elapsed_ms,
        "content_type": content_type,
    }


def run_http_phase(catalog: dict, out_dir: Path, workers: int, refresh: bool) -> dict:
    urls = sorted({url for channel in catalog["channels"] for url in channel.get("streams", [])})
    result_path = out_dir / "http_results.json"
    existing = {}
    if result_path.exists() and not refresh:
        existing = json.loads(result_path.read_text(encoding="utf-8"))
    pending = [url for url in urls if url not in existing]
    log(f"[http] unique={len(urls)} cached={len(existing)} pending={len(pending)} workers={workers}")
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe_http, url): url for url in pending}
        done = 0
        for future in as_completed(futures):
            url = futures[future]
            try:
                existing[url] = future.result()
            except Exception as error:
                existing[url] = {"url": url, "ok": False, "error": f"worker:{error}", "status": None, "elapsed_ms": 0}
            done += 1
            if done % 100 == 0 or done == len(pending):
                ok = sum(1 for item in existing.values() if item.get("ok"))
                log(
                    f"[http] {done}/{len(pending)} checked | ok={ok}/{len(existing)} | "
                    f"elapsed={time.monotonic() - started:.0f}s",
                )
                result_path.write_text(
                    json.dumps(existing, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                )
    result_path.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for item in existing.values() if item.get("ok"))
    log(f"[http] done ok={ok}/{len(existing)} -> {result_path}")
    return existing


def channel_candidates(channel: dict, http_results: dict, max_attempts: int) -> list[str]:
    ok_urls = [url for url in channel.get("streams", []) if http_results.get(url, {}).get("ok")]
    return ok_urls[:max_attempts]


def kill_process_group(process: subprocess.Popen) -> None:
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


def capture_frames(url: str, work_dir: Path, timeout_seconds: int) -> dict:
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(VLC),
        "-I", "dummy",
        "--ignore-config",
        "--no-audio",
        "--no-spu",
        "--no-osd",
        "--no-video-title-show",
        "--vout=vdummy",
        "--aout=dummy",
        "--avcodec-hw=none",
        "--quiet",
        "--ipv4",
        "--http-user-agent", UA,
        "--video-filter=scene",
        "--scene-format=jpg",
        f"--scene-width={FRAME_WIDTH}",
        f"--scene-height={FRAME_HEIGHT}",
        "--scene-ratio=50",
        "--scene-path", str(work_dir),
        "--scene-prefix=frame_",
        "--run-time=7",
        "--play-and-exit",
        url,
    ]
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    started = time.monotonic()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        if process.poll() is None:
            kill_process_group(process)
    frames = sorted(work_dir.glob("frame_*.jpg"))
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "frames": frames,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "elapsed_ms": elapsed_ms,
    }


def select_frames(frames: list[Path]) -> list[Image.Image]:
    if not frames:
        return []
    indexes = sorted({0, len(frames) // 2, len(frames) - 1})
    images = []
    for index in indexes:
        try:
            image = Image.open(frames[index])
            image.load()
            images.append(image.convert("RGB"))
        except Exception:
            continue
    return images


def frame_name(index: int, part: int) -> str:
    return f"{index:04d}_{part}.jpg"


def process_channel(channel: dict, index: int, candidates: list[str], out_dir: Path, timeout_seconds: int) -> dict:
    frames_dir = out_dir / "frames"
    work_root = out_dir / "work"
    attempts = []
    selected_images: list[Image.Image] = []
    if not candidates:
        return {
            "id": channel["id"],
            "index": index,
            "name": channel["name"],
            "category": channel["category"],
            "subgroup": channel.get("subgroup", ""),
            "streams": len(channel.get("streams", [])),
            "status": "no_http_line",
            "attempts": [],
            "frames": [],
        }

    for attempt_index, url in enumerate(candidates):
        work_dir = work_root / f"{index:04d}_{attempt_index}"
        capture = capture_frames(url, work_dir, timeout_seconds)
        attempts.append({
            "url": url,
            "frames": len(capture["frames"]),
            "elapsed_ms": capture["elapsed_ms"],
            "exit_code": capture["exit_code"],
            "timed_out": capture["timed_out"],
        })
        if capture["frames"]:
            selected_images = select_frames(capture["frames"])
            break

    saved_frames = []
    if selected_images:
        for part, image in enumerate(selected_images):
            target = frames_dir / frame_name(index, part)
            image.save(target, "JPEG", quality=86)
            saved_frames.append(str(target.relative_to(ROOT)))
    if work_root.exists():
        for work_dir in work_root.glob(f"{index:04d}_*"):
            shutil.rmtree(work_dir, ignore_errors=True)
    status = "frame_ok" if saved_frames else "no_frame"
    return {
        "id": channel["id"],
        "index": index,
        "name": channel["name"],
        "category": channel["category"],
        "subgroup": channel.get("subgroup", ""),
        "streams": len(channel.get("streams", [])),
        "status": status,
        "attempts": attempts,
        "frames": saved_frames,
    }


def run_capture_phase(
    catalog: dict,
    http_results: dict,
    out_dir: Path,
    workers: int,
    max_attempts: int,
    timeout_seconds: int,
    limit: int,
    retry_failed: bool,
) -> None:
    frames_dir = out_dir / "frames"
    work_dir = out_dir / "work"
    frames_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "channel_results.jsonl"
    existing = {}
    if result_path.exists():
        for line in result_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            existing[item["id"]] = item

    todo = []
    for index, channel in enumerate(catalog["channels"]):
        previous = existing.get(channel["id"])
        if previous and not (retry_failed and previous.get("status") != "frame_ok"):
            continue
        candidates = channel_candidates(channel, http_results, max_attempts)
        todo.append((index, channel, candidates))
    if limit:
        todo = todo[:limit]
    log(f"[capture] todo={len(todo)} cached={len(existing)} workers={workers} attempts={max_attempts}")
    started = time.monotonic()
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool, result_path.open("a", encoding="utf-8") as output:
        futures = {
            pool.submit(process_channel, channel, index, candidates, out_dir, timeout_seconds): channel
            for index, channel, candidates in todo
        }
        for future in as_completed(futures):
            channel = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "id": channel["id"],
                    "index": catalog["channels"].index(channel),
                    "name": channel["name"],
                    "category": channel["category"],
                    "subgroup": channel.get("subgroup", ""),
                    "streams": len(channel.get("streams", [])),
                    "status": "worker_error",
                    "attempts": [],
                    "frames": [],
                    "error": f"{type(error).__name__}: {error}",
                }
            output.write(json.dumps(result, ensure_ascii=False) + "\n")
            output.flush()
            existing[result["id"]] = result
            completed += 1
            if completed % 10 == 0 or completed == len(todo):
                ok = sum(1 for item in existing.values() if item.get("status") == "frame_ok")
                log(
                    f"[capture] {completed}/{len(todo)} done | frames={ok} | "
                    f"elapsed={time.monotonic() - started:.0f}s",
                )
    log(f"[capture] finished -> {result_path}")


def font_candidates(bold: bool) -> list[str]:
    if bold:
        return [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        ]
    return [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in font_candidates(bold):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def selected_frame_for(out_dir: Path, index: int) -> Path | None:
    candidates = [out_dir / "frames" / frame_name(index, part) for part in (1, 2, 0)]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def draw_contact_sheets(catalog: dict, out_dir: Path) -> None:
    result_path = out_dir / "channel_results.jsonl"
    if not result_path.exists():
        raise SystemExit("运行 capture 阶段后再生成contact sheet")
    results = {}
    for line in result_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            results[item["id"]] = item
    reviewable = [
        channel for channel in catalog["channels"]
        if results.get(channel["id"], {}).get("status") == "frame_ok"
    ]
    sheet_dir = out_dir / "sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    for old in sheet_dir.glob("page_*.jpg"):
        old.unlink()

    per_page = 24
    pages = (len(reviewable) + per_page - 1) // per_page
    header_font = load_font(28, bold=True)
    title_font = load_font(18, bold=True)
    label_font = load_font(15)
    small_font = load_font(12)
    width, height = 1920, 1000
    margin_x, margin_y = 16, 14
    cell_w, cell_h = 306, 205
    image_h = 172
    manifest = []

    for page_index in range(pages):
        page = Image.new("RGB", (width, height), (9, 14, 24))
        draw = ImageDraw.Draw(page)
        draw.text(
            (margin_x, 10),
            f"全频道抽帧复核  {page_index + 1}/{pages}  ·  {len(reviewable)} 个有画面频道  ·  缺台标/广告需人工标记",
            fill=(255, 255, 255),
            font=header_font,
        )
        draw.line((margin_x, 48, width - margin_x, 48), fill=(45, 60, 86), width=1)
        page_items = reviewable[page_index * per_page:(page_index + 1) * per_page]
        for cell_index, channel in enumerate(page_items):
            result = results[channel["id"]]
            frame_path = selected_frame_for(out_dir, result["index"])
            col = cell_index % 6
            row = cell_index // 6
            x = margin_x + col * (cell_w + 14)
            y = 60 + row * (cell_h + 10)
            draw.rectangle((x, y, x + cell_w, y + cell_h), fill=(18, 27, 46))
            if frame_path and frame_path.exists():
                try:
                    image = Image.open(frame_path).convert("RGB")
                    page.paste(image.resize((cell_w, image_h)), (x, y))
                except Exception:
                    draw.rectangle((x, y, x + cell_w, y + image_h), fill=(40, 40, 40))
            draw.text(
                (x + 7, y + image_h + 3),
                f"#{result['index']:04d}  {channel['name'][:22]}",
                fill=(255, 196, 80),
                font=title_font,
            )
            draw.text(
                (x + 7, y + image_h + 25),
                f"{channel.get('subgroup', '')[:10]}  {len(channel.get('streams', []))}线",
                fill=(160, 175, 198),
                font=small_font,
            )
            manifest.append({
                "page": page_index + 1,
                "cell": cell_index + 1,
                "index": result["index"],
                "id": channel["id"],
                "name": channel["name"],
                "subgroup": channel.get("subgroup", ""),
                "frame": str(frame_path.relative_to(ROOT)) if frame_path else "",
            })
        page.save(sheet_dir / f"page_{page_index + 1:03d}.jpg", "JPEG", quality=88)
    (out_dir / "sheet_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    log(f"[sheets] pages={pages} channels={len(reviewable)} -> {sheet_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["http", "capture", "sheets", "all"])
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--http-workers", type=int, default=32)
    parser.add_argument("--capture-workers", type=int, default=4)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=22)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--refresh-http", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()

    catalog = load_catalog(args.catalog)
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    http_path = out_dir / "http_results.json"
    http_results = {}
    if http_path.exists():
        http_results = json.loads(http_path.read_text(encoding="utf-8"))
    if args.phase in ("http", "all"):
        http_results = run_http_phase(catalog, out_dir, args.http_workers, args.refresh_http)
    if args.phase in ("capture", "all"):
        if not http_results:
            raise SystemExit("先运行 http 阶段")
        run_capture_phase(
            catalog,
            http_results,
            out_dir,
            args.capture_workers,
            args.max_attempts,
            args.timeout,
            args.limit,
            args.retry_failed,
        )
    if args.phase in ("sheets", "all"):
        draw_contact_sheets(catalog, out_dir)


if __name__ == "__main__":
    main()
