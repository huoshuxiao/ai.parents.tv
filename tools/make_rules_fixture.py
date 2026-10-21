#!/usr/bin/env python3
"""生成 Python / Kotlin 两侧判定规则的对拍夹具 app/src/test/resources/stream_rules.json。

夹具自带 nowEpochSeconds，所以「鉴权已过期」这类判据不会随时间腐烂。
改了 tools/stream_rules.py 或 app 端 StreamRules/StreamUrl 之后要重跑本脚本再跑 JVM 测试。
"""
from __future__ import annotations

import datetime
import json
import random
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "tools"))
import stream_rules

OUT = BASE / "app" / "src" / "test" / "resources" / "stream_rules.json"
# 固定时刻：2026-10-21 18:00（北京时间）。夹具里所有过期判断都相对它，所以不会随时间腐烂。
NOW = int(datetime.datetime(2026, 10, 21, 18, 0,
                            tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())

CURATED = [
    # 三类注定播不出来
    "http://cm.wshls.homecdn.com/live/7cc9.flv",
    "http://a.xttv.top/xttv/5ebe09cb.flv?auth_key=1861891200-0-0-d86d",
    "http://h/live.rmvb",
    "http://58.248.112.205:8006/GD_CUCC/G_CCTV-5-CQ.m3u8?Authinfo=abc&mac=d4:38:44:a5:72:8b",
    "http://hlsztemgsplive.miguvideo.com:8080/wd_r2/cctv/cctv5hdnew/600/index.m3u8?msisdn=20261020180255c16a",
    "http://jxcbn.ws-cdn.gitv.tv/hls/CCTV5_HD/index.m3u8?gMac=unknown&livodToken=0a8daf_1720507885_604800&fromCDN=ws",
    # 看着像但不该误杀
    "http://jxcbn.ws-cdn.gitv.tv/hls/CCTV1HD/index.m3u8?gMac=unknown&livodToken=bb80_1920507885_604800",
    "http://38.75.136.137:98/gslb/dsdqbv/cctv5p.m3u8?auth=test20251009",
    "http://h/live.m3u8?mac=",
    "http://h/live.m3u8?deviceid=unknown",
    "http://h/live.m3u8?programid=4200000611",
    "http://h/live.m3u8?Authinfo=3iRp8RZG1d3hGsaPmj%2FtHfjI6wzw",
    "http://a.m3u8?expire=1920507885",
    "http://a.m3u8?expire=1620507885",
    "https://ali-m-l.cztv.com/channels/lantian/channel002/1080p.m3u8?",
    "https://ali-m-l.cztv.com/channels/lantian/channel002/1080p.m3u8",
    "http://live.zohi.tv:80/video/s10001-fztv-3/index.m3u8",
    "http://live.zohi.tv/video/s10001-fztv-3/index.m3u8",
    "http://dassby.qqff.top:99/live/辽宁体育/index.m3u8",
    "http://dassby.qqff.top:99/live/%E8%BE%BD%E5%AE%81%E4%BD%93%E8%82%B2/index.m3u8",
    "HTTP://Example.COM:80/Live/X.m3u8/",
    "http://example.com/Live/X.m3u8",
    "http://[2409:8087:7000:20:1000::22]:6060/yinhe/2/ch00000090990000001334/index.m3u8?virtualDomain=yinhe.live_hls.zte.com",
    "https://[2409:8087:7000:20:1000::22]:6060/yinhe/2/ch00000090990000001334/index.m3u8",
    "http://h/g.m3u8?Authinfo=a%2Fb",
    "http://h/g.m3u8?Authinfo=a/b",
    "http://h/g.m3u8?Authinfo=AAA",
    "http://h/g.m3u8?Authinfo=BBB",
    "Not A URL",
    "",
]


def main() -> int:
    catalog = json.loads((BASE / "app/src/main/assets/channels.json").read_text(encoding="utf-8"))
    urls = list(dict.fromkeys(u for ch in catalog["channels"] for u in ch["streams"]))
    rng = random.Random(20261021)
    sample = rng.sample(urls, 150)
    picked = CURATED + sample
    cases = []
    for url in dict.fromkeys(picked):
        cases.append({
            "url": url,
            "verdict": stream_rules.never_playable(url, NOW),
            "canonical": stream_rules.canon(url),
            "ipv6": stream_rules.is_ipv6(url),
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generatedBy": "tools/make_rules_fixture.py",
        "nowEpochSeconds": NOW,
        "cases": cases,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"[write] {OUT} cases={len(cases)} verdicts="
          f"{dict(Counter(c['verdict'] or 'usable' for c in cases))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
