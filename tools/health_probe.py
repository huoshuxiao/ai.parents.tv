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


def host_of(url: str) -> str:
    """取 URL 的「主机:端口」（小写，去掉路径）。302 换了主机就是换了喂内容的人。"""
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/]+)", url or "")
    return (m.group(1) if m else "").lower()


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
    if head[:4] == b".RMF":
        return "rmvb"
    if head[:4] == b"\x30\x82\xb2\x7b":
        return "asf"
    if head[:4] == b"RIFF" and body[8:12] == b"AVI ":
        return "avi"
    if body.lstrip()[:7] == b"#EXTM3U":
        return "playlist"
    if body.lstrip()[:1] == b"<":
        return "html"
    return "binary"


# ExoPlayer 没有解复用器的容器（与 app 端 StreamRules.unsupportedExt / StreamProbe.undemuxableContainer 同一档）。
# 名单要两边一起长：以前这一把只认 flv/mkv，RMVB/ASF/AVI 的正文会被归成 "binary" ⇒
# 报成「可取到分片」，于是在目录里排到能播的位置、又不进黑名单，而电视上是黑屏。
UNDEMUXABLE_KINDS = ("flv", "mkv", "rmvb", "asf", "avi")

# 「定长成片冒充直播」的形状（用户 10-27 指令：这类病要按所属频道类型整组回测，不许一条条撞）。
# 触发样本：湖南卫视 线1 `txmov2.a.kwimgs.com/bs3/video-hls/…_hlsb.m3u8`
#   = `#EXT-X-ENDLIST` ＋ `MEDIA-SEQUENCE:0` ＋ 2096 片、合计 10479.3 秒的一整档片子；
#   六轮门禁各自抓的第一帧（10-25 13:06 → 10-27 15:14，跨 50 小时）逐字节相同
#   （md5 前 16 位 `3c9f59cb750daf9b`、99459 B）⇒ 每次打开都从同一档片头重播。
#   与 `placeholder_hint` 那一族（8.138.7.223 的 12 条 302 到同一枚 aad.mp4）是同一个病的两种形状：
#   那种靠跨主机重定向看得见，这一种**同主机、ts 分片**，旧那一把尺完全没反应。
EXTINF = re.compile(r"^#EXTINF:([0-9.]+)", re.M)
# 阈值是判据口径，改这两个数＝改判定，要同批把依据报出来：
# 300 秒取的是「直播 DVR 回看窗口一般几十秒到两三分钟」，够不到这一档；30 片防的是单片长窗口的误判。
STALE_ASSET_MIN_SECONDS = 300.0
STALE_ASSET_MIN_SEGMENTS = 30


def window_bucket(endlist, media_sequence, pl_segments, pl_seconds) -> str:
    """四格读数 → 「窗口形状」那一句话（台账里只有这四格时，重算判定也必须走这一个函数）。

    ⚠️`#EXT-X-ENDLIST` **单独不定罪**。10-27 按类型整组回测当场抓到 4 条假阳性：
    烟台新闻综合／烟台公共／烟台影视频道／烟台经济科技 四条线都带 ENDLIST、12 秒 3 片，
    但 `MEDIA-SEQUENCE` 是 163 万且在涨——那是直播短窗，不是存好的片子
    （真读数存 `tools/fixtures/yantai_live_playlist.m3u8`，sha16 `5fdd031774d05c1b`）。
    所以只有「seq 压在 0 且表长」才叫定长候选，其余带 ENDLIST 的一律留「待复采」，
    由复采那一棒比两次抓到的清单字节是否逐字节相同来定音。
    """
    if endlist is None or pl_segments is None:
        return "未证（没读到清单）"
    if not endlist:
        return f"直播窗口（{pl_segments}片）"
    if media_sequence:
        # 「seq 不为 0」只说明这是打包器的编号，**不等于它此刻在涨**——10-27 复采实测：
        # 烟台 4 条 90 秒后 seq 一字未变。所以这里不下"在涨"的判词，留给复采那一格定音。
        return f"待复采（ENDLIST、seq={media_sequence}）"
    if pl_seconds is None:
        return "未证（清单里没有 EXTINF 时长表）"
    if pl_seconds >= STALE_ASSET_MIN_SECONDS and pl_segments >= STALE_ASSET_MIN_SEGMENTS:
        return f"定长候选（{pl_seconds:.0f}s/{pl_segments}片，seq=0）"
    return f"待复采（ENDLIST 短表：{pl_seconds:.0f}s/{pl_segments}片）"


def classify_window(text: str) -> dict:
    """这份媒体清单是「直播滚动窗口」还是「一整档存好的成片」。

    只记嫌疑、**不改 verdict**：判定一改就顺着 `blocked_lines()` 把摘线＋拉黑一起带走，那是删除动作，
    要人工定案（与 `placeholder_hint` 同一规矩）。
    没读到清单、主清单（只有 variant 没有时长表）、有 EXTINF 却没分片行——这些都是「未证」，
    不许折成「直播窗口」，也不许折成「定长」。
    """
    out = {"endlist": None, "media_sequence": None,
           "pl_segments": 0, "pl_seconds": None, "asset": "未证（不是媒体清单）"}
    if not text or not text.lstrip().startswith("#EXTM3U"):
        return out
    durations = [float(x) for x in EXTINF.findall(text)]
    seq = re.findall(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
    out["endlist"] = "#EXT-X-ENDLIST" in text
    out["media_sequence"] = int(seq[-1]) if seq else None
    if not durations:
        # 主清单只有一行行 variant 地址，`SEGMENTS` 会把它们数成"分片"（夹具 U1 实测：
        # 一行 720.m3u8 被读成「直播窗口（1片）」＝把没量到读成结论）。没有时长表就不算媒体清单。
        out["asset"] = "未证（清单里没有 EXTINF 时长表）"
        return out
    segments = [s.strip() for s in SEGMENTS.findall(text) if s.strip()]
    out["pl_segments"] = len(segments)
    if not segments:
        out["asset"] = "未证（有 EXTINF 却没有分片行）"
        return out
    out["pl_seconds"] = round(sum(durations), 1)
    out["asset"] = window_bucket(out["endlist"], out["media_sequence"],
                                 out["pl_segments"], out["pl_seconds"])
    return out


def probe_one_guarded(url: str, connect_timeout: float, read_timeout: float, hard: float = 45.0) -> dict:
    """给每条线一个**总时限**。

    病征（2026-10-28 现读）：`probe_one` 里 urlopen 的 timeout 是「单次读」超时，遇到
    「能连上、慢慢吐字节」的源可以拖到无限——那场跑到 488/498 后停住 8 分钟以上，整条链就卡死。
    这里用一条后台线程跑，`hard` 秒拿不到结果就记一条「响应超时（硬超时）」，
    那条线程留在后台自生自灭（一次性工具，不回收）。
    """
    box: dict = {}

    def run() -> None:
        try:
            box["row"] = probe_one(url, connect_timeout, read_timeout)
        except Exception as exc:  # noqa: BLE001 - 线程里的任何异常都要带回主线程记成一行
            box["err"] = exc

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(hard)
    if th.is_alive():
        return {
            "url": url, "canonical": stream_rules.canon(url), "rule": None,
            "final_url": url, "cross_host": False, "placeholder_hint": "",
            "endlist": None, "media_sequence": None, "pl_segments": None,
            "pl_seconds": None, "asset": None,
            "verdict": "响应超时", "detail": f"硬超时 {hard:.0f}s",
            "playlist_ms": None, "segment_ms": None, "http_status": None,
            "kind": None, "seg_status": None, "seg_kind": None, "seg_bytes": 0,
            "key_status": None, "elapsed_ms": int(hard * 1000),
        }
    row = box.get("row")
    if row is None:
        return {
            "url": url, "canonical": stream_rules.canon(url), "rule": None,
            "final_url": url, "cross_host": False, "placeholder_hint": "",
            "endlist": None, "media_sequence": None, "pl_segments": None,
            "pl_seconds": None, "asset": None,
            "verdict": "探测异常", "detail": str(box.get("err"))[:80],
            "playlist_ms": None, "segment_ms": None, "http_status": None,
            "kind": None, "seg_status": None, "seg_kind": None, "seg_bytes": 0,
            "key_status": None, "elapsed_ms": 0,
        }
    return row


def probe_one(url: str, connect_timeout: float, read_timeout: float) -> dict:
    started = time.time()
    result = {
        "url": url,
        "canonical": stream_rules.canon(url),
        "rule": stream_rules.never_playable(url),
        "final_url": url,
        "cross_host": False,
        "placeholder_hint": "",
        # 窗口形状四格：没取到清单时留 None（＝没量到），不许折成「未证」那档文字，
        # 否则下游分不清"这台压根没探到"和"探到了但判不出形状"。
        "endlist": None, "media_sequence": None, "pl_segments": None,
        "pl_seconds": None, "asset": None,
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
    # 落地这一把真正拿内容的地址：302 会把人带到另一台主机上，只看请求地址看不见这一点
    # （10-27 的教训：8.138.7.223 那 12 条 302 到同一枚定长广告片，正文照样是 ftyp，
    #  于是第 1、2 级证据全绿，电视上"出了画"放的却是那片广告）。
    result["final_url"] = response.url or url
    result["cross_host"] = host_of(result["final_url"]) != host_of(url)
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
    if kind in UNDEMUXABLE_KINDS:
        return finish(result, started, "容器不支持", f"{kind} 字节头")
    if kind == "empty":
        return finish(result, started, "清单空", "")

    if kind != "playlist":
        # 直连型（裸 TS / MP4）：这一把读到的就是媒体字节。
        result["seg_status"] = status
        result["seg_kind"] = kind
        result["seg_bytes"] = len(body)
        playable = kind in ("ts", "fmp4", "binary") and len(body) > 1024
        # 只记一笔嫌疑，不改 verdict：改判定就会顺着 blocked_lines() 把"摘线＋拉黑"一起带走，
        # 那是删除动作，要人工定案（本轮先把看得见装上，判定等点档）。
        if playable and kind in ("ts", "fmp4") and result["cross_host"]:
            result["placeholder_hint"] = "定长片（跨主机重定向到 " + host_of(result["final_url"]) + "）"
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

    result.update(classify_window(text))
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
                and seg_kind in ("ts", "fmp4", "binary")):
            result["segment_ms"] = int((time.time() - started) * 1000)
            return finish(result, started, "可取到分片", seg_kind)
        if seg_kind == "html":
            return finish(result, started, "分片是网页", "")
        if seg_response.status_code in (403, 404, 410):
            return finish(result, started, "分片被拒", f"HTTP {seg_response.status_code}")
        if seg_kind in UNDEMUXABLE_KINDS:
            # 分片是真字节，但装的是电视解不开的容器：这一条不是「取不到」，是「取到了也播不出来」，
            # 标签要分开，因为目录侧靠「容器不支持」这个结论把它排到最后并写进地址黑名单。
            return finish(result, started, "容器不支持", f"分片 {seg_kind} 字节头")
    return finish(result, started, "分片取不到", str(result.get("seg_status")))


def finish(result, started, verdict, detail):
    result["verdict"] = verdict
    result["detail"] = detail[:120]
    result["elapsed_ms"] = int((time.time() - started) * 1000)
    return result


def channel_types(channel) -> set:
    """一台的三种可点名写法：组（cctv）、组/子组（cctv/卫视）、子组（卫视）。"""
    cat = str(channel.get("category") or "")
    sub = str(channel.get("subgroup") or "")
    out = {cat, f"{cat}/{sub}"}
    if sub:
        out.add(sub)
    return out


def load_urls(catalog_path: Path, types=None):
    """按类型选线（用户 10-27「以后线路问题，按所属频道类型回测」）。

    types 为 None/空＝全目录（照旧）。给了类型就只取那一组，并把「选到几台/几条」打出来——
    命中 0 台必须是显式的一行，不许读成"这一组没问题"。
    """
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    urls = []
    picked = 0
    for channel in data["channels"]:
        if types and not (channel_types(channel) & set(types)):
            continue
        picked += 1
        for url in channel["streams"]:
            urls.append(url)
    if types:
        print(f"[type] 点名 {sorted(set(types))} -> 选中 {picked} 台 / {len(urls)} 条"
              + ("  ⚠️命中 0 台：这一组一台都没选到，不等于这一组没问题" if picked == 0 else ""))
    return urls


def advance_verdict(a: dict, b: dict, gap_seconds: float) -> str:
    """两次 `probe_one` 的四格读数比一比，给「待复采／定长候选」定音。

    取数一律走 probe_one 本身——不复用第二把解析器，否则两次抓的口径会和台账不一致。
    ⚠️「两次不推进」**不等于定长片**：10-27 复采真跑就撞上这件事——烟台那 4 条 90 秒后四格一字未变，
    可它们是 12 秒 3 片、seq 163 万的形态（直播打包器的编号），那是**直播源此刻不推进（冻帧/断流候选）**，
    归冻帧那条线管；只有「窗口本身长得像一档节目」（seq 压在 0、片表够长）不推进才叫定长实证。
    """
    for side in (a, b):
        if side.get("asset") is None or side.get("endlist") is None:
            return "未证（有一边没读到清单）"
    per_seg = None
    if a.get("pl_segments") and a.get("pl_seconds"):
        per_seg = a["pl_seconds"] / a["pl_segments"]
    same = all(a.get(k) == b.get(k) for k in
               ("endlist", "media_sequence", "pl_segments", "pl_seconds"))
    if not same:
        return (f"直播窗口在推进（seq {a.get('media_sequence')}→{b.get('media_sequence')}，"
                f"{a.get('pl_segments')}→{b.get('pl_segments')}片）")
    if per_seg is None or gap_seconds < max(2.0 * per_seg, 30.0):
        return f"未证（间隔 {gap_seconds:.0f}s 太短，单片约 {per_seg if per_seg else 0:.1f}s，看不出涨没涨）"
    shape = window_bucket(a.get("endlist"), a.get("media_sequence"),
                          a.get("pl_segments"), a.get("pl_seconds"))
    if shape.startswith("定长候选"):
        return f"窗口纹丝不动（四格全同，间隔 {gap_seconds:.0f}s）＝定长实证"
    return (f"窗口纹丝不动（四格全同，间隔 {gap_seconds:.0f}s）——形态是直播短窗"
            f"（seq={a.get('media_sequence')}、{a.get('pl_seconds')}s/{a.get('pl_segments')}片）"
            f"⇒ 直播源此刻不推进（冻帧/断流候选），不是定长片")


def recheck_urls(ledger_path: Path, catalog_path: Path) -> list[tuple[str, str]]:
    """台账里现尺判成「待复采／定长候选」的线路，带上台名/线号。"""
    rows = {}
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rows[row["url"]] = row          # 同址多次：后写的算数
    names = {}
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    for ch in data["channels"]:
        for i, u in enumerate(ch["streams"]):
            names[u] = f"{ch['name']} 线{i + 1}[{ch['category']}/{ch['subgroup']}]"
    out = []
    for url, row in rows.items():
        bucket = window_bucket(row.get("endlist"), row.get("media_sequence"),
                               row.get("pl_segments"), row.get("pl_seconds"))
        if bucket.startswith(("待复采", "定长候选")):
            out.append((names.get(url, "(不在库)"), url))
    return out


def recheck_main(args) -> int:
    """复采定音：把台账里「待复采／定长候选」那几条抓两次比四格。

    ⚠️默认 `--out` 就是生产健康目录，复采绝不能顺手写进去 ⇒ 不给 `--out` 直接拒绝（读数走自己的目录）。
    """
    if "--out" not in sys.argv:
        raise SystemExit(f"[拒绝] --recheck 必须另给 --out（默认值是生产健康台账 {DEFAULT_OUT}，"
                         f"复采读数不该混进构建要读的那一份）")
    if not args.recheck.exists():
        raise SystemExit(f"[拒绝] 台账不存在：{args.recheck}")
    cands = recheck_urls(args.recheck, args.catalog)
    print(f"[recheck] 待复采/定长候选 {len(cands)} 条，间隔 {args.gap:.0f}s")
    if not cands:
        print("[recheck] 零命中：台账里没有任何一条被判成待复采或定长候选（这不等于「这一类没问题」，"
              "只是这把尺这一场没选出候选）")
        return 0

    def probe(item):
        name, url = item
        return name, url, probe_one(url, args.connect_timeout, args.read_timeout)

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(cands)))) as pool:
        first = list(pool.map(probe, cands))
    log(f"[recheck] 第一遍抓完 {len(first)} 条，睡 {args.gap:.0f}s")
    time.sleep(args.gap)
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(cands)))) as pool:
        second = list(pool.map(probe, cands))

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "recheck.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for (name, url, a), (_, _, b) in zip(first, second):
            verdict = advance_verdict(a, b, args.gap)
            row = {"channel": name, "url": url, "gap_seconds": round(args.gap, 1),
                   "before": {k: a.get(k) for k in ("verdict", "endlist", "media_sequence",
                                                    "pl_segments", "pl_seconds", "asset")},
                   "after": {k: b.get(k) for k in ("verdict", "endlist", "media_sequence",
                                                   "pl_segments", "pl_seconds", "asset")},
                   "recheck": verdict}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {name} 现尺={window_bucket(a.get('endlist'), a.get('media_sequence'), a.get('pl_segments'), a.get('pl_seconds'))} -> {verdict}")
    print(f"[recheck] 落盘 {path}（{len(first)} 行）")
    return 0


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
    parser.add_argument("--recheck", type=Path, default=None, metavar="台账.jsonl",
                        help="只复采台账里现尺判成「待复采／定长候选」的那几条，抓两次定音（比四格）")
    parser.add_argument("--gap", type=float, default=90.0,
                        help="复采两抓之间的间隔秒数（默认 90；短于两枚分片会被判「未证」）")
    parser.add_argument("--type", action="append", dest="types", metavar="组[/子组]",
                        help="只探这一组频道（cctv／cctv/卫视／卫视 三种写法都认），可重复给。"
                             "用户 10-27 指令：一条线查出问题要按所属频道类型整组回测。")
    args = parser.parse_args()

    if args.recheck:
        return recheck_main(args)

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

    urls = load_urls(args.catalog, args.types)
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
        row = probe_one_guarded(url, args.connect_timeout, args.read_timeout)
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
