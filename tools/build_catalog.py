#!/usr/bin/env python3
"""Build the bundled Android TV channel catalog from public IPTV playlists.

The output is app/src/main/assets/channels.json. Stream URLs are third-party
and can expire; every channel carries several fallback URLs when available.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stream_rules  # noqa: E402  (与 App 端 StreamUrl.kt 同一把尺)

BASE = Path(__file__).resolve().parents[1]
CACHE = BASE / "tools" / ".cache"
OUT = BASE / "app" / "src" / "main" / "assets" / "channels.json"
DEFAULT_HEALTH = CACHE / "health" / "line_health.jsonl"
DEFAULT_FRAMES = CACHE / "prepack" / "line_frames.jsonl"

# 每频道线路数上限：0 表示不限（实际可用线路有多少就留多少）。
MAX_STREAMS = 0

# 是否删掉证据档位到 BAD_TIER 及以上的线路（对端自己答复说没有／画面是别的台）。
# 删完一条线路都不剩的频道，整个从目录里消失。
DROP_DEAD = True
BAD_TIER = 4

# tools/health_probe.py 的分片级实测结果（canonical url -> verdict）。
# 空表示没有健康数据可依据，此时线路顺序退化成「只按来源优先级」。
HEALTH = {}

# 抽帧看过画面的线路（"频道名|canonical url" -> 结论）。分片能拉到不等于画面就是这台，
# 所以身份结论优先于分片结论：台标相符才许排第一，画面是别的台/占位页的排到最后。
IDENTITY = {}
IDENTITY_GOOD = {"台标相符"}

# prepack 抽帧拿到的原生画面尺寸（canonical url -> [宽, 高]）。
# 只用来在同一证据档里比高下，缺读数不等于线路坏（读 0 当未知）。
FRAME_RES = {}
# 抽帧画面命中「已知占位页/推广页」指纹的线路：canonical url -> 那一页是什么。
FRAME_PLACEHOLDER = {}
# 门禁那 7 秒抽帧窗口里几帧字节完全相同（时刻性卡流）：canonical url -> 「卡流：N 帧…」。
# 只降分不删线（10-21 口径「稳定坏才删线，时刻性坏只降分」）：两个演员站定的镜头连 7 秒
# 不动也会得到同样的读数（实测 CCTV-8《枫桥警事》就是这么被读成"卡流"的），拿它删线会误伤。
FRAME_STALLED = {}
# freeze_scan.py 用长窗（默认 150 秒）确认「源没在给新画面」的线路：canonical url -> 结论。
# 分钟级不动排除了固定镜头这一解释，才算稳定坏 ⇒ 删线＋拉黑。
# 但「稳定坏」要先是「这条线坏」：同一条台的所有线在同一场扫描里一起冻住，那是台/上游
# 此刻的状态（县台半夜自动播出卡住），删掉这条线既修不好它、还可能把整台删没。
# 所以只有「同台北面至少有一条线在同一场里正常推进」时，这一条才算线路专属故障。
# 闸门在 load_freeze_all + gate_frozen_by_sibling 那一处，三个消费点（evidence/usable/blocked_lines）
# 都只读这一本 FROZEN——判定层与删除层分开改过一次就出过事故（10-23 02:16 CCTV-11 整台消失）。
FROZEN = {}
# 被上面那道闸挡下来的冻帧：canonical url -> 台名。只记账、不删线，报告里要如实念出来。
FROZEN_NO_SIBLING = {}
# 纯音频族：地址里就写着 /audio/，health 那把尺报「可取到分片」，但 VLC 抽 40 秒 0 帧＝没有视频轨，
# 电视上永远不会出画面。设备端「立即更新线路」会从公开列表把这些地址再捞一次，
# 只在本机不收是挡不住的，必须按前缀进黑名单（10-24 现读：这一族台账里 20 条，
# 只有抽过帧的 4 条因为「解不出画面」进了黑名单，另外 16 条是敞口的）。
AUDIO_ONLY_PREFIXES = ("https://piccpndali.v.myalicdn.com/audio/",
                       "http://piccpndali.v.myalicdn.com/audio/")

# 对端根本没能答复的失败：只说明「本机网络到不了」，不说明线路本身坏了。
# 10-22 用户指令「连接失败的线路，除超时线路保留外，其余都删除」⇒ 这一族里只留「超时」两种
#（连接超时 / 响应超时：慢与到不了分不开，换个网络可能通）。
# 「连不上」（拒绝、DNS 解不出、主机不可达）从这一族挪出去，落到可删那一档：
# 那是对端/网络层主动回了「这个连接不认」，不像超时那样还可能只是慢。
NO_REPLY = {"连接超时", "响应超时"}

# 播放器解不了的容器（health_probe 按字节头嗅出来的，与 StreamRules.unsupportedContainer 同一件事
# 的两种来源：一种看扩展名，一种看真身）。这类线在电视上一定是黑屏。
PLAYER_UNDEMUXABLE = {"容器不支持"}

# 目录里删掉的线，设备端自动更新会一遍遍从公开源捞回来（10-22 模拟器现读：一次开机后台更新
# 就把 files/channels_live.json 写成比内置目录多 561 条线，其中 151 条本机已实测判坏）。
# 所以黑名单要和线路一起打进 APK，让设备端合并时按同一份证据挡回去。
# 「本机连不通」不进黑名单：10-21 选项A——那只说明本机到不了，家里网络可能通，设备上要继续试。
GOOD_VERDICT = "可取到分片"
# 台标是别的台：同一条地址挂在 A 台名下是错的、挂在 B 台名下是对的（四川卫视那种），
# 按地址拉黑会把真身那一档一起摘掉，所以它归 RelabeledStreams 的按名规则管，不进地址黑名单。
NAME_RELATIVE = {"画面是别的台"}


def card_counts_as_picture(name, url, card=""):
    """「抽到占位页」这一格要不要让位给台标（用户 10-23 02:14「CCTV-4K、CCTV 8K 有台标就算真出画面」）。

    三层都从这一个函数过（画面判定 has_picture / 本轮闸门 has_picture_now / 档位 evidence），
    免得同一条例外在两处口径不一。作用域写死在 CARD_IS_PICTURE_CHANNELS：必看 20 台＋CCTV-8K。
    只放行**测试卡**：那是这台自己端出来的色条卡（凌晨没有在播节目而已），画面左上角的台标就是它本人。
    推广页／购物广告／带二维码的下载页不进这个门——它们与「台标相符」本来互斥，
    用户 10-22 那句「占位页赢」对那一类照旧有效（夹具 26 格盯着）。
    """
    if "测试卡" not in (card or ""):
        return False
    if name not in CARD_IS_PICTURE_CHANNELS:
        return False
    return IDENTITY.get(f"{name}|{stream_rules.canon(url)}") in IDENTITY_GOOD


def evidence(url, name=""):
    """线路的证据档位，小的排前面。

    0 抽帧看过、台标与频道名相符；1 拉到过视频分片但画面身份没验；2 没测过；
    3 本机连不通（换张网可能通）；4 对端明确答复了坏，或画面是别的台/占位页。
    「没测过」排在「连不通」前面：家里网络与本机不同，未验证的线路仍有机会，
    而对端已经回了 404/403/网页的线路在什么地方都不会通。
    """
    key = f"{name}|{stream_rules.canon(url)}"
    canon = stream_rules.canon(url)
    row = HEALTH.get(canon)
    if row and row.get("verdict") in PLAYER_UNDEMUXABLE:
        # 容器判定走在身份判定前面：「抽帧认过台标」那一档是**用 VLC 抽的帧**认的，VLC 能解 FLV，
        # ExoPlayer 没有对应的解复用器。13 条 huya 电影轮播线就是这么带着档 0 一直排在第 1 位的。
        return (4, 0)
    if FROZEN.get(canon):
        # 长窗（分钟级）确认「源没在给新画面」：这是稳定坏，与占位页同级 ⇒ 删线那一侧。
        return (4, 0)
    verdict = IDENTITY.get(key)
    if FRAME_PLACEHOLDER.get(canon):
        # 抽到帧 ≠ 抽到节目：这一条给的是测试卡/带二维码的推广页，和「对端回了 404」同级。
        # 这一格走在身份判定前面（用户 10-22 指令「占位页赢」）：同一地址既被复核过台标、
        # 又被抽到占位页时，肉眼看到的这一帧更硬——按名字留下的那 32 枚是另一回事（usable() 管）。
        # 一处例外（用户 10-22 23:5x「已实证有画面的线路不删除」＋10-23 02:14「有台标就算真出画面」）：
        # CARD_IS_PICTURE_CHANNELS 那几台如果这一帧是**它自己的测试卡**，只降分不删线——10-23 02:01
        # CCTV-11 那条 GSLB 端出 1080/50I 色条卡（卡左上角写着「CCTV 11 戏曲」），18 分钟前它刚抽到
        # 正常画面、模拟器也真播过；那是"此刻没节目"的时刻性状态，不是稳定坏（10-21 口径）。
        # 只放宽 has_picture 不够：这一档在 strict_filter 之前就把线摘了，台会整个消失。
        # 推广页那一类不进这个门，别组也不进（夹具 41/42/43 格各盯一半）。
        if card_counts_as_picture(name, url, FRAME_PLACEHOLDER[canon]):
            return (1, 0)
        return (4, 0)
    if verdict:
        if verdict not in IDENTITY_GOOD:
            return (4, 0)
        # 时刻性卡流（门禁那 7 秒窗口几帧字节相同）不删线，但也不许它占着档 0 排第一：
        # 压到档 1，让同档里没被读过卡流的线先上（10-21 口径「时刻性坏只降分」）。
        return (1, 0) if FRAME_STALLED.get(canon) else (0, 0)
    if row is None:
        return 2, 0
    verdict = row.get("verdict")
    if verdict == GOOD_VERDICT:
        return 1, row.get("segment_ms") or 0
    if verdict in NO_REPLY:
        return 3, 0
    return 4, 0


def usable(name, url):
    """这条线路还配留在目录里吗。判据只有这一处，排序和删除都从这里过。"""
    return (not DROP_DEAD) or evidence(url, name)[0] < BAD_TIER


FRESH_RES = {}   # 「本轮」那一份帧文件抽到过画面的地址（归一键）。必看台的闸门只认它，旧帧只证明曾经能播。
DEVICE_PROOF = {}  # 设备（模拟器）实测记到 line_<id> 的那条地址：归一键 -> 台名。必看台那一侧算实证。


def has_picture_now(name, url):
    """这一轮有没有真看到这条线的正常画面。

    与 has_picture() 的差别只有一处：帧那一格只看最新那一份帧文件（FRESH_RES），不看累计。
    必看台（央视 1-17＋5+＋4K＋辽宁体育，共 20 台）用这把尺——旧帧会让"其实现在播不出来"的台假绿过去。
    占位页那一格的例外与 has_picture() 同源（同一个 card_counts_as_picture）：本轮抽到的是
    这台自己的测试卡、台标复核过相符 ⇒ 算这一轮看到了画面（用户 10-23 02:14）。
    """
    canon = stream_rules.canon(url)
    row = HEALTH.get(canon)
    if row and row.get("verdict") in PLAYER_UNDEMUXABLE:
        return False
    if FROZEN.get(canon):
        # 长窗确认源在喂旧画面：这一轮看到的"画面"是同一种画面重复，不算看到了节目。
        return False
    card = FRAME_PLACEHOLDER.get(canon) or ""
    if card and not card_counts_as_picture(name, url, card):
        return False
    return bool(FRESH_RES.get(canon))


def has_picture(name, url):
    """这条线有没有「正常节目画面」的证据，口径与打包前报告那三把尺一致：

    抽到过帧（原生尺寸在案）且不是占位页，且容器是 ExoPlayer 解得开的那种；
    或者抽帧复核过台标相符（那一档本身就是看着帧认出来的）。
    「取到过分片」不算——拉到字节 ≠ 电视上有画面。
    """
    canon = stream_rules.canon(url)
    row = HEALTH.get(canon)
    if row and row.get("verdict") in PLAYER_UNDEMUXABLE:
        return False
    if FROZEN.get(canon):
        # 长窗冻帧：抽到过帧不等于抽到节目，同一种画面重复分钟级也是"帧"。
        return False
    # 用户 10-23 02:14 的口径：**「CCTV-4K、CCTV 8K 有台标就算真出画面，算正常频道线路」**
    # ⇒ 台标复核过「相符」的线走在这把尺最前面，即使那一帧是频道自己的测试卡/色条卡。
    # 凌晨的 CCTV-4K／CCTV-8K／CCTV-11 端的就是这种卡（画面左上角写着「CCTV 4K 超高清」「CCTV 11 戏曲」），
    # 按旧口径它会被 FRAME_PLACEHOLDER 一票否决，等于把一条真源判成不能用。
    card = FRAME_PLACEHOLDER.get(canon) or ""
    verdict = IDENTITY.get(f"{name}|{canon}")
    if verdict and verdict in IDENTITY_GOOD:
        # 这一半只覆盖**频道自己的测试卡**：画面是这台端出来的、此刻没节目而已。
        # 别的占位页（二维码推广页／满屏商品广告）不是这台的内容，"台标相符"和它本来就互斥，
        # 照旧一票否决——夹具第 26 格盯的就是这一半，不许顺手放宽成"有身份结论就放行"。
        if not card or card_counts_as_picture(name, url, card):
            return True
    if card:
        return False
    return bool(FRAME_RES.get(canon))


def blocked_lines(keep=frozenset()):
    """按**地址**判坏的线路（canonical url -> 为什么别再试），随目录一起打进 APK。

    来源与 usable() 同一批读数，只收「换个名字也还是这条坏地址」的那几类：
    对端答复过的坏（清单404/拒绝访问/返回网页/证书错误…）、播放器解不开的容器、
    抽帧看到的占位页/无信号/解不出。NO_REPLY 那两类（超时）不在其中——那是本机网络到不了，
    不是线路坏（10-21 选项A）。画面是别的台 属于「名字与地址的配对」，也不进这一份。
    [keep] 是目录自己按名字救回来的那些地址（usable() 按「名字+地址」判，身份档能把它捞回来），
    它们从黑名单里摘出去：留下和拉黑不能同时成立。返回（黑名单, 被豁免的地址）。
    """
    out = {}
    if not DROP_DEAD:
        # --keep-dead 那一次目录本身就留着坏线，再让设备端挡回去是自相矛盾，这两处得同一个开关。
        return out, []
    for key, row in HEALTH.items():
        verdict = row.get("verdict")
        if not verdict or verdict == GOOD_VERDICT or verdict in NO_REPLY:
            continue
        out[key] = verdict
    for key, label in FRAME_PLACEHOLDER.items():
        if label:
            out[key] = "占位页"
    for key, verdict in FROZEN.items():
        if verdict:
            # 长窗冻帧要跟着进黑名单：设备端「立即更新线路」会从公开列表把这条地址再捞一次，
            # 只在目录里摘掉它挡不住回来（与占位页同一套处置）。
            out[key] = "长窗冻帧"
    for key in HEALTH:
        # 按前缀收，不按抽过帧的那几条收：这一族有没有视频轨与台名无关，
        # 公开列表明天还会把它们以别的名字再送一遍。
        if key.startswith(AUDIO_ONLY_PREFIXES):
            out[key] = "纯音频无视频轨"
    for key, verdict in IDENTITY.items():
        if not verdict or verdict in IDENTITY_GOOD or verdict in NAME_RELATIVE:
            continue
        _, _, url = key.partition("|")
        if url:
            out.setdefault(url, verdict)
    exempted = [k for k in out if k in keep]
    # 键排好序再落盘：名单的顺序本来取决于「这次传了哪几份文件、什么顺序」，
    # 同一套证据换个写法就该得到同一份字节（否则每次重建都有一屏无关的 diff，
    # 也没法用「字节相同」证明这份资产真能复现）。
    return {k: out[k] for k in sorted(out) if k not in keep}, exempted


def resolution_rank(url):
    """这条线自报/实测到的画面尺寸，用来把高分辨率的线排前面。

    两个来源，都拿到才算数，不从 URL 里的 1080/hd/蓝光 这类字样猜——实测那些字样既出现在
    主机名也出现在节目号里（`live.hd.hdbs.cn` 就是域名带 hd），拿来排序会把标错名的坏线顶到前面。
      1) prepack 抽到的原生尺寸（真画面，最可信）；
      2) 清单自报的 `#EXT-X-STREAM-INF:…RESOLUTION=宽x高`（health_probe 记的）。
    都没有就返回 0（当作未知），让证据档和 IPv6 决定次序。
    """
    key = stream_rules.canon(url)
    size = FRAME_RES.get(key) or (HEALTH.get(key) or {}).get("resolution")
    if not size:
        return 0
    try:
        return int(size[0]) * int(size[1])
    except (TypeError, ValueError, IndexError):
        return 0


SOURCES = {
    "fanmingming": "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/index.m3u",
    "vbsky": "https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u",
    "zbefine": "https://raw.githubusercontent.com/zbefine/iptv/main/iptv.m3u",
    "bigbig": "https://raw.githubusercontent.com/BigBigGrandG/IPTV-URL/release/Gather.m3u",
    "yang": "https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u",
    "epg_hk": "https://epg.pw/test_channels_hong_kong.m3u",
    "epg_tw": "https://epg.pw/test_channels_taiwan.m3u",
    "epg_mo": "https://epg.pw/test_channels_macau.m3u",
    "aptv": "https://raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u",
    "iptv_org": "https://iptv-org.github.io/iptv/index.m3u",
    "pca": "https://raw.githubusercontent.com/modood/Administrative-divisions-of-China/master/dist/pca-code.json",
}

# Lower value = stream is tried earlier.
SOURCE_PRIORITY = {
    "manual_4k": -1,
    "fanmingming": 0,
    "iptv_org": 1,
    "zbefine": 2,
    "bigbig": 3,
    "vbsky": 4,
    "epg": 5,
    "yang": 6,
    "aptv": 7,
    # 人工补进已有频道的候选线路：排在最后，不挤掉公开源里已经能播的第一条。
    "manual_extra": 80,
    # 人工新建的频道（公开列表里根本没有这个名字），线路只有这一条，排在自补线路之前。
    "manual_sports": 79,
}

PROVINCE_ORDER = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
]
PROVINCE_GROUPS = set(PROVINCE_ORDER)

CATEGORY_ORDER = ["cctv", "local", "hktw", "featured", "sports", "international"]
CATEGORY_NAMES = {
    "cctv": "央视·卫视",
    "local": "地方频道",
    "hktw": "港澳台",
    "featured": "特色频道",
    "sports": "体育频道",
    "international": "国际频道",
    # 「无画面单独分组」（用户 2026-10-22 指令）：目录里一条正常画面证据都没有的台收在这里，
    # subgroup 保持原样（省份/组名不丢），所以播放器里仍显示「待验证 · 河南」这种。
    "pending": "待验证",
}
PENDING_CAT = "pending"
# 「央视 1-17 加 4K 超高清、辽宁体育频道画面必须能正常播放」（用户 2026-10-22 23:4x 新判据，原话「此为最新判据」）。
# 用户 2026-10-23 10:2x 追加一句「CCTV-5+为央视台属于G2」⇒ 名单从 19 台变 20 台，加进 CCTV-5+ 体育赛事。
# 这一份名单同时管三处：①不许删空（缺线时构建判红，逼人去公开源补）；②这些台里「没有正常画面证据」的线路一律删；
# ③构建结束 [check] 报 missing / no_picture，任一非空 EXIT=1。
# 以前那份「点名必留」名单（咪咕体育、明珠台、NewTV/CHC、各 4K…）按同一句新判据作废——
# 那些组属于「无可用源删除频道」的范围（卫视/地方/港澳台/特色/体育/国际）。
MUST_PLAY_CHANNELS = [
    "CCTV-1 综合", "CCTV-2 财经", "CCTV-3 综艺", "CCTV-4 中文国际", "CCTV-5 体育",
    "CCTV-6 电影", "CCTV-7 国防军事", "CCTV-8 电视剧", "CCTV-9 纪录", "CCTV-10 科教",
    "CCTV-11 戏曲", "CCTV-12 社会与法", "CCTV-13 新闻", "CCTV-14 少儿", "CCTV-15 音乐",
    "CCTV-16 奥林匹克", "CCTV-17 农业农村", "CCTV-5+ 体育赛事", "CCTV-4K 超高清", "辽宁体育",
]
REQUIRED_CHANNELS = MUST_PLAY_CHANNELS
# 「测试卡算真出画面」这条例外只管这几台：20 台必看台（含 CCTV-4K 超高清）＋ CCTV-8K 超高清
# （用户 10-23 02:14 点名的就是 4K／8K 那两台；8K 不在必看名单，那句指令没把它加进去，
# 所以它享受同一条画面口径、但不享受"不许删空"那道闸）。
CARD_IS_PICTURE_CHANNELS = set(MUST_PLAY_CHANNELS) | {"CCTV-8K 超高清"}
SUBGROUP_ORDER = {
    "cctv": ["央视", "卫视", "4K 超高清"],
    "local": PROVINCE_ORDER + ["其他"],
    "hktw": ["香港", "台湾", "澳门", "海外华人"],
    "featured": ["影视轮播", "NewTV", "CHC", "少儿"],
    "sports": ["国内体育", "咪咕直播", "港台体育"],
    "international": ["新闻", "纪实", "综合"],
}

# 这几组的 order 是频道号（CCTV-1…、卫视、体育的点名次序），长辈靠它找台，
# 所以不参与「可用线路优先」的重排；其余各组按可用性提序。
# 用户 2026-10-22 13:03 原话：「央视/卫视不要按可用性重排」——这一条是他点的档，不是我推的。
# 同一份豁免顺带盖住了 cctv 大类下的「4K 超高清」和整个 sports 组（他没提这两处）：
# 体育组里「辽宁体育」是必须能看的那一档，被一次本机抽帧失败顶到组尾不是他想要的结果。
# 要按字面只豁免央视/卫视，把下面这个集合改成按 (category, subgroup) 对。
CONVENTION_ORDER = {"cctv", "sports"}

CCTV_NAMES = {
    "1": "CCTV-1 综合",
    "2": "CCTV-2 财经",
    "3": "CCTV-3 综艺",
    "4": "CCTV-4 中文国际",
    "5": "CCTV-5 体育",
    "6": "CCTV-6 电影",
    "7": "CCTV-7 国防军事",
    "8": "CCTV-8 电视剧",
    "9": "CCTV-9 纪录",
    "10": "CCTV-10 科教",
    "11": "CCTV-11 戏曲",
    "12": "CCTV-12 社会与法",
    "13": "CCTV-13 新闻",
    "14": "CCTV-14 少儿",
    "15": "CCTV-15 音乐",
    "16": "CCTV-16 奥林匹克",
    "17": "CCTV-17 农业农村",
    # 「5+」排在 17 之后，不是排在 5 之后：用户 10-23 要的是数字键 1-17 给 CCTV-1…17、
    # 18 给 CCTV-5+，而遥控器那串数字念的就是目录里的次序（app/src/main/java/com/parentstv/live/data/ChannelNumbers.kt）。
    # 两边各排各的会出现「屏幕上第 6 个台、台号却是 18」，所以目录侧同批跟一条规则。
    "5+": "CCTV-5+ 体育赛事",
    "4K": "CCTV-4K 超高清",
}
CCTV_ORDER = {v: i for i, v in enumerate(CCTV_NAMES.values())}
CCTV_ORDER.update({
    "CGTN": 90, "CGTN 纪录": 91, "CGTN 法语": 92,
    "CGTN 西语": 93, "CGTN 阿语": 94, "CGTN 俄语": 95,
})

SAT_ORDER = [
    "北京卫视", "东方卫视", "天津卫视", "重庆卫视", "河北卫视", "山西卫视",
    "内蒙古卫视", "辽宁卫视", "吉林卫视", "黑龙江卫视", "江苏卫视",
    "浙江卫视", "安徽卫视", "东南卫视", "江西卫视", "山东卫视",
    "山东教育卫视", "河南卫视", "湖北卫视", "湖南卫视", "广东卫视",
    "深圳卫视", "广西卫视", "海南卫视", "三沙卫视",
    # 用户 2026-10-23 17:2x 点档「2 删」：南方卫视／康巴卫视／农林卫视 三个名字从卫视名单里删掉
    # （公开列表里 0 候选，不再当缺失项追踪）。
    "四川卫视", "贵州卫视", "云南卫视", "西藏卫视",
    "陕西卫视", "甘肃卫视", "青海卫视", "安多卫视",
    # 用户同批点档「1 删」：兵团卫视也从名单里删掉（ifengli 那条在这台电视上两次都没起画面，
    # 按用户拍板整台删除；它的三条线在下面 dead_stream_prefixes 里按台名摘掉）。
    "宁夏卫视", "新疆卫视", "延边卫视",
]

TR2CN = str.maketrans({
    "鳳": "凤", "視": "视", "東": "东", "綜": "综", "藝": "艺", "財": "财",
    "經": "经", "體": "体", "聞": "闻", "電": "电", "劇": "剧", "灣": "湾",
    "歡": "欢", "樂": "乐", "華": "华", "衛": "卫", "龍": "龙", "爾": "尔",
    "愛": "爱", "達": "达", "網": "网", "亞": "亚", "歐": "欧", "陸": "陆",
    "廣": "广", "蘇": "苏", "遼": "辽", "雲": "云", "貴": "贵", "陝": "陕",
    "寧": "宁", "瓊": "琼", "閩": "闽", "贛": "赣", "魯": "鲁", "豫": "豫",
    "粵": "粤", "趙": "赵", "錢": "钱", "孫": "孙", "劉": "刘", "張": "张",
    "榮": "荣", "訊": "讯", "線": "线", "頻": "频", "道": "道", "數": "数",
    "據": "据", "齊": "齐", "內": "内", "麥": "麦", "點": "点", "鏡": "镜",
    "頭": "头", "環": "环", "球": "球", "紀": "纪", "錄": "录",
    "專": "专", "業": "业", "師": "师", "優": "优", "選": "选", "節": "节",
    "場": "场", "開": "开", "關": "关", "將": "将", "軍": "军", "動": "动",
    "畫": "画", "兒": "儿", "童": "童", "國": "国", "際": "际", "會": "会",
    "員": "员", "歸": "归", "傳": "传", "統": "统", "歷": "历", "史": "史",
    "門": "门", "運": "运", "車": "车", "馬": "马", "鳥": "鸟", "魚": "鱼",
    "說": "说", "語": "语", "讓": "让", "時": "时", "間": "间", "陳": "陈",
    "黃": "黄", "楊": "杨", "吳": "吴", "葉": "叶", "澤": "泽", "與": "与",
})


def clean_name(raw: str) -> str:
    n = unicodedata.normalize("NFKC", str(raw or ""))
    n = re.sub(r"\[[^\]]*(?:geo|24/7|BD|HD|SD|VGA|UHD|4K)[^\]]*\]", "", n, flags=re.I)
    n = re.sub(r"\((?:1080|720|576|540|480|2160|1080i)[pi]?\)", "", n, flags=re.I)
    n = re.sub(r"「[^」]*」", "", n)
    n = re.sub(r"\s*\[[A-Za-z0-9 ]+\]\s*", " ", n)
    n = re.sub(r"\s+(?:HD|SD|UHD)$", "", n, flags=re.I)
    n = re.sub(r"(?:高清|超清|标清)$", "", n)
    n = re.sub(r"\s+", " ", n).strip(" -|·")
    return n.strip()


def norm_key(raw: str) -> str:
    n = clean_name(raw).translate(TR2CN).lower()
    n = re.sub(r"[\s\-_·•]+", "", n)
    return n


def match_key(raw: str) -> str:
    n = clean_name(raw).translate(TR2CN).lower()
    n = re.sub(r"\b(hd|sd|uhd|fhd)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def is_ipv6(url: str) -> bool:
    m = re.match(r"https?://\[", url)
    return bool(m)


def is_geo(raw: str) -> bool:
    return "geo" in raw.lower()


def parse_m3u(path: Path, source: str):
    entries = []
    cur = None
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if line.startswith("#EXTINF"):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            name = line.rsplit(",", 1)[-1].strip()
            cur = {
                "name": name,
                "raw": line,
                "group": attrs.get("group-title", ""),
                "logo": attrs.get("tvg-logo", ""),
                "source": source,
                "url": None,
            }
        elif line.startswith("#EXTGRP:") and cur:
            cur["group"] = line.split(":", 1)[1].strip()
        elif line and not line.startswith("#") and cur:
            if line.startswith(("http://", "https://")) or line.startswith("rtmp://"):
                cur["url"] = line
                entries.append(cur)
                cur = None
    return entries


def strip_admin(name: str) -> str:
    for suffix in (
        "特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区", "自治区",
        "自治州", "自治县", "地区", "林区", "新区", "省", "市", "区", "县", "盟", "旗",
    ):
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            return name[: -len(suffix)]
    return name


class ProvinceIndex:
    def __init__(self, pca_path: Path):
        data = json.loads(pca_path.read_text(encoding="utf-8"))
        candidates = defaultdict(set)
        for prov in data:
            pname = strip_admin(prov["name"])
            candidates[prov["name"]].add(pname)
            candidates[pname].add(pname)
            for city in prov.get("children", []):
                if city["name"] in ("市辖区", "省直辖县级行政区划", "自治区直辖县级行政区划"):
                    continue
                cname = strip_admin(city["name"])
                candidates[city["name"]].add(pname)
                candidates[cname].add(pname)
                for area in city.get("children", []):
                    aname = strip_admin(area["name"])
                    candidates[area["name"]].add(pname)
                    candidates[aname].add(pname)
        self.mapping = {k: next(iter(v)) for k, v in candidates.items() if len(v) == 1}
        self.manual = {
            "南国都市": "海南", "中国蓝新闻": "浙江", "中国交通": "海南", "三沙": "海南",
            "康巴": "四川", "安多": "青海", "农林": "陕西", "兵团": "新疆",
            "延边": "吉林", "深圳": "广东", "厦门": "福建", "青岛": "山东",
            "大连": "辽宁", "宁波": "浙江", "苏州": "江苏", "无锡": "江苏",
            "温州": "浙江", "佛山": "广东", "东莞": "广东", "珠海": "广东",
            "泉州": "福建", "洛阳": "河南", "唐山": "河北", "保定": "河北",
        }

    def detect(self, name: str):
        n = clean_name(name)
        for p in PROVINCE_ORDER:
            if n.startswith(p):
                return p
        for length in range(min(len(n), 8), 1, -1):
            prefix = n[:length]
            if prefix in self.mapping:
                return self.mapping[prefix]
            if prefix in self.manual:
                return self.manual[prefix]
        return None


HK_CHANNELS = [
    ("翡翠台", ["翡翠台", "翡翠", "jade", "tvb翡翠", "tvb翡翠台"]),
    ("明珠台", ["明珠台", "明珠", "pearl"]),
    ("TVB J2", ["j2", "tvbj2"]),
    ("TVB Plus", ["tvbplus"]),
    ("无线新闻", ["无线新闻", "tvb新闻"]),
    ("无线财经体育资讯台", ["无线财经体育资讯台", "无线财经"]),
    ("凤凰中文", ["凤凰中文", "凤凰卫视中文台", "凤凰卫视", "凤凰中文台",
                  "phoenixchinesechannel", "phoenixchinese"]),
    ("凤凰资讯", ["凤凰资讯", "凤凰资讯台", "phoenixinfonewschannel", "phoenixinfonews"]),
    ("凤凰香港", ["凤凰香港", "phoenixhongkong"]),
    ("HOY TV", ["hoytv"]),
    ("HOY资讯台", ["hoy资讯台", "hoyinfotainment"]),
    ("ViuTV", ["viutv"]),
    ("ViuTV6", ["viutv6"]),
    ("香港国际财经", ["香港国际财经", "hkinternationalbusinesschannel"]),
    ("RTHK 31", ["rthk31", "rhk31", "港台电视31"]),
    ("RTHK 32", ["rthk32", "rhk32", "港台电视32"]),
    ("星河台", ["星河台", "tvb星河"]),
    ("美亚电影", ["美亚电影", "meiahmoviechannel"]),
]

TW_CHANNELS = [
    ("TVBS", ["tvbs", "tvbs综合"]),
    ("TVBS新闻", ["tvbs新闻", "tvbsnews"]),
    ("TVBS欢乐台", ["tvbs欢乐台", "tvbs欢乐"]),
    ("TVBS亚洲", ["tvbs亚洲", "tvbs-asia", "tvbsasia"]),
    ("中天新闻", ["中天新闻", "ctinews"]),
    ("中天综合", ["中天综合", "ctivariety"]),
    ("中天娱乐", ["中天娱乐", "ctientertainment"]),
    ("东森新闻", ["东森新闻", "ebcnews"]),
    ("东森财经新闻", ["东森财经新闻", "ebcfinancialnews"]),
    ("东森综合", ["东森综合"]),
    ("东森电影", ["东森电影"]),
    ("东森洋片", ["东森洋片"]),
    ("三立新闻", ["三立新闻", "setnews"]),
    ("三立台湾", ["三立台湾"]),
    ("三立戏剧", ["三立戏剧"]),
    ("民视", ["民视", "ftv"]),
    ("民视新闻", ["民视新闻"]),
    ("台视", ["台视", "ttv"]),
    ("台视新闻", ["台视新闻"]),
    ("中视", ["中视", "ctv"]),
    ("中视经典", ["中视经典"]),
    ("中视菁采", ["中视菁采"]),
    ("华视", ["华视", "cts"]),
    ("公视", ["公视", "pts"]),
    ("大爱一台", ["大爱", "大爱电视", "daai1"]),
    ("大爱二台", ["大爱2", "daai2"]),
    ("八大第一台", ["八大第一台"]),
    ("八大戏剧", ["八大戏剧"]),
    ("八大综合", ["八大综合"]),
    ("纬来日本", ["纬来日本"]),
    ("卫视中文台", ["卫视中文台", "卫视中文"]),
    ("非凡新闻", ["非凡新闻"]),
    ("好莱坞电影", ["好莱坞电影", "hollywoodmovies"]),
    ("AXN台湾", ["axn台湾", "axn"]),
    ("人间卫视", ["人间卫视"]),
    ("番薯台", ["番薯台"]),
    ("靖天卡通", ["靖天卡通"]),
    ("大立电视", ["大立电视"]),
]

MO_CHANNELS = [
    ("澳视澳门", ["澳视澳门", "tdmoumun", "tdmoumum"]),
    ("澳视资讯", ["澳视资讯", "tdminformation"]),
    ("澳视综艺", ["澳视综艺", "tdmentertainment"]),
    ("澳视葡文", ["澳视葡文", "tdmportuguese"]),
    ("澳门莲花", ["澳门莲花", "lotustv", "莲花卫视"]),
    ("澳门MACAU", ["澳门macau", "canalmacau"]),
]

OVERSEAS_CHANNELS = [
    ("美国中文电视", ["美国中文电视", "sinovision"]),
    ("华语电视", ["华语电视"]),
]

REGION_LISTS = {
    "香港": HK_CHANNELS,
    "台湾": TW_CHANNELS,
    "澳门": MO_CHANNELS,
    "海外华人": OVERSEAS_CHANNELS,
}

REGION_ALIAS = {}
for _region, _channels in REGION_LISTS.items():
    for _idx, (_display, _aliases) in enumerate(_channels):
        for _alias in _aliases:
            REGION_ALIAS[norm_key(_alias)] = (_region, _display, _idx)
REGION_SUFFIX = sorted(REGION_ALIAS.items(), key=lambda kv: len(kv[0]), reverse=True)


INTL_CHANNELS = [
    ("新闻", "BBC News BBC新闻", [r"^bbc news\b", r"^bbc world news$"], 0),
    ("新闻", "CNN 国际新闻", [r"^cnn$", r"^cnn international$"], 1),
    ("新闻", "FOX News 福克斯新闻", [r"^fox news channel\b", r"^fox news\b"], 2),
    ("新闻", "Al Jazeera 半岛电视台", [r"^al jazeera english\b", r"^半岛英语$",
                                        r"^半岛新闻.*英文", r"^半岛新闻.*英语"], 3),
    ("新闻", "DW 德国之声", [r"^dw english\b"], 4),
    ("新闻", "France 24 法国24", [r"^france 24 english\b"], 5),
    ("新闻", "Euronews 欧洲新闻", [r"^euronews english\b", r"^euronews$"], 6),
    ("新闻", "NHK World NHK世界", [r"^nhk world.?japan\b"], 7),
    ("新闻", "Bloomberg 彭博财经", [r"^bloomberg tv$", r"^bloomberg tv asia\b",
                                     r"^bloomberg news asia$", r"^bloomberg$"], 8),
    ("新闻", "CNBC 财经", [r"^cnbc asia\b", r"^cnbc$"], 9),
    ("新闻", "CNA 亚洲新闻台", [r"^亚洲新闻$"], 10),
    ("纪实", "Discovery 探索频道", [r"^discovery channel\b", r"^探索频道$"], 20),
    ("纪实", "Discovery Asia 探索亚洲", [r"^discovery asia\b", r"^探索亚洲$"], 21),
    ("纪实", "National Geographic 国家地理", [r"^national geographic($| )"], 22),
    ("纪实", "Nat Geo Wild 国家地理野生", [r"^national geographic wild\b",
                                           r"^国家地理野生$"], 23),
    ("纪实", "BBC Earth BBC地球", [r"^bbc earth\b"], 24),
    ("纪实", "Animal Planet 动物星球", [r"^animal planet\b", r"^动物星球$"], 25),
    ("纪实", "History 历史频道", [r"^history$", r"^history channel$", r"^历史频道$"], 26),
    ("纪实", "求索纪录", [r"^求索记录$"], 27),
    ("纪实", "求索科学", [r"^求索科学$"], 28),
    ("纪实", "求索动物", [r"^求索动物$"], 29),
    ("纪实", "求索生活", [r"^求索生活$"], 30),
    ("综合", "Arirang TV 阿里郎", [r"^arirang tv\b"], 40),
    ("综合", "NHK World Premium", [r"^nhk world premium\b"], 41),
]


THEMED_MOVIES = [
    "刘德华电影", "周星驰电影", "成龙电影", "李连杰电影", "林正英电影",
    "沈腾电影", "古天乐电影", "强森电影", "武侠电影", "战争电影",
    "枪战电影", "功夫电影", "怪兽电影", "恐怖电影", "喜剧电影",
    "经典香港电影", "黑莓电影",
]

KIDS_ALIASES = [
    "金鹰卡通", "卡酷少儿", "嘉佳卡通", "优漫卡通", "哈哈炫动",
    "浙江少儿", "福州少儿", "黑龙江少儿", "海南少儿", "靖天卡通",
    "宝宝巴士", "宝宝课堂", "中国教育1台", "中国教育2台",
]

NEWTV_ORDER = [
    "超级电影", "超级电视剧", "超级综艺", "超级体育", "精品大剧",
    "家庭剧场", "欢乐剧场", "军旅剧场", "古装剧场", "东北热剧",
    "热播精选", "精品综合", "精品纪录", "精品体育", "金牌综艺",
    "明星大片", "中国功夫", "武博世界", "军事评论", "潮妈辣婆",
    "怡伴健康", "农业致富", "炫舞未来",
]


class Builder:
    def __init__(self):
        self.channels = {}
        self.stats = defaultdict(int)

    def add(self, display, category, subgroup, url, *, logo="", source="", order=999,
            raw="", key_name=None):
        if not url or not url.startswith(("http://", "https://")):
            return
        display = unicodedata.normalize("NFKC", str(display or "")).strip()
        if not display:
            return
        key = (category, subgroup, norm_key(key_name or display))
        ch = self.channels.get(key)
        if ch is None:
            ch = {
                "name": display,
                "category": category,
                "subgroup": subgroup,
                "logo": logo or "",
                "order": order,
                "streams": [],
                "_seen": set(),
            }
            self.channels[key] = ch
        if logo and not ch["logo"]:
            ch["logo"] = logo
        ch["order"] = min(ch["order"], order)
        if url not in ch["_seen"]:
            ch["_seen"].add(url)
            ch["streams"].append({
                "url": url,
                "source": source,
                "ipv6": is_ipv6(url),
                "geo": is_geo(raw),
                "seq": len(ch["streams"]),
            })

    def final_channels(self):
        rows = []
        for (category, subgroup, _), ch in self.channels.items():
            streams = sorted(
                ch["streams"],
                key=lambda s: (
                    # 1) 有没有实测证据说这条能出画面（可用的排最前）
                    evidence(s["url"], ch["name"])[0],
                    # 2) 同一档里 IPv6 优先（用户 2026-10-22 指令；家里网络有 v6 出口，
                    #    而本机没有，所以 v6 线路只可能是「本机连不通」，不能因此排到可用线后面）
                    0 if s["ipv6"] else 1,
                    # 3) 清单自报的分辨率，高的在前
                    -resolution_rank(s["url"]),
                    # 4) 细排：分片快慢、地区限制、来源、原始次序
                    evidence(s["url"], ch["name"]),
                    s["geo"],
                    SOURCE_PRIORITY.get(s["source"], 50),
                    s["seq"],
                ),
            )
            urls = []
            keys = set()
            for s in streams:
                key = stream_rules.canon(s["url"])
                if key in keys:
                    continue
                if not usable(ch["name"], s["url"]):
                    continue
                keys.add(key)
                urls.append(s["url"])
                if MAX_STREAMS and len(urls) >= MAX_STREAMS:
                    break
            if not urls and ch["name"] in REQUIRED_CHANNELS:
                # 「咪咕体育保留」（用户 2026-10-22 指令）：点名必留的台不许被删空。
                # 同一把尺去重后整排留着，档 4 本来就排在能播的线后面 ⇒ 沉底而不是消失。
                for s in streams:
                    key = stream_rules.canon(s["url"])
                    if key and key not in keys:
                        keys.add(key)
                        urls.append(s["url"])
                if not urls:
                    continue
                print(f"[keep-empty] {ch['name']} 全部 {len(urls)} 条线都判坏，按必留名单保留（排在最后）")
            elif not urls:
                continue
            # 这台有没有「实测能用」的线路：抽帧认过台标（档 0）或拉到过视频分片（档 1）。
            best_tier = min((evidence(u, ch["name"])[0] for u in urls), default=4)
            ch_id = f"{category}-{hashlib.sha1(('|'.join([category, subgroup, ch['name']])).encode()).hexdigest()[:10]}"
            rows.append({
                "id": ch_id,
                "name": ch["name"],
                "category": category,
                "subgroup": subgroup,
                "logo": ch["logo"],
                "streams": urls,
                "_order": ch["order"],
                "_avail": 0 if best_tier <= 1 else 1,
            })
        merge_log = []
        merged = merge_duplicate_rows(rows, merge_log)
        if merged:
            print(f"[merge] 合并 {merged} 个重复台条目（台名相同／只差后缀且共用同一条地址／"
                  f"一条落在「其他」兜底组），线路取并集，一条不丢")
            for name, src, dst in merge_log:
                print(f"[merge]   挪组：{name}  {src} → {dst}")
        cat_idx = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        # 「线路可用性优于频道数量」（用户 2026-10-22 指令）：地方台/轮播/港澳台/国际这几组的
        # 次序以前是按「生成目录时已经建了几个频道」定的（add_local 里
        # len(builder.channels) % 1000、NewTV 里 100 + len(builder.channels)），
        # 也就是谁先被抓进列表谁靠前，跟这条线能不能播毫无关系。
        # 现在同一组内先把「有实测可用证据线路」的台提到没有的前面，台内相对次序仍走原 order。
        # 央视/卫视/体育这三组不参与重排：它们的 order 是频道号，是长辈认的约定（用户 10-22 明确指令）。
        # 注意别再拿「本机在境外、没 IPv6 出口」当理由：10-22 现读这台机器有全局 IPv6（2409:8a15… 联通段）、
        # curl -6 到 baidu 拿 200、默认网关 192.168.1.1 —— 它就是父母家那个网络，「本机连不通」≈家里也连不通。
        rows.sort(key=lambda r: (
            cat_idx.get(r["category"], 99),
            SUBGROUP_ORDER.get(r["category"], []).index(r["subgroup"])
            if r["subgroup"] in SUBGROUP_ORDER.get(r["category"], []) else 99,
            0 if r["category"] in CONVENTION_ORDER else r["_avail"],
            r["_order"], r["name"],
        ))
        for row in rows:
            row.pop("_order", None)
            row.pop("_avail", None)
        return rows


NAME_SUFFIX_TOKENS = ("综合频道", "新闻综合", "电视频道", "电视台", "电视", "融媒", "综合", "频道", "一套")
LOCAL_FALLBACK = "其他"  # local 组的兜底桶：源里没带地区信息的都先落这里


def name_base(name):
    """把只差后缀的台名归成一个底名：庆元综合频道 / 庆元新闻综合 / 庆元综合 → 庆元。"""
    base = name
    for token in NAME_SUFFIX_TOKENS:
        base = base.replace(token, "")
    return base or name


def merge_duplicate_rows(rows, log=None):
    """就地合并「同一个台被写了两遍」的条目，线路取并集，一条都不丢。

    公开 IPTV 源里同一个县台常以「XX综合」「XX综合频道」两个名字各进一次列表，
    10-22 全频道抽帧实测：这类条目 27 个（17 组只差后缀且共用同一条地址、10 个台名一模一样），
    台数虚增 27，能看的台一个没多。用户这一批的指令是「线路可用性优于频道数量」，
    所以并条目、不并内容。
    只并三种：台名一模一样；底名相同且两边共用至少一条地址；
    同名且其中一条落在「其他」兜底组（交给 merge_other_bucket_duplicates）。
    底名相同但地址不同、台名也不同（县里真有两个频道）不动它——那种并错会把能看的线弄丢。
    """
    groups = {}
    for index, row in enumerate(rows):
        groups.setdefault((row["category"], row["subgroup"], name_base(row["name"])), []).append(index)
    drop = set()
    for (_, _, _), indexes in groups.items():
        if len(indexes) < 2:
            continue
        # 组内再做一次并查集：只连「台名相同」或「共用一条地址」的两条
        parent = {i: i for i in indexes}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a in indexes:
            for b in indexes:
                if a >= b:
                    continue
                same_name = rows[a]["name"] == rows[b]["name"]
                shared = ({stream_rules.canon(u) for u in rows[a]["streams"]}
                          & {stream_rules.canon(u) for u in rows[b]["streams"]})
                if same_name or (name_base(rows[a]["name"]) == name_base(rows[b]["name"]) and shared):
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[rb] = ra
        clusters = {}
        for i in indexes:
            clusters.setdefault(find(i), []).append(i)
        for members in clusters.values():
            if len(members) < 2:
                continue
            # 留线路最多的那个名字；一样多就留名字短的（「庆元综合」比「庆元综合频道」更像台名）
            keep = sorted(members, key=lambda i: (-len(rows[i]["streams"]), len(rows[i]["name"]), rows[i]["name"]))[0]
            target = rows[keep]
            seen = {stream_rules.canon(u) for u in target["streams"]}
            for other in members:
                if other == keep:
                    continue
                for url in rows[other]["streams"]:
                    if stream_rules.canon(url) not in seen:
                        seen.add(stream_rules.canon(url))
                        target["streams"].append(url)
                if not target.get("logo") and rows[other].get("logo"):
                    target["logo"] = rows[other]["logo"]
                target["_order"] = min(target["_order"], rows[other]["_order"])
                drop.add(other)
            resort_streams(target)
    for index in sorted(drop, reverse=True):
        rows.pop(index)
    extra = merge_other_bucket_duplicates(rows, log if log is not None else [])
    for row in rows:
        best_tier = min((evidence(u, row["name"])[0] for u in row["streams"]), default=4)
        row["_avail"] = 0 if best_tier <= 1 else 1
    return len(drop) + extra


def resort_streams(row):
    """合并过线路后台内重排一次，用 final_channels 同一把尺（证据档 → IPv6 → 分辨率 → 快慢）。

    并进来的线原本排在被并那条的第 1 位，直接 append 会让「抽帧认过台标」的线掉到第 3 位。
    """
    name = row["name"]
    row["streams"] = sorted(
        row["streams"],
        key=lambda u: (evidence(u, name)[0], 0 if is_ipv6(u) else 1, -resolution_rank(u), evidence(u, name)),
    )


def merge_other_bucket_duplicates(rows, log):
    """把「其他」兜底组里与省份组同名的重复条目并回省份组。

    公开源里同一个县台常被同时抓成两条：一条带地区信息落到「河北」，一条没落到「其他」。
    10-22 现读这种跨组同名重复 10 个（津南一套、铜梁综合、伊犁维吾尔…），
    台数虚增 10，能看的台一个没多。只并「其中一边在兜底组」的：
    两边各自有归属（浙江的庆元综合 vs 四川的庆元综合）不并——那是两个台。
    """
    groups = {}
    for index, row in enumerate(rows):
        if row["category"] != "local":
            continue
        groups.setdefault((row["category"], row["name"]), []).append(index)
    drop = set()
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        if not any(rows[i]["subgroup"] == LOCAL_FALLBACK for i in indexes):
            continue
        # 留省份组那一条（不是兜底组的那条），线路一样多时留线多的
        keep = sorted(indexes, key=lambda i: (rows[i]["subgroup"] == LOCAL_FALLBACK,
                                              -len(rows[i]["streams"])))[0]
        target = rows[keep]
        seen = {stream_rules.canon(u) for u in target["streams"]}
        for other in indexes:
            if other == keep:
                continue
            for url in rows[other]["streams"]:
                if stream_rules.canon(url) not in seen:
                    seen.add(stream_rules.canon(url))
                    target["streams"].append(url)
            if not target.get("logo") and rows[other].get("logo"):
                target["logo"] = rows[other]["logo"]
            target["_order"] = min(target["_order"], rows[other]["_order"])
            drop.add(other)
            log.append((rows[other]["name"], rows[other]["subgroup"], target["subgroup"]))
        resort_streams(target)
    for index in sorted(drop, reverse=True):
        rows.pop(index)
    return len(drop)


def canonical_cctv(name):
    n = clean_name(name)
    up = n.upper().replace(" ", "")
    if "CCTV+" in up or "CCTV＋" in n:
        return None
    if "欧洲" in n or "美洲" in n:
        return None
    if re.search(r"CCTV[-\s]*4K", n, re.I):
        return CCTV_NAMES["4K"]
    m = re.search(r"CCTV[-\s]*(\d+)(\+)?", n, re.I)
    if m:
        num = str(int(m.group(1)))
        key = num + ("+" if m.group(2) else "")
        return CCTV_NAMES.get(key)
    if up.startswith("CGTN"):
        if "纪录" in n or "DOC" in up:
            return "CGTN 纪录"
        if "法语" in n or "FRENCH" in up:
            return "CGTN 法语"
        if "西语" in n or "SPANISH" in up:
            return "CGTN 西语"
        if "阿语" in n or "ARABIC" in up:
            return "CGTN 阿语"
        if "俄语" in n or "RUSSIAN" in up:
            return "CGTN 俄语"
        return "CGTN"
    return None


def canonical_satellite(name):
    n = clean_name(name)
    if not n.endswith("卫视"):
        return None
    if any(x in n for x in ("凤凰", "香港", "澳门", "台湾", "星空")):
        return None
    if n.startswith("CCTV"):
        return None
    if norm_key(n) in REGION_ALIAS:
        return None
    return n


def sat_order(name):
    try:
        return SAT_ORDER.index(name)
    except ValueError:
        return 200


def add_cctv(builder, entry):
    name = canonical_cctv(entry["name"])
    if not name:
        return False
    builder.add(name, "cctv", "央视", entry["url"], logo=entry["logo"],
                source=entry["source"], order=CCTV_ORDER.get(name, 150),
                raw=entry["raw"], key_name=name)
    return True


def add_satellite(builder, entry):
    name = canonical_satellite(entry["name"])
    if not name:
        return False
    builder.add(name, "cctv", "卫视", entry["url"], logo=entry["logo"],
                source=entry["source"], order=sat_order(name),
                raw=entry["raw"], key_name=name)
    return True


ID_COUNTRY = re.compile(r'tvg-id="[^".]*\.([a-zA-Z]{2,3})(?:@[^"]*)?"')
GREATER_CHINA = {"cn", "hk", "mo", "tw"}


def id_country(raw: str):
    """iptv-org 的 tvg-id 形如 `AnadoluNetTV.tr@SD`，尾巴上就是国家码。"""
    m = ID_COUNTRY.search(raw or "")
    return m.group(1).lower() if m else None
def region_suffix_matches(alias, key, spaced):
    """CJK 别名按后缀匹配（“某某卫视”）；纯 ASCII 短别名必须整词命中，
    否则 RCCTV 会被当成“中视”、BibleTV 会被当成“台视”。"""
    if not key.endswith(alias):
        return False
    if alias.isascii():
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", spaced))
    return True


def add_region(builder, entry):
    n = norm_key(entry["name"])
    hit = REGION_ALIAS.get(n)
    if not hit:
        spaced = match_key(entry["name"])
        for alias, value in REGION_SUFFIX:
            if len(alias) >= 3 and region_suffix_matches(alias, n, spaced):
                hit = value
                break
    if not hit:
        return False
    region, display, idx = hit
    if region in ("香港", "台湾", "澳门"):
        country = id_country(entry.get("raw", ""))
        if country and country not in GREATER_CHINA:
            return False
    builder.add(display, "hktw", region, entry["url"], logo=entry["logo"],
                source=entry["source"], order=idx, raw=entry["raw"],
                key_name=display)
    return True


def add_intl(builder, entry):
    n = match_key(entry["name"])
    if not n:
        return False
    for subgroup, display, patterns, order in INTL_CHANNELS:
        if any(re.search(p, n) for p in patterns):
            builder.add(display, "international", subgroup, entry["url"],
                        logo=entry["logo"], source=entry["source"],
                        order=order, raw=entry["raw"], key_name=display)
            return True
    return False


def add_chc(builder, entry):
    n = norm_key(entry["name"])
    if "chc" not in n:
        return False
    if "家庭" in n:
        display = "CHC家庭影院"
    elif "动作" in n:
        display = "CHC动作电影"
    elif "影迷" in n:
        display = "CHC影迷电影"
    else:
        display = "CHC高清电影"
    order = ["CHC家庭影院", "CHC动作电影", "CHC高清电影", "CHC影迷电影"].index(display)
    builder.add(display, "featured", "CHC", entry["url"], logo=entry["logo"],
                source=entry["source"], order=order, raw=entry["raw"],
                key_name=display)
    return True


def add_newtv(builder, entry):
    n = clean_name(entry["name"])
    if "newtv" not in n.lower() and entry.get("group") != "NewTV":
        return False
    base = re.sub(r"(?i)^newtv\s*", "", n)
    base = re.sub(r"^超级", "超级", base)
    if base in NEWTV_ORDER:
        idx = NEWTV_ORDER.index(base)
    else:
        # Names such as 超级综艺 / 精品体育 arrive without the NewTV prefix.
        idx = 100 + len(builder.channels)
    display = f"NewTV {base}" if base else "NewTV"
    builder.add(display, "featured", "NewTV", entry["url"], logo=entry["logo"],
                source=entry["source"], order=idx, raw=entry["raw"],
                key_name=display)
    return True


def add_kids(builder, entry):
    n = norm_key(entry["name"])
    for display in KIDS_ALIASES:
        if norm_key(display) == n:
            builder.add(display, "featured", "少儿", entry["url"],
                        logo=entry["logo"], source=entry["source"],
                        order=list(KIDS_ALIASES).index(display),
                        raw=entry["raw"], key_name=display)
            return True
    return False


def add_sports(builder, entry, subgroup=None, display=None, order=None):
    n = clean_name(entry["name"]).translate(TR2CN)
    nn = norm_key(n)
    if "CCTV" in n.upper():
        return False
    lower = n.lower()
    if not subgroup:
        if "咪咕" in n or "睛彩竞技" in n or "睛彩篮球" in n:
            subgroup = "咪咕直播"
        elif any(k in n for k in ("纬来体育", "爱尔达", "博斯", "澳视体育", "澳门体育")) or \
                any(k in lower for k in ("eleven", "tdm sports")):
            subgroup = "港台体育"
        elif "体育" in n:
            subgroup = "国内体育"
        else:
            return False
    if not display:
        display = n
        if "咪咕直播" in n:
            m = re.search(r"咪咕直播\s*(\d+)\s*K?\s*-?\s*(\d+)?", n, re.I)
            if "4K" in n.upper():
                num = re.search(r"4K\s*-?\s*(\d+)", n, re.I)
                display = f"咪咕直播 4K-{num.group(1) if num else '1'}"
            elif m:
                display = f"咪咕直播 {m.group(1)}"
        # Keep the sports category useful: 34 numbered event feeds crowd out real channels.
        if "咪咕直播" in display:
            m2 = re.match(r"^咪咕直播\s*(4K)?\s*-?\s*(\d+)$", display, re.I)
            if m2:
                display = f"咪咕直播 {'4K-' if m2.group(1) else ''}{int(m2.group(2))}"
        if display.startswith("咪咕直播") and "4K" not in display:
            num = re.search(r"(\d+)$", display)
            if num and int(num.group(1)) > 8:
                return True
    if order is None:
        special = {
            "五星体育": 0, "足球频道": 10, "精品体育": 11,
            "广东体育": 20, "上海体育": 21, "北京体育": 22, "天津体育": 23,
            "辽宁体育": 24, "山东体育": 25, "江苏体育": 26, "湖北体育": 27,
            "四川体育": 28, "福建体育": 29,
        }
        if display.startswith("咪咕直播 4K"):
            order = 40
        elif display.startswith("咪咕直播"):
            order = 60
        elif subgroup == "港台体育":
            order = 100
        else:
            order = special.get(display, 80)
    display = display.translate(TR2CN)
    display = {
        "爱尔达体育2": "爱尔达体育2台",
        "爱尔达体育3": "爱尔达体育3台",
        "博斯運动2": "博斯运动2",
        "博斯运动2": "博斯运动2",
        "ELEVEN 体育1台": "Eleven Sports 1",
        "ELEVEN 体育2台": "Eleven Sports 2",
        "ELEVEN 体育3台": "Eleven Sports 3",
    }.get(display, display)
    if "澳视体育" in display or "澳门体育" in display:
        display = "澳视体育"
    builder.add(display, "sports", subgroup, entry["url"], logo=entry["logo"],
                source=entry["source"], order=order, raw=entry["raw"],
                key_name=display)
    return True


def add_local(builder, entry, province):
    name = clean_name(entry["name"])
    if not name:
        return False
    if canonical_cctv(name) or canonical_satellite(name):
        return False
    if add_chc(builder, entry) or add_newtv(builder, entry):
        return True
    if add_kids(builder, entry):
        return True
    if "体育" in name or "咪咕" in name:
        return add_sports(builder, entry)
    priority = 10
    for kw, val in (("新闻综合", 0), ("综合", 1), ("都市", 2), ("经济", 3),
                    ("影视", 4), ("公共", 5), ("生活", 6), ("少儿", 7),
                    ("文旅", 8), ("科教", 9)):
        if kw in name:
            priority = val
            break
    builder.add(name, "local", province, entry["url"], logo=entry["logo"],
                source=entry["source"], order=priority * 1000 + len(builder.channels) % 1000,
                raw=entry["raw"], key_name=name)
    return True


def process_fanmingming(builder, entries):
    for e in entries:
        if add_cctv(builder, e) or add_satellite(builder, e):
            continue


def process_vbsky(builder, entries, provinces):
    for e in entries:
        g = e["group"]
        if g == "央视频道":
            add_cctv(builder, e)
        elif g == "卫视频道":
            add_satellite(builder, e)
        elif g in ("地方频道", "数字频道"):
            prov = provinces.detect(e["name"])
            if prov:
                add_local(builder, e, prov)
            elif g == "地方频道":
                add_local(builder, e, "其他")
        elif g == "电影频道":
            if add_chc(builder, e):
                continue
            name = clean_name(e["name"])
            if name in THEMED_MOVIES:
                builder.add(name, "featured", "影视轮播", e["url"], logo=e["logo"],
                            source=e["source"], order=THEMED_MOVIES.index(name),
                            raw=e["raw"], key_name=name)
        elif g == "儿童频道":
            add_kids(builder, e)
        elif g == "体育频道":
            add_sports(builder, e)


def process_zbefine(builder, entries):
    for e in entries:
        g = e["group"]
        if add_chc(builder, e) or add_newtv(builder, e):
            continue
        if g == "央视":
            add_cctv(builder, e)
        elif strip_admin(g) in PROVINCE_GROUPS:
            if add_satellite(builder, e) or add_cctv(builder, e):
                continue
            add_local(builder, e, strip_admin(g))
        elif g == "网络":
            name = clean_name(e["name"])
            if name in ("黑莓电影", "纯享4K", "纯享超清"):
                display = "纯享4K" if "纯享" in name else name
                builder.add(display, "featured", "影视轮播", e["url"],
                            logo=e["logo"], source=e["source"], order=60,
                            raw=e["raw"], key_name=display)


def process_bigbig(builder, entries):
    province_groups = {
        "北京频道": "北京", "上海频道": "上海", "浙江频道": "浙江",
        "海南频道": "海南", "湖南频道": "湖南", "广东频道": "广东",
    }
    for e in entries:
        g = e["group"]
        if g == "央视频道":
            if add_chc(builder, e):
                continue
            add_cctv(builder, e)
        elif g == "卫视频道":
            add_satellite(builder, e)
        elif g in ("香港频道", "港澳台「P」", "•港澳台"):
            if not add_region(builder, e):
                name = clean_name(e["name"]).translate(TR2CN)
                if any(k in name for k in ("体育", "爱尔达", "博斯", "Eleven")):
                    add_sports(builder, e)
        elif g in province_groups:
            add_local(builder, e, province_groups[g])
        elif g in ("NewTV系列",):
            add_newtv(builder, e)
        elif g in ("IPV6·咪咕", "咪咕「移动」"):
            add_sports(builder, e)
        elif g == "IHOT":
            display = "iHOT·" + clean_name(e["name"]).replace("iHOT", "").strip()
            if display == "iHOT·":
                continue
            builder.add(display, "featured", "影视轮播", e["url"],
                        logo=e["logo"], source=e["source"], order=20,
                        raw=e["raw"], key_name=display)
        elif g == "•影视":
            name = clean_name(e["name"])
            if "解说" in name:
                display = name.replace("•", " ")
                builder.add(display, "featured", "影视轮播", e["url"],
                            logo=e["logo"], source=e["source"], order=50,
                            raw=e["raw"], key_name=display)
        elif g == "•少儿":
            add_kids(builder, e)
        elif g in ("求索系列",):
            name = clean_name(e["name"])
            if name.startswith("求索"):
                add_intl(builder, e)
        elif g in ("•国际", "国际频道"):
            add_intl(builder, e)
        elif g == "超清频道":
            name = clean_name(e["name"])
            if "纯享" in name:
                builder.add("纯享4K", "featured", "影视轮播", e["url"],
                            logo=e["logo"], source=e["source"], order=60,
                            raw=e["raw"], key_name="纯享4K")


def process_yang(builder, entries):
    for e in entries:
        g = e["group"]
        if g == "•咪咕「移动」":
            name = clean_name(e["name"])
            if "咪咕直播" in name or "睛彩竞技" in name or "睛彩篮球" in name:
                add_sports(builder, e)
        elif g == "•影视「轮播」":
            name = clean_name(e["name"])
            display = name.replace("「", "").replace("」", "").strip()
            if not display:
                continue
            builder.add(display, "featured", "影视轮播", e["url"],
                        logo=e["logo"], source=e["source"], order=55,
                        raw=e["raw"], key_name=display)


def process_epg(builder, entries):
    for e in entries:
        if add_region(builder, e):
            continue
        name = clean_name(e["name"]).translate(TR2CN)
        if "体育" in name or "爱尔达" in name or "博斯" in name or "Eleven" in name:
            add_sports(builder, e)


def process_iptv_org(builder, entries):
    for e in entries:
        if add_region(builder, e):
            continue
        if add_intl(builder, e):
            continue
        add_cctv(builder, e)


def process_aptv(builder, entries):
    for e in entries:
        if add_region(builder, e):
            continue
        if add_chc(builder, e):
            continue
        if "五星体育" in e["name"]:
            add_sports(builder, e, subgroup="国内体育", display="五星体育", order=0)


# 用户 2026-10-21 点名的 4K 频道。判定出自 tools/.cache/linecheck/4k/candidates.json：
# 只收「播放列表取到、并且真的拉到一个分片」的线路（本机网络实测）。
# 没进来的几条都是假源：江苏卫视4K 那个中转脚本三轮里两轮返回与广告文件同 MD5 的 MP4；
# CCTV-8K、CCTV-16 4K、北京/浙江/山东/四川/深圳/湖南卫视4K 要么分片全 404，要么只有 IPv6
# （本机没有 IPv6 出口，测不到就等于没验过，不加）。
# ↑ 上面这半句理由作废（10-22 20:10 现读）：这台机器有全局 IPv6（2409:8a15:…家宽段）和默认 v6 路由，
# 出口部分可用（同批 curl -6 cloudflare 200/1.3MB/3.876s，test-ipv6.com 三连超时＝会抖）。
# 带闸重跑 tools/.cache/linecheck/4k/candidate_set_v6.json 那 29 条：清单 2xx 0、真 m3u8 0、分片 0——
# 4 条 No route to host（2409:8087:5e00:24::2d:6610），其余 9 秒不回；最远的两条 GSLB 回 302 并签发了
# 当天时刻的新 IASHttpSessionId，但跳转后的媒体机（…:6410）不回。⇒ 这些是移动 IPTV 网关内地址，
# 家宽 v6 前缀到不了，不是「没 v6 所以没测」。这一批的处置从「没验过」改成「验过、这台拿不到」。
MANUAL_4K = [
    # CCTV-8K 超高清：10-23 01:47 那一场（tools/.cache/cand4k2）四条都抽到 1920×1090 的帧，
    # 画面左上角写着「CCTV 8K 超高清」、正中写着「8K UHDTV / CCTV」——是频道自己的测试卡，
    # 按用户 02:14 那句「有台标就算真出画面」算正常线路。台标身份结论记在
    # tools/.cache/linecheck/identity_review.jsonl。
    ("CCTV-8K 超高清", [
        "http://38.75.136.137:98/gslb/dsdqpub/cctv8k.m3u8?auth=testpub",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv8k",
        "http://107.150.60.122/live/cctv8k.m3u8",
        "http://192.151.150.154/live/cctv8k.m3u8",
    ]),
    ("东方卫视 4K", ["https://bp-resource-dfl.bestv.cn/148/3/video.m3u8"]),
    ("河北 4K", ["http://event.pull.hebtv.com/live/live101.m3u8"]),
    ("苏州 4K", [
        "http://tylive.kan0512.com/norecord/csztv4k_4k.m3u8",
        "http://live-auth.51kandianshi.com/szgd/csztv4k_hd.m3u8",
    ]),
    ("咪咕体育 4K", [
        "http://gslbserv.itv.cmvideo.cn:80/1.m3u8?channel-id=FifastbLive"
        "&Contentid=3000000010000005180&livemode=1&stbId=yu&mode=1",
    ]),
]
# 台标靠抽帧认，不靠列表里写的名字：
# 3221227021 画面左上角是「CCTV5 体育」，正在播 WTT 中国大满贯女双决赛；
# 3221226899 右上角「咪咕视界」水印，足球转播。两条都是 2026-10-21 18:41 抽到帧、
# 18:52 再各自取首分片算 MD5（互不相同）确认在出真画面。
MANUAL_EXTRA = [
    ("咪咕体育", "sports", "咪咕直播",
     "http://39.134.61.218/PLTV/88888888/224/3221226899/index.m3u8"),
    ("咪咕体育", "sports", "咪咕直播",
     "http://39.134.61.218/PLTV/88888888/224/3221226901/index.m3u8"),
    ("咪咕体育", "sports", "咪咕直播",
     "http://39.134.61.218/PLTV/88888888/224/3221227120/index.m3u8"),
    ("CCTV-4K 超高清", "cctv", "央视", "http://8.138.7.223/tv/cctvnews.php?id=cctv4k"),
    # 2026-10-23：安徽卫视不在当前公开列表的输入文件里，走人工频道入口补进去。
    # 帧证据：10-23 13:29 抽到《男生女生向前冲》，左上角安徽卫视台标，1920×1090。
    ("安徽卫视", "cctv", "卫视", "http://gmxw.7766.org:808/hls/40/index.m3u8"),
    # 2026-10-23 15:0x 卫视扩容第二批：这 20 台在公开列表里重捞后逐台抽帧认过台标，各收第 1 条。
    # 帧证据：tools/.cache/sat_new*/（每台一张 frame_first.jpg，左上角台标与台名逐张对过）；
    # 河北用官方 event.pull.hebtv.com（《主角》＋「河北卫视」），兵团用 ifengli。
    ("北京卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=bjwshd"),
    ("重庆卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cqwshd"),
    ("河北卫视", "cctv", "卫视", "http://event.pull.hebtv.com/jishi/weishi_tingyun.m3u8"),
    ("辽宁卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=lnwshd"),
    ("吉林卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=jlwshd"),
    ("黑龙江卫视", "cctv", "卫视", "http://107.150.60.122/live/hljwshd.m3u8"),
    ("江苏卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=jswshd"),
    ("东南卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=dnwshd"),
    ("山东卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=sdwshd"),
    ("湖北卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=hbwshd"),
    ("广西卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=gxwshd"),
    ("三沙卫视", "cctv", "卫视", "http://107.150.60.122/live/ssws.m3u8"),
    ("贵州卫视", "cctv", "卫视", "http://107.150.60.122/live/gzwshd.m3u8"),
    ("云南卫视", "cctv", "卫视", "http://107.150.60.122/live/ynwshd.m3u8"),
    ("西藏卫视", "cctv", "卫视", "http://107.150.60.122/live/xzws.m3u8"),
    ("陕西卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=snwshd"),
    ("甘肃卫视", "cctv", "卫视", "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=gswshd"),
    ("青海卫视", "cctv", "卫视", "http://107.150.60.122/live/qhws.m3u8"),
    ("宁夏卫视", "cctv", "卫视", "http://107.150.60.122/live/nxws.m3u8"),
]

# 用户 2026-10-22 点名要补的省市体育频道，只收抽帧认过台标的那一条：
# 风云足球 10-22 02:45 现抽 1920×1090，画面右上角写着「CCTV 风云足球」，正在播英超第 22 轮
# 伍尔弗汉普顿—纽卡斯尔联，所以名字照台标写「风云足球」，不写成用户口里的「足球频道」。
# 另外 10 个名字（精品体育/广东/上海/北京/天津/山东/江苏/湖北/四川/福建体育）这一轮没加：
# 21 份公开播放列表里 5 个根本没有，剩下的要么本机连不通（山东/江苏是境内单播），
# 要么抽到的帧是带二维码的下载 App 推广页（epg.pw 代理，已在 prepack 指纹里钉死）。
MANUAL_SPORTS = [
    ("风云足球", "sports", "国内体育", 12,
     "http://38.75.136.137:98/gslb/dsdqpub/fyzq.m3u8?auth=testpub"),
]


def add_manual_channels(builder):
    for order, (display, urls) in enumerate(MANUAL_4K):
        for url in urls:
            builder.add(display, "cctv", "4K 超高清", url, source="manual_4k", order=order)
    for display, category, subgroup, url in MANUAL_EXTRA:
        builder.add(display, category, subgroup, url, source="manual_extra")
    for display, category, subgroup, order, url in MANUAL_SPORTS:
        builder.add(display, category, subgroup, url, source="manual_sports", order=order)


def load_health(path):
    """读 health_probe.py 追加写的 jsonl。同一条线路多次探测时，后写的那次算数。

    键一律用**现尺**重算（`stream_rules.canon(row["url"])`），不信文件里存的 canonical：
    那一格是写文件那一刻的尺量出来的，尺改过（IPv6 字面量补方括号那一次），旧文件里就有
    134 行的键与现尺不一致（10-22 现读 tools/.cache/health/line_health.jsonl）。
    照单收的话，这些读数挂在谁也查不到的键上：排序查不到、黑名单也查不到，
    探到的坏等于没探。
    """
    rows = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = stream_rules.canon(row.get("url") or "") or row.get("canonical") or ""
            if key:
                rows[key] = row
    return rows


def load_frames(path):
    """读 prepack 抽帧的结论：原生尺寸（canonical url -> [宽, 高]）+ 哪些是占位页。

    占位页那一档不是「抽到画面就算能看」：测试卡/带二维码的下载 App 推广页也是画面，
    但它证明这条线在给的不是节目。prepack 那边按已知图指纹判完写进 jsonl，这里照单收。
    """
    rows = {}
    placeholders = {}
    stalled = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            width, height = row.get("width") or 0, row.get("height") or 0
            key = stream_rules.canon(row.get("url", ""))
            if not key:
                continue
            if "placeholder" in row:
                # 有这一格才算一种「意见」：非空＝这一帧是占位页，空＝看过、是正常节目画面。
                # 早于该字段落盘的老文件里没有这一格，那是「没意见」，
                # 不许拿它去清掉新读数给出的判坏结论（多份证据累加时这一条会漏台）。
                placeholders[key] = row.get("placeholder") or ""
            if "stalled" in row:
                # 同一套累加规矩：这一格有才算意见。7 秒窗口的卡流只降分（见 FRAME_STALLED）。
                stalled[key] = row.get("stalled") or ""
            if width > 0 and height > 0:
                rows[key] = [int(width), int(height)]
    return rows, placeholders, stalled


def load_freeze_all(paths):
    """把所有长窗扫描按「后写的算数」合并成一份台级台账。

    返回 (frozen, owner, live)：
      frozen  canonical url -> 冻帧结论（只收「冻帧」那一档）
      owner   canonical url -> 这一条当时挂在哪个台名下
      live    这一批台账里至少有一条线「画面正常推进」的台名

    为什么不能拿门禁那 7 秒的读数当删线依据：抽帧窗口比一个固定镜头还短，
    「几帧字节相同」既可能是源没在给新画面，也可能是节目里两个人站着不动说了 7 秒台词。
    拉到分钟级，后一种解释才排掉。

    为什么要连 live 一起算：分钟级排掉了「固定镜头」，排不掉「这台半夜本来就没节目」。
    10-24 凌晨那一场 427 条里有 16 条判冻，其中浙江卫视是 3 条里冻 1 条（线路专属，该删），
    平湖/开化是 2 条全冻、兰溪/永嘉/普陀/武义/余姚是单线台（没有一条活线作对照，
    删了也不知道是线的错还是台的错）——两类的帧都看过，都是节目画面在动不了，不是测试卡。

    「换底」按 canon 逐条覆盖，所以一台的旧「正常推进」被新扫描改成「不作判」时，
    它不会继续给后面的删线当对照。
    """
    latest = {}
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = row.get("canonical") or stream_rules.canon(row.get("url", ""))
                verdict = row.get("verdict") or ""
                if not key or not verdict:
                    continue
                latest[key] = (row.get("channel") or "", verdict)
    live = {ch for ch, verdict in latest.values() if "画面正常推进" in verdict}
    frozen = {k: v for k, (ch, v) in latest.items() if v.startswith("冻帧")}
    owner = {k: ch for k, (ch, v) in latest.items() if v.startswith("冻帧")}
    return frozen, owner, live


def gate_frozen_by_sibling(frozen, owner, live):
    """只让「同台北面有活线对照」的冻帧进删除那一侧。

    返回 (该删的, 不该删只记账的)。台名不在 live 里＝这一场没有一条同线台的活读数，
    这条冻帧就只是「这台此刻没在动」，不是「这条地址坏了」。
    """
    delete, hold = {}, {}
    for key, verdict in frozen.items():
        name = owner.get(key) or ""
        if name in live:
            delete[key] = verdict
        else:
            hold[key] = name or "(无台名)"
    return delete, hold


def load_freeze(path):
    """单份文件的长窗冻帧结论（canonical url -> 结论），口径与 load_freeze_all 同一处。"""
    return load_freeze_all([path])[0]


def load_identity(path):
    """读抽帧复核结论（"频道名|canonical url" -> 结论），同样后写的算数。"""
    rows = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = f"{row.get('name', '')}|{stream_rules.canon(row.get('url') or '') or row.get('canonical') or ''}"
            if row.get("verdict"):
                rows[key] = row["verdict"]
    return rows


def load_device_proof(path):
    """读模拟器截图台账（`must_shot_pass.py` 落的 jsonl）里应用自己记下的那条播通线路。

    只收 `recorded_line` 非空、且 `still_on_target` 为真的行：前者是"这台电视真播过这条线"，
    后者排掉"试完所有线自动换台之后拍到的别人"。台账里没有这两个字段的旧行一律不算证据。
    """
    out = {}
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not row.get("recorded_line") or row.get("still_on_target") is not True:
                continue
            key = stream_rules.canon(row["recorded_line"])
            if key and row.get("name"):
                out[key] = row["name"]
    return out


def device_proof(name, url):
    """这台电视（模拟器）上真播过这条线吗——应用自己写下的那条记录算一级证据。

    为什么要有这一档：用户的判据写的是「必须通过模拟器测试截图」，截图是最高一级的事实；
    而抽帧那把尺看到的是"频道此刻在放什么"。凌晨的 CCTV-4K 在放它自己的测试卡，
    抽帧判它「不是节目画面」，可模拟器上电视确实出画面、应用也确实把这条线记成了播通的那条
    （`tools/.cache/must_shots/ledger.jsonl` 01:54 那一行）。两句话不冲突，缺的正是
    「设备实测能出画面」这一档证据，所以这里单独收，不冒充"节目画面"。
    """
    return DEVICE_PROOF.get(stream_rules.canon(url)) == name


def move_slow_lines_back(channels, health, is_device_played):
    """甲（用户 10-23 20:5x 点档）：首分片 >10 秒的慢线挪到台内最后当备份。

    同一把尺＝本轮 HEALTH 的最新读数 `elapsed_ms > 10000`，与 20:4x 那份慢线清单同口径；
    设备播过的那条永不挪（「已播过的线路绝不自动换线」）；台内已排在最后一位的慢线
    挪了也是原位置，名单只记真要动的那批。只重排、不删线、不动黑名单。
    """
    moved: list[tuple[str, str]] = []
    for ch in channels:
        streams = ch["streams"]
        n = len(streams)
        if n <= 1:
            continue
        slow = {
            s for s in streams
            if (health.get(stream_rules.canon(s)) or {}).get("elapsed_ms") is not None
            and (health.get(stream_rules.canon(s)) or {}).get("elapsed_ms") > 10000
            and not is_device_played(ch["name"], s)
        }
        if not slow:
            continue
        idx = {s: i for i, s in enumerate(streams)}
        to_move = [s for s in streams if s in slow and idx[s] < n - 1]
        if not to_move:
            continue
        keep = [s for s in streams if s not in slow or idx[s] == n - 1]
        ch["streams"] = keep + to_move
        moved.extend((ch["name"], stream_rules.canon(s)) for s in to_move)
    return channels, moved


def strict_filter(channels):
    """删台/删线判据（用户 10-22 23:4x「此为最新判据」＋10-23 17:5x 卫视组删线指令）。

    三处作用域分开，不许互相串：
      ①必看台（央视 1-17＋5+＋4K 超高清＋辽宁体育）：台内**没有任何画面实证**的线路删掉；
        一条实证都没有的台**保留在目录里**并交给结尾 [check] no_picture 判红——
        不许它悄悄消失，也不许拿播不出来的线冒充可用（那种台要去公开源补线）。
        "已实证"按**累计**证据算（旧场次的抽帧/台标复核＋**设备实测播过**同样算实证），
        本轮没复核到只报数、不删线。
      ②卫视组（用户 10-23 17:5x「很多频道有多个不可用线路，已验证过的可用线路保留，
        不可用线路删除」＋18:5x「曾经出过真实画面的有台标的线路就留」）：整台没有一条有画面的线
        ⇒ 删除频道；活下来的台，台内**本机连不通**（档 3：连接超时/响应超时）的线路删掉——
        本机就是父母家那张网（10-23 网络口径），连不通的线只会让电视白等换线超时。
        保留档 0/1（抽帧认过台标／取到过分片）＋档 2（没测过，可能通）＋设备实测播过的线
        （「已播过的线路绝不自动换线」）＋**曾经出过真实画面的线**（has_picture：旧场次抽到过
        正常节目帧也算——天津 222.169.85.8、河南 111.59.139.82 就是这一类，现在连不通也留）。
        这一侧同样不吃设备实测那一档当**台级**证据。
      ③其余各组（地方/港澳台/特色/体育/国际）：整台找不到一条有画面的线 ⇒ 删除频道；
        活下来的台，台内线路**一条都不动**（用户只说删台，没说删线）。
      ④特色频道整组删除（用户 10-23 20:0x「删除特色频道」）：category 是 featured 的台
        不管有没有画面实证一律不进目录——影视轮播/NewTV/CHC/少儿那几台是用户点名不要的，
        与「没有可用源」不是一回事，计数单独一格（dropped_featured），不许并进 dropped_no_source。
    """
    stats = {"must_total": len(MUST_PLAY_CHANNELS), "pruned_lines": 0,
             "must_no_picture": [], "dropped_no_source": 0, "dropped_featured": 0,
             "stale_only": [], "sat_pruned": []}
    survivors = []
    for ch in channels:
        if ch["category"] == "featured":
            stats["dropped_featured"] += 1
            continue
        proven = [s for s in ch["streams"]
                  if has_picture(ch["name"], s) or device_proof(ch["name"], s)]
        if ch["name"] in MUST_PLAY_CHANNELS:
            stats["pruned_lines"] += len(ch["streams"]) - len(proven)
            if proven:
                ch["streams"] = proven
                if not any(has_picture_now(ch["name"], s) for s in proven):
                    stats["stale_only"].append(ch["name"])
            else:
                stats["must_no_picture"].append(ch["name"])
            survivors.append(ch)
        elif proven:
            if ch["subgroup"] == "卫视":
                kept, dropped = [], []
                for s in ch["streams"]:
                    if (evidence(s, ch["name"])[0] < 3 or device_proof(ch["name"], s)
                            or has_picture(ch["name"], s)):
                        kept.append(s)
                    else:
                        dropped.append(s)
                if kept and dropped:
                    ch["streams"] = kept
                    stats["sat_pruned"].extend(
                        (ch["name"], stream_rules.canon(s),
                         (HEALTH.get(stream_rules.canon(s)) or {}).get("verdict", ""))
                        for s in dropped)
            survivors.append(ch)
        else:
            stats["dropped_no_source"] += 1
    return survivors, stats


def build(args):
    global MAX_STREAMS, HEALTH, IDENTITY, DROP_DEAD, FRAME_RES, FRAME_PLACEHOLDER, FRESH_RES, DEVICE_PROOF
    global FRAME_STALLED, FROZEN, FROZEN_NO_SIBLING
    MAX_STREAMS = max(0, int(args.max_streams))
    DROP_DEAD = not args.keep_dead
    out_path = Path(args.out) if args.out else OUT
    # 证据是可以攒的：一场跑只覆盖它探过的那些线路，没探过的沿用早先那场的读数。
    # 只吃一份文件时，晚的一场会把早的一场「探到过、这轮没探」的结论整片抹掉，
    # 那些线路退回「没测过」，实测解不了的容器（ExoPlayer 没有对应解复用器）就又被留在目录里。
    # 默认那一份永远在第一位：`--health` 只给一份时不会把默认读数顶掉（给了就丢默认＝换个目录跑一次
    # 就把攒下的证据清零，正是这一格以前藏着的坑）。
    health_paths = list(dict.fromkeys([DEFAULT_HEALTH] + [Path(p) for p in (args.health or [])]))
    HEALTH = {}
    for path in health_paths:
        if path.exists():
            HEALTH.update(load_health(path))
    identity_path = Path(args.identity)
    IDENTITY = load_identity(identity_path) if identity_path.exists() else {}
    frame_paths = list(dict.fromkeys([DEFAULT_FRAMES] + [Path(p) for p in (args.frames or [])]))
    FRAME_RES, FRAME_PLACEHOLDER, FRAME_STALLED = {}, {}, {}
    for path in frame_paths:
        if path.exists():
            res, placeholders, stalled = load_frames(path)
            FRAME_RES.update(res)
            for url, label in placeholders.items():
                # 空串是「看过、画面正常」这条意见，非空是「这一帧是占位页」。
                if label:
                    FRAME_PLACEHOLDER[url] = label
                else:
                    FRAME_PLACEHOLDER.pop(url, None)
            for url, label in stalled.items():
                # 同上：7 秒窗口的卡流只降分（evidence 里把档 0 压到档 1），不进删除那一侧。
                if label:
                    FRAME_STALLED[url] = label
                else:
                    FRAME_STALLED.pop(url, None)
    # 长窗冻帧（freeze_scan.py）：这一档才删线＋拉黑。默认那一份不存在时不报错，
    # 没扫过就是没意见，与「扫过且没冻」是两种状态。
    # 删线之前先过「同台北面有没有活线」那道闸（见 gate_frozen_by_sibling）。
    FROZEN = {}
    FROZEN_NO_SIBLING = {}
    freeze_paths = [Path(p) for p in (args.freeze or []) if Path(p).exists()]
    if freeze_paths:
        frozen_all, frozen_owner, live_channels = load_freeze_all(freeze_paths)
        FROZEN, FROZEN_NO_SIBLING = gate_frozen_by_sibling(frozen_all, frozen_owner, live_channels)
        print(f"[freeze] {len(freeze_paths)} 份长窗扫描、{len(live_channels)} 台有活线对照："
              f"{len(FROZEN)} 条线路专属冻帧（删线＋拉黑）、"
              f"{len(FROZEN_NO_SIBLING)} 条整台都冻住（只记账，删了也修不好）")
        for key, name in sorted(FROZEN_NO_SIBLING.items(), key=lambda kv: kv[1])[:12]:
            print(f"[freeze]   不删：{name} {key[:78]}")
        if len(FROZEN_NO_SIBLING) > 12:
            print(f"[freeze]   …另有 {len(FROZEN_NO_SIBLING) - 12} 条同档，未逐条列出")
    # 「本轮」＝传进来的最后一份帧文件（我的调用链按 mtime 老→新给，所以最后一份就是刚跑完那一场）。
    # 必看台的闸门只吃这一份：累计帧里那些 13:00 抽到的画面只能证明"曾经能播"，不能证明现在。
    if args.device_proof and Path(args.device_proof).exists():
        DEVICE_PROOF = load_device_proof(Path(args.device_proof))
        print(f"[device] {len(DEVICE_PROOF)} 条线路有「这台电视真播过」的记录（必看台那一侧算实证）")
    last_frame_path = None
    for path in frame_paths:
        if path.exists():
            last_frame_path = path
    FRESH_RES = load_frames(last_frame_path)[0] if last_frame_path else {}
    if HEALTH or IDENTITY:
        print(f"[evidence] {len(HEALTH)} 条分片级读数（{len(health_paths)} 份文件）、"
              f"{len(IDENTITY)} 条抽帧身份结论、"
              f"{len(FRAME_PLACEHOLDER)} 条抽帧占位页结论、"
              f"{len(FRAME_STALLED)} 条 7 秒窗口卡流（只降分）、"
              f"{len(FROZEN)} 条长窗冻帧（删线＋拉黑），"
              f"线路按「抽帧认过台标 > 取到过分片 > 没测过 > 本机连不通 > 已确认坏」排")
    else:
        print(f"[evidence] 没有可用读数（{'、'.join(str(p) for p in health_paths)}），线路只按来源优先级排")
    print(f"[resolution] {len(FRAME_RES)} 条抽帧尺寸读数 + {sum(1 for r in HEALTH.values() if r.get('resolution'))} 条清单自报尺寸"
          f"（同一证据档内高分辨率排前，IPv6 优先于 IPv4）")
    if not args.offline:
        ensure_sources()
    entries = {}
    for key in ("fanmingming", "vbsky", "zbefine", "bigbig", "yang",
                "epg_hk", "epg_tw", "epg_mo", "aptv", "iptv_org"):
        path = CACHE / f"{key}.m3u"
        if not path.exists():
            print(f"[warn] missing {path}, skipping", file=sys.stderr)
            entries[key] = []
            continue
        entries[key] = parse_m3u(path, key)
        print(f"[load] {key}: {len(entries[key])} entries")
    provinces = ProvinceIndex(CACHE / "pca-code.json")

    builder = Builder()
    process_fanmingming(builder, entries["fanmingming"])
    process_vbsky(builder, entries["vbsky"], provinces)
    process_zbefine(builder, entries["zbefine"])
    process_bigbig(builder, entries["bigbig"])
    process_yang(builder, entries["yang"])
    process_epg(builder, entries["epg_hk"] + entries["epg_tw"] + entries["epg_mo"])
    process_iptv_org(builder, entries["iptv_org"])
    process_aptv(builder, entries["aptv"])
    add_manual_channels(builder)

    channels = builder.final_channels()
    if not channels:
        raise SystemExit("catalog is empty")

    # 公开列表缺少稳定来源的地方台，用人工实测线路补齐。
    # 辽宁体育只留 dassby 一条：用户在电视上实测只有这条能出画面（10-21 晚三次播出），
    # bfgd 那两条（清单 120.76.248.139 与 playurl 直连 httplive.slave.bfgd.com.cn:14311）
    # 按用户 2026-10-22 的话判为不可用摘掉——本机对 bfgd 分片主机 TCP 一直是超时，从未出过画面。
    # 辽宁移动三条单播（清单 404）由「丙」档的 usable() 摘除，不再写在这里。
    manual_streams = {
        "辽宁体育": [
            "http://dassby.qqff.top:99/live/辽宁体育/index.m3u8",
        ],
    }
    # CCTV-5 体育：辽宁移动网关这条是抽帧看过台标的真 CCTV5 画面（见 MANUAL_EXTRA 上方注释），
    # 排在公开列表前面，体育大赛期间比源多的那几条更稳。
    manual_streams["CCTV-5 体育"] = [
        "http://39.134.61.218/PLTV/88888888/224/3221227021/index.m3u8",
        # 10-23 00:16 候选探测（tools/.cache/cand_probe）抽到帧，台标「CCTV 5 体育」在画面右上角，
        # 播的是女足 U17 世界杯 E 组第 2 轮 CHN 4-0 SAM；这条公开列表里没有，是同一台主机的兄弟 id。
        "http://74.91.26.218:82/live/cctv5hd.m3u8",
    ]
    # CCTV-6 电影：这台在 20 台必看名单里，而公开列表给它的 2 条本机一条都没抽出过画面。
    # 同一台主机的 cctv6hd 兄弟 id 抽帧认过台标（左上角「CCTV 6 电影」＋右下角「电影频道」，
    # 正在播《老捕快》），10-23 00:16 那一轮 1280×738 出帧。
    manual_streams["CCTV-6 电影"] = [
        "http://74.91.26.218:82/live/cctv6hd.m3u8",
    ]
    # CCTV-4K 超高清：这台在 20 台必看名单里，原来只有 `8.138.7.223/tv/cctvnews.php` 那一条转发代理，
    # 模拟器连着两次（01:24、01:28）都没让它把线路记进 prefs，试完就自动换台 ⇒ 按「删除不能正常播放的
    # 线路」这条判据它该摘掉。换上的是同一家 GSLB（风云足球/CCTV-5/6/7… 十几台都走它）的
    # `dsdqpub/cctv4k`：01:47 抽到 1920×1090 的帧，左上角写着「CCTV 4K 超高清」「3840×2160/50/P」，
    # 台标对得上——**但那一帧是频道自己的测试卡**（凌晨没有节目），已钉成自动指纹。
    manual_streams["CCTV-4K 超高清"] = [
        "http://38.75.136.137:98/gslb/dsdqpub/cctv4k.m3u8?auth=testpub",
    ]
    # CCTV-5+ 体育赛事：用户 10-23 10:2x「CCTV-5+为央视台属于G2」把它加进必看名单，而它上一轮被
    # 「无可用源删除频道」整台摘掉过（4 条线全「连接超时」）。10-23 10:32 从公开列表重捞 27 条候选
    # （tools/.cache/cand5p）逐条探：只有这一条拿到分片并抽出帧，画面左上角写着「CCTV 5+ / 体育赛事」，
    # 正在播海滩＋「文明旅游」公益广告 ⇒ 台标相符。同一台 GSLB 主机（CCTV-4K/6/7 都走它）的 dsdq* 包。
    manual_streams["CCTV-5+ 体育赛事"] = [
        "http://38.75.136.137:98/gslb/dsdqbv/cctv5p.m3u8?auth=test20251009",
    ]
    # 「西藏卫视」的 0111_1 抽帧认台标是**四川卫视**（10-21 21:31 现抽，画面左上角写着四川卫视），
    # 而四川卫视自己那两条本机都连不通 ⇒ 用户 10-21 选「挪到四川卫视名下」：
    # 这条前置到四川卫视第 1 位，同时从西藏卫视摘掉（摘法见下面 reassign_prefixes）。
    # 2026-10-23 14:36：从公开列表重捞候选，63.141／107.150 两条串行抽帧出「四川卫视」台标
    # （电视剧《兄弟们上》），前置到最前；112.27 那条保留在后面兜底。
    manual_streams["四川卫视"] = [
        "http://107.150.60.122/live/scwshd.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=scwshd",
        "http://112.27.5.218:9901/tsfile/live/0111_1.m3u8?key=txiptv&playlive=1&authid=0",
    ]
    # 2026-10-23 14:33 卫视缺源四台补线：天津／河南／深圳／四川的原有前几条在这一轮全「连接超时」，
    # 从公开列表重捞后在 63.141.230.178／107.150.60.122 两台已验证主机上抽到画面，逐帧看过台标：
    #   天津 63.141/107.150 是广告时段但左上角「天津」台标在；河南是《西北岁月》＋「河南卫视」；
    #   深圳是电视剧＋「深圳卫视」；四川见上。38.75 dsdqpub 的天津/深圳/四川帧是无台标整屏广告，不收。
    manual_streams["天津卫视"] = [
        "http://107.150.60.122/live/tjwshd.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=tjwshd",
    ]
    manual_streams["河南卫视"] = [
        "http://107.150.60.122/live/hawshd.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=hawshd",
        "http://38.75.136.137:98/gslb/dsdqpub/hawshd.m3u8?auth=testpub",
    ]
    manual_streams["深圳卫视"] = [
        "http://107.150.60.122/live/szwshd.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=szwshd",
    ]
    # 2026-10-23 卫视扩容第二批（20 台）：第 1 条都逐帧看过台标；第 2 条是同台在另一台
    # 已验证主机上的分片级备份（health 200 + 262144 字节分片，没有逐帧认台标）。
    manual_streams["北京卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=bjwshd",
        "http://107.150.60.122/live/bjwshd.m3u8",
    ]
    manual_streams["重庆卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cqwshd",
        "http://107.150.60.122/live/cqwshd.m3u8",
    ]
    manual_streams["河北卫视"] = [
        # 用户 10-23 19:1x「删除标清源，线路通过公开数据源查找」：旧的 weishi_tingyun 抽帧
        # 原生只有 960×546（标清）；换成同一家官方主机的 live101——19:0x 串行抽帧原生
        # 3840×2178（4K）、首分片 0.56 秒。旧线按台名摘（见下面 dead_stream_prefixes）。
        "https://event.pull.hebtv.com:443/live/live101.m3u8",
    ]
    manual_streams["辽宁卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=lnwshd",
        "http://107.150.60.122/live/lnwshd.m3u8",
    ]
    manual_streams["吉林卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=jlwshd",
        "http://38.75.136.137:98/gslb/dsdqpub/jlwshd.m3u8?auth=testpub",
    ]
    manual_streams["黑龙江卫视"] = [
        "http://107.150.60.122/live/hljwshd.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=hljwshd",
    ]
    manual_streams["江苏卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=jswshd",
        "http://107.150.60.122/live/jswshd.m3u8",
    ]
    manual_streams["东南卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=dnwshd",
        "http://107.150.60.122/live/dnwshd.m3u8",
    ]
    manual_streams["山东卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=sdwshd",
        "http://107.150.60.122/live/sdwshd.m3u8",
    ]
    manual_streams["湖北卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=hbwshd",
        "http://107.150.60.122/live/hbwshd.m3u8",
    ]
    manual_streams["广西卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=gxwshd",
        "http://107.150.60.122/live/gxwshd.m3u8",
    ]
    manual_streams["三沙卫视"] = [
        "http://107.150.60.122/live/ssws.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=ssws",
    ]
    manual_streams["贵州卫视"] = [
        "http://107.150.60.122/live/gzwshd.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=gzwshd",
    ]
    manual_streams["云南卫视"] = [
        "http://107.150.60.122/live/ynwshd.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=ynwshd",
    ]
    manual_streams["西藏卫视"] = [
        "http://107.150.60.122/live/xzws.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=xzws",
    ]
    manual_streams["陕西卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=snwshd",
        "http://107.150.60.122/live/snwshd.m3u8",
    ]
    manual_streams["甘肃卫视"] = [
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=gswshd",
        "http://38.75.136.137:98/gslb/dsdqpub/gswshd.m3u8?auth=testpub",
    ]
    manual_streams["青海卫视"] = [
        "http://107.150.60.122/live/qhws.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=qhws",
    ]
    manual_streams["宁夏卫视"] = [
        "http://107.150.60.122/live/nxws.m3u8",
        "http://198.204.228.26/live/nxws.m3u8",
    ]
    # 2026-10-23 卫视扩容：这两台在公开列表里一条画面都没抽到过，以前被 strict_filter 整台删掉。
    # 现在各补一条**本轮逐帧看过台标**的线并前置（证据在 tools/.cache/ev_sat_1023/line_frames.jsonl）：
    #   山西卫视 119.39.9.8:9901 抽到电视剧画面，左上角「山西卫视」台标（1920×1090）；
    #   安徽卫视 gmxw.7766.org 抽到《男生女生向前冲》，左上角安徽卫视台标（1920×1090）。
    # 同一轮其它公开候选都没过：东南卫视那张是「暂不支持播放该时段内容」占位页，
    # 其余 24 台在三台可达主机上都没抽到一帧。
    manual_streams["山西卫视"] = [
        "http://119.39.9.8:9901/tsfile/live/0118_1.m3u8",
    ]
    manual_streams["安徽卫视"] = [
        "http://gmxw.7766.org:808/hls/40/index.m3u8",
    ]
    # 本机实测端口不通/分片 404 的主机，公开列表里仍会带回来，按前缀剔除。
    # dassby.qqff.top 不在此列：本机网络测不通，但用户在电视上实测能出画面。
    dead_stream_prefixes = {
        "辽宁体育": (
            "http://59.44.102.18:8888/",
            "http://42.176.185.28:9901/",
        ),
        # 这一条不是死线，是**挂错台**：对端给的是四川卫视的画面。
        "西藏卫视": ("http://112.27.5.218:9901/tsfile/live/0111_1.m3u8",),
        # CCTV-4K 那条转发代理：00:13 抽帧确实给过真节目（热气球＋「CCTV 4K 超高清」台标），
        # 但装机后模拟器连着两次（01:24、01:28）它一条都没播出来、应用直接换台。
        # 摘它的依据是**屏幕上没画面**这一条，不是"这台主机坏"——同主机别的 id 另说。
        "CCTV-4K 超高清": ("http://8.138.7.223/tv/cctvnews.php?id=cctv4k",),
        # 用户 2026-10-23 17:2x 点档「1 删」：兵团卫视整台删掉。ifengli 那条在这台电视上
        # 两次实测（15:4x 与 16:5x）都没起画面，试完全部线后自动换到延边卫视。
        # 按台名摘掉它所有可能来源的线，摘空之后 strict_filter 会整台删掉。
        "兵团卫视": (
            "http://cc2022.ifengli.com:2000/live/btws-sd/",
            "http://[2409:8087:1e03:21::2]:6060/",
        ),
        # 用户 10-23 18:5x「CCTV13 线路1 不是当前频道的画面，删除」：浙江台的 channel21
        # 原来复核过「台标相符」（10-21 19:0x 工信部发布会那一帧），现在电视上放的不是本台画面
        # （内容漂移，和 CCTV-4K 时而 CGTN 同一类）。只按台名摘，同主机别的 id 不动。
        "CCTV-13 新闻": (
            "http://ali-m-l.cztv.com/channels/lantian/channel21/",
        ),
        # 用户 10-23 19:1x「删除标清源」：河北卫视旧首条 weishi_tingyun 抽帧原生 960×546（标清），
        # 换官方主机同门的 live101（4K）后按台名摘掉，公开列表若再带回也一并摘。
        "河北卫视": (
            "http://event.pull.hebtv.com/jishi/weishi_tingyun.m3u8",
        ),
        # 用户 10-23 22:5x 目标「同一个频道全高清线路时有时无修复，CCTV8」：bztv 这条 12:22
        # 当备用线加进来时抽帧有真台标（跨主机备份），后来变成时有时无——近 6 轮 health 全
        # 连不上、门禁那帧是纯色蓝屏、23:05 现抽又出真画面。它是台内第 3 条，播过一次就会被
        # 「记住上次线路」当起点，下一轮进台从这条时有时无的线起播＝用户看到的时有时无。
        # 按台名摘掉；同主机别的频道不动。
        "CCTV-8 电视剧": (
            "http://bztv.tvbus.cc:8081/cdnlive/cctv8.m3u8",
        ),
        # 用户 10-24 11:0x 真机现读「CCTV-2 线路5 直播流 8.138.7.223 没有台标、是微信扫码，
        # 不是正常线路应该删除」。摘它的依据是**屏幕上放的不是节目**这一条，不是"这台主机坏"。
        # 主持机 04:2x 现抽这一条还给过真画面（1920×1090、左上「CCTV 2 财经」《经济半小时》），
        # 但 3.8 秒就断流，同族 `id=cctv1`／`id=cctv16` 抽到 0 帧，而 `id=cctv16` 在 03:50、04:04
        # 两次端出来的就是那张「服务器超负载…微信扫码支持」告警页 ⇒ 这一族超载时会换页，
        # 按"时刻性坏"本不该删线，是用户现读点名才摘（证据 tools/.cache/qr_probe_20261024/）。
        "CCTV-2 财经": (
            "http://8.138.7.223/tv/api.php?id=cctv2",
        ),
    }
    for ch in channels:
        extra = manual_streams.get(ch["name"])
        if extra:
            ch["streams"] = list(dict.fromkeys(extra + ch["streams"]))
        dead = dead_stream_prefixes.get(ch["name"])
        if dead:
            ch["streams"] = [s for s in ch["streams"] if not s.startswith(dead)]

    # 用户 10-24 点档 #86「选项B：白天重扫，还在冻才摘」。蓝田 `ali-m-l.cztv.com` 这一族
    # 13 个地址在 11:08–11:25（白天）重扫 **13/13 全部判冻**
    # （`tools/.cache/freeze/scan_lantian_day_20261024.jsonl`，连续 13 帧字节相同＝≥60 秒没新画面）。
    # 12 格画面逐张看过（`tools/.cache/lantian_day_grid_0..2.png`）：**不是休台测试卡**，
    # 是节目定格＋电视购物广告卡——「五养牌破壁灵芝孢子粉 ¥1998／4001-288-288」
    # 「豪美仕竹盐枇杷干 99 元 8 袋／400-710-8844」「消费满 5000 元就送瑞士包／400-7658-666」，
    # 每张都带二维码 ⇒ 按用户 10-22「画面不能有二维码、必须排除广告」是双重不合格。
    # 这 15 个台名每台只有这一条线，摘线即删台（空台由 strict_filter 删掉）。
    lantian_frozen = (
        "https://ali-m-l.cztv.com/channels/lantian/channel002/1080p.m3u8",
        "http://ali-m-l.cztv.com/channels/lantian/channel03/1080p.m3u8",
        "http://ali-m-l.cztv.com/channels/lantian/channel003/1080p.m3u8",
        "https://ali-m-l.cztv.com/channels/lantian/channel007/1080p.m3u8",
        "https://ali-m-l.cztv.com/channels/lantian/channel010/1080p.m3u8?",
        "http://ali-m-l.cztv.com/channels/lantian/channel007/720p.m3u8",
        "http://ali-m-l.cztv.com/channels/lantian/channel12/720p.m3u8",
        "https://ali-m-l.cztv.com/channels/lantian/channel006/1080p.m3u8",
        "http://ali-m-l.cztv.com/channels/lantian/channel006/720p.m3u8",
        "http://ali-m-l.cztv.com/channels/lantian/channel02/1080p.m3u8",
        "http://ali-m-l.cztv.com/channels/lantian/channel04/720p.m3u8",
        "http://ali-m-l.cztv.com/channels/lantian/channel004/1080p.m3u8",
        "https://ali-m-l.cztv.com/channels/lantian/channel003/1080p.m3u8",
        # 装机后设备侧现读补漏：这一条不在内置目录里（按主机路径已经摘掉），但设备端自动更新
        # 从公开列表把它捞回来挂在浙江卫视名下（10-24 12:0x 现读 305 台／723 条里唯一一条
        # 「同一平台又没被拉黑」的线）⇒ 一起进黑名单才算挡住。
        "https://ali-m-l.cztv.com/channels/lantian/channel001/1080p.m3u8",
    )
    for ch in channels:
        ch["streams"] = [s for s in ch["streams"]
                         if "ali-m-l.cztv.com/channels/lantian/" not in s]

    # 必看台的**备用**线路（用户 2026-10-23 12:2x 回「要」：给只有 1 条线的 18 台补备用线）。
    # 与 manual_streams 的差别只有一个：**追加在最后**。现有第一条是这台电视上真播过的那条，
    # 按「已播过的线路绝不自动换线」不许被新候选挤掉；新线只在那条播不出来时兜底。
    # 证据（都逐帧看过台标，不是只看分片）：
    #   ① `38.75.136.137:98/gslb/dsdqbv/*` 这一套 12:22 抽帧 17 台全出画面（1280×738、4K 1920×1090），
    #      逐张看台标全部相符（CCTV-1..17 的 hd 包、cctv4k、cctv5p）——它和现在用的 cctv5p/cctv4k
    #      是同一台 GSLB 主机；这是这一批的主备份。CCTV-5+ 已经在用 cctv5p 这一条，不重复加。
    #   ② 另几台在**别的主机**上还有一条相符的（跨主机才有意义，同一台主机换条路径不解决主机挂掉）：
    #      63.141.230.178:82 的 zbdq5（CCTV-1/6/7/9/17）、107.150.60.122 的 live（CCTV-4/12）、
    #      bztv.tvbus.cc 的 cdnlive（CCTV-8）。CCTV-5+ 那条 223.78.65.165 的 TS 单播也相符。
    #      没进来的三条都看过帧：CCTV-14/15/16 在 63.141/204.12/207.56 上抽到的是药品广告
    #      （400-179-9565／400-689-3979／400-066-2600），CCTV-2 跨主机那两条是带二维码的股市画面。
    #
    # 2026-10-23 13:3x 第二批（用户「增加1080/720源…至少三条可用线路」）：
    #   逐帧看过台标的：CCTV-2/10 用 107.150.60.122；CCTV-3/5/11/13/14/15/16 用 63.141.230.178。
    #   CCTV-5 那条原生 1920×1090，其余 1280×738（≈720p）；全部**追加**在原第一条之后。
    #   CCTV-11/14 那一帧正好是广告时段（屏幕上有 400 电话），但左上角频道台标在画面上——
    #   按 10-23 02:14 的「有台标就算真出画面」口径收；没有台标的纯广告一律不收，
    #   dsdqpub 的 cctv5p/cctv4k 就是这样被挡掉的。
    #   仍只有 2 条：CCTV-5+（没有第三条可用源）、CCTV-4K（两条都在 38.75.136.137 同一台主机）。
    manual_backups = {
        "CCTV-1 综合": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv1hd.m3u8?auth=test20251009",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv1hd",
            # 用户 10-24 目标「增加央视频道的可用线路」：2874 条候选里串行复测能取分片、
            # 且抽帧认过左上角「CCTV 1 综合」台标的只这一条（720×576 标清，
            # 排在那两条 1920 之后当备用，不动首条）。
            "http://124.116.183.146:9901/tsfile/live/0001_1.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:31 抽帧 1920×1090、左上角「CCTV 1 综合」＋《山水间的家 浙江篇》；这条起播会抖（01:34 那一场 0 帧），所以只当备份。
            "http://8.138.7.223/tv/api.php?id=cctv1",
            # 用户 10-24 目标「增加央视频道的可用线路」第三批：150 秒长窗确认画面正常推进，04:04 逐张看 2×2 放大格认过台标（tools/.cache/b3_sel.json）。
            "http://8.138.7.223/tv/cctvnews.php?id=cctv1",
        ],
        "CCTV-2 财经": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv2hd.m3u8?auth=test20251009",
            "http://107.150.60.122/live/cctv2hd.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1920×1090、左上角「CCTV 2 财经」＋《赢在AI+》。只收 cctv2.php 这一条：同主机的 api.php?id=cctv2 用户 10-24 11:0x 在真机上现读是「服务器超负载…微信扫码」告警页，已摘＋拉黑（见 dead_stream_prefixes）。
            "http://8.138.7.223/tv/cctv2.php",
            # 用户 10-24 目标「增加央视频道的可用线路」第三批：150 秒长窗确认画面正常推进，04:04 逐张看 2×2 放大格认过台标（tools/.cache/b3_sel.json）。
            "http://192.151.150.154/live/cctv2hd.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」第三批：150 秒长窗确认画面正常推进，04:04 逐张看 2×2 放大格认过台标（tools/.cache/b3_sel.json）。
            "http://198.204.228.26/live/cctv2hd.m3u8",
        ],
        "CCTV-3 综艺": [
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv3hd",
            # 用户 10-24 目标「增加央视频道的可用线路」：2874 条候选里抽帧认过
            # 左上角「CCTV 3 综艺」台标的一条，1920×1090（这台此前只有 3 条线）。
            "http://124.116.183.146:9901/tsfile/live/0003_1.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」第三批：150 秒长窗确认画面正常推进，04:04 逐张看 2×2 放大格认过台标（tools/.cache/b3_sel.json）。
            "http://107.150.60.122/live/cctv3hd.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」第三批：150 秒长窗确认画面正常推进，04:04 逐张看 2×2 放大格认过台标（tools/.cache/b3_sel.json）。
            "http://192.151.150.154/live/cctv3hd.m3u8",
        ],
        "CCTV-4 中文国际": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv4hd.m3u8?auth=test20251009",
            "http://107.150.60.122/live/cctv4hd.m3u8",
            # 10-23 19:5x 抽帧 1920×1090、左上角「CCTV 4 中文国际」台标（新闻眼/今日亚洲）；首分片 5.0 秒。
            "http://38.75.136.137:98/gslb/dsdqpub/cctv4hd.m3u8?auth=testpub",
            # 用户 10-24 目标「增加央视频道的可用线路」：124.116.183.146 族系里认过台标的一条，
            # 10-24 00:59 抽帧 720×578、左上角「CCTV 4 中文国际」＋《今生有你》角标；首分片 0.5 秒。
            "http://124.116.183.146:9901/tsfile/live/0004_1.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1920×1090、左上角「CCTV 4 中文国际」＋《中国文艺》「流淌的旋律」。
            "http://8.138.7.223/tv/cctvnews.php?id=cctv4",
        ],
        "CCTV-5 体育": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv5hd.m3u8?auth=test20251009",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv5hd",
            # 用户 10-24 目标「增加央视频道的可用线路」：10-24 00:59 抽帧 1920×1090、
            # 左上角「CCTV 5 体育」＋央视网水印（洛阳棋圣战围棋解说）；首分片 0.2 秒。
            "http://124.116.183.146:9901/tsfile/live/0005_1.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1280×738、左上角「CCTV 5 体育」＋世界体操锦标赛女子全能决赛（咪咕角标）。
            "http://8.138.7.223/tv/api.php?id=cctv5",
        ],
        "CCTV-6 电影": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv6hd.m3u8?auth=test20251009",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv6hd",
            # 用户 10-24 目标「增加央视频道的可用线路」第三批：150 秒长窗确认画面正常推进，04:04 逐张看 2×2 放大格认过台标（tools/.cache/b3_sel.json）。
            "http://192.151.150.154/live/cctv6hd.m3u8",
        ],
        "CCTV-7 国防军事": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv7hd.m3u8?auth=test20251009",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv7hd",
            # 10-23 19:5x 抽帧 1920×1090、左上角「CCTV 7 国防军事」台标（军事报道）；首分片 10.1 秒。
            "http://38.75.136.137:98/gslb/dsdqpub/cctv7hd.m3u8?auth=testpub",
            # 用户 10-24 目标「增加央视频道的可用线路」：10-24 00:59 抽帧 720×578、
            # 左上角「CCTV 7 国防军事」＋右下「国防军事」（《揭秘美步兵战车换代计划》）；首分片 0.3 秒。
            "http://124.116.183.146:9901/tsfile/live/0007_1.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1920×1090、左上角「CCTV 7 国防军事」台标，那一帧是这台自己的 1080/50I 色条卡（按 10-23 02:14「有台标就算真出画面」收）。
            "http://8.138.7.223/tv/cctvnews.php?id=cctv7",
        ],
        "CCTV-8 电视剧": [
            # 10-23 23:11 抽帧 1920×1088、左上角「CCTV 8 电视剧」台标相符（画廊画作画面）；
            # health 两轮可取到分片（7.2/8.3 秒）。排在 dsdqbv 前面：它 404 的时候应用落到
            # 720p 备份还留在本台，不会换台（bztv 摘掉后的排序，见 dead_stream_prefixes）。
            "http://38.75.136.137:98/gslb/dsdqpub/cctv8hd.m3u8?auth=testpub",
            "http://38.75.136.137:98/gslb/dsdqbv/cctv8hd.m3u8?auth=test20251009",
            # 用户 10-24 目标「增加央视频道的可用线路」第三批：150 秒长窗确认画面正常推进，04:04 逐张看 2×2 放大格认过台标（tools/.cache/b3_sel.json）。
            "http://198.204.228.26/live/cctv8hd.m3u8",
        ],
        "CCTV-9 纪录": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv9hd.m3u8?auth=test20251009",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv9hd",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1920×1090、左上角「CCTV 9 纪录」台标，野生动物节目。
            "http://8.138.7.223/tv/cctvnews.php?id=cctv9",
            # 用户 10-24 目标「增加央视频道的可用线路」第三批：150 秒长窗确认画面正常推进，04:04 逐张看 2×2 放大格认过台标（tools/.cache/b3_sel.json）。
            "http://192.151.150.154/live/cctv9hd.m3u8",
        ],
        "CCTV-10 科教": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv10hd.m3u8?auth=test20251009",
            "http://107.150.60.122/live/cctv10hd.m3u8",
            # 10-23 19:5x 抽帧 1920×1090、左上角「CCTV 10 科教」台标（自然传奇）；首分片 15.1 秒。
            "http://38.75.136.137:98/gslb/dsdqpub/cctv10hd.m3u8?auth=testpub",
            # 用户 10-24 目标「增加央视频道的可用线路」：10-24 00:59 抽帧 720×578、
            # 左上角「CCTV 10 科教」＋「味道」角标（《四季风味 44》）；首分片 0.3 秒。
            "http://124.116.183.146:9901/tsfile/live/0010_1.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1920×1090、左上角「CCTV 10 科教」＋「探索·发现」《世纪考古大发现》第二季(70)。
            "http://8.138.7.223/tv/cctvnews.php?id=cctv10",
        ],
        "CCTV-11 戏曲": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv11hd.m3u8?auth=test20251009",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv11hd",
            # 用户 10-24 目标「增加央视频道的可用线路」：10-24 00:59 抽帧 720×578、
            # 左上角「CCTV 11 戏曲」＋右下「中国IPTV」；首分片 0.3 秒。
            "http://124.116.183.146:9901/tsfile/live/0011_1.m3u8",
                    # 用户 10-24 目标「增加央视频道的可用线路」：150 秒长窗确认画面正常推进，且抽帧认过左上角台标（看图结论在 tools/.cache/cand_sel.json）。
            "http://192.151.150.154/live/cctv11hd.m3u8",
        ],
        "CCTV-12 社会与法": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv12hd.m3u8?auth=test20251009",
            "http://107.150.60.122/live/cctv12hd.m3u8",
            # 10-23 19:5x 抽帧 1920×1090、左上角「CCTV 12 社会与法」台标（普法剧场《终极较量》）；首分片 24.6 秒。
            "http://38.75.136.137:98/gslb/dsdqpub/cctv12hd.m3u8?auth=testpub",
            # 用户 10-24 目标「增加央视频道的可用线路」：10-24 00:59 抽帧 720×578、
            # 左上角「CCTV 12 社会与法」＋左下《一线》栏目标（《小桥人家》）；首分片 0.2 秒。
            "http://124.116.183.146:9901/tsfile/live/0012_1.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1920×1090、左上角「CCTV 12 社会与法」＋《普法剧场》「终极较量·精编版（第一集）」。
            "http://8.138.7.223/tv/cctvnews.php?id=cctv12",
        ],
        "CCTV-13 新闻": [
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv13hd",
            # 10-23 18:5x 用户点档「CCTV13 线路1 不是当前频道的画面，删除，添加新线路」换进来的新线：
            # 10-23 10:5x 串行抽帧，左上角「CCTV 13 新闻」台标（广告时段），1280×738；
            # 首分片 8.1 秒。原第 1 条 cztv channel21 见下面 dead_stream_prefixes。
            "http://107.150.60.122/live/cctv13hd.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：抽帧认过左上角「CCTV 13 新闻」
            # 台标（《新闻30分》片头），720×578 标清，当备用线排在两条 1280 之后。
            "http://124.116.183.146:9901/tsfile/live/0013_1.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1920×1090、左上角「CCTV 13 新闻」＋《焦点访谈》重播。
            "http://8.138.7.223/tv/cctvnews.php?id=cctv13",
            # 用户 10-24 目标「增加央视频道的可用线路」第三批：150 秒长窗确认画面正常推进，04:04 逐张看 2×2 放大格认过台标（tools/.cache/b3_sel.json）。
            "http://192.151.150.154/live/cctv13hd.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」第三批：150 秒长窗确认画面正常推进，04:04 逐张看 2×2 放大格认过台标（tools/.cache/b3_sel.json）。
            "http://38.75.136.137:98/gslb/dsdqbv/cctv13hd.m3u8?auth=test20251009",
        ],
        "CCTV-14 少儿": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv14hd.m3u8?auth=test20251009",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv14hd",
            # 用户 10-24 目标「增加央视频道的可用线路」：10-24 00:59 抽帧 720×578、
            # 左上角「CCTV 14 少儿」台标在（这一帧正好是节目尾「再见」落版，不是节目画面，
            # 但按 10-23 02:14「有台标就算真出画面」口径收）；首分片 0.3 秒。
            "http://124.116.183.146:9901/tsfile/live/0014_1.m3u8",
                    # 用户 10-24 目标「增加央视频道的可用线路」：150 秒长窗确认画面正常推进，且抽帧认过左上角台标（看图结论在 tools/.cache/cand_sel.json）。
            "http://107.150.60.122/live/cctv14hd.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：150 秒长窗确认画面正常推进，且抽帧认过左上角台标（看图结论在 tools/.cache/cand_sel.json）。
            "http://192.151.150.154/live/cctv14hd.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：150 秒长窗确认画面正常推进，且抽帧认过左上角台标（看图结论在 tools/.cache/cand_sel.json）。
            "http://47.97.252.137:8088/daili/cctv14hd.m3u8?line=1",
        ],
        "CCTV-15 音乐": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv15hd.m3u8?auth=test20251009",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv15hd",
                    # 用户 10-24 目标「增加央视频道的可用线路」：150 秒长窗确认画面正常推进，且抽帧认过左上角台标（看图结论在 tools/.cache/cand_sel.json）。
            "http://192.151.150.154/live/cctv15hd.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：150 秒长窗确认画面正常推进，且抽帧认过左上角台标（看图结论在 tools/.cache/cand_sel.json）。
            "http://198.204.228.26/live/cctv15hd.m3u8",
        ],
        "CCTV-16 奥林匹克": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv16hd.m3u8?auth=test20251009",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv16hd",
                    # 用户 10-24 目标「增加央视频道的可用线路」：150 秒长窗确认画面正常推进，且抽帧认过左上角台标（看图结论在 tools/.cache/cand_sel.json）。
            "http://198.204.228.26/live/cctv16hd.m3u8",
        ],
        "CCTV-17 农业农村": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv17hd.m3u8?auth=test20251009",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv17hd",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1920×1090、左上角「CCTV 17 农业农村」台标，那一帧是这台自己的 1080/50I 色条卡（同上口径收）。
            "http://8.138.7.223/tv/cctvnews.php?id=cctv17",
                    # 用户 10-24 目标「增加央视频道的可用线路」：150 秒长窗确认画面正常推进，且抽帧认过左上角台标（看图结论在 tools/.cache/cand_sel.json）。
            "http://192.151.150.154/live/cctv17hd.m3u8",
            # 用户 10-24 目标「增加央视频道的可用线路」：150 秒长窗确认画面正常推进，且抽帧认过左上角台标（看图结论在 tools/.cache/cand_sel.json）。
            "http://198.204.228.26/live/cctv17hd.m3u8",
        ],
        "CCTV-5+ 体育赛事": [
            "http://223.78.65.165:9901/tsfile/live/0006_1.m3u8?key=txiptv&playlive=1&authid=0",
            # 10-23 14:04 串行抽帧：WTT 中国大满贯女单 1/4 决赛，左上角「CCTV 5+ 体育赛事」。
            # 107.150 原生 1920×1090；63.141 原生 1280×738（≈720p）。
            "http://107.150.60.122/live/cctv5p.m3u8",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv5p",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1280×738、左上角「CCTV 5+ 体育赛事」＋「CBA揭幕战」比分条 上海 16:19 深圳。
            "http://8.138.7.223/tv/api.php?id=cctv5p",
        ],
        "CCTV-4K 超高清": [
            "http://38.75.136.137:98/gslb/dsdqbv/cctv4k.m3u8?auth=test20251009",
            # 10-23 14:04 串行抽帧：纪录片《生命树》，左上角「CCTV 4K 超高清」，两条都 1920×1090。
            "http://107.150.60.122/live/cctv4k.m3u8",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv4k",
            # 用户 10-24 目标「增加央视频道的可用线路」：8.138.7.223 那台 PHP 代理（12/12 抽帧认过台标，见 tools/.cache/cctv_8138_add/），01:34 抽帧 1920×1090、左上角「CCTV 4K 超高清」＋「CCTV UHD」，卡面自报 3840×2160/50/P（这台自己的测试卡）。
            "http://8.138.7.223/tv/cctvnews.php?id=cctv4k",
        ],
    }
    # 用户 10-24 11:3x 点档「候选认台标 → 落盘」：第四批 20 条。
    # 39 条长窗确认「画面正常推进」的候选，逐张看 2×2 放大格认过台标的 20 条收进来；
    # 判红 17 条全是 `47.97.252.137:8088`（另 `198.204.228.26/live/cctv9hd` 一条）端出来的
    # 药品／电视购物广告帧（满屏「咨询热线 400-179-9565／400-788-3916／订购电话 400-689-3979」，
    # 整帧无本台角标）；另 2 条是**别的台的画面挂在本台名下**（CCTV-4 那条抽到「CCTV 4K 超高清」卡、
    # CCTV-5 那条抽到「CCTV 5+ 体育赛事」西甲）⇒ 不收。逐条结论与依据在 tools/.cache/logo39/verdicts.json。
    # 追加语义照旧：只往每台现有线路后面接，第一条一律不动。
    for _name, _urls in (
        ('CCTV-1 综合', (
            "http://192.151.150.154/live/cctv1hd.m3u8",
        )),
        ('CCTV-10 科教', (
            "http://192.151.150.154/live/cctv10hd.m3u8",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv10hd",
        )),
        ('CCTV-12 社会与法', (
            "http://192.151.150.154/live/cctv12hd.m3u8",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv12hd",
        )),
        ('CCTV-14 少儿', (
            "http://bztv.tvbus.cc:8081/cdnlive/cctv14.m3u8",
        )),
        ('CCTV-16 奥林匹克', (
            "http://bztv.tvbus.cc:8081/cdnlive/cctv16.m3u8",
        )),
        ('CCTV-2 财经', (
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv2hd",
            "http://8.138.7.223/tv/cctvnews.php?id=cctv2",
            "http://bztv.tvbus.cc:8081/cdnlive/cctv2.m3u8",
        )),
        ('CCTV-3 综艺', (
            "http://198.204.228.26/live/cctv3hd.m3u8",
            "http://38.75.136.137:98/gslb/dsdqbv/cctv3hd.m3u8?auth=test20251009",
            "http://bztv.tvbus.cc:8081/cdnlive/cctv3.m3u8",
        )),
        ('CCTV-4 中文国际', (
            "http://192.151.150.154/live/cctv4hd.m3u8",
            "http://198.204.228.26/live/cctv4hd.m3u8",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv4hd",
        )),
        ('CCTV-5 体育', (
            "http://107.150.60.122/live/cctv5hd.m3u8",
        )),
        ('CCTV-7 国防军事', (
            "http://192.151.150.154/live/cctv7hd.m3u8",
        )),
        ('CCTV-8 电视剧', (
            "http://198.204.228.26/live/cctv8hd.m3u8",
        )),
        ('CCTV-9 纪录', (
            "http://74.91.26.218:82/live/cctv9hd.m3u8",
        )),
    ):
        manual_backups.setdefault(_name, []).extend(_urls)
    for ch in channels:
        backups = manual_backups.get(ch["name"])
        if backups:
            ch["streams"] = list(dict.fromkeys(list(ch["streams"]) + backups))
    # 卫视备份线（用户 10-23 18:5x「卫视加备份线路，曾经出过真实画面的有台标的线路就留」）：
    # 与 manual_backups 同一追加语义——现有第一条是验证过的（或设备播过的），不被新线挤掉。
    # 10-23 19:0x 那批：三台已验证主机（63.141 zbdq5／107.150 live／38.75 dsdqpub）逐台探测＋
    # 串行抽帧（tools/.cache/sat_backup_cands/，帧 1920×1090）：
    #   山西 sxwshd（山西新闻联播，台标相符）、浙江 zjwshd×2（公益广告/整点片头，台标相符）、
    #   安徽 ahwshd×2（转播新闻联播，左上角「安徽卫视」）、江西 jxwshd（转播新闻联播，「江西卫视」）、
    #   东方 dfwshd（上海气象台天气节目，东方卫视台标）——都逐帧看过台标；
    #   湖南 hnwshd×2、广东 gdwshd、黑龙江 hljwshd×2 抽到的是转播新闻联播/广告、帧里没照到本台台标，
    #   按「分片级备份」（health 可取到分片＋262144 字节）收——与卫视扩容第二批第 2 条同一档。
    # 河北/内蒙古/新疆/安多/延边/山东教育/大湾区在这三台主机上没有别的源（分片太小），仍单线。
    sat_manual_backups = {
        "东方卫视": [
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=dfwshd",
            "http://107.150.60.122/live/dfwshd.m3u8",
        ],
        "山西卫视": [
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=sxwshd",
        ],
        "黑龙江卫视": [
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=hljwshd",
            "http://38.75.136.137:98/gslb/dsdqpub/hljwshd.m3u8?auth=testpub",
        ],
        "浙江卫视": [
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=zjwshd",
            "http://38.75.136.137:98/gslb/dsdqpub/zjwshd.m3u8?auth=testpub",
        ],
        "安徽卫视": [
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=ahwshd",
            "http://38.75.136.137:98/gslb/dsdqpub/ahwshd.m3u8?auth=testpub",
        ],
        "江西卫视": [
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=jxwshd",
        ],
        "湖南卫视": [
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=hnwshd",
            "http://107.150.60.122/live/hnwshd.m3u8",
        ],
        "广东卫视": [
            "http://38.75.136.137:98/gslb/dsdqpub/gdwshd.m3u8?auth=testpub",
        ],
        "山东教育卫视": [
            "https://test1.live.sdetv.com.cn/live/dianshizhibo/playlist.m3u8",
        ],
    }
    for ch in channels:
        backups = sat_manual_backups.get(ch["name"])
        if backups:
            ch["streams"] = list(dict.fromkeys(list(ch["streams"]) + backups))

    # 人工前置的那几条不走 final_channels，这里再按同一个判据扫一遍，
    # 免得「实测播不出来的人工备用线」从后门回到目录里。删空的频道整个去掉。
    if DROP_DEAD:
        survivors = []
        for ch in channels:
            alive = [s for s in ch["streams"] if usable(ch["name"], s)]
            if not alive and ch["name"] in REQUIRED_CHANNELS:
                # 必留名单不许删空：这一格的计数在 final_channels 那一处已经报过，这里只兜住
                # 「人工前置的那几条」这条后门，不再重复打印。
                pass
            else:
                ch["streams"] = alive
            if ch["streams"]:
                survivors.append(ch)
        channels = survivors

    # 新判据三半，各自的作用域写清楚（见 strict_filter 的 docstring）：
    #   ①必看台「删除不能正常播放的线路」：台内只留有正常画面证据的线；
    #     一条都没有的台**先原样留着**，交给结尾 [check] 判红点名——不许让它悄悄消失，也不许拿坏线冒充可用。
    #   ②卫视组（用户 10-23 17:5x）：无画面证据的整台删掉；活下来的台删掉本机连不通的线。
    #   ③其余各组「无可用源删除频道」：整台没有任何可播源的直接删掉；活下来的台内线路一个都不删。
    # 「待验证」分组从这一版起不再产生：以前是"没画面也留在目录里挪个组"，新判据是直接删。
    survivors, strict_stats = strict_filter(channels)
    channels = survivors
    print(f"[strict] 必看台 {strict_stats['must_total']} 台：删掉没有画面证据的线路 {strict_stats['pruned_lines']} 条；"
          f"仍无可播画面的 {len(strict_stats['must_no_picture'])} 台 -> " +
          ("、".join(strict_stats['must_no_picture']) or "无"))
    if strict_stats['stale_only']:
        # 只报数、不删线：用户 10-22 23:5x「已实证有画面的线路不删除」——旧场次的实证照样算实证。
        print(f"[strict] 其中 {len(strict_stats['stale_only'])} 台靠的是旧场次的画面实证（本轮没复核到），"
              f"要靠模拟器截图定音：{('、'.join(strict_stats['stale_only']))}")
    sat_names = sorted({name for name, _, _ in strict_stats['sat_pruned']})
    print(f"[strict] 卫视组删掉本机连不通的线 {len(strict_stats['sat_pruned'])} 条（{len(sat_names)} 台），"
          f"并按地址送进黑名单防设备端更新捞回")
    print(f"[strict] 其余各组无可用源、整台删除 {strict_stats['dropped_no_source']} 台（活下来的台内线路一条没动）")
    print(f"[strict] 特色频道整组删除（用户 10-23 20:0x）{strict_stats['dropped_featured']} 台")
    must_no_picture = strict_stats['must_no_picture']

    channels, slow_moved = move_slow_lines_back(channels, HEALTH, device_proof)
    slow_names = sorted({name for name, _ in slow_moved})
    if slow_moved:
        print(f"[slow] 甲（用户 10-23 20:5x 点档）：首分片 >10 秒的慢线挪到台内最后 "
              f"{len(slow_moved)} 条（{len(slow_names)} 台）：{'、'.join(slow_names)}")
    else:
        print(f"[slow] 甲（用户 10-23 20:5x 点档）：没有要挪的慢线")

    present = defaultdict(set)
    for ch in channels:
        present[ch["category"]].add(ch["subgroup"])

    categories = []
    for cat in CATEGORY_ORDER:
        subs = [s for s in SUBGROUP_ORDER[cat] if s in present[cat]]
        if subs:
            categories.append({"id": cat, "name": CATEGORY_NAMES[cat], "subgroups": subs})

    # 目录自己留下的线不能同时进黑名单：黑名单是给设备端「别再把它捞回来」用的，
    # 两边都从同一批读数算，但 usable() 是按「名字+地址」判的，身份档能把它救回来。
    kept = {stream_rules.canon(s) for ch in channels for s in ch["streams"]}
    blocked, exempted = blocked_lines(kept)
    # 卫视组刚删掉的那批「本机连不通」线：目录里没了，设备端自动更新还会从公开源一遍遍捞回来
    # （10-22 模拟器现读：一次开机后台更新就把缓存写成比内置目录多 561 条线）。
    # 和黑名单同一批交付，键仍排好序（这一份的顺序不许随证据文件的传法变）。
    for _, canon, verdict in strict_stats['sat_pruned']:
        if canon:
            blocked[canon] = verdict or "本机连不通"
    # 用户点档的「内容漂移」线：证据还是好证据（可取到分片＋旧复核台标相符），按证据算它不坏，
    # 所以进不了上面的自动黑名单；但电视上现在放的不是本台画面（用户 10-23 18:5x 现读）。
    # 目录侧由 dead_stream_prefixes 按台名摘；这里按地址拉黑，防设备端更新从 vbsky 捞回。
    user_flagged_blocks = {
        stream_rules.canon("http://ali-m-l.cztv.com/channels/lantian/channel21/1080p.m3u8"):
            "画面是别的台（用户现读）",
        stream_rules.canon("http://bztv.tvbus.cc:8081/cdnlive/cctv8.m3u8"):
            "时有时无（近 6 轮连不上＋蓝屏帧，用户 10-23 22:5x 目标摘除）",
        # 与上面 dead_stream_prefixes 里 CCTV-2 那一条同批：目录侧摘掉，设备端自动更新也不许再捞回来。
        stream_rules.canon("http://8.138.7.223/tv/api.php?id=cctv2"):
            "真机现读是「服务器超负载…微信扫码支持」告警页（用户 10-24 11:0x）",
        # 蓝田 ali-m-l 整族 13 个地址：白天重扫 13/13 判冻＋画面是电视购物广告卡（见上面
        # lantian_frozen 那段）。目录侧按主机路径摘，这里按地址拉黑，防设备端从公开列表再捞回来。
        **{stream_rules.canon(u): "白天重扫仍判冻，画面是带二维码的电视购物广告卡（用户 10-24 点档 #86 选项B）"
           for u in lantian_frozen},
    }
    blocked.update({k: v for k, v in user_flagged_blocks.items() if k not in kept})
    blocked = {k: blocked[k] for k in sorted(blocked)}
    catalog = {
        "schemaVersion": 1,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sourceNote": "频道与线路来自互联网公开 IPTV 列表，仅用于学习与个人测试；线路可能随时失效，应用会自动尝试备用线路。",
        "blockedLines": blocked,
        "categories": categories,
        "channels": channels,
    }
    if args.dry_run:
        # 只看数不落盘：退出码/统计照量，默认路径那份在库资产一个字都不动。
        print(f"\n[dry-run] 未落盘（本可写 {out_path}）")
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n[write] {out_path} ({out_path.stat().st_size / 1024:.0f} KiB)")
    print(f"[stats] {len(channels)} channels")
    reason_count = defaultdict(int)
    for v in blocked.values():
        reason_count[v] += 1
    print(f"[blocked] 随目录打进 APK 的地址黑名单 {len(blocked)} 条（{len(reason_count)} 种对端结论）：" +
          ", ".join(f"{k}:{v}" for k, v in sorted(reason_count.items(), key=lambda kv: -kv[1])) +
          f"；另有 {len(exempted)} 条按名字被 usable() 救回目录，不进黑名单")
    for cat in CATEGORY_ORDER + [PENDING_CAT]:
        counts = defaultdict(int)
        for ch in channels:
            if ch["category"] == cat:
                counts[ch["subgroup"]] += 1
        if counts:
            print(f"  {CATEGORY_NAMES[cat]}: {sum(counts.values())} -> " +
                  ", ".join(f"{k}:{v}" for k, v in counts.items()))

    required = REQUIRED_CHANNELS
    names = {ch["name"] for ch in channels}
    missing = [n for n in required if n not in names]
    print("[check] missing:", missing if missing else "none")
    # 新判据里"必须能正常播放"这一句要落在退出码上：台在目录里、但没有任何一条有画面的线路，
    # 也算不通过——那种台要去公开数据源补线，不能靠构建绿灯混过去。
    print("[check] no_picture:", must_no_picture if must_no_picture else "none")
    if missing or must_no_picture:
        return 1
    return 0


def ensure_sources():
    CACHE.mkdir(parents=True, exist_ok=True)
    for key, url in SOURCES.items():
        name = "pca-code.json" if key == "pca" else f"{key}.m3u"
        path = CACHE / name
        if path.exists() and path.stat().st_size > 100:
            continue
        print(f"[fetch] {key}")
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                if len(data) > 100:
                    path.write_bytes(data)
                    break
            except Exception as exc:
                print(f"  attempt {attempt + 1} failed: {exc}", file=sys.stderr)
                time.sleep(2)
        else:
            print(f"[warn] could not download {key}", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use only cached playlists")
    ap.add_argument("--max-streams", type=int, default=0,
                    help="每频道线路数上限，0 表示不限（默认）")
    ap.add_argument("--out", type=Path, default=None,
                    help="输出到指定路径，默认写 app/src/main/assets/channels.json")
    ap.add_argument("--health", type=Path, action="append", default=None,
                    help=f"分片级健康探测结果（jsonl），可重复给多份；默认那一份 {DEFAULT_HEALTH} 始终参与，"
                         f"额外给的按线路覆盖它")
    ap.add_argument("--device-proof", type=Path, default=None,
                    help="模拟器截图台账 jsonl：应用记到 line_<id> 且截图时仍在这台的那些线，必看台那一侧算实证")
    ap.add_argument("--identity", type=Path,
                    default=CACHE / "linecheck" / "identity_review.jsonl",
                    help="抽帧复核的台标身份结论（jsonl），优先于分片级结论决定线路顺序")
    ap.add_argument("--frames", type=Path, action="append", default=None,
                    help=f"抽帧得到的原生画面尺寸与占位页结论（jsonl），可重复给多份；"
                         f"默认那一份 {DEFAULT_FRAMES} 始终参与，额外给的按线路覆盖它")
    ap.add_argument("--freeze", type=Path, action="append", default=None,
                    help="freeze_scan.py 的长窗冻帧结论（jsonl），可重复给多份；"
                         "只有分钟级确认「源没在给新画面」的那一档会删线＋拉黑")
    ap.add_argument("--keep-dead", action="store_true",
                    help="保留「对端明确答复了坏／画面是别的台」的线路（默认删掉，删空的频道整个消失）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只算数不落盘：量统计与退出码时保护默认路径那份在库资产（给了 --out 也不写）")
    raise SystemExit(build(ap.parse_args()))
