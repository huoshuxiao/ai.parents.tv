#!/usr/bin/env python3
"""验「线路可用性优于频道数量」这一批的几把尺：重复台合并、报告里容器解不了不算画面、
目录里容器解不了压过台标、抽帧窗口内卡流不算正常画面、地址黑名单随目录一起进 APK 挡回公开源、
读数文件的键一律用现尺重算。

下面每一格都写清自己挡的是哪一种说谎；格子数不写死（10-06 那次「写死的条数」教训），
最后一行由 check() 累加出来的实际格数念：
  1 同名两条、线路不同 → 并成 1 台，两条线一条不丢
  2 只差后缀（庆元综合 / 庆元综合频道）且共用同一条地址 → 并成 1 台
  3 负对照：底名相同但台名不同、地址不相交 → 不并（县里真有两个频道的那种）
  4 负对照：台名一模一样但分属不同大类（local / featured）→ 不并
  5 首条线容器解不了（VLC 出帧、ExoPlayer 出不了）→ 那条不算数，且不停手，第二条能播的线被找出来
  6 全台只有一条解不了的线 → 这台落到「没有正常画面」（旧口径会拿那张 jpg 当证据）
  7 正对照：同一形状但首条 verdict 是「可取到分片」→ 算有画面，第二条按「至少一条」口径跳过
  8 同名且其中一条落在「其他」兜底组 → 并回省份组，线路取并集
  9 负对照：同名但两边各有省份归属（浙江 / 四川）→ 不并（两个省各有同名县台）
 10 「抽帧认过台标」＋「容器解不了」同时成立 → 判档 4（VLC 认的台标不等于电视上能播）
 11 负对照：同一线把容器读数换成「可取到分片」→ 回到档 0，证明起作用的是容器那一格
 12 同一条线三帧字节全同 → 判卡流；走生产入口时首条卡流不算正常画面、且探帧不停手
 13 负对照：三帧各不相同 → 不判卡流（慢镜头像素差近 0 也不算卡，平湖那条航拍就是这种）
 14 黑名单收对端答复过的坏，不收「本机连不通」和「取到过分片」（10-21 选项A）
 15 播放器解不开的容器 → 进黑名单
 16 抽帧判坏按地址进黑名单；「画面是别的台」不进（那是名字与地址的配对，归改挂规则管）
 17 目录自己按名字救回来的地址从黑名单豁免——「留下」与「拉黑」不许同时成立
 18 --keep-dead 时黑名单为空：目录留坏线与设备挡坏线走同一个开关
 19 已落盘目录里那份黑名单：每枚键都是归一键（设备端按 StreamUrl.canonical 查），且不与目录冲突
 20 读数行里旧尺写的 canonical 不许当键：按 url 用现尺重算，档位与黑名单都查得到
 20b 负对照：抹掉 url 只剩旧尺那格时读数就挂在查不到的键上（档回落到 2＝没测过）
 21 五种解不开的容器字节头（FLV/MKV/RMVB/ASF/AVI）都要闻得出来，别再归成 binary 报「可取到分片」
 22 负对照：裸 TS 与 MP4 解得开不许误杀；名单与 URL 扩展名那把尺同源（wmv 走 ASF 字节头）
 23 --dry-run 不落盘：量统计／退出码时不许写在库资产（真踩过：漏 --out 把 1030/1465/668 覆成 1035/1478/655）
 23b 负对照：同一串参数去掉 --dry-run 就真落盘（证明 23 拦的是开关，不是参数写错）
 24 「有正常画面」只认抽到过帧且非占位页、容器解得开（挪分组的判据与报告那三把尺同一口径）
 24b 负对照：只拉到过分片不算画面——拉到字节≠电视上有节目
 25 落盘产物：待验证分组在、分项之和==总台数、咪咕体育 保留且 3 条线被「目录留下即豁免」挡在黑名单外
 26 占位页走在身份判定前面：复核过台标 + 抽到推广页 ⇒ 档 4、不算画面、被删（用户「第5档删线路」）
 26b 负对照：摘掉占位页那一格，同一地址回到档 0 且算画面（证明咬人的是占位页读数）
 27 「连不上」不再算「本机连不通」：档 4、进黑名单、可删（用户指令「除超时线路保留外，其余都删除」）
 27b 负对照：连接超时／响应超时仍是档 3、不拉黑、不许删（保留那一半也要有牙）
 41 必看台＋台标相符＋测试卡 ⇒ 只降分不删线（evidence() 那一层也要放行，否则 strict_filter 之前台就空了）
 42 负对照：同一形状把卡换成推广页 ⇒ 档 4、被删（例外只吃「测试卡」）
 43 负对照：非必看台同一形状 ⇒ 档 4、被删（豁免不外溢到六组）
 44 卫视组删线（用户 10-23 17:5x「不可用线路删除」）：台内「连接超时」线删掉、实证线留下、进 sat_pruned 名单
 44b 负对照：同形状非卫视组，台内线路一条不动（这台只删卫视组的线，不外溢）
 45 「响应超时」同一把尺删
 45b 负对照：设备实测播过的那条「连接超时」线不许删（「已播过的线路绝不换线」盖过删线指令）
 46 卫视台只剩「连接超时」但抽过帧的线：删无可删时一条不动（不许把有实证的台删空）
 46b 负对照：同一形状把身份换成台标相符 ⇒ 档 0、留下（证明咬的是证据档位那一格，不是台名）
 47 「曾经出过真实画面的有台标的线路就留」（用户 10-23 18:5x）：台内同为档 3，抽过帧的那条留下、没实证的同档线删掉
 48 特色频道整组删除（用户 10-23 20:0x「删除特色频道」）：有画面实证的台也删、计数单独一格、别的组不动
 49 甲（用户 10-23 20:5x 点档）：首分片 >10 秒的慢线挪到台内最后、其余保序
 49b 负对照：设备播过的慢线原地不动（「已播过不挪位」盖过甲）
 49c 负对照：已排在末位的慢线不记进挪线名单（名单只记真要动的那批）
跑法：python3 tools/tests/test_availability_count.py    （退出码 0 才算过）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402
import prepack_report as pr  # noqa: E402

failures: list[str] = []
# 格数由 check() 自己累加：写死的「N/N」在加判据那一笔会先变成假话（10-06 踩过「条数锚点」那一类）。
CELLS = [0]


def check(cell: str, ok: bool, detail: str = "") -> None:
    CELLS[0] += 1
    print(f"{'通过' if ok else '判红'}  {cell}" + (f" —— {detail}" if detail else ""))
    if not ok:
        failures.append(cell)


def row(name, urls, category="local", subgroup="浙江", order=0):
    return {"id": f"{category}-x", "name": name, "category": category, "subgroup": subgroup,
            "logo": "", "streams": list(urls), "_order": order, "_avail": 1}


U1 = "http://l.cztvcloud.com/channels/lantian/SXqingyuan1/720p.m3u8"
U2 = "http://l.cztvcloud.com/channels/lantian/SXqingyuan1/1080p.m3u8"
U3 = "http://other.example.com/live/qingyuan2.m3u8"

# ── 1 同名不同线路：并成一台，线路取并集
rows = [row("庆元综合", [U1]), row("庆元综合", [U2])]
merged = bc.merge_duplicate_rows(rows)
check("1 同名两条并成 1 台、两条线都不丢",
      len(rows) == 1 and merged == 1 and sorted(rows[0]["streams"]) == sorted([U1, U2]),
      f"剩 {len(rows)} 台、摘掉 {merged} 条、并集 {len(rows[0]['streams'])} 条线")

# ── 2 只差后缀且共用地址
rows = [row("庆元综合频道", [U1]), row("庆元综合", [U1, U2])]
merged = bc.merge_duplicate_rows(rows)
kept = rows[0]["name"] if len(rows) == 1 else "?"
check("2 只差后缀且共用地址 → 并成 1 台",
      len(rows) == 1 and merged == 1 and sorted(rows[0]["streams"]) == sorted([U1, U2]),
      f"留下的台名是「{kept}」")

# ── 3 负对照：底名相同、台名不同、地址不相交
rows = [row("庆元新闻综合", [U1]), row("庆元综合", [U3])]
merged = bc.merge_duplicate_rows(rows)
check("3 底名相同但地址不相交、台名不同 → 不许并",
      len(rows) == 2 and merged == 0, f"仍是 {len(rows)} 台、摘掉 {merged} 条")

# ── 4 负对照：同名但分属不同大类
rows = [row("都市剧场", [U1], category="local", subgroup="上海"),
        row("都市剧场", [U1], category="featured", subgroup="影视轮播")]
merged = bc.merge_duplicate_rows(rows)
check("4 同名但不同大类（local / featured）→ 不许并", len(rows) == 2 and merged == 0,
      f"仍是 {len(rows)} 台")

# ── 5／6 抽帧口径：容器解不了的线不算「有正常画面」，并且不能让它把探帧提前收手
CAPTURED: list[str] = []


def fake_grab_frame(url, work_dir, timeout, reuse=False, no_capture=False):  # noqa: ARG001
    CAPTURED.append(url)
    return {"frame": Path(f"/tmp/{abs(hash(url))}.jpg"), "bytes": 200_000,
            "width": 1920, "height": 1080, "timed_out": False, "elapsed_ms": 0}


def fake_fingerprint(_path):
    return None


pr.grab_frame = fake_grab_frame
pr.fingerprint_hit = fake_fingerprint
CHANNEL = {"id": "local-test", "name": "测试台", "subgroup": "影视轮播", "category": "featured",
           "streams": [U1, U2]}


def verdicts(first):
    CAPTURED.clear()
    health = {bc.stream_rules.canon(U1): {"verdict": first, "http_status": 200, "seg_bytes": 262144,
                                          "resolution": None},
              bc.stream_rules.canon(U2): {"verdict": "可取到分片", "http_status": 200,
                                          "seg_bytes": 262144, "resolution": None}}
    return pr.probe_channel(CHANNEL, health, Path("/tmp"), 8, True)


r = verdicts("容器不支持")
check("5 首条容器解不了 → 不收手，第二条能播的线被找出来",
      r["clean_frame_ok"] == 1 and r["frame_ok"] == 2 and r["undemuxable"] == 1
      and CAPTURED == [U1, U2],
      f"抽帧 {r['frame_ok']} 条、算数的 {r['clean_frame_ok']} 条、探到第 {len(CAPTURED)} 条")

# ── 6 只有一条线、且那条解不了：这台必须落到「没有正常画面」
SINGLE = {"id": "local-single", "name": "独苗台", "subgroup": "影视轮播", "category": "featured",
          "streams": [U1]}
CAPTURED.clear()
r = pr.probe_channel(SINGLE, {bc.stream_rules.canon(U1): {"verdict": "容器不支持", "http_status": 200,
                                                          "seg_bytes": 262144, "resolution": None}},
                     Path("/tmp"), 8, True)
check("6 全台只有一条解不了的线 → 算没有正常画面（旧口径会把这台的 jpg 当证据）",
      r["clean_frame_ok"] == 0 and r["frame_ok"] == 1 and r["undemuxable"] == 1,
      f"抽帧 {r['frame_ok']} 条、算数的 {r['clean_frame_ok']} 条")

# ── 7 正对照：同样形状但首条能取分片 → 算有画面，第二条按「至少一条」口径跳过
r = verdicts("可取到分片")
check("7 同样形状但首条能取分片 → 算有画面，第二条按口径跳过",
      r["clean_frame_ok"] == 1 and r["undemuxable"] == 0 and CAPTURED == [U1],
      f"算数的 {r['clean_frame_ok']} 条、只探了第 {len(CAPTURED)} 条")

# ── 8 同名、其中一条落在「其他」兜底组 → 并回省份组，线路取并集
rows = [row("铜梁综合", [U1], subgroup="重庆"), row("铜梁综合", [U3], subgroup="其他")]
log = []
merged = bc.merge_duplicate_rows(rows, log)
check("8 同名且一条在「其他」兜底组 → 并回省份组，两条线都不丢",
      len(rows) == 1 and merged == 1 and rows[0]["subgroup"] == "重庆"
      and sorted(rows[0]["streams"]) == sorted([U1, U3]) and log == [("铜梁综合", "其他", "重庆")],
      f"剩 {len(rows)} 台、组别 {rows[0]['subgroup']}、并集 {len(rows[0]['streams'])} 条线")

# ── 9 负对照：两条各自有省份归属、台名相同 → 不并（两个省各有同名县台）
rows = [row("庆元综合", [U1], subgroup="浙江"), row("庆元综合", [U3], subgroup="四川")]
merged = bc.merge_duplicate_rows(rows)
check("9 同名但两边各有省份归属 → 不许并", len(rows) == 2 and merged == 0, f"仍是 {len(rows)} 台")

# ── 10 「抽帧认过台标」+「容器解不了」同时成立时，必须判坏（这条线在电视上是黑屏）
HUYA = "https://live.metshop.top/huya/11342424"
hk = bc.stream_rules.canon(HUYA)
bc.HEALTH = {hk: {"verdict": "容器不支持", "detail": "FLV 字节头"}}
bc.IDENTITY = {f"刘德华电影|{hk}": "台标相符"}
tier = bc.evidence(HUYA, "刘德华电影")
check("10 认过台标 + 容器解不了 → 判档 4（不能因为「VLC 里认出台标」压回第 1 位）",
      tier[0] == bc.BAD_TIER and not bc.usable("刘德华电影", HUYA), f"档位 {tier[0]}")

# ── 11 负对照：同一线把容器读数改成「可取到分片」，身份结论必须重新生效（证明起作用的是容器那一格）
bc.HEALTH = {hk: {"verdict": "可取到分片", "detail": "m3u8"}}
tier_ok = bc.evidence(HUYA, "刘德华电影")
check("11 摘掉容器读数后同一线回到档 0 → 差异确实来自容器那一格",
      tier_ok == (0, 0) and bc.usable("刘德华电影", HUYA), f"档位 {tier_ok}")
bc.HEALTH, bc.IDENTITY = {}, {}

# ── 12／13 卡流判据：同一条线几帧字节全同＝窗口内没出新画面；两帧不同＝不作判
FIX = BASE / "tools" / "fixtures"
import shutil  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    same = Path(tmp) / "same"
    diff_ = Path(tmp) / "diff"
    same.mkdir(); diff_.mkdir()
    for i in range(3):
        shutil.copyfile(FIX / "frame_program.jpg", same / f"frame_{i:05d}.jpg")
    shutil.copyfile(FIX / "frame_program.jpg", diff_ / "frame_00001.jpg")
    shutil.copyfile(FIX / "frame_dark_program.jpg", diff_ / "frame_00002.jpg")
    shutil.copyfile(FIX / "frame_qr_placeholder.jpg", diff_ / "frame_00003.jpg")
    stalled_same = pr.frames_stalled(sorted(same.glob("frame_*.jpg")))
    stalled_diff = pr.frames_stalled(sorted(diff_.glob("frame_*.jpg")))
    check("12 三帧字节全同 → 判卡流（这条线在窗口里没出新画面）",
          bool(stalled_same) and "卡流" in stalled_same, f"{stalled_same}")
    check("13 负对照：三帧各不相同 → 不判卡流（慢镜头的像素差近 0 也不算卡）",
          stalled_diff is None, f"读数为 {stalled_diff}")

    # 走一遍生产入口：首条卡流时不能算「有正常画面」，且不能提前收手
    def grab_with_shots(url, work_dir, timeout, reuse=False, no_capture=False):  # noqa: ARG001
        src = same if url == U1 else diff_
        return {"frame": work_dir / "frame_first.jpg", "bytes": 200_000, "width": 1920,
                "height": 1080, "timed_out": False, "elapsed_ms": 0,
                "shots": sorted(src.glob("frame_*.jpg"))}

    pr.grab_frame = grab_with_shots
    health = {bc.stream_rules.canon(U1): {"verdict": "可取到分片", "http_status": 200,
                                          "seg_bytes": 262144, "resolution": None},
              bc.stream_rules.canon(U2): {"verdict": "可取到分片", "http_status": 200,
                                          "seg_bytes": 262144, "resolution": None}}
    CAPTURED.clear()
    r = pr.probe_channel(CHANNEL, health, Path(tmp), 8, True)
    check("12b 首条卡流、第二条正常 → 只有第二条算正常画面，且第一条没让探帧停手",
          r["clean_frame_ok"] == 1 and r["frame_ok"] == 2
          and r["lines"][0].get("stalled") and not r["lines"][1].get("stalled"),
          f"抽帧 {r['frame_ok']} 条、算数的 {r['clean_frame_ok']} 条")

# ── 14~19 地址黑名单（blockedLines）：设备端自动更新挡回判坏线路的那一份证据
U4 = "http://blocked.example.com/live/a.m3u8"
U5 = "http://unreachable.example.com/live/b.m3u8"
U6 = "http://flv.example.com/live/c.m3u8"
U7 = "http://placeholder.example.com/live/d.m3u8"
U8 = "http://elsewhere.example.com/live/e.m3u8"
canon = bc.stream_rules.canon


def reset_evidence():
    bc.DROP_DEAD = True
    bc.HEALTH = {canon(U4): {"verdict": "清单404"}, canon(U5): {"verdict": "连接超时"},
                 canon(U6): {"verdict": "容器不支持"},
                 canon("http://good.example.com/live/g.m3u8"): {"verdict": "可取到分片"}}
    bc.IDENTITY = {}
    bc.FRAME_PLACEHOLDER = {}


reset_evidence()
blocked, exempt = bc.blocked_lines()
check("14 黑名单收对端答复过的坏，不收本机连不通/取到过分片",
      canon(U4) in blocked and canon(U5) not in blocked
      and canon("http://good.example.com/live/g.m3u8") not in blocked,
      f"黑名单 {len(blocked)} 条、豁免 {len(exempt)} 条；「连接超时」不进＝10-21 选项A")

check("15 播放器解不开的容器 → 进黑名单（这类线在电视上一定黑屏）",
      canon(U6) in blocked and blocked.get(canon(U6)) == "容器不支持",
      f"读数 {blocked.get(canon(U6))}")

bc.IDENTITY = {f"青海卫视|{canon(U7)}": "占位页", f"四川卫视|{canon(U8)}": "画面是别的台"}
blocked, exempt = bc.blocked_lines()
check("16 抽帧判坏按地址进黑名单，「画面是别的台」不进（那是名字与地址的配对，另有改挂规则）",
      canon(U7) in blocked and canon(U8) not in blocked,
      f"占位页在名单里、错挂的那条不在（黑名单只管整条地址都坏的那些）")

reset_evidence()
blocked, exempt = bc.blocked_lines(keep={canon(U4)})
check("17 目录自己按名字救回来的地址从黑名单豁免（留下与拉黑不许同时成立）",
      canon(U4) not in blocked and exempt == [canon(U4)],
      f"豁免 {exempt}")

bc.DROP_DEAD = False
blocked, exempt = bc.blocked_lines()
check("18 --keep-dead 时黑名单为空：目录留坏线与设备挡坏线得走同一个开关",
      blocked == {} and exempt == [], f"名单 {len(blocked)} 条")
reset_evidence()

ASSET = BASE / "app" / "src" / "main" / "assets" / "channels.json"
if ASSET.exists():
    cat = json.loads(ASSET.read_text(encoding="utf-8"))
    keys = list(cat.get("blockedLines") or {})
    not_canon = [k for k in keys if canon(k) != k]
    in_catalog = [k for k in keys if k in {canon(s) for c in cat["channels"] for s in c["streams"]}]
    check("19 已落盘目录里的黑名单：每一枚键都是归一键（设备端按 StreamUrl.canonical 查，写法不统一整份废），"
          "且没有一枚指向目录自己留下的线",
          bool(keys) and not not_canon and not in_catalog,
          f"{len(keys)} 枚键、非归一 {len(not_canon)} 枚、与目录冲突 {len(in_catalog)} 枚")
else:
    check("19 已落盘目录里的黑名单可读（缺 channels.json 时这一格无从现读，按未查处理）",
          False, f"没有 {ASSET}")

# ── 20 读数文件里存的 canonical 是「写文件那一刻的尺」量出来的，键必须用现尺重算
with tempfile.TemporaryDirectory() as tmp:
    stale = Path(tmp) / "stale.jsonl"
    v6url = "http://[2409:8087:1e03:21::2]:6060/cms001/ch00000090990000001022/index.m3u8"
    stale.write_text(json.dumps({"url": v6url,
                                 "canonical": "http://2409:8087:1e03:21::2:6060/cms001/"
                                              "ch00000090990000001022/index.m3u8",
                                 "verdict": "清单404"}) + "\n", encoding="utf-8")
    reset_evidence()
    rows = bc.load_health(stale)
    bc.HEALTH = rows
    live = bc.evidence(v6url, "某某卫视")[0]
    check("20 旧尺写的 canonical（IPv6 少方括号那 134 行那种）不许当键用：现尺重算后读数才挂在查得到的键上",
          list(rows) == [canon(v6url)] and live == 4 and canon(v6url) in bc.blocked_lines()[0],
          f"键 {list(rows)}，档位 {live}")
    # 负对照：把这一行的 url 抹掉，只留旧尺那格 canonical —— 那就退化成「查不到的读数」，
    # 档位回到 2（没测过），证明这格判据咬的是键、不是那句 verdict。
    nofurl = Path(tmp) / "nofurl.jsonl"
    nofurl.write_text(stale.read_text(encoding="utf-8").replace('"url": "%s", ' % v6url, ""),
                      encoding="utf-8")
    rows2 = bc.load_health(nofurl)
    bc.HEALTH = rows2
    check("20b 负对照：没有 url 时才沿用存的键，此时这条读数在现尺下查不到（档 2）",
          list(rows2) != [canon(v6url)] and bc.evidence(v6url, "某某卫视")[0] == 2,
          f"键 {list(rows2)}")
    reset_evidence()

# ── 21~22 闻字节头认容器：分片取到字节 ≠ 电视解得开
import health_probe as hp  # noqa: E402

FLV = b"FLV\x08\x04" + b"\x00" * 64
MKV = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
RMVB = b".RMF" + b"\x00" * 64
ASF = b"\x30\x82\xb2\x7b" + b"\x00" * 64
AVI = b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 64
TSB = b"\x47" + b"\x00" * 187
MP4 = b"\x00\x00\x00\x18ftypiso5" + b"\x00" * 64
kinds = {n: hp.looks_like_video(b) for n, b in
         [("FLV", FLV), ("MKV", MKV), ("RMVB", RMVB), ("ASF", ASF), ("AVI", AVI)]}
check("21 五种 ExoPlayer 解不开的容器字节头都要闻得出来（以前只认 flv/mkv，其余归成 binary 就报「可取到分片」）",
      all(k == n.lower() for n, k in kinds.items())
      and all(k in hp.UNDEMUXABLE_KINDS for k in kinds.values()),
      f"闻到 {kinds}")
check("22 负对照：裸 TS（0x47）与 MP4（ftyp）解得开，不许被这一格打死；"
      "名单与 URL 扩展名那把尺同源（wmv 由 ASF 字节头代劳）",
      hp.looks_like_video(TSB) == "ts" and hp.looks_like_video(MP4) == "fmp4"
      and "ts" not in hp.UNDEMUXABLE_KINDS and "fmp4" not in hp.UNDEMUXABLE_KINDS
      and set(bc.stream_rules.UNSUPPORTED_EXT) - {"wmv"} == set(hp.UNDEMUXABLE_KINDS),
      f"扩展名 {sorted(bc.stream_rules.UNSUPPORTED_EXT)} / 字节头 {sorted(hp.UNDEMUXABLE_KINDS)}")

# ── 23~23b --dry-run：量退出码时不许碰在库资产
# 起因是一次真事故：为量「全套证据重跑退出码」写了条 `build_catalog.py --offline --health <单份>` 而漏了 --out，
# 默认路径就是 app/src/main/assets/channels.json —— 1030/1465/668 那份当场被覆成 1035/1478/655。
import subprocess  # noqa: E402

DRY = BASE / "tools" / ".cache" / "dryrun_arm.json"
if DRY.exists():
    DRY.unlink()
cli = [sys.executable, str(BASE / "tools" / "build_catalog.py"), "--offline",
       "--health", str(BASE / "tools" / ".cache" / "health_cctv5" / "line_health.jsonl")]
subprocess.run(cli + ["--dry-run", "--out", str(DRY)], capture_output=True, text=True, check=False)
check("23 --dry-run 不落盘：文件不存在（这一格挡的是「只想量个数却把在库资产写了」）",
      not DRY.exists(), f"--dry-run 之后 {DRY.name} 存在={DRY.exists()}")
subprocess.run(cli + ["--out", str(DRY)], capture_output=True, text=True, check=False)
check("23b 负对照：同一串参数去掉 --dry-run 就真落盘（证明 23 拦的是开关，不是参数写错）",
      DRY.exists() and DRY.stat().st_size > 1024,
      f"落盘 {DRY.stat().st_size // 1024} KiB" if DRY.exists() else "没落盘＝开关根本没起作用")
if DRY.exists():
    DRY.unlink()


# ── 24~25 「无画面单独分组」与「咪咕体育保留」（用户 2026-10-22 指令）
U9 = "http://picture.example.com/live/p.m3u8"      # 抽到过帧、画面正常
U10 = "http://onlyseg.example.com/live/q.m3u8"     # 只拉到过分片，没验过画面
U11 = "http://card.example.com/live/r.m3u8"        # 抽到过帧但是测试卡
canon = bc.stream_rules.canon


def reset_picture_evidence():
    bc.DROP_DEAD = True
    bc.HEALTH = {canon(U9): {"verdict": "可取到分片"}, canon(U10): {"verdict": "可取到分片"},
                 canon(U11): {"verdict": "可取到分片"},
                 canon("http://bad-container.example.com/live/s.m3u8"): {"verdict": "容器不支持"}}
    bc.IDENTITY = {}
    bc.FRAME_PLACEHOLDER = {canon(U11): "测试卡"}
    # U10 故意不给帧读数：它只有「拉到过分片」这一条证据，正是 24b 要抓的那种形状
    bc.FRAME_RES = {canon(U9): [1920, 1080], canon(U11): [640, 360]}


reset_picture_evidence()
check("24 「有正常画面」只认抽到过帧且非占位页、容器解得开（挪分组的判据与报告那三把尺同一口径）",
      bc.has_picture("测试台", U9) and not bc.has_picture("测试台", U11)
      and not bc.has_picture("测试台", "http://bad-container.example.com/live/s.m3u8"),
      f"干净帧 True / 测试卡 False / 容器解不开 False")
seg_tier = bc.evidence(U10, "测试台")[0]
seg_usable = bc.usable("测试台", U10)
seg_picture = bc.has_picture("测试台", U10)
check("24b 负对照：只拉到过分片不算画面（拉到字节≠电视上有节目），这一格不许把纯分片证据的台留在原分类",
      (not seg_picture) and seg_usable and seg_tier == 1,
      f"usable={seg_usable} 档={seg_tier} has_picture={seg_picture}（要的是 True/1/False）")
pending_asset = BASE / "tools" / ".cache" / "pending_asset.json"
if pending_asset.exists():
    pa = json.loads(pending_asset.read_text(encoding="utf-8"))
    cats = {c["id"]: c for c in pa["categories"]}
    by_cat = {}
    for ch in pa["channels"]:
        by_cat[ch["category"]] = by_cat.get(ch["category"], 0) + 1
    migu = [c for c in pa["channels"] if c["name"] == "咪咕体育"]
    blocked_keys = set(pa["blockedLines"])
    migu_free = migu and all(canon(s) not in blocked_keys for s in migu[0]["streams"])
    check("25 落盘产物：「待验证」分组真在目录里、分项之和==总台数、咪咕体育 保留（3 条线）且这 3 条线因『目录留下』被豁免出黑名单",
          "pending" in cats and sum(by_cat.values()) == len(pa["channels"])
          and by_cat.get("pending", 0) == len([c for c in pa["channels"] if c["category"] == "pending"]) > 0
          and bool(migu) and len(migu[0]["streams"]) == 3 and migu_free,
          f"待验证 {by_cat.get('pending')} 台 / 总 {len(pa['channels'])} 台 / 咪咕体育 "
          f"{len(migu[0]['streams']) if migu else 0} 条线、在黑名单 {not migu_free}")
else:
    check("25 落盘产物那一格没跑（缺 tools/.cache/pending_asset.json，按未查处理）", False, "文件不存在")

# ── 26~26b 占位页赢过身份结论（用户 2026-10-22「第 5 档：删线路」，实测影响 7 条线）
U12 = "http://conflict.example.com/live/t.m3u8"    # 同一地址：复核说台标相符，抽帧又打出占位页


def reset_conflict(with_placeholder: bool):
    reset_picture_evidence()
    bc.IDENTITY = {f"测试台|{canon(U12)}": "台标相符"}
    bc.FRAME_RES[canon(U12)] = [1280, 720]
    if with_placeholder:
        bc.FRAME_PLACEHOLDER[canon(U12)] = "带二维码的推广页"
    else:
        bc.FRAME_PLACEHOLDER.pop(canon(U12), None)


reset_conflict(True)
tier_conflict = bc.evidence(U12, "测试台")[0]
pic_conflict = bc.has_picture("测试台", U12)
check("26 占位页走在身份判定前面：复核过台标 + 抽到推广页 ⇒ 档 4、不算画面、会被删",
      tier_conflict == bc.BAD_TIER and not pic_conflict and not bc.usable("测试台", U12),
      f"档={tier_conflict} has_picture={pic_conflict} usable={bc.usable('测试台', U12)}")
reset_conflict(False)
tier_clean = bc.evidence(U12, "测试台")[0]
pic_clean = bc.has_picture("测试台", U12)
check("26b 负对照：把占位页那一格摘掉，同一地址回到档 0 且算画面（证明咬人的是占位页读数，不是身份表失效）",
      tier_clean == 0 and pic_clean and bc.usable("测试台", U12),
      f"档={tier_clean} has_picture={pic_clean}（要的是 0/True）")


# ── 27~27b 「连接失败的线路，除超时线路保留外，其余都删除」（用户 2026-10-22 指令）
U12 = "http://refused.example.com/live/t.m3u8"      # 连不上：拒绝/DNS/主机不可达
U13 = "http://slow.example.com/live/u.m3u8"         # 连接超时
U14 = "http://slowread.example.com/live/v.m3u8"     # 响应超时
bc.DROP_DEAD = True
bc.IDENTITY = {}
bc.FRAME_PLACEHOLDER = {}
bc.FRAME_RES = {}
bc.HEALTH = {canon(U12): {"verdict": "连不上"}, canon(U13): {"verdict": "连接超时"},
             canon(U14): {"verdict": "响应超时"}}
blk27 = bc.blocked_lines()[0]
check("27 「连不上」不再算「本机连不通」：档 4、进地址黑名单、usable 判 False（『其余都删除』那一半）",
      bc.evidence(U12, "测试台")[0] == 4 and not bc.usable("测试台", U12)
      and canon(U12) in blk27 and blk27[canon(U12)] == "连不上",
      f"档 {bc.evidence(U12, '测试台')[0]}、黑名单命中={canon(U12) in blk27}")
check("27b 负对照：两种「超时」仍是档 3、不拉黑、不许删（保留那一半必须有牙，不然整族被误删）",
      all(bc.evidence(u, "测试台")[0] == 3 and bc.usable("测试台", u) and canon(u) not in blk27
          for u in (U13, U14)),
      f"连接超时 档 {bc.evidence(U13, '测试台')[0]}／响应超时 档 {bc.evidence(U14, '测试台')[0]}，这一族拉黑 0 条")

# ── 28~30 「央视 1-17 加 4K 超高清、辽宁体育必须能正常播放」（用户 10-22 23:4x 新判据，原话「此为最新判据」）
U15 = "http://oldframe.example.com/live/w.m3u8"      # 早先场次抽到过画面、本轮这一场没抽到
U16 = "http://freshframe.example.com/live/x.m3u8"    # 本轮抽到
FR = {"width": 1920, "height": 1080, "placeholder": ""}
bc.DROP_DEAD = True
bc.IDENTITY = {}
bc.FRAME_PLACEHOLDER = {}
bc.HEALTH = {canon(U15): {"verdict": "可取到分片"}, canon(U16): {"verdict": "可取到分片"}}
bc.FRAME_RES = {canon(U15): FR, canon(U16): FR}          # 累计帧：两条都有画面
bc.FRESH_RES = {canon(U16): FR}                          # 本轮帧：只有 U16
check("28 必看台只认「本轮帧」：同一地址旧场次有画面、本轮没有 ⇒ has_picture 真、has_picture_now 假（旧帧不许让必看台假绿）",
      bc.has_picture("CCTV-5 体育", U15) and not bc.has_picture_now("CCTV-5 体育", U15)
      and bc.has_picture_now("CCTV-5 体育", U16),
      f"U15 累计={bc.has_picture('CCTV-5 体育', U15)}/本轮={bc.has_picture_now('CCTV-5 体育', U15)}"
      f"，U16 本轮={bc.has_picture_now('CCTV-5 体育', U16)}")
bc.FRESH_RES = {canon(U15): FR, canon(U16): FR}
check("28b 负对照：把本轮帧补上同一地址，两把尺同判 True（证明咬人的是 FRESH_RES 那一格，不是容器表或占位页表）",
      all(bc.has_picture("CCTV-5 体育", u) and bc.has_picture_now("CCTV-5 体育", u) for u in (U15, U16)),
      "两把尺对 U15/U16 同判 True 才算这一格有牙")
bc.FRESH_RES = {}

_names = {c["name"] for c in json.loads(
    (BASE / "app/src/main/assets/channels.json").read_text(encoding="utf-8"))["channels"]}
check("29 必看名单本身要能对上现目录：20 台、无重名、每个名字都在目录里找得到（名单写错名字＝闸门恒过）",
      len(bc.MUST_PLAY_CHANNELS) == 20 and len(set(bc.MUST_PLAY_CHANNELS)) == 20
      and all(n in _names for n in bc.MUST_PLAY_CHANNELS),
      f"名单 {len(bc.MUST_PLAY_CHANNELS)} 台、去重 {len(set(bc.MUST_PLAY_CHANNELS))}，"
      f"目录里找不到的：{[n for n in bc.MUST_PLAY_CHANNELS if n not in _names] or '无'}")
check("30 只许有一份名单：REQUIRED_CHANNELS 就是 MUST_PLAY_CHANNELS（『必看组与必留名单是两份名单』那旧病不许复发）",
      bc.REQUIRED_CHANNELS is bc.MUST_PLAY_CHANNELS,
      f"is 同一对象={bc.REQUIRED_CHANNELS is bc.MUST_PLAY_CHANNELS}，长度 {len(bc.REQUIRED_CHANNELS)}")

# ── 31~36 strict_filter：删台/删线两处作用域（用户 10-22 23:4x＋23:5x「已实证有画面的线路不删除」）
P1 = "http://proven1.example.com/a.m3u8"     # 旧场次抽到过画面（累计实证），本轮也抽到
P2 = "http://proven2.example.com/c.m3u8"     # 只有旧场次抽到（本轮没复核到）
B1 = "http://noimg1.example.com/b.m3u8"      # 从没抽到过画面
B2 = "http://noimg2.example.com/d.m3u8"
B3 = "http://noimg3.example.com/e.m3u8"
B4 = "http://noimg4.example.com/f.m3u8"
bc.DROP_DEAD = True
bc.HEALTH = {}
bc.IDENTITY = {}
bc.FRAME_PLACEHOLDER = {}
bc.FRAME_RES = {canon(P1): FR, canon(P2): FR}
bc.FRESH_RES = {canon(P1): FR}


def mk(name, cat, sub, urls):
    return {"name": name, "category": cat, "subgroup": sub, "streams": list(urls)}


def run_strict(chs):
    import copy
    return bc.strict_filter([copy.deepcopy(c) for c in chs])


surv, st = run_strict([mk("CCTV-5 体育", "cctv", "央视", [B1, P1])])
check("31 必看台：没有画面实证的线路删掉、有实证的留下（顺序照旧）",
      [c["name"] for c in surv] == ["CCTV-5 体育"] and surv[0]["streams"] == [P1]
      and st["pruned_lines"] == 1 and not st["must_no_picture"],
      f"剩余线路={surv[0]['streams']} 删线数={st['pruned_lines']} no_picture={st['must_no_picture']}")
surv2, st2 = run_strict([mk("CCTV-6 电影", "cctv", "央视", [B2])])
check("32 必看台一条实证都没有：台必须留在目录里并进 no_picture（不许悄悄消失，也不许拿坏线冒充可用）",
      [c["name"] for c in surv2] == ["CCTV-6 电影"] and surv2[0]["streams"] == [B2]
      and st2["must_no_picture"] == ["CCTV-6 电影"],
      f"台数={len(surv2)} no_picture={st2['must_no_picture']}")
surv3, st3 = run_strict([mk("驻马店综合", "local", "河南", [P2, B3])])
check("33 六组台只要有实证：台保留，且台内线路一条都不动（那句指令只说删台，没说删线）",
      [c["name"] for c in surv3] == ["驻马店综合"] and surv3[0]["streams"] == [P2, B3]
      and st3["dropped_no_source"] == 0 and st3["pruned_lines"] == 0,
      f"台内线路={surv3[0]['streams']}")
surv4, st4 = run_strict([mk("某无源县台", "local", "河南", [B4])])
check("34 六组台整台无实证：频道删除（『无可用源删除频道』那一半）",
      surv4 == [] and st4["dropped_no_source"] == 1,
      f"剩余台数={len(surv4)} 删除={st4['dropped_no_source']}")
surv5, st5 = run_strict([mk("CCTV-11 戏曲", "cctv", "央视", [P2])])
check("35 已实证有画面的线路不删除：只有旧场次实证的必看台线路照样留，只进「本轮没复核到」名单、不判 no_picture",
      surv5[0]["streams"] == [P2] and st5["stale_only"] == ["CCTV-11 戏曲"]
      and not st5["must_no_picture"] and st5["pruned_lines"] == 0,
      f"线路={surv5[0]['streams']} stale_only={st5['stale_only']} no_picture={st5['must_no_picture']}")
bc.FRESH_RES = {canon(P1): FR, canon(P2): FR}
surv6, st6 = run_strict([mk("CCTV-11 戏曲", "cctv", "央视", [P2])])
check("36 负对照：把本轮帧补上同一地址，「本轮没复核到」名单立刻变空（证明 35 咬的是 FRESH_RES 那一格）",
      st6["stale_only"] == [] and st6["must_no_picture"] == [],
      f"stale_only={st6['stale_only']}")
bc.FRESH_RES = {}

bc.DEVICE_PROOF = {canon(B2): "CCTV-6 电影"}
surv7, st7 = run_strict([mk("CCTV-6 电影", "cctv", "央视", [B2])])
check("37 设备实测那一档算必看台的实证：抽帧判「不是节目画面」的线，只要这台电视真播过（截图台账记到 line_ 且仍在这台）就留着、不判 no_picture",
      surv7[0]["streams"] == [B2] and st7["must_no_picture"] == [] and st7["pruned_lines"] == 0,
      f"线路={surv7[0]['streams']} no_picture={st7['must_no_picture']}")
surv8, st8 = run_strict([mk("驻马店综合", "local", "河南", [B2])])
check("38 负对照：设备实测那一档不外溢到六组——同一地址同一线，六组的台照样按「无可用源」删除",
      surv8 == [] and st8["dropped_no_source"] == 1,
      f"剩余台数={len(surv8)} 删除={st8['dropped_no_source']}")
bc.DEVICE_PROOF = {canon(B2): "CCTV-5 体育"}
surv9, st9 = run_strict([mk("CCTV-6 电影", "cctv", "央视", [B2])])
check("39 负对照：设备记录挂在别的台名下时不算这台的实证（证明咬的是「台名逐字相同」那一格）",
      st9["must_no_picture"] == ["CCTV-6 电影"] and st9["pruned_lines"] == 1,
      f"no_picture={st9['must_no_picture']} 删线={st9['pruned_lines']}")
bc.DEVICE_PROOF = {}

bc.IDENTITY["CCTV-4K 超高清|" + canon(U12)] = "台标相符"
bc.FRAME_PLACEHOLDER[canon(U12)] = "占位页：CCTV 4K 超高清 测试卡（色条 +「3840×2160/50/P」）"
pic_card = bc.has_picture("CCTV-4K 超高清", U12)
bc.FRAME_PLACEHOLDER[canon(U12)] = "带二维码的推广页"
pic_ad = bc.has_picture("CCTV-4K 超高清", U12)
bc.FRAME_PLACEHOLDER.pop(canon(U12), None)
bc.IDENTITY.pop("CCTV-4K 超高清|" + canon(U12), None)
check("40 有台标就算真出画面（用户 10-23 02:14）：只认频道自己的测试卡那一类；推广页那类照样否决（与 26 格分界）",
      pic_card is True and pic_ad is False,
      f"测试卡={pic_card} 推广页={pic_ad}")

# ── 41~43 同一条例外要管到「删线」那一层：evidence() 的占位页档（10-23 02:01 CCTV-11 实测）
# 只放宽 has_picture 不够——evidence() 在 strict_filter 之前就把这条线摘掉了，台会整个消失。
U13 = "http://gslb.example.com/live/cctv11hd.m3u8"   # 必看台唯一线路，本轮抽到它自己的色条卡
bc.DROP_DEAD = True
bc.HEALTH = {canon(U13): {"verdict": "可取到分片", "segment_ms": 120}}
bc.FRAME_RES = {canon(U13): [1280, 738]}
bc.FRESH_RES = {canon(U13): [1280, 738]}
bc.IDENTITY = {f"CCTV-11 戏曲|{canon(U13)}": "台标相符"}
bc.FRAME_PLACEHOLDER = {canon(U13): "占位页：CCTV 测试卡（色条卡家族）"}
tier_card = bc.evidence(U13, "CCTV-11 戏曲")
check("41 必看台＋台标相符＋测试卡：只降分不删线（档 1、usable 通过、has_picture 与 has_picture_now 都算这一轮看到了画面）",
      tier_card[0] == 1 and bc.usable("CCTV-11 戏曲", U13)
      and bc.has_picture("CCTV-11 戏曲", U13) and bc.has_picture_now("CCTV-11 戏曲", U13),
      f"档位={tier_card[0]}（BAD_TIER={bc.BAD_TIER}）usable={bc.usable('CCTV-11 戏曲', U13)}")
bc.FRAME_PLACEHOLDER = {canon(U13): "占位页：带二维码的下载 App 推广页"}
tier_ad = bc.evidence(U13, "CCTV-11 戏曲")
check("42 负对照：同一台同一地址，卡换成推广页 ⇒ 回到档 4、被删（例外只吃「测试卡」那一类，没顺手放宽）",
      tier_ad[0] == bc.BAD_TIER and not bc.usable("CCTV-11 戏曲", U13)
      and not bc.has_picture("CCTV-11 戏曲", U13),
      f"档位={tier_ad[0]} usable={bc.usable('CCTV-11 戏曲', U13)}")
bc.FRAME_PLACEHOLDER = {canon(U13): "占位页：CCTV 测试卡（色条卡家族）"}
bc.IDENTITY = {f"湖南卫视|{canon(U13)}": "台标相符"}
tier_other = bc.evidence(U13, "湖南卫视")
surv10, st10 = run_strict([mk("湖南卫视", "satellite", "卫视", [U13])])
check("43 负对照：这条豁免不外溢到六组——非必看台带着台标相符＋测试卡照样档 4、被删（用户那句只点了 4K/8K 那一类画面）",
      tier_other[0] == bc.BAD_TIER and not bc.usable("湖南卫视", U13)
      and st10["dropped_no_source"] == 1,
      f"档位={tier_other[0]} 删除={st10['dropped_no_source']}")

# ── 44~46b 卫视组删线（用户 10-23 17:5x「很多频道有多个不可用线路，已验证过的可用线路保留，不可用线路删除」）
# 只删台内档 3 的线（本机连不通）；档 0/1 留下；设备播过的那条永远不许删；删空了不许把台删没。
V1 = "http://satproven.example.com/a.m3u8"      # 取到过分片＋抽过帧（档 1，有画面实证）
V2 = "http://sattimeout.example.com/b.m3u8"     # 连接超时（档 3）
V3 = "http://satreadtimeout.example.com/c.m3u8" # 响应超时（档 3）
bc.DROP_DEAD = True
bc.HEALTH = {canon(V1): {"verdict": "可取到分片"}, canon(V2): {"verdict": "连接超时"},
             canon(V3): {"verdict": "响应超时"}}
bc.FRAME_RES = {canon(V1): FR}
bc.FRESH_RES = {}
bc.IDENTITY = {}
bc.FRAME_PLACEHOLDER = {}
bc.DEVICE_PROOF = {}
surv44, st44 = run_strict([mk("测试卫视", "cctv", "卫视", [V1, V2, V3])])
check("44 卫视组删线：台内「连接超时／响应超时」线删掉、实证线留下、删掉的线带台名+归一键+对端结论进 sat_pruned 名单",
      [c["name"] for c in surv44] == ["测试卫视"] and surv44[0]["streams"] == [V1]
      and {c for _, c, _ in st44["sat_pruned"]} == {canon(V2), canon(V3)}
      and {v for _, _, v in st44["sat_pruned"]} == {"连接超时", "响应超时"},
      f"剩线={surv44[0]['streams']} 删线名单={st44['sat_pruned']}")
surv44b, st44b = run_strict([mk("测试县台", "local", "河南", [V1, V2])])
check("44b 负对照：同形状非卫视组，台内线路一条不动（这台只删卫视组的线，不外溢到六组）",
      surv44b[0]["streams"] == [V1, V2] and st44b["sat_pruned"] == []
      and st44b["dropped_no_source"] == 0,
      f"剩线={surv44b[0]['streams']} 删线名单={st44b['sat_pruned']}")
bc.DEVICE_PROOF = {canon(V2): "测试卫视"}
surv45, st45 = run_strict([mk("测试卫视", "cctv", "卫视", [V1, V2])])
check("45 负对照：设备实测播过的那条「连接超时」线不许删（『已播过的线路绝不自动换线』盖过删线指令）",
      surv45[0]["streams"] == [V1, V2] and st45["sat_pruned"] == [],
      f"剩线={surv45[0]['streams']} 删线名单={st45['sat_pruned']}")
bc.DEVICE_PROOF = {}
bc.FRAME_RES = {canon(V1): FR, canon(V2): FR}
surv46, st46 = run_strict([mk("测试卫视", "cctv", "卫视", [V2])])
check("46 卫视台只剩「连接超时」但抽过帧的线：删无可删时一条不动（不许把有画面实证的台删空）",
      [c["name"] for c in surv46] == ["测试卫视"] and surv46[0]["streams"] == [V2]
      and st46["sat_pruned"] == [] and st46["dropped_no_source"] == 0,
      f"剩线={surv46[0]['streams']} 删线名单={st46['sat_pruned']}")
bc.IDENTITY = {f"测试卫视|{canon(V2)}": "台标相符"}
surv47, st47 = run_strict([mk("测试卫视", "cctv", "卫视", [V1, V2])])
check("46b 负对照：同一形状把身份换成台标相符 ⇒ 档 0、留下（证明咬的是证据档位那一格，不是台名）",
      surv47[0]["streams"] == [V1, V2] and st47["sat_pruned"] == [],
      f"剩线={surv47[0]['streams']} 删线名单={st47['sat_pruned']}")
bc.IDENTITY = {}
surv48, st48 = run_strict([mk("测试卫视", "cctv", "卫视", [V2, V3])])
check("47 台内同为档 3：抽过帧的那条留下、没实证的那条删掉（『曾经出过真实画面的线就留』按条生效）",
      surv48[0]["streams"] == [V2]
      and st48["sat_pruned"] == [("测试卫视", canon(V3), "响应超时")],
      f"剩线={surv48[0]['streams']} 删线名单={st48['sat_pruned']}")
surv49, st49 = run_strict([mk("七龙珠", "featured", "影视轮播", [V2]),
                            mk("咪咕直播 1", "sports", "咪咕直播", [V2])])
check("48 特色频道整组删除（用户 10-23 20:0x「删除特色频道」）：有画面实证的台也删、计数单独一格、别的组不动",
      [c["name"] for c in surv49] == ["咪咕直播 1"]
      and st49["dropped_featured"] == 1 and st49["dropped_no_source"] == 0
      and st49["sat_pruned"] == [],
      f"剩台={[c['name'] for c in surv49]} featured删={st49['dropped_featured']}")

# ── 49 甲：慢线挪到台内最后（用户 10-23 20:5x 点档「甲」）
UA = "http://fast.example.com/a.m3u8"
UB = "http://slow.example.com/b.m3u8"
UC = "http://fast.example.com/c.m3u8"
UD = "http://slow2.example.com/d.m3u8"
UE = "http://slow3.example.com/e.m3u8"
health49 = {bc.stream_rules.canon(UB): {"verdict": "可取到分片", "elapsed_ms": 15000},
            bc.stream_rules.canon(UD): {"verdict": "可取到分片", "elapsed_ms": 15000},
            bc.stream_rules.canon(UE): {"verdict": "可取到分片", "elapsed_ms": 15000}}
ch49 = row("甲测试台", [UA, UB, UC])
chans49, moved49 = bc.move_slow_lines_back([ch49], health49, lambda n, u: False)
check("49 甲：首分片 >10 秒的慢线挪到台内最后、其余保序",
      chans49[0]["streams"] == [UA, UC, UB] and moved49 == [("甲测试台", bc.stream_rules.canon(UB))],
      f"现序={chans49[0]['streams']} 挪={moved49}")
ch49b = row("甲测试台2", [UA, UD])
chans49b, moved49b = bc.move_slow_lines_back([ch49b], health49, lambda n, u: u == UD)
check("49b 负对照：设备播过的慢线原地不动（「已播过不挪位」盖过甲）",
      chans49b[0]["streams"] == [UA, UD] and moved49b == [],
      f"现序={chans49b[0]['streams']} 挪={moved49b}")
ch49c = row("甲测试台3", [UA, UE])
chans49c, moved49c = bc.move_slow_lines_back([ch49c], health49, lambda n, u: False)
check("49c 负对照：已排在末位的慢线不记进挪线名单（名单只记真要动的那批）",
      chans49c[0]["streams"] == [UA, UE] and moved49c == [],
      f"现序={chans49c[0]['streams']} 挪={moved49c}")
bc.DROP_DEAD = False
bc.HEALTH, bc.IDENTITY, bc.FRAME_PLACEHOLDER, bc.FRAME_RES, bc.FRESH_RES, bc.DEVICE_PROOF = {}, {}, {}, {}, {}, {}

print()
if failures:
    print(f"[verdict] {len(failures)}/{CELLS[0]} 格判红：{'、'.join(failures)}")
    sys.exit(1)
print(f"[verdict] {CELLS[0]}/{CELLS[0]} 格通过")
sys.exit(0)
