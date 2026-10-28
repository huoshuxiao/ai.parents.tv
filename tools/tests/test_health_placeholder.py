#!/usr/bin/env python3
"""`health_probe.probe_one()` 看得见「定长片占位」这一族的夹具。

10-27 的教训：`8.138.7.223` 那 12 条线 302 到同一枚广告片 `aad.mp4`，正文头是 `ftyp`，
于是第 1、2 级证据（清单可达／分片可取）全绿，电视上"出了画"放的却是那片广告。
这一把尺只加"看得见"，不改 verdict——改 verdict 会顺着 `build_catalog.blocked_lines()`
把摘线＋拉黑一起带走，那是删除动作，要人工定案。每格说明它挡的是哪一种谎：

  H1 跨主机 302 回定长 mp4 → 记嫌疑，但 verdict 仍是「可取到分片」（挡"顺手加硬"）
  H2 同主机裸 mp4 → 不许记嫌疑（挡"把所有 mp4 都当占位片"＝连坐）
  H3 跨主机但回的是清单 → 不许记嫌疑（挡"只看了重定向没看内容形态"）
  H4 跨主机 mp4 但字节不足 1KB → verdict「分片太小」且不记嫌疑（挡"没播成也标成占位"）
  H5 没重定向 → final_url 就是请求地址、cross_host=False（挡"final_url 只在出事时才有"）
  H6 跨主机裸 TS → 记嫌疑（挡"这一族只认 mp4"）
  H7 判据本体（host_of）被换掉时两臂都要红：
       PLACEHOLDER_BROKEN=1  恒判同主机 → H1/H6 红（挡"这把尺根本没牙"）
       PLACEHOLDER_BROKEN2=1 落地地址与请求地址只要字面不同就判跨主机 → H2 红（挡"响成一片也算过关"）
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import health_probe  # noqa: E402

fails, n = [], 0


def check(name, got, want):
    global n
    n += 1
    if got != want:
        fails.append(f"{name}: 期望 {want!r} 实得 {got!r}")


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096
TS = b"\x47" + b"\x00" * 4096
PLAYLIST = b"#EXTM3U\n#EXT-X-ENDLIST\n"


class FakeRaw:
    def __init__(self, body):
        self.body = body

    def read(self, size, decode_content=False):
        return self.body[:size]


class FakeResponse:
    def __init__(self, url, status, body):
        self.url = url
        self.status_code = status
        self.raw = FakeRaw(body)

    def close(self):
        pass


class FakeSession:
    """把「请求地址 → (落地地址, 状态码, 正文)」写死，整场不发一个真请求。"""

    def __init__(self, script):
        self.script = script

    def get(self, url, timeout=None, headers=None, allow_redirects=True, stream=False):
        row = self.script.get(url)
        if row is None:
            raise AssertionError(f"夹具没安排这一条请求：{url}")
        final, status, body = row
        return FakeResponse(final, status, body)


SCRIPT = {
    "http://8.138.7.223/tv/cctvnews.php?id=cctv2":
        ("https://raw.giteeusercontent.com/lmds2027/2027/raw/master/aad.mp4", 200, MP4),
    "http://cdn.example.com/live/gd.m3u8":
        ("https://other.example.net/live/gd.m3u8", 200, PLAYLIST),
    "http://cdn.example.com/live/hn.mp4":
        ("http://cdn.example.com/upic/hn.mp4", 200, MP4),
    "http://tiny.example.com/a.mp4":
        ("http://other.example.com/a.mp4", 200, b"\x00\x00\x00\x18ftypmp42"),
    "http://ts.example.com/b.ts":
        ("http://other.example.com/b.ts", 200, TS),
    "http://same.example.com/c.ts":
        ("http://same.example.com/c.ts", 200, TS),
}

if os.environ.get("PLACEHOLDER_BROKEN") == "1":
    health_probe.host_of = lambda url: "x"          # 恒判"同一台主机"→ 嫌疑永远不响
if os.environ.get("PLACEHOLDER_BROKEN2") == "1":
    # 恒判"换了主机"：拿长度当主机名会撞车（H2 那对地址正好同长＝假绿，实跑抓过一次），
    # 所以直接给落地地址后面加一个字，保证两次取值必不相等。
    health_probe.host_of = lambda url: (url or "") + "!"  # → 同主机也记嫌疑


def probe(url):
    health_probe.thread_session = lambda: FakeSession(SCRIPT)
    return health_probe.probe_one(url, 6.0, 10.0)


r = probe("http://8.138.7.223/tv/cctvnews.php?id=cctv2")
check("H1 verdict 没被顺手改硬", r["verdict"], "可取到分片")
check("H1 cross_host", r["cross_host"], True)
check("H1 记了嫌疑", bool(r["placeholder_hint"]), True)

r = probe("http://cdn.example.com/live/hn.mp4")
check("H2 同主机换路径 cross_host", r["cross_host"], False)
check("H2 不许记嫌疑", r["placeholder_hint"], "")

r = probe("http://cdn.example.com/live/gd.m3u8")
check("H3 跨主机回清单 cross_host", r["cross_host"], True)
check("H3 不许记嫌疑", r["placeholder_hint"], "")

r = probe("http://tiny.example.com/a.mp4")
check("H4 verdict", r["verdict"], "分片太小")
check("H4 不许记嫌疑", r["placeholder_hint"], "")

r = probe("http://same.example.com/c.ts")
check("H5 没重定向时 final_url 照样落", r["final_url"], "http://same.example.com/c.ts")
check("H5 cross_host", r["cross_host"], False)

r = probe("http://ts.example.com/b.ts")
check("H6 裸 TS 跨主机也记嫌疑", bool(r["placeholder_hint"]), True)

broken = os.environ.get("PLACEHOLDER_BROKEN") == "1" or os.environ.get("PLACEHOLDER_BROKEN2") == "1"
print(f"[fixture] health_probe 占位片 {n} 格，判红 {len(fails)} 格{'（人为拆牙那一臂）' if broken else ''}")
for f in fails:
    print(f"  [红] {f}")
sys.exit(1 if fails else 0)
