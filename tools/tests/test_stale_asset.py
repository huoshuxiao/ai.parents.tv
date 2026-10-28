#!/usr/bin/env python3
"""验的是「定长成片冒充直播」这一类（用户 10-27：湖南卫视 线1 查出来后要求「按所属频道类型回测，防止相同问题重复发生」）。

尺在 `tools/health_probe.py::classify_window`，接进 `probe_one` 的写法是**只记四格读数、不改 verdict**
（改 verdict 就顺着 `blocked_lines()` 把摘线＋拉黑一起带走＝删除动作，要人工定案）。
本夹具盯五种会说谎的情形：

  S) 真清单对表：两份**真拉下来存进仓库**的播放列表（定长的湖南卫视、直播的湖南卫视线3），
     逐字节 sha 钉住；读数必须一个是「定长成片」、一个是「直播窗口」。
     这两臂是同一把尺、同一天、同一个台的相邻两条线——差异只可能来自清单本身。
  T) 阈值两侧的臂：400 s/40 片＋ENDLIST＝定长成片；60 s/40 片＋ENDLIST＝定长短片；
     400 s/40 片**没有** ENDLIST＝直播窗口（证明起作用的判别是 ENDLIST，不是时长）。
  U) 四种「未证」各归各：主清单（没有时长表）、不是清单、有 EXTINF 却没有分片行、ENDLIST 却无时长——
     都不许折成「直播窗口」，也不许折成「定长」。这一组第一轮就抓到尺子自己的缺陷：
     主清单里那行 variant 地址被 `SEGMENTS` 数成「1 片」→ 读成了「直播窗口（1片）」。
  V) 真跑那一臂：`probe_one` 吃到定长清单时 verdict 必须**不变**（照旧"可取到分片"），
     而 asset 那一格必须写上"定长成片"——只记账、不动删除，这一格就是这条尺的牙。
  Y) 假阳性对照：带 ENDLIST、但 media-sequence 在涨的直播短窗（真清单存进仓库）
     绝不能被判成定长——10-27 整组回测当场抓到 4 条这样的（烟台 4 台，12s/3 片、seq 163 万）。
     而复采又把这 4 条判成「直播源此刻不推进」＝第三种病（冻帧/断流），不是定长片。
  X) 复采定音：四格全同**且形态本身像一档节目**才叫「定长实证」；seq 或片数变了＝在推进；
     形态是直播短窗（10-27 烟台真跑：12s/3 片、seq 163 万）而两次不推进 ⇒ 判「直播源此刻不推进」，
     这一格是我把「seq 数字大」当成"在涨"报给用户之后，被自己的复采打回来才补的。
     间隔短于两枚分片／有一边没读到清单＝未证（不许把"没等到变化"读成"没有变化"）。
  W) 按类型选线（那条指令的动作面）：`--type` 数到的台/线条数必须与独立数出来的对表，
     点名一个不存在的组时要打「命中 0」，不许让"选到 0 条"读成"这一组没问题"。

跑法：python3 tools/tests/test_stale_asset.py    （退出码 0 才算过）
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import health_probe as hp  # noqa: E402

STALE_FILE = BASE / "tools/fixtures/hunan_hlsb_playlist.m3u8"      # 湖南卫视 线1（真拉）
LIVE_FILE = BASE / "tools/fixtures/live_hnwshd_playlist.m3u8"      # 湖南卫视 线3（真拉）
YT_FILE = BASE / "tools/fixtures/yantai_live_playlist.m3u8"        # 烟台新闻综合 线1（真拉，假阳性对照）
YT_SHA = "5fdd031774d05c1b"
STALE_SHA = "8186cb3b317117d2"
LIVE_SHA = "7b7cdf535b1bc968"
CATALOG = BASE / "app/src/main/assets/channels.json"

failures: list[str] = []
ran = 0


def check(label: str, got, want) -> None:
    global ran
    ran += 1
    if got != want:
        failures.append(f"{label}: 得到 {got!r}，期望 {want!r}")


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def synth(n: int, seconds: float, endlist: bool) -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{int(seconds)}",
             "#EXT-X-MEDIA-SEQUENCE:0"]
    for i in range(n):
        lines += [f"#EXTINF:{seconds:.6f},", f"seg_{i:05d}.ts"]
    if endlist:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def part_s() -> None:
    """两份真清单：sha 钉住，读数一读一定一活。"""
    check("S1 定长夹具在仓库里", STALE_FILE.exists(), True)
    check("S2 直播夹具在仓库里", LIVE_FILE.exists(), True)
    sb, lb = STALE_FILE.read_bytes(), LIVE_FILE.read_bytes()
    check("S3 定长夹具字节没被换过", hashlib.sha256(sb).hexdigest()[:16], STALE_SHA)
    check("S4 直播夹具字节没被换过", hashlib.sha256(lb).hexdigest()[:16], LIVE_SHA)
    a = hp.classify_window(read_text(STALE_FILE))
    b = hp.classify_window(read_text(LIVE_FILE))
    check("S5 真·定长清单读数", a["asset"], "定长候选（10479s/2096片，seq=0）")
    check("S6 真·定长四格", (a["endlist"], a["media_sequence"], a["pl_segments"], a["pl_seconds"]),
          (True, 0, 2096, 10479.3))
    check("S7 真·直播清单读数", b["asset"], f"直播窗口（{b['pl_segments']}片）")
    check("S8 真·直播 endlist/seq", (b["endlist"], b["media_sequence"]), (False, 57711))
    check("S9 两臂读数必须相反", a["asset"].startswith("定长") and b["asset"].startswith("直播"), True)


def part_t() -> None:
    """阈值两侧＋"判别是 ENDLIST 不是时长"。"""
    check("T1 400s/40片＋ENDLIST＋seq=0＝定长候选",
          hp.classify_window(synth(40, 10.0, True))["asset"], "定长候选（400s/40片，seq=0）")
    check("T2 60s/40片＋ENDLIST＝待复采（不够定长那一档）",
          hp.classify_window(synth(40, 1.5, True))["asset"], "待复采（ENDLIST 短表：60s/40片）")
    check("T3 400s/40片 无 ENDLIST＝直播窗口（时长不单独定罪）",
          hp.classify_window(synth(40, 10.0, False))["asset"], "直播窗口（40片）")
    check("T4 阈值常量现读", (hp.STALE_ASSET_MIN_SECONDS, hp.STALE_ASSET_MIN_SEGMENTS), (300.0, 30))


def part_u() -> None:
    """三种未证各归各，不许折成任何一种判定。"""
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n720.m3u8\n"
    got = hp.classify_window(master)
    check("U1 主清单＝未证（没有时长表）", got["asset"], "未证（清单里没有 EXTINF 时长表）")
    # 这一格是夹具第一轮真抓到的缺陷：主清单里那行 variant 曾被 SEGMENTS 数成「1 片」，
    # 于是"没量到"被读成「直播窗口（1片）」。分片数必须落成 0。
    check("U1b 主清单不许把 variant 地址数成分片", (got["pl_segments"], got["pl_seconds"]), (0, None))
    check("U2 不是清单＝未证", hp.classify_window("")["asset"], "未证（不是媒体清单）")
    check("U3 空 None＝未证", hp.classify_window(None)["pl_segments"], 0)
    no_dur = "#EXTM3U\n#EXT-X-ENDLIST\nseg_00000.ts\n"
    check("U4 ENDLIST 无时长＝未证",
          hp.classify_window(no_dur)["asset"], "未证（清单里没有 EXTINF 时长表）")
    check("U6 有 EXTINF 却没分片行＝第三种未证",
          hp.classify_window("#EXTM3U\n#EXTINF:10.0,\n")["asset"], "未证（有 EXTINF 却没有分片行）")
    check("U5 未证不许读成直播窗口",
          [hp.classify_window(t)["asset"].startswith("直播")
           for t in ("", master, no_dur, "#EXTM3U\n#EXTINF:10.0,\n")],
          [False, False, False, False])


def part_v() -> None:
    """真跑那一臂：定长清单进 probe_one，verdict 照旧、asset 必须记账。"""
    body = STALE_FILE.read_bytes()
    ts = bytes([0x47]) + b"\x00" * 187  # 188 字节一枚最小 TS 包（首字节 0x47＝TS_MAGIC）

    class Resp:
        def __init__(self, url):
            self.status_code, self.url = 200, url

        def close(self):
            pass

    def fake_fetch(session, url, timeout, range_header=False):
        return Resp(url), (body if url.endswith(".m3u8") else ts * 8)

    keep = {n: getattr(hp, n) for n in ("fetch", "thread_session")}
    hp.fetch = fake_fetch
    hp.thread_session = lambda: object()
    try:
        row = hp.probe_one("http://9.9.9.9/live/stale.m3u8", 3.0, 3.0)
    finally:
        for n, f in keep.items():
            setattr(hp, n, f)
    check("V1 verdict 没被这条尺改动（只记嫌疑）", row["verdict"], "可取到分片")
    check("V2 asset 必须写上定长候选", row["asset"], "定长候选（10479s/2096片，seq=0）")
    check("V3 四格随行落盘", (row["endlist"], row["pl_segments"], row["pl_seconds"]),
          (True, 2096, 10479.3))
    # 负对照：同一条路吃直播清单，asset 必须变成"直播窗口"而不是恒为定长
    live = LIVE_FILE.read_bytes()

    def fake_fetch2(session, url, timeout, range_header=False):
        return Resp(url), (live if url.endswith(".m3u8") else ts * 8)
    hp.fetch = fake_fetch2
    hp.thread_session = lambda: object()
    try:
        row2 = hp.probe_one("http://9.9.9.9/live/live.m3u8", 3.0, 3.0)
    finally:
        for n, f in keep.items():
            setattr(hp, n, f)
    check("V4 负对照（直播清单）asset 不同", row2["asset"].startswith("直播窗口"), True)
    check("V5 负对照 verdict 也照旧", row2["verdict"], "可取到分片")


def part_w() -> None:
    """按类型选线：数到的台/条要能独立对表，命中 0 必须点名。"""
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_cat = {}
    for ch in data["channels"]:
        by_cat.setdefault(ch["category"], [0, 0])
        by_cat[ch["category"]][0] += 1
        by_cat[ch["category"]][1] += len(ch["streams"])
    for spec, key in (("cctv", "cctv"), ("卫视", None)):
        urls = hp.load_urls(CATALOG, [spec])
        if key:
            check(f"W1 --type {spec} 线数与独立计数对表", len(urls), by_cat[key][1])
        else:
            subs = [c for c in data["channels"] if c.get("subgroup") == spec]
            check(f"W1 --type {spec} 线数与独立计数对表",
                  len(urls), sum(len(c["streams"]) for c in subs))
    check("W2 不点名＝全目录", len(hp.load_urls(CATALOG, None)),
          sum(len(c["streams"]) for c in data["channels"]))
    check("W3 查无此组＝0 条（不许读成「这一组没问题」）",
          len(hp.load_urls(CATALOG, ["这个组不存在"])), 0)
    check("W4 台名三种写法都认",
          (hp.channel_types({"category": "cctv", "subgroup": "卫视"})),
          {"cctv", "cctv/卫视", "卫视"})
    check("W5 组/子组整写法能选", len(hp.load_urls(CATALOG, ["cctv/4K 超高清"])),
          sum(len(c["streams"]) for c in data["channels"]
              if c.get("subgroup") == "4K 超高清"))



def part_y() -> None:
    """假阳性对照：带 ENDLIST 但 seq 在涨的直播短窗，绝不能被判成定长（10-27 整组回测真抓到 4 条）。"""
    check("Y1 烟台真清单在仓库里", YT_FILE.exists(), True)
    check("Y2 烟台真清单字节没被换过",
          hashlib.sha256(YT_FILE.read_bytes()).hexdigest()[:16], YT_SHA)
    y = hp.classify_window(read_text(YT_FILE))
    check("Y3 烟台那条不许叫定长", y["asset"].startswith("定长"), False)
    check("Y4 烟台那条＝待复采（不许替它断言「在涨」）",
          y["asset"], "待复采（ENDLIST、seq=%d）" % y["media_sequence"])
    check("Y5 烟台四格现读", (y["endlist"], y["pl_segments"], y["pl_seconds"]), (True, 3, 12.0))
    # 两个入口一把尺：台账里只有四格时重算，必须与吃清单文本得到同一句话
    for f in (STALE_FILE, LIVE_FILE, YT_FILE):
        d = hp.classify_window(read_text(f))
        check(f"Y6 四格重算与文本同尺（{f.name}）",
              hp.window_bucket(d["endlist"], d["media_sequence"],
                               d["pl_segments"], d["pl_seconds"]), d["asset"])
    check("Y7 整组回测里那 4 条烟台线全在这一形状下（不许有漏网的定长判词）",
          {hp.classify_window(read_text(YT_FILE))["asset"].split("（")[0]}, {"待复采"})



def part_x() -> None:
    """复采定音那一格：四格怎么比才算「定长实证」，什么时候只能报未证。"""
    stale = {"verdict": "可取到分片", "endlist": True, "media_sequence": 0,
             "pl_segments": 2096, "pl_seconds": 10479.3,
             "asset": "定长候选（10479s/2096片，seq=0）"}
    yantai = {"verdict": "可取到分片", "endlist": True, "media_sequence": 1639195,
              "pl_segments": 3, "pl_seconds": 12.0,
              "asset": "待复采（ENDLIST 但 seq=1639195 在涨）"}
    check("X1 四格全同且间隔够＝定长实证",
          hp.advance_verdict(stale, dict(stale), 90.0),
          "窗口纹丝不动（四格全同，间隔 90s）＝定长实证")
    moved = dict(yantai, media_sequence=1639217)
    check("X2 seq 涨了＝直播在推进",
          hp.advance_verdict(yantai, moved, 90.0).startswith("直播窗口在推进"), True)
    check("X3 推进那行要把两个 seq 都念出来",
          ("1639195" in hp.advance_verdict(yantai, moved, 90.0)) and
          ("1639217" in hp.advance_verdict(yantai, moved, 90.0)), True)
    check("X4 一边没读到清单＝未证",
          hp.advance_verdict({**stale, "asset": None}, stale, 90.0),
          "未证（有一边没读到清单）")
    # 间隔短于两枚分片时，seq 不动看不出涨没涨——不许读成定长
    check("X5 间隔太短＝未证（烟台那种 4 秒单片，10 秒间隔定不了音）",
          hp.advance_verdict(yantai, dict(yantai), 10.0).startswith("未证（间隔"), True)
    check("X7 直播短窗两次不推进＝不能叫定长实证（10-27 复采真撞上的）",
          hp.advance_verdict(yantai, dict(yantai), 90.0),
          "窗口纹丝不动（四格全同，间隔 90s）——形态是直播短窗（seq=1639195、12.0s/3片）"
          "⇒ 直播源此刻不推进（冻帧/断流候选），不是定长片")
    check("X8 负对照：把形态换成定长候选那一臂，同一句不许出现「不是定长片」",
          "不是定长片" in hp.advance_verdict(stale, dict(stale), 90.0), False)
    check("X6 片数变了也算在推进（seq 相同不蒙混）",
          hp.advance_verdict(stale, dict(stale, pl_segments=2097), 90.0).startswith("直播窗口在推进"), True)


def main() -> int:
    for fn in (part_s, part_t, part_u, part_v, part_w, part_y, part_x):
        fn()
    for line in failures:
        print(f"[红] {line}")
    print(f"[stale_asset 夹具] {ran - len(failures)}/{ran} 过 · 红 {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
