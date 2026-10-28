#!/usr/bin/env python3
"""每条线的**音轨编码**台账：让「有画没声」这类缺陷下次按所属频道类型整组看得见。

为什么要有它（用户 10-27 原话「36.110.54.118…解决没声音问题」＋「以后线路问题，按所属频道类型回测」）：
一条线报"画面有、声音没有"，主持机这边以前只有 health（能不能取到分片）与画面身份两把尺，
都答不了"音轨是什么编码"。这次用 ffprobe 定了音：`36.110.54.118:866` 那 9 条里 **8 条是 MP2**，
而随包的 Media3 1.5.1 里根本没有 MP2 的渲染器（现读 APK 6 枚 dex：
`androidx/media3/exoplayer/audio/MpegAudioRenderer` 命中 0、`MPEG1L2` 命中 0）⇒ ExoPlayer 不开音轨，
于是画面照跑、声音没有。同一族里北京卫视 线2 是 AAC（正常）。

只读，除了 `--out` 指定的台账文件。口径：
  · 「解不了」按**这台盒子的 Media3 支持面**判，不按"ffprobe 认得"判；名单见 DECODABLE；
  · 探测失败／超时＝**未证**，不许折成"源没音轨"，也不许折成"能播"；
  · `--type 组[/子组]` 按所属频道类型选线（与 health_probe 同一写法）。

用法（读数类用途一律先空跑，写盘要显式给 --out）：
  python3 tools/audio_codec_probe.py --dry-run --limit 20
  python3 tools/audio_codec_probe.py --type cctv/卫视 --out tools/.cache/audio/all.jsonl
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
UA = "VLC/3.0.16 LibVLC/3.0.16"

# 这台盒子（Media3 1.5.1 + Android TV）软件/硬件解得了的音频编码。
# 依据是现读不是记忆：APK 里只有 MediaCodecAudioRenderer/DecoderAudioRenderer，
# 没有 MP2/AC-3/DTS 的自带软解；AC-3/E-AC-3 依赖平台 MediaCodec 组件，逐台不同 ⇒ 标成"待真机验"而不是"解得了"。
DECODABLE = {"aac": "解得了", "mp3": "解得了", "mp1": "待真机验", "opus": "解得了",
             "vorbis": "解得了", "flac": "解得了", "pcm_s16le": "解得了", "pcm_f32le": "解得了"}
NOT_DECODABLE = {"mp2": "解不了（Media3 无 MP2 渲染器）",
                 "ac3": "待真机验（要看平台 MediaCodec 有没有 ac3）",
                 "eac3": "待真机验", "dts": "解不了", "truehd": "解不了"}


def load_urls(catalog: Path, types=None):
    cat = json.loads(catalog.read_text(encoding="utf-8"))
    out = []
    for ch in cat["channels"]:
        grp = f"{ch['category']}/{ch.get('subgroup') or ''}"
        if types and not any(ch["category"] == t or grp == t or grp.split("/")[0] == t for t in types):
            continue
        for i, u in enumerate(ch["streams"], 1):
            out.append((u, ch["name"], grp, i, len(ch["streams"])))
    return out


def probe(url, timeout=30):
    """一条地址跑一次 ffprobe，返回 (行, 状态)。状态 ∈ {ok, timeout, fail}。"""
    cmd = ["ffprobe", "-hide_banner", "-v", "error", "-user_agent", UA,
           "-show_entries", "stream=codec_type,codec_name", "-of", "csv=p=0", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", "timeout"
    if r.returncode != 0:
        return (r.stderr or "").strip().splitlines()[-1] if r.stderr else "", "fail"
    return (r.stdout or "").strip(), "ok"


def classify(text):
    """ffprobe 的 `codec_type,codec_name` 行 → 音/视频编码与该不该有声。"""
    audio, video = [], []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if len(parts) != 2:
            continue
        a, b = parts[0].lower(), parts[1].lower()
        # ffprobe 的 csv 列序跟着它内部字段序走（10-27 现读是 `mp2,audio` 这种"名字在前"），
        # 两种顺序都认，别把 codec_type 当成编码名去查表。
        if b in ("audio", "video"):
            name, kind = a, b
        elif a in ("audio", "video"):
            name, kind = b, a
        else:
            continue
        (audio if kind == "audio" else video if kind == "video" else []).append(name)
    if not audio and not video:
        return {"audio": [], "video": [], "verdict": "未证（没解出任何流）"}
    verdicts = [DECODABLE.get(a) or NOT_DECODABLE.get(a) or f"未知编码 {a}" for a in audio]
    if not audio:
        verdict = "无音轨（清单里一条音频流都没有）"
    elif any(v and not v.startswith("解得了") for v in verdicts):
        verdict = "有音轨但可能解不了：" + "、".join(filter(None, verdicts))
    else:
        verdict = "有音轨（解得了）：" + "、".join(audio)
    return {"audio": audio, "video": video, "verdict": verdict}


def selfcheck() -> int:
    """尺子自己的六格（含负对照）：写死的 ffprobe 输出进 classify，判红就 EXIT=1。"""
    cases = [
        ("SC1 MP2 要判成解不了", classify("mp2,audio\nh264,video")["verdict"].startswith("有音轨但可能解不了"), True),
        ("SC2 AAC 要判成解得了", classify("aac,audio\nh264,video")["verdict"].startswith("有音轨（解得了）"), True),
        ("SC3 只有视频才是真无音轨", classify("h264,video")["verdict"], "无音轨（清单里一条音频流都没有）"),
        ("SC4 空输出是未证，不是无音轨", classify("")["verdict"].startswith("未证"), True),
        ("SC5 负对照：乱行不许被当成流", classify("garbage line\na,b,c")["verdict"].startswith("未证"), True),
        ("SC6 负对照：未知编码不许折成解得了", "未知编码" in classify("dolby_x,audio\nh264,video")["verdict"], True),
    ]
    red = 0
    for tag, got, want in cases:
        ok = got == want
        print(("  [绿] " if ok else "  [红] ") + f"{tag}：实得 {got!r}")
        red += 0 if ok else 1
    print(f"[自检] 判红 {red} 格")
    return 1 if red else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", type=Path, default=BASE / "app/src/main/assets/channels.json")
    ap.add_argument("--out", type=Path, default=None,
                    help="台账 jsonl（追加写）。不给就只打印不落盘——不给 --out 又要落盘是本仓的硬规矩")
    ap.add_argument("--type", action="append", dest="types", metavar="组[/子组]")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--retry-unknown", action="store_true", help="只重跑台账里判「未证/探测失败」的那几条")
    ap.add_argument("--dry-run", action="store_true", help="不发请求，只打选线计划（读数类用途一律先跑这个）")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck()

    urls = load_urls(args.catalog, args.types)
    print(f"[选线] 目录 {args.catalog.name} 命中 {len(urls)} 条线"
          + (f"（点名 {args.types}）" if args.types else "（全目录）"))
    if not urls:
        print("[警告] 命中 0 条 ⇒ 这不等于「这一组没问题」，是名单写错了")
        return 2
    if args.limit:
        urls = urls[:args.limit]

    done = {}
    if args.out and args.out.exists():
        for line in args.out.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            done[row["url"]] = row
        if args.retry_unknown:
            done = {u: r for u, r in done.items()
                    if not (r.get("verdict", "").startswith("未证") or r.get("status") in ("timeout", "fail"))}
    todo = [t for t in urls if t[0] not in done]
    print(f"[计划] 待探 {len(todo)} 条（已有台账 {len(done)} 条）｜并发 {args.workers}")
    if args.dry_run:
        for u, name, grp, idx, total in todo[:10]:
            print(f"    {name} 线{idx}/{total} [{grp}] {u[:70]}")
        print("[空跑] 没发一个请求、没写一个字节")
        return 0

    lock = threading.Lock()
    fh = args.out.open("a", encoding="utf-8") if args.out else None
    rows = []

    def work(item):
        u, name, grp, idx, total = item
        text, status = probe(u, args.timeout)
        r = classify(text) if status == "ok" else {"audio": [], "video": [],
                                                   "verdict": f"未证（{status}：{text[:60]}）"}
        r.update({"url": u, "channel": name, "group": grp, "line": idx, "lines": total,
                  "status": status})
        with lock:
            rows.append(r)
            if fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                fh.flush()
            if len(rows) % 25 == 0:
                print(f"  [{len(rows)}/{len(todo)}]")
        return r

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    if fh:
        fh.close()

    tally = collections.Counter()
    silent_first = []
    for r in rows:
        key = r["verdict"].split("：")[0]
        tally[key] += 1
        if r["line"] == 1 and r["verdict"].startswith("有音轨但可能解不了"):
            silent_first.append(r)
    print("[汇总] " + "、".join(f"{k} {v}" for k, v in tally.most_common()))
    print(f"[首条就没声音] {len(silent_first)} 台（打开这一台直接哑，最该先动的一档）")
    for r in sorted(silent_first, key=lambda x: (x["group"], x["channel"]))[:20]:
        print(f"    {r['channel']} [{r['group']}] {r['verdict'][:52]} · {r['url'][:56]}")
    bad = [r for r in rows if r["status"] != "ok"]
    print(f"[未证] {len(bad)} 条：这把尺没读到流，**不等于**这些线没问题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
