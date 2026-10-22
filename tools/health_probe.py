#!/usr/bin/env python3
"""逐线路健康探测：清单取到之后，再真的抓一段媒体分片。

只判「有没有回数据」是不够的：清单在、分片 404 的线路在电视上一样是黑屏。
这一版把三级证据都记下来，供目录生成按判据取舍。

    python3 tools/health_probe.py --catalog tools/.cache/catalog_uncapped.json
    python3 tools/health_probe.py --retry-error   # 只重跑上次没定论的
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stream_rules

BASE = Path(__file__).resolve().parents[1]
DEFAULT_OUT = BASE / "tools" / ".cache" / "health"
UA = (
    "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "*/*"}
SEGMENT_RANGE = {"User-Agent": UA, "Accept": "*/*", "Range": "bytes=0-262143"}
TS_MAGIC = 0x47
PRINT_LOCK = threading.Lock()


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def thread_session() -> requests.Session:
    session = getattr(threading.current_thread(), "_health_session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        threading.current_thread()._health_session = session
    return session


def fetch(session, url, timeout, range_header=False):
    headers = SEGMENT_RANGE if range_header else None
    response = session.get(url, timeout=timeout, headers=headers, allow_redirects=True,
                           stream=True)
    body = response.raw.read(300_000, decode_content=True)
    response.close()
    return response, body


EXT_URIS = re.compile(r"^#EXT-X-KEY:.*?URI=\"([^\"]+)\"", re.M)
VARIANTS = re.compile(r"^#EXT-X-STREAM-INF:[^\n]*\n([^\n#]+)", re.M)
SEGMENTS = re.compile(r"^(?!#)(\S+)$", re.M)
# 变体清单里自报的画面尺寸，目录生成时用来把「分辨率高」的线排在前面。
RESOLUTION = re.compile(r"RESOLUTION=(\d+)x(\d+)", re.I)


def declared_resolution(text: str):
    """取这份清单里自报的最大画面尺寸；没有 RESOLUTION 就返回 None（不猜）。"""
    best = None
    for width, height in RESOLUTION.findall(text):
        pair = (int(width), int(height))
        if best is None or pair[0] * pair[1] > best[0] * best[1]:
            best = pair
    return best


def resolve(base, ref):
    if not ref:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", ref):
        return ref
    return requests.compat.urljoin(base, ref)


def looks_like_video(body: bytes) -> str:
    if not body:
        return "empty"
    head = body[:12]
    if head[0] == TS_MAGIC:
        return "ts"
    if b"ftyp" in head:
        return "fmp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "mkv"
    if head[:3] == b"FLV":
        return "flv"
    if body.lstrip()[:7] == b"#EXTM3U":
        return "playlist"
    if body.lstrip()[:1] == b"<":
        return "html"
    return "binary"


def probe_one(url: str, connect_timeout: float, read_timeout: float) -> dict:
    started = time.time()
    result = {
        "url": url,
        "canonical": stream_rules.canon(url),
        "rule": stream_rules.never_playable(url),
    }
    deterministic = result["rule"]
    if deterministic:
        result.update(verdict=deterministic, detail="", playlist_ms=None, segment_ms=None,
                      http_status=None, kind=None, seg_status=None, seg_kind=None,
                      seg_bytes=0, key_status=None, elapsed_ms=int((time.time() - started) * 1000))
        return result

    session = thread_session()
    timeout = (connect_timeout, read_timeout)
    try:
        response, body = fetch(session, url, timeout)
    except requests.exceptions.ConnectTimeout:
        return finish(result, started, "连接超时", "")
    except requests.exceptions.ReadTimeout:
        return finish(result, started, "响应超时", "")
    except requests.exceptions.SSLError:
        return finish(result, started, "证书错误", "")
    except requests.exceptions.TooManyRedirects:
        return finish(result, started, "跳转过多", "")
    except requests.exceptions.ConnectionError:
        return finish(result, started, "连不上", "")
    except Exception as exc:  # noqa: BLE001 - 探测脚本要把任何异常落成状态而不是中断
        return finish(result, started, "探测异常", type(exc).__name__)

    status = response.status_code
    kind = looks_like_video(body)
    result["http_status"] = status
    result["kind"] = kind
    result["playlist_ms"] = int((time.time() - started) * 1000)
    if status in (401, 403):
        return finish(result, started, "拒绝访问", f"HTTP {status}")
    if status == 404:
        return finish(result, started, "清单404", f"HTTP {status}")
    if status >= 500:
        return finish(result, started, "服务端错误", f"HTTP {status}")
    if not 200 <= status < 300:
        return finish(result, started, "非常规响应", f"HTTP {status}")
    if kind == "html":
        return finish(result, started, "返回网页", "")
    if kind == "flv":
        return finish(result, started, "容器不支持", "FLV 字节头")
    if kind == "empty":
        return finish(result, started, "清单空", "")

    if kind != "playlist":
        # 直连型（裸 TS / MP4）：这一把读到的就是媒体字节。
        result["seg_status"] = status
        result["seg_kind"] = kind
        result["seg_bytes"] = len(body)
        playable = kind in ("ts", "fmp4", "binary") and len(body) > 1024
        return finish(result, started, "可取到分片" if playable else "分片太小", kind)

    text = body.decode("utf-8", "replace")
    best = declared_resolution(text)
    if best:
        result["resolution"] = list(best)
    variants = VARIANTS.findall(text)
    # 清单常被 302 到另一台主机（如 74.91.26.218 → 63.141.226.98），分片是相对那一台写的；
    # 拿原始地址当基址会把每条分片都读成 404。
    media_url = response.url or url
    if variants:
        media_url = resolve(media_url, variants[-1].strip())
        try:
            response, body = fetch(session, media_url, timeout)
        except Exception:  # noqa: BLE001
            return finish(result, started, "变体清单取不到", media_url)
        if not 200 <= response.status_code < 300:
            return finish(result, started, "变体清单报错", f"HTTP {response.status_code}")
        text = body.decode("utf-8", "replace")
        if not text.lstrip().startswith("#EXTM3U"):
            return finish(result, started, "变体非清单", "")
        media_url = response.url or media_url

    key_ref = EXT_URIS.search(text)
    if key_ref:
        key_url = resolve(media_url, key_ref.group(1))
        try:
            key_response, _ = fetch(session, key_url, timeout)
            result["key_status"] = key_response.status_code
            key_response.close()
        except Exception as exc:  # noqa: BLE001
            result["key_status"] = type(exc).__name__

    segments = [s.strip() for s in SEGMENTS.findall(text) if s.strip()]
    if not segments:
        return finish(result, started, "清单无分片", "")
    # 直播清单最后几枚是当前画面，取末两枚 + 中间一枚，避开刚写完的那一枚。
    picks = segments[-2:] + ([segments[len(segments) // 2]] if len(segments) > 3 else [])
    for ref in picks:
        seg_url = resolve(media_url, ref)
        try:
            seg_response, seg_body = fetch(session, seg_url, timeout, range_header=True)
        except requests.exceptions.ReadTimeout:
            result["seg_status"] = "timeout"
            continue
        except Exception as exc:  # noqa: BLE001
            result["seg_status"] = type(exc).__name__
            continue
        seg_kind = looks_like_video(seg_body)
        result["seg_status"] = seg_response.status_code
        result["seg_kind"] = seg_kind
        result["seg_bytes"] = len(seg_body)
        seg_response.close()
        if (200 <= seg_response.status_code < 300 and len(seg_body) > 1024
                and seg_kind in ("ts", "fmp4", "mkv", "binary")):
            result["segment_ms"] = int((time.time() - started) * 1000)
            return finish(result, started, "可取到分片", seg_kind)
        if seg_kind == "html":
            return finish(result, started, "分片是网页", "")
        if seg_response.status_code in (403, 404, 410):
            return finish(result, started, "分片被拒", f"HTTP {seg_response.status_code}")
    return finish(result, started, "分片取不到", str(result.get("seg_status")))


def finish(result, started, verdict, detail):
    result["verdict"] = verdict
    result["detail"] = detail[:120]
    result["elapsed_ms"] = int((time.time() - started) * 1000)
    return result


def load_urls(catalog_path: Path):
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    urls = []
    for channel in data["channels"]:
        for url in channel["streams"]:
            urls.append(url)
    return urls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=BASE / "app/src/main/assets/channels.json")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--connect-timeout", type=float, default=6.0)
    parser.add_argument("--read-timeout", type=float, default=8.0)
    parser.add_argument("--retry-error", action="store_true",
                        help="只重跑上次判定为「探测异常/响应超时」这类没定论的")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "line_health.jsonl"

    done = {}
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if args.retry_error and row.get("verdict") not in ("探测异常", "响应超时", "连接超时"):
                continue
            done[row["url"]] = row

    urls = load_urls(args.catalog)
    order = {u: i for i, u in enumerate(dict.fromkeys(urls))}
    if args.retry_error:
        todo = [u for u in done if done[u].get("verdict") in ("探测异常", "响应超时", "连接超时")]
    else:
        todo = [u for u in order if u not in done]
    if args.limit:
        todo = todo[: args.limit]
    log(f"[plan] catalog lines={len(order)} already={len(done) - (0 if args.retry_error else 0)} todo={len(todo)}")
    if not todo:
        return 0

    handle = results_path.open("a", encoding="utf-8")
    counted = {"可取到分片": 0, "其他": 0}
    started_at = time.time()

    def work(url):
        row = probe_one(url, args.connect_timeout, args.read_timeout)
        with PRINT_LOCK:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
        counted["可取到分片" if row["verdict"] == "可取到分片" else "其他"] += 1
        total = counted["可取到分片"] + counted["其他"]
        if total % 50 == 0 or total == len(todo):
            rate = total / max(1e-6, time.time() - started_at)
            eta = (len(todo) - total) / max(rate, 1e-6)
            log(f"[{total}/{len(todo)}] ok={counted['可取到分片']} "
                f"rate={rate:.1f}/s eta={eta:.0f}s")
        return row

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for _ in pool.map(work, todo):
            pass
    handle.close()
    log(f"[done] wrote {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
