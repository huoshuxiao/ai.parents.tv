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
# 人工点档的「降序」名册（只换序、不删线）：三轮回测三轮 0 帧＋35 秒长窗仍 0 帧的线挪到台内末位。
DEMOTE_FILE = BASE / "tools" / "demote_lines.json"

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
# 门禁各轮 `review.json` 里人工看图的台标结论（canonical url -> {logo, channel}）。
# 身份台账 `identity_review.jsonl` 是手抄的一份，抄漏的那几条（CCTV-11/14/15 的测试卡线就是这么掉的）
# 会让「有台标」这条真证据在构建侧读不到，所以这一份按轮次累加直接进构建（用户 10-24「保留有台标的线路」）。
REVIEW_LOGO = {}
# 三轮全线路回测的汇总（tools/retest3_summary.py 的产物）：canonical url -> {ok, fails, verdicts}。
# 用户 10-24 18:3x：央视·卫视、地方频道全线路回测 3 次，**三次都不通、又没有本台台标**的线才删。
RETEST = {}
RETEST_FAIL_LIMIT = 3
# 真正要被这条判据摘掉的地址（启动时算一次）：evidence() 与黑名单两侧读同一个集合。
RETEST_CUT = set()
# 央视·卫视「全线路回测 3 次」那一轮（用户 10-24 20:1x）：三帧齐全且三轮都没本台台标、或三轮都没帧
# 又没有台标实证 ⇒ 删线。文件是 tools/.cache/cctv3/summary.json（程序复核＋人工复核都在里面）。
# 这一格与 RETEST_CUT 并存：RETEST 是「三轮不通」那批，CCTV3 是「三轮无台标」那批，理由分开记。
CCTV3_CUT = {}
# 同一批 `review.json` 里的「这一帧是不是节目」那一半（只收 测试卡/广告/占位页/推广页 四类）。
# 为什么要接进来：门禁的 `placeholder` 字段只在**画面指纹命中已知图**时才写，人工看图新认出来的
# 那一屏购物广告不会落进 `line_frames.jsonl`——于是构建侧看到的还是「正常画面」，
# 「保留有台标的线路」这条判据在真跑数据上永远不触发（10-24 A/B 实测：链内证据下新旧口径都是 538 条）。
REVIEW_PICTURE = {}
# 「台标相符」结论没写台名、而这条地址又同时挂在两个以上台名下 ⇒ 不许替其中任何一个台说话。
# 由 build() 在候选目录拼齐后算出来（见 AMBIGUOUS_LOGO 赋值处），默认空集是给夹具用的。
AMBIGUOUS_LOGO = set()

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

# 档 3「本机连不通」可删的作用域。用户 10-23 那次点档只批了卫视组；
# 10-27 点档「3 从卫视组扩到地方/国际/港澳台」⇒ 加上这三组，央视名册与体育仍不动。
TIER3_PRUNE_CATS = {"local", "international", "hktw"}

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


def logo_verified(name, url):
    """这条地址有没有「本台台标相符」的人工看图结论——两个来源，同一把尺。

    ① 手抄的身份台账 `IDENTITY`（键是「台名|地址」）；
    ② 门禁各轮 `review.json` 里看图写下的 `logo`（键只有地址：行里带 channel 时必须对得上台名；
      老行没写 channel 的，只在这条地址**只挂在一个台名下**时才作数——挂在两个台名下的那种
      正是「改挂」那一类，A 台验过的台标不能替 B 台说话，见 `AMBIGUOUS_LOGO`）。
    以前只认①，结果 CCTV-11/14/15 三条测试卡线明明在 review.json 里被写成「台标相符」，
    构建侧却读不到 ⇒ 10-23 02:14 那条「有台标就算真出画面」从没为它们生效过，
    下一次重建会把这三条必看台的线摘掉（10-24 实测）。
    """
    canon = stream_rules.canon(url)
    if IDENTITY.get(f"{name}|{canon}") in IDENTITY_GOOD:
        return True
    row = REVIEW_LOGO.get(canon) or {}
    if not (row.get("logo") or "").startswith("台标相符"):
        return False
    owner = row.get("channel") or ""
    if owner:
        return owner == name
    return canon not in AMBIGUOUS_LOGO


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
    return logo_verified(name, url)


def logo_keeps_line(name, url):
    """「保留有台标的线路」（用户 10-24 指令）：本台台标验过的地址，抽到广告/占位页只降分、不摘线。

    为什么要有这一格：12:29 那一批 6 条卫视备份线抽到的整屏是购物广告，按旧口径它们会在下一次
    重建时被摘掉＋拉黑；14:05 复抽同一批地址，4 条已经在放本台节目（《父母爱情》《黑狐之风影》
    《武松》《军行天下》）。**广告是这台此刻在播的东西，不是这条地址坏了**——摘掉它等于少一条真源。

    三道边界，缺一条都不放行（夹具各盯一半）：
      ① 身份结论必须是「台标相符」——「画面是别的台」不是本台台标，走 NAME_RELATIVE 那套按名规则；
      ② 只管「画面不是节目内容」这一族（FRAME_PLACEHOLDER）。播放器解不开的容器、长窗冻帧、
         对端明确答复坏都在它前面 return，换了台标也还是播不出来／喂旧画面，不在这次放宽里；
      ③ 只影响**保留**这一侧。「算不算真出画面」（has_picture / 报告侧 card_is_picture）一行没动，
         所以广告帧照旧进 G2/G3 的红名单，只是这条线不会被摘掉。

    台标证据与 10-23 02:14 那一档同源（都走 logo_verified），一份结论不许抄成两处口径。
    """
    return logo_verified(name, url)


def no_own_logo(name, url):
    """本台名下抽到的帧**没有一枚**认得出本台台标 ⇒ 这一条线的画面不算本台画面。

    用户 10-24 点档 #96＝乙。触发形状：安多卫视唯一那条线（`liveout.xntv.tv/a65jur/96iln2.m3u8`）
    11 枚唯一帧看过 9 枚、16:44 现场复抽 3 枚，全是西宁市台（XNTV 色卡／「新闻综合」蓝黄旋标／
    《夏都零距离》＋「西宁12345」角标），7 轮人工看图都写「台标是别的台」——旧口径里"有画面"
    不问"是谁的画面"，于是父母打开「安多卫视」看到的是西宁市台，而这条线还占着画面分。

    四条边界，缺一条都不算（夹具各盯一半）：
      ① **没有任何观察就不发明**：台账与人工看图都没写过这一 (台名,地址) ⇒ 返回 False，照旧；
      ② **漂移不算**：只要有一枚观察说「台标相符」就返回 False——延边卫视/CCTV-7/CCTV-8 那三条
         都是"同一地址时而本台、时而别家或广告"，按 10-21 口径是时刻性现象，不摘；
      ③ **只有"点名是别的台"才算反证**：结论里要出现「别的台」（台账那枚 verdict 就是
         `画面是别的台`，人工那批写的是「台标是别的台（画面写着 XNTV…）」）。「台标看不到」不算——
         河北卫视那条 `event.pull.hebtv.com/live/live101.m3u8` 六轮都写「台标看不到…无法归到河北台名下」，
         那是"这一帧没台标"（直播外景常常真没有角标），不是"这一帧是别家台的台标"；按字面把它算成反证，
         第一次跑就多切掉 河北卫视／河北 4K 两台（10-24 实测 306 台，比预期少 2 台），已收窄掉。
      ④ **不借证**：人工那一枚必须归属对得上本台名才作观察（无归属且地址挂在多个台名下的一律不算），
         与 `logo_verified` 同一套归属尺，不许两处口径不一。
    只管**判定层**（算不算画面），不进 `blocked_lines()`：那是"名字与地址的配对"问题，
    地址本身没坏（同一枚地址在 西宁新闻综合／井研综合／营山电视台 名下是真源），拉黑会把真源一起挡掉。
    """
    canon = stream_rules.canon(url)
    observations = []
    ident = IDENTITY.get(f"{name}|{canon}")
    if ident:
        observations.append(("台账", ident))
    row = REVIEW_LOGO.get(canon) or {}
    logo = (row.get("logo") or "").strip()
    if logo:
        owner = row.get("channel") or ""
        if owner == name or (not owner and canon not in AMBIGUOUS_LOGO):
            observations.append(("人工", logo))
    return logo_contradicts_station(observations)


def logo_contradicts_station(observations):
    """纯函数那一半：给一批「这一 (台名,地址) 的台标结论」，判有没有一枚认得出本台台标。

    抽出来是因为**两层都要用同一把尺**：构建侧 `no_own_logo()` 与门禁报告侧 G4
    （`prepack_report` 的 `clean_frame_ok`）各自拼自己的观察列表，判定只能有一处。
    规则与上面 ①②③ 完全相同：没观察⇒不发明（False）；有一枚「台标相符」⇒漂移（False）；
    只有点名「别的台」的反证才 True；"台标看不到"不算反证。
    """
    for_ours, against = [], []
    for _, text in observations:
        if text.startswith("台标相符") or text in IDENTITY_GOOD:
            for_ours.append(text)
        elif "别的台" in text:
            against.append(text)
    return bool(against) and not for_ours


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
    if canon in RETEST_CUT:
        # 三轮回测都不通、且没有本台台标（用户 10-24 18:3x）。这一格走在身份判定前面：
        # 有「台标相符」的地址根本进不了这个集合（compute_retest_cuts 那把尺已经排掉）。
        return (4, 0)
    if canon in CCTV3_CUT:
        # 央视·卫视三轮回测：三轮都无本台台标（人工复核过），三轮都没帧且无台标实证，
        # 或任一轮抽出「服务器超负载…微信扫码」告警页（用户 10-26 真机点档）。
        # 理由随地址进黑名单，设备端自动更新不会把它们捞回来。
        return (4, 0)
    verdict = IDENTITY.get(key)
    card = FRAME_PLACEHOLDER.get(canon)
    human_card = "" if card else review_card(name, canon)
    if card or human_card:
        # 抽到帧 ≠ 抽到节目：这一条给的是测试卡/带二维码的推广页，和「对端回了 404」同级。
        # 这一格走在身份判定前面（用户 10-22 指令「占位页赢」）：同一地址既被复核过台标、
        # 又被抽到占位页时，肉眼看到的这一帧更硬——按名字留下的那 32 枚是另一回事（usable() 管）。
        # 一处例外（用户 10-22 23:5x「已实证有画面的线路不删除」＋10-23 02:14「有台标就算真出画面」）：
        # CARD_IS_PICTURE_CHANNELS 那几台如果这一帧是**它自己的测试卡**，只降分不删线——10-23 02:01
        # CCTV-11 那条 GSLB 端出 1080/50I 色条卡（卡左上角写着「CCTV 11 戏曲」），18 分钟前它刚抽到
        # 正常画面、模拟器也真播过；那是"此刻没节目"的时刻性状态，不是稳定坏（10-21 口径）。
        # 只放宽 has_picture 不够：这一档在 strict_filter 之前就把线摘了，台会整个消失。
        # 推广页那一类不进这个门，别组也不进（夹具 41/42/43 格各盯一半）。
        if card_counts_as_picture(name, url, card or human_card):
            return (1, 0)
        if logo_keeps_line(name, url):
            # 「保留有台标的线路」（用户 10-24）：本台台标验过 ⇒ 这一帧是广告只说明此刻在放广告，
            # 降到档 1（不占第一）但线留着；blocked_lines() 那边由 kept 自动把它摘出黑名单。
            return (1, 0)
        if human_card:
            # 只有人工看图那一格说「这不是节目」时也只降分：指纹是钉死的稳定坏，人工那一枚
            # 是一次观测——CCTV-6 那条 74.91.26.218 的药品广告帧在 7 份 review.json 里 md5 全等于
            # 456f0b637421（10-23 03:26 抽的那一张，03:40 被证据搬运抄了 7 遍），不是复现了 7 次。
            # 按 10-21 口径「稳定坏才删线，时刻性坏只降分」，摘一条必看台的线要等它自己变成指纹。
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


PINNED = set()   # 用户点档「钉回目录」的地址（归一键），每轮构建从名册重载；夹具直接改这一格。


def pin_specs(specs=None):
    """名册里 `pin: true` 那一格＝用户点档「钉回目录」的地址（与 `apply_demotions` 同一份名册）。

    用户 10-27 #149 点档「甲」：`Al Jazeera 半岛电视台` 唯一那条线在构建收得到的 11 轮 health
    台账里 10 轮「可取到分片」、只有 10-27 15:45 那一轮回了「清单404」⇒ 按 10-21 口径算时刻性坏，
    线留着、只降分，台不许整台消失。
    """
    if specs is None:
        specs = json.loads(DEMOTE_FILE.read_text(encoding="utf-8")).get("lines") or []
    return {stream_rules.canon(s["url"]) for s in specs if s.get("pin") and s.get("url")}


def pin_rescues(name, url):
    """这一条「钉回」能救回什么——只挡**按 health 判词**发起的删除（时刻性/网络性那一类）。

    钉不动的那几类都是**稳定坏**，与这一轮的回话无关：长窗冻帧、三轮回测定案、央视·卫视三轮
    无台标定案、播放器解不开的容器、抽帧看到的占位页、台标认出来是别的台。人工名册不许把这几类
    捞回来（要捞得先改判据，那是要单独点档的事）。
    """
    canon = stream_rules.canon(url)
    if canon not in PINNED:
        return False
    if FROZEN.get(canon) or canon in RETEST_CUT or canon in CCTV3_CUT or FRAME_PLACEHOLDER.get(canon):
        return False
    row = HEALTH.get(canon)
    if row and row.get("verdict") in PLAYER_UNDEMUXABLE:
        return False
    verdict = IDENTITY.get(f"{name}|{canon}")
    if verdict and verdict not in IDENTITY_GOOD:
        return False
    return True


def usable(name, url):
    """这条线路还配留在目录里吗。判据只有这一处，排序和删除都从这里过。"""
    return (not DROP_DEAD) or pin_rescues(name, url) or evidence(url, name)[0] < BAD_TIER


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
    if no_own_logo(name, url):
        # 本台名下没有一枚帧认得出本台台标 ⇒ 那是别人家的节目，不算这一轮看到了本台画面。
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
    「画面是别人家的节目」也不算（`no_own_logo`，用户 10-24 #96 乙）——本台名下没有一枚帧认得出本台台标，
    这条线就不替本台占画面分。
    """
    canon = stream_rules.canon(url)
    row = HEALTH.get(canon)
    if row and row.get("verdict") in PLAYER_UNDEMUXABLE:
        return False
    if FROZEN.get(canon):
        # 长窗冻帧：抽到过帧不等于抽到节目，同一种画面重复分钟级也是"帧"。
        return False
    if no_own_logo(name, url):
        # 「画面是别的台」在这一把尺上同样不算本台画面（用户 10-24 #96 乙）：
        # 安多卫视那条线抽到 11 枚帧、9 枚看过全是西宁市台，旧口径它照样算"有画面"。
        return False
    # 用户 10-23 02:14 的口径：**「CCTV-4K、CCTV 8K 有台标就算真出画面，算正常频道线路」**
    # ⇒ 台标复核过「相符」的线走在这把尺最前面，即使那一帧是频道自己的测试卡/色条卡。
    # 凌晨的 CCTV-4K／CCTV-8K／CCTV-11 端的就是这种卡（画面左上角写着「CCTV 4K 超高清」「CCTV 11 戏曲」），
    # 按旧口径它会被 FRAME_PLACEHOLDER 一票否决，等于把一条真源判成不能用。
    card = FRAME_PLACEHOLDER.get(canon) or ""
    if logo_verified(name, url):
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
    for key in RETEST_CUT:
        out.setdefault(key, "三轮回测都不通且无台标")
    for key, reason in CCTV3_CUT.items():
        # 央视·卫视那三轮「无台标」回测的删除清单也要跟着拉黑：设备端「立即更新线路」
        # 会从公开列表把这些地址再捞一次，只在目录里摘掉挡不住它们回来。
        out.setdefault(key, reason)
    for _name, url, _size in SD_DROPPED:
        # 「未来新增的线路也不添加标清线路」这句指令的另一半在这里：公开列表明天还会把这些
        # 标清地址换个台名再送一遍，设备端「立即更新线路」只认黑名单，不进黑名单就会回来。
        out.setdefault(stream_rules.canon(url),
                       "标清线路（实测尺寸不到高清档或清单写着「标清」，用户 10-25 指令不进目录）")
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


def line_size(url):
    """这条线实测/自报到的画面尺寸（宽, 高）；两个来源都没有就返回 None（当作未知）。

    与 [resolution_rank] 同一批来源，只是不折成面积：判「标清」要用「高/宽各是多少」，
    光有面积会把 1536×864 与 1280×1040 这种算成同一档。
    """
    key = stream_rules.canon(url)
    size = FRAME_RES.get(key) or (HEALTH.get(key) or {}).get("resolution")
    if not size:
        return None
    try:
        w, h = int(size[0]), int(size[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return (w, h) if w > 0 and h > 0 else None


# 与设备端选线面板那一把尺完全对齐（PlayerScreen.qualityText：高 ≥700 或宽 ≥1200 才叫「高清」，
# 否则「标清」）。两处口径不一致时，用户在屏幕上看到的档就和构建里删的档不是同一批线。
SD_MAX_HEIGHT = 699
SD_MIN_WIDTH = 1200
# 「标清」字样只认清单标题里的独立一档（CCTV-2 标清 / XX卫视-标清），
# 尺寸没读到的线只在这一条证据下才摘——URL 里的 480/576 可能是端口或节目号，拿来判档会误杀。
SD_TITLE = re.compile(r"(?:^|[\s\-_/·(（\[])(?:标清|SD)(?:$|[\s\-_/·)）\]])", re.IGNORECASE)


def is_sd_line(url, raw="", manual=False):
    """这条线是不是「标清」。用户 10-25 指令：「全频道删除标清线路，未来新增的线路也不添加标清线路」。

    尺寸未知、标题里也没有「标清」档名的 ⇒ 不判标清（不摘）。宁可留一条未知，
    也不从 URL 字样猜档：10-24 现读 `live.hd.hdbs.cn` 那种域名带 hd 的假信号，反过来同样成立。
    """
    size = line_size(url)
    if size:
        w, h = size
        return h <= SD_MAX_HEIGHT and w < SD_MIN_WIDTH
    if manual:
        # 用户点名加的手工备份线：第三方列表给的那行原文可能挂着「标清」档名，
        # 而本机实测是 1080p（10-28 黑龙江 idclive 就是这么被误摘的）。没实测才轮到档名，
        # 手工线在没实测时不按档名摘。
        return False
    return bool(SD_TITLE.search(raw or ""))


# ── 清晰度这一档（用户 10-25 指令「全频道删除标清线路，未来新增的线路也不添加标清线路，
#    删除没有线路的频道」＋「央视·卫视、地方频道，线路按清晰度由高到低排序」）──────
DROP_SD = True        # --keep-sd 把它关掉；关掉的试算里标清线原样留着，用于对表
MANUAL_LINES = set()  # 手工备份线（manual_backups/sat_manual_backups 追加的）：判标清时不看第三方标题档名
STREAM_RAW = {}       # 规范地址 -> 公开列表原文那一行（尺寸没读到的线靠它判「标清」档名）
SD_DROPPED = []       # 本轮摘掉的标清线（台名, 地址, 尺寸/原文），[definition] 那一笔报数用
SD_EXEMPT = []        # 必留名单里「整台都是标清」所以原样保留的那几条
# 这两组按清晰度排线路；其余组仍按证据档优先（用户这一句只点名央视·卫视与地方频道）。
# 用户 2026-10-30：「写在 AGENTS.md 中的线路排序规则…没有按要求实现」——AGENTS 那条是
# 全目录口径（清晰度由高到低，同清晰度抖动小的排前），不再是只点名央视·卫视与地方频道。
RES_FIRST_CATS = ("cctv", "local", "sports", "hktw", "international", "tencent")


def resolution_rank(url):
    """这条线实测/自报到的**清晰度档**（0 未知/1 准高清/2 全高清/3 4K），用来把清晰度高的线排前面。

    两个来源，都拿到才算数，不从 URL 里的 1080/hd/蓝光 这类字样猜——实测那些字样既出现在
    主机名也出现在节目号里（`live.hd.hdbs.cn` 就是域名带 hd），拿来排序会把标错名的坏线顶到前面。
      1) prepack 抽到的原生尺寸（真画面，最可信）；
      2) 清单自报的 `#EXT-X-STREAM-INF:…RESOLUTION=宽x高`（health_probe 记的）。

    为什么折成档而不是比面积：VLC 报 1920×1090、ffprobe 报 1920×1080，差的 10 行是填充，
    在屏幕上根本看不出差别；按面积排会让 1090 那一条永远压在 1080 前面，每换一次抽帧尺就
    换一条首条线（10-30 那场重建 45 台首条变化里就有这种纯填充差异）。同档内谁先由
    `jitter_key`（抖动小）→ `slow_penalty`（慢线）→ 证据档决定，正是 AGENTS 里
    「相同清晰度抖动小的排前」那一句。宽度分档与 `tools/.cache/reorder_asset.py` 同一口径。
    """
    size = line_size(url)
    if not size:
        return 0
    w = size[0]
    if w >= 3840:
        return 3
    if w >= 1920:
        return 2
    if w >= 1280:
        return 1
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

# 分组先后＝首页 tab 的先后，同一个台号（数字键）也按这个顺序排。
# 用户 10-25 00:0x：「频道分组顺序，央视·卫视、体育频道、地方频道、港澳台频道、国际频道」
# ⇒ sports 提到 local 前（19:5x 那格）之后再越过 hktw，港澳台落到地方之后。
# featured「特色频道」现读 0 台（10-23 20:3x 整组删台），放在 hktw 前只影响它自己那条 tab 的位置。
CATEGORY_ORDER = ["cctv", "sports", "local", "hktw", "featured", "international", "tencent"]
CATEGORY_NAMES = {
    "cctv": "央视·卫视",
    "local": "地方频道",
    "hktw": "港澳台",
    "featured": "特色频道",
    "sports": "体育频道",
    "international": "国际频道",
    # 用户 10-25：「加腾讯体育频道分组在国际频道后」。这个组由设备端「手动挂」临时线填充，
    # 构建产物里一般没有台，但分类定义要一直在，否则设置页挂上的场次无处可去。
    "tencent": "腾讯体育",
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
    # cctv 的顺序按用户 10-30 那句「频道排序，央视、卫视4K、卫视」：4K 排在卫视**前**。
    # 这里以前写的是 央视→卫视→4K，与在库目录不一致 ⇒ 下一次重建会把 4K 那台甩到卫视后面去。
    "cctv": ["央视", "4K 超高清", "卫视"],
    "local": PROVINCE_ORDER + ["其他"],
    "hktw": ["香港", "台湾", "澳门", "海外华人"],
    "featured": ["影视轮播", "NewTV", "CHC", "少儿"],
    "sports": ["国内体育", "咪咕直播", "港台体育"],
    "international": ["新闻", "纪实", "综合"],
    "tencent": ["腾讯体育"],
}

# 整台换组：台名 → (category, subgroup)。覆盖点在 builder.add（所有公开源/手工源唯一的收口点），
# 所以同一条台不管从哪份列表进来都落进同一个键，不会分裂成两个台。
# ⚠️换组会改掉频道 id：id = sha1(category|subgroup|台名) 前 10 位（见 final_channels），
# 设备端收藏、`line_<id>` 记住的线路、last_channel 三处都挂在 id 上。落这批前 192.168.1.4 现读：
# favorites 键不存在（133 个键里 0 枚）、这台记住的线路就是它自己的线 1、last_channel 是别的台
# ⇒ 这台电视上换 id 的实际损失量出来是 0。以后要动别的台，先按这三处重新量一遍。
# 目标组必须已在 SUBGROUP_ORDER 里声明——首页分组那一段（present[cat] ∩ SUBGROUP_ORDER[cat]）
# 只认声明过的 subgroup，写错一个字的后果是这台在界面上整个消失，所以这里直接拒绝起构建。
CHANNEL_GROUP_OVERRIDE = {
    "山东教育卫视": {
        "category": "local",
        "subgroup": "山东",
        "why": "用户 10-27 原话「山东教育卫视 频道 页放 地方频道」：整台从 央视·卫视 挪到 地方频道·山东，"
               "线路一条不删、不进黑名单",
    },
}


def group_overridden(name):
    """这台是不是被「整台换组」名单点过名——表键是 norm_key 归一后的，比较也必须走同一把尺。"""
    return norm_key(name) in CHANNEL_GROUP_OVERRIDE


def group_move_coverage(channels):
    """换组名单的「实效力」两列：真落进目录的台、名单点了名却没进目录的台。

    抽成纯函数是为了让「命中 0」那一格有夹具可咬（内联在 build() 里就只能在几十分钟的真跑里看）。
    """
    moved = [ch for ch in channels if group_overridden(ch["name"])]
    missing = sorted(set(CHANNEL_GROUP_OVERRIDE) - {norm_key(ch["name"]) for ch in moved})
    return moved, missing


def check_group_override():
    """覆盖表自检：目标 (category, subgroup) 必须是声明过的真实分组。"""
    for name, spec in CHANNEL_GROUP_OVERRIDE.items():
        cat, sub = spec["category"], spec["subgroup"]
        if sub not in SUBGROUP_ORDER.get(cat, []):
            raise SystemExit(
                f"[拒绝] 整台换组 {name}→{cat}/{sub}：这个组没在 SUBGROUP_ORDER 里声明，"
                f"落盘后首页看不见这台。要么补声明，要么改目标组。")
        print(f"[换组] {name} → {cat}/{sub}（id 会按新组重算；{spec['why']}）")


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
    # 18 给 CCTV-5+，而遥控器那串数字念的就是目录里的次序（app/src/main/java/com/sun/tv/parents/data/ChannelNumbers.kt）。
    # 两边各排各的会出现「屏幕上第 6 个台、台号却是 18」，所以目录侧同批跟一条规则。
    "5+": "CCTV-5+ 体育赛事",
    "4K": "CCTV-4K 超高清",
}
CCTV_ORDER = {v: i for i, v in enumerate(CCTV_NAMES.values())}
CCTV_ORDER.update({
    "CGTN": 90, "CGTN 纪录": 91, "CGTN 法语": 92,
    "CGTN 西语": 93, "CGTN 阿语": 94, "CGTN 俄语": 95,
})
# 用户 10-24 18:3x：CCTV-8K 要排在 CCTV-4K **后面**（原先它在「4K 超高清」子分组里，
# 隔着一整片卫视，找台时跳得太远）。用半步值插进去，别的台号一个都不动。
CCTV_ORDER["CCTV-8K 超高清"] = CCTV_ORDER["CCTV-4K 超高清"] + 0.5

SAT_ORDER = [
    # 用户 2026-10-30：「地方卫视组内排序，北京、辽宁排前」——辽宁原来排第 8，现挪到北京之后。
    "北京卫视", "辽宁卫视", "东方卫视", "天津卫视", "重庆卫视", "河北卫视", "山西卫视",
    "内蒙古卫视", "吉林卫视", "黑龙江卫视", "江苏卫视",
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
        name_key = norm_key(key_name or display)
        override = CHANNEL_GROUP_OVERRIDE.get(name_key)
        if override:
            category, subgroup = override["category"], override["subgroup"]
        key = (category, subgroup, name_key)
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
                # 清单原文那一行（台名后缀常写着「标清/高清/蓝光」）。尺寸没读到的线靠它判档，
                # 见 [is_sd_line]——摘线那一棒只剩地址，所以原文按规范地址存一份。
                "raw": raw or "",
                "seq": len(ch["streams"]),
            })
            STREAM_RAW[stream_rules.canon(url)] = raw or ""

    def final_channels(self):
        rows = []
        for (category, subgroup, _), ch in self.channels.items():
            # 央视·卫视、地方频道这两组按「清晰度由高到低」（用户 10-25 指令），
            # 别的组仍按「证据档优先」。两组都用一条底线：判坏的线（档 4）不许顶到前面——
            # 清晰度再高，连不上就是把「正在连接」摆到第 1 条。
            res_first = category in RES_FIRST_CATS
            streams = sorted(
                ch["streams"],
                key=lambda s: (
                    # 1) 有没有实测证据说这条能出画面（可用的排最前）
                    #    res_first 那一支把「坏/不坏」折成两档，剩下的次序交给分辨率
                    (1 if evidence(s["url"], ch["name"])[0] >= 4 else 0) if res_first
                    else evidence(s["url"], ch["name"])[0],
                    # 2) 清晰度（实测尺寸），高的在前
                    -resolution_rank(s["url"]) if res_first else 0,
                    # 2b) 同一清晰度档内，慢线排到快线之后（不跨档，见 slow_penalty）
                    slow_penalty(s["url"], ch["name"]) if res_first else 0,
                    # 3) 同一档里 IPv6 优先（用户 2026-10-22 指令；家里网络有 v6 出口，
                    #    而本机没有，所以 v6 线路只可能是「本机连不通」，不能因此排到可用线后面）
                    0 if s["ipv6"] else 1,
                    # 4) 清单自报的分辨率，高的在前
                    -resolution_rank(s["url"]),
                    # 5) 细排：分片快慢、地区限制、来源、原始次序
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


def slow_penalty(url, name):
    """首分片 >10 秒的线在**同一清晰度档**里排到快线之后（用户 10-25 18:5x 点档「乙：清晰度优先」）。

    只做同档内的次序微调，绝不跨分辨率——1920×1090 的慢线仍然排在 1280 快线前面；
    设备播过的那条不压（「已播过的线路绝不自动换线」）。
    """
    canon = stream_rules.canon(url)
    ms = (HEALTH.get(canon) or {}).get("elapsed_ms")
    if ms is None or ms <= 10000:
        return 0
    return 0 if DEVICE_PROOF.get(canon) == name else 1


def res_first_key(name):
    """央视·卫视、地方频道组内的次序尺子：清晰度由高到低，判坏的线（档 4）一律沉底。

    用户 10-25 的原话是「线路按清晰度由高到低排序」，这里比字面软一点点：
    实测连不上/解不开的线不许因为像素多就顶到第 1 条——那正是他一直在说的「正在连接」。
    慢线（`slow_penalty`）只在同一清晰度档内往后排，不跨档。

    用户 10-28 在 AGENTS.md 补的第二句「相同清晰度**抖动小的排前**」就落在 `jitter_key` 那一格上。
    """
    def key(url):
        ev = evidence(url, name)
        return (1 if ev[0] >= 4 else 0, -resolution_rank(url), *jitter_key(url),
                slow_penalty(url, name), ev[0], ev)
    return key


# 实测余量（同一时间窗内 12 轮交替采样、每次取倒数第二片、用该片 EXTINF 算内容码率，记「下载/码率」）。
# 只收真跑过的那几条；**没测过的线不进这张表**，也不许拿猜测补位。
LINE_JITTER = {
    # 东方卫视：10-28 13:11–13:14 交替采样（tools/.cache/jitter_20261028.json）
    "http://36.110.54.118:866/hls/28/index.m3u8": {"median": 1.17, "min": 0.68, "rounds": 12},
    "http://27.129.145.110:4433/hls/40/index.m3u8": {"median": 2.10, "min": 0.89, "rounds": 12},
}


def jitter_key(url):
    """同清晰度档内的次序：实测过且过 1.2 倍闸的排最前，没测过的居中，实测贴着/低于码率的沉底。

    为什么要把「没测过」单列一档：折进任何一边都会骗人——折到前面等于拿未知当合格，
    折到后面等于拿未知当坏线。这一格只排**同清晰度**内的次序，跨档仍由 `resolution_rank` 说了算。
    """
    j = LINE_JITTER.get(stream_rules.canon(url))
    if not j:
        return (1, 0.0)
    return (0, -j["median"]) if j["median"] >= 1.2 else (2, -j["median"])


def remount_by_logo(channels, moves):
    """按台标把挂错频道的卫视线改挂到台标对应的那一台（用户 10-25「卫视频道，台标与频道不一致的，
    根据台标移入对应卫视频道」）。

    证据文件是一份人工复核过的清单（`--wshi-moves`），每条写 from/to/证据。这里只做搬运，不做判定：
    判定在 `tools/.cache/wshi_mismatch.py`（跨模板打分）＋人工看单张角标，两道都过才写进清单。
    三条硬规矩：
      ①from 与 to 都必须在现读目录里存在，且这条地址确实挂在 from 名下——否则 `[拒绝]` 并跳过，
        不凭一条结论凭空造台名；
      ②只允许卫视组内部互挂（`subgroup == 卫视`），跨组改挂会把分组顺序那两条指令搅乱；
      ③改挂后的地址要落进目录顶层的 `lineMoves`，设备端合并公开列表时按它挡回错误台名
        （公开列表明天还会按原名把这条地址再送一遍——与 blockedLines 同一个道理）。
    """
    by_name = {ch["name"]: ch for ch in channels}
    done = {}
    for canon, spec in (moves or {}).items():
        src, dst = spec.get("from"), spec.get("to")
        ch_src, ch_dst = by_name.get(src), by_name.get(dst)
        if not ch_src or not ch_dst:
            print(f"[拒绝] 改挂 {src}→{dst}：目录里现读没有这个台名，跳过 {canon}")
            continue
        if ch_src.get("subgroup") != "卫视" or ch_dst.get("subgroup") != "卫视":
            # 默认仍只允许卫视组内互挂（②那条硬规矩不变）。要跨组得在清单里**逐条显式**写
            # `"cross_group": true`——第一枚是用户 10-27 指令「东方卫视 线1，移到 地方频道」：
            # 那条线画面左上角是「新纪实 LIVE24」、清单 6/6 分片都是 /material/<存档日期>/ 存好的素材，
            # 既不是东方卫视此刻的节目、地方组里又没有现成落点（现读 local 159 台 0 命中），
            # 所以只能新建一台「新纪实」再跨组搬过去。放行条件写成"清单点名了才放行"，不许从台名字典猜。
            if not spec.get("cross_group"):
                print(f"[拒绝] 改挂 {src}→{dst}：不是卫视组内部（跨组要在清单里显式写 cross_group），跳过 {canon}")
                continue
            print(f"[logo_move] 跨组改挂被允许：{src}→{dst}（清单显式 cross_group，用户 10-27 指令）")
        hit = next((s for s in ch_src["streams"] if stream_rules.canon(s) == canon), None)
        if not hit:
            print(f"[拒绝] {src} 名下现读没有这条地址，跳过 {canon}")
            continue
        ch_src["streams"] = [s for s in ch_src["streams"] if stream_rules.canon(s) != canon]
        if not any(stream_rules.canon(s) == canon for s in ch_dst["streams"]):
            ch_dst["streams"] = list(ch_dst["streams"]) + [hit]
        done[canon] = {"from": src, "to": dst, "evidence": spec.get("evidence", "")}
    if done:
        print(f"[logo_move] 按台标改挂 {len(done)} 条：" +
              "、".join(f'{v["from"]}→{v["to"]}' for v in done.values()))
    cross_check_relabels(done)
    return done


def cross_check_relabels(moves):
    """改挂这件事现在有两名册：随目录走的 `lineMoves` 与设备端手抄的 `RelabeledStreams`（Kotlin）。
    两份各写各的就会打架——构建按一份搬完，设备端合并公开列表时按另一份摘掉，那条线两头都没有。
    这里只报 `[check]` 不选边（与 #94 那一枚同一处置）：真要对表，把 Kotlin 那 4 条收进 lineMoves。
    """
    src_kt = BASE / "app/src/main/java/com/sun/tv/parents/data/StreamUrl.kt"
    if not src_kt.exists():
        # 读不到不许静默跳过：10-30 改包名（com.parentstv.live → com.sun.tv.parents）之后
        # 这一行还在读老路径，名册对表整格变成空转，而输出里一个字都没有。
        print(f"[check] 改挂名册对表：读不到 {src_kt.relative_to(BASE)} ⇒ 这一格没跑（不是「无冲突」）")
        return
    text = src_kt.read_text(encoding="utf-8")
    start = text.find("private val targets")
    if start < 0:
        print("[check] 改挂名册对表：没在 StreamUrl.kt 里找到 RelabeledStreams 的 targets，跳过")
        return
    table = text[start:start + text[start:].index("\n    )")]
    kt = {stream_rules.canon(url): owner for url, owner in
          re.findall(r'"([^"]+)"\s+to\s+"([^"]+)"', table)}
    clash = [(c, kt[c], v["to"]) for c, v in moves.items() if c in kt and kt[c] != v["to"]]
    print(f"[check] 改挂名册对表：lineMoves {len(moves)} 条、设备端 RelabeledStreams {len(kt)} 条"
          + (f"，同一地址两个主人 {len(clash)} 条：" +
             "、".join(f"{c}（设备端{a} / 目录{b}）" for c, a, b in clash) if clash else "，无冲突"))



def drop_sd_lines(channels):
    """摘掉全目录的标清线，并把央视·卫视、地方频道按清晰度重排（用户 10-25 两句指令）。

    为什么设在备用线追加之后、strict_filter 之前：`manual_backups`/`sat_manual_backups`
    是「追加」语义，在 final_channels 那一站之后才进来，闸门放在它们上游就管不住手工线；
    而摘线这件事只留这一处出口，避免「校验器放行、下游又判一遍」两套口径。
    两条处置：
      ①摘空的台整台删掉（同句指令「删除没有线路的频道」），当场报是哪几台；
      ②必留名单（REQUIRED_CHANNELS：必看 20 台含辽宁体育）里的台不许被摘空——
        辽宁体育只有一条线，摘完「辽宁体育必须能看」就没了。这种台原样保留并打进 [必留豁免]。
    返回（留下的台, 摘空的台名）。
    """
    dropped_channels = []
    survivors = []
    for row in channels:
        keep, sd = [], []
        for url in row["streams"]:
            raw = STREAM_RAW.get(stream_rules.canon(url), "")
            (sd if is_sd_line(url, raw, manual=url in MANUAL_LINES) else keep).append(url)
        if sd and not keep and row["name"] in REQUIRED_CHANNELS:
            print(f"[definition][必留豁免] {row['name']} 全部 {len(sd)} 条都判标清 ⇒ 整台原样保留"
                  f"（这台在必留名单里，不许被摘空）")
            SD_EXEMPT.extend((row["name"], u) for u in sd)
            keep, sd = list(row["streams"]), []
        SD_DROPPED.extend((row["name"], u, line_size(u) or STREAM_RAW.get(stream_rules.canon(u), "")[:28])
                          for u in sd)
        if not keep:
            dropped_channels.append(row["name"])
            continue
        row = dict(row)
        row["streams"] = keep
        if row["category"] in RES_FIRST_CATS:
            row["streams"] = sorted(keep, key=res_first_key(row["name"]))
        survivors.append(row)
    return survivors, dropped_channels


def enforce_sat_4k(channels):
    """卫视 4K 频道只许留 3840 宽的线（AGENTS「卫视4K频道线路分辨率3840x2160」）。

    用户 2026-10-30 点档原话：「将卫视4K频道非3840线路移入对应的卫视频道，要与原有线路去重」。
    这一条必须写进构建：10-30 那次重建把 `东方卫视 4K` 用一条 1920×1090 的线又造了出来
    （用户已裁决这个 4K 台该消失），手工改资产挡不住下一次重建。
    四条边界：
      ①只动 `subgroup == "4K 超高清"` 且台名以 `4K` 结尾的台；`CCTV-4K 超高清`（央视组）不在此列；
      ②对应基础台必须存在且是「卫视」组——找不到就原样留着并点名，不凭一条结论造台、也不静默删线；
      ③尺寸未知的线不搬（判不了是不是 3840），留在 4K 台里，宁可留着也不误判；
      ④搬过去按 canon 去重，再按 `res_first_key` 重排基础台（搬进来的线也遵守清晰度次序）。
    返回（留下的台, 搬运记录 (4K台, 基础台, 待搬, 实搬), 搬空被删的台名）。
    """
    by_name = {ch["name"]: ch for ch in channels}
    moved, dropped, survivors = [], [], []
    for ch in channels:
        name = ch["name"]
        m = re.search(r"\s*4K$", name)
        if ch.get("subgroup") != "4K 超高清" or not m:
            survivors.append(ch)
            continue
        base = by_name.get(name[:m.start()].strip())
        if base is None or base.get("subgroup") != "卫视":
            print(f"[4k] {name}：找不到对应的卫视频道 ⇒ 原样保留 {len(ch['streams'])} 条（不搬、不删）")
            survivors.append(ch)
            continue
        stay, move = [], []
        for url in ch["streams"]:
            size = line_size(url)
            (move if size and size[0] < 3840 else stay).append(url)
        if move:
            seen = {stream_rules.canon(u) for u in base["streams"]}
            added = []
            for url in move:
                key = stream_rules.canon(url)
                if key in seen:
                    continue
                seen.add(key)
                base["streams"].append(url)
                added.append(url)
            if added:
                base["streams"] = sorted(base["streams"], key=res_first_key(base["name"]))
            moved.append((name, base["name"], len(move), len(added)))
        if stay:
            row = dict(ch)
            row["streams"] = stay
            survivors.append(row)
        else:
            dropped.append(name)
    return survivors, moved, dropped


def line_quality_map(channels):
    """目录里每条存活线路的实测尺寸（规范地址 -> "宽x高"），随目录打进 APK 给选线面板用。

    设备端现场只探得到正在播的那一条，其余线的尺寸只能构建时带上
    （用户 10-25「线路选择画面没看到每条线路的清晰度」）。没实测到的线不进这一份（显示「未知」）。
    """
    out = {}
    for row in channels:
        for url in row["streams"]:
            size = line_size(url)
            if size:
                out[stream_rules.canon(url)] = f"{size[0]}x{size[1]}"
    return out


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
    # 10-24 18:3x 起它的位置由 `MANUAL_4K_PINNED` 管（用户要求排在 CCTV-4K 后面），不在这张表里。
    ("东方卫视 4K", ["https://bp-resource-dfl.bestv.cn/148/3/video.m3u8"]),
    ("河北 4K", ["http://event.pull.hebtv.com/live/live101.m3u8"]),
    ("苏州 4K", [
        "http://tylive.kan0512.com/norecord/csztv4k_4k.m3u8",
        "http://live-auth.51kandianshi.com/szgd/csztv4k_hd.m3u8",
    ]),
]

# 位置要钉死的台（不进「4K 超高清」子分组）：用户 10-24 18:3x 原话
# 「调整CCTV8K至CCTV4K后面，咪咕体育4K移入体育频道」。
# 8K 归到「央视」子分组、order 用 CCTV_ORDER 里那个半步值，目录里就紧挨 CCTV-4K；
# 咪咕体育 4K 走 MANUAL_SPORTS_ORDERED 落进 sports/咪咕直播（order=50，紧挨 4K-1/4K-2）。
MANUAL_4K_PINNED = [
    ("CCTV-8K 超高清", "cctv", "央视", CCTV_ORDER["CCTV-8K 超高清"], [
        "http://38.75.136.137:98/gslb/dsdqpub/cctv8k.m3u8?auth=testpub",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv8k",
        "http://107.150.60.122/live/cctv8k.m3u8",
        "http://192.151.150.154/live/cctv8k.m3u8",
    ]),
]
# 台标靠抽帧认，不靠列表里写的名字：
# 3221227021 画面左上角是「CCTV5 体育」，正在播 WTT 中国大满贯女双决赛；
# 3221226899 右上角「咪咕视界」水印，足球转播。两条都是 2026-10-21 18:41 抽到帧、
# 18:52 再各自取首分片算 MD5（互不相同）确认在出真画面。
MANUAL_SPORTS_ORDERED = [
    # 用户 10-24 18:3x：「咪咕体育 4K 移入体育频道」，并挨着同类的咪咕直播 4K-1/4K-2
    # （add_sports 给那两台 order=40，这里取 50：在 4K 之后、咪咕直播 1 之前）。
    ("咪咕体育 4K", "sports", "咪咕直播", 50,
     "http://gslbserv.itv.cmvideo.cn:80/1.m3u8?channel-id=FifastbLive"
     "&Contentid=3000000010000005180&livemode=1&stbId=yu&mode=1"),
    # 用户 10-24 19:4x 点档「允许新增 咪咕直播 9… 这类新台」。order 写 200+台号：
    # 走 MANUAL_EXTRA 的默认 999 时同类台按台名字典序排，「9」会掉到「36」后面
    # （字典序 "10" < "9"）；给显式 order 才能让 9→36 按数字排在一起。
    ("咪咕直播 9", "sports", "咪咕直播", 200 + 9,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000001000028638&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 10", "sports", "咪咕直播", 200 + 10,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000001000031494&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 11", "sports", "咪咕直播", 200 + 11,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000000097&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 12", "sports", "咪咕直播", 200 + 12,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000002019&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 13", "sports", "咪咕直播", 200 + 13,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000002809&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 14", "sports", "咪咕直播", 200 + 14,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000003915&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 15", "sports", "咪咕直播", 200 + 15,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000004193&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 16", "sports", "咪咕直播", 200 + 16,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000005837&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 17", "sports", "咪咕直播", 200 + 17,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000006077&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 18", "sports", "咪咕直播", 200 + 18,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000006658&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 19", "sports", "咪咕直播", 200 + 19,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000009788&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 20", "sports", "咪咕直播", 200 + 20,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000010833&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 21", "sports", "咪咕直播", 200 + 21,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000011297&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 22", "sports", "咪咕直播", 200 + 22,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000011518&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 23", "sports", "咪咕直播", 200 + 23,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000012558&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 24", "sports", "咪咕直播", 200 + 24,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000012616&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 25", "sports", "咪咕直播", 200 + 25,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000015470&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 26", "sports", "咪咕直播", 200 + 26,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000015560&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 27", "sports", "咪咕直播", 200 + 27,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000019839&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 28", "sports", "咪咕直播", 200 + 28,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000021904&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 29", "sports", "咪咕直播", 200 + 29,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000023434&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 30", "sports", "咪咕直播", 200 + 30,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000025380&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 31", "sports", "咪咕直播", 200 + 31,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000010000027691&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 32", "sports", "咪咕直播", 200 + 32,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000020000011518&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 33", "sports", "咪咕直播", 200 + 33,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000020000011519&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 34", "sports", "咪咕直播", 200 + 34,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000020000011520&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 35", "sports", "咪咕直播", 200 + 35,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000020000011521&livemode=1&stbId=YanG-1989"),
    ("咪咕直播 36", "sports", "咪咕直播", 200 + 36,
     "http://gslbserv.itv.cmvideo.cn/index.m3u8?channel-id=FifastbLive&Contentid=3000000020000011522&livemode=1&stbId=YanG-1989"),
]

MANUAL_EXTRA = [
    # 咪咕体育 4K 已挪进 MANUAL_SPORTS_ORDERED（order=50）：用户 10-24 18:3x 「移入体育频道」，
    # 原先它挂在 cctv/「4K 超高清」下，与「体育频道」隔了两个分类；按台名与内容都该归体育。

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
    # 用户 10-29 01:0x「hls/28 兵团 属于 新疆，**符合线路标准后**，参照河北卫视做」：
    # hls/28 那条实测只有 720×578（标清），不达标 ⇒ 不用它。
    # 达标的是 ylt 主机 n34：09:1x 抽帧 1920×1090、左上「兵团卫视」红黄星标（逐张看过），
    # 按河北卫视那套新建频道、归到 地方频道·新疆。
    # 用户 10-29 10:2x「算卫视频道」：兵团卫视归 央视·卫视（不再放 地方频道·新疆）。
    ("兵团卫视", "cctv", "卫视", "http://ylt.7766.org:333/hls/34/index.m3u8"),
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
    # 2026-10-27 用户指令「东方卫视 线1，移到 地方频道」：这条 bestv 素材回放的画面左上角是
    # 「新纪实 LIVE24」（SMG 纪实），台名照画面写，不照用户口述写（同 10-22「风云足球」那次规矩）。
    # 地方组里当时没有任何纪实/人文台（现读 local 159 台 0 命中）⇒ 只能新建这一台当落点，
    # 再由 `tools/wshi_logo_moves.json` 那条 cross_group 的改挂把它从东方卫视名下搬进来。
    ("新纪实", "local", "上海", "http://bp-resource-dfl.bestv.cn/155/3/video.m3u8"),
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


# ---- 用户 10-29 01:1x 四台新主机逐号扫描（ylt / 27.129 / 1.24.39 / 36.110）----
# 只收抽帧 ≥1920×1080 且台标与台名相符的号（帧证据 tools/.cache/hosts_frames/<host>/n<号>/ 逐张人工认过；
# 尺寸证据 tools/.cache/newhost_frames/line_frames.jsonl）。没有线路加线路；没有频道加频道。
# ---- 用户 10-29 01:1x 四台新主机逐号扫描（ylt / 27.129 / 1.24.39 / 36.110）----
# 只收抽帧 ≥1920×1080 且台标与台名相符的号（帧证据 tools/.cache/hosts_frames/<host>/n<号>/ 逐张人工认过；
# 尺寸证据 tools/.cache/newhost_frames/line_frames.jsonl）。没有线路加线路；没有频道加频道。
MANUAL_EXTRA.extend([
    ("梨园频道", "cctv", "央视", "http://ylt.7766.org:333/hls/18/index.m3u8"),
    ("河北经济生活", "local", "河北", "http://ylt.7766.org:333/hls/23/index.m3u8"),
    ("河北都市", "local", "河北", "http://ylt.7766.org:333/hls/24/index.m3u8"),
    ("河北少儿科教", "local", "河北", "http://ylt.7766.org:333/hls/26/index.m3u8"),
    ("三农频道（河北）", "local", "河北", "http://ylt.7766.org:333/hls/28/index.m3u8"),
    ("邯郸新闻综合", "local", "河北", "http://ylt.7766.org:333/hls/30/index.m3u8"),
    ("邯郸公共", "local", "河北", "http://ylt.7766.org:333/hls/32/index.m3u8"),
    ("金鹰纪实", "local", "湖南", "http://ylt.7766.org:333/hls/67/index.m3u8"),
    ("金鹰卡通", "local", "湖南", "http://ylt.7766.org:333/hls/73/index.m3u8"),
    ("CCTV-第一剧场", "cctv", "央视", "http://ylt.7766.org:333/hls/76/index.m3u8"),
    ("CCTV-风云剧场", "cctv", "央视", "http://ylt.7766.org:333/hls/77/index.m3u8"),
    ("CCTV-怀旧剧场", "cctv", "央视", "http://ylt.7766.org:333/hls/78/index.m3u8"),
    ("CCTV-风云音乐", "cctv", "央视", "http://ylt.7766.org:333/hls/79/index.m3u8"),
    ("CCTV-高尔夫·网球", "cctv", "央视", "http://ylt.7766.org:333/hls/81/index.m3u8"),
    ("CCTV-兵器科技", "cctv", "央视", "http://ylt.7766.org:333/hls/83/index.m3u8"),
    ("CCTV-世界地理", "cctv", "央视", "http://ylt.7766.org:333/hls/84/index.m3u8"),
    ("CCTV-女性时尚", "cctv", "央视", "http://ylt.7766.org:333/hls/98/index.m3u8"),
    ("内蒙古蒙语频道", "local", "内蒙古", "http://1.24.39.180:808/hls/18/index.m3u8"),
    ("内蒙古新闻综合", "local", "内蒙古", "http://1.24.39.180:808/hls/19/index.m3u8"),
    ("内蒙古经济生活", "local", "内蒙古", "http://1.24.39.180:808/hls/20/index.m3u8"),
    ("内蒙古文体娱乐", "local", "内蒙古", "http://1.24.39.180:808/hls/21/index.m3u8"),
    ("内蒙古农牧频道", "local", "内蒙古", "http://1.24.39.180:808/hls/22/index.m3u8"),
    ("内蒙古少儿频道", "local", "内蒙古", "http://1.24.39.180:808/hls/23/index.m3u8"),
    ("CGTN 纪录", "cctv", "央视", "http://36.110.54.118:866/hls/17/index.m3u8"),
    ("CGTN 俄语", "cctv", "央视", "http://36.110.54.118:866/hls/19/index.m3u8"),
    ("CETV-1", "cctv", "央视", "http://36.110.54.118:866/hls/37/index.m3u8"),
    ("金鹰纪实卫视", "local", "湖南", "http://36.110.54.118:866/hls/38/index.m3u8"),
    ("高清电影", "local", "北京", "http://36.110.54.118:866/hls/21/index.m3u8"),
])



def add_manual_channels(builder):
    for order, (display, urls) in enumerate(MANUAL_4K):
        for url in urls:
            builder.add(display, "cctv", "4K 超高清", url, source="manual_4k", order=order)
    for display, category, subgroup, order, urls in MANUAL_4K_PINNED:
        for url in urls:
            builder.add(display, category, subgroup, url, source="manual_4k", order=order)
    for display, category, subgroup, url in MANUAL_EXTRA:
        builder.add(display, category, subgroup, url, source="manual_extra")
    for display, category, subgroup, order, url in MANUAL_SPORTS_ORDERED:
        builder.add(display, category, subgroup, url, source="manual_sports", order=order)
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


def load_review_logos(paths):
    """读门禁各轮 `review.json` 的人工看图台标结论（后一轮覆盖前一轮）。

    只收 `logo` 那一格：`picture` 是「这一帧算不算节目画面」，与「这条地址是不是这台的真源」两回事。
    行里没有 channel 的老结论照收，但归属留空——那种情况按地址给任何台名作证据都不成立，
    所以调用方要拿台名核对（见 logo_keeps_line）。
    """
    out = {}
    for path in paths:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in data.get("reviews", []):
            canon = stream_rules.canon(row.get("url") or "")
            if not canon or not (row.get("logo") or row.get("picture")):
                continue
            # 台账里台名的字段名是 `name`（门禁自己写的），`channel` 是人工补写那批用的；
            # 只认其中一个就会把 160 行有台名的结论读成「没写台名」，整片地址被当成歧义不借证。
            out[canon] = {"logo": row["logo"], "picture": row.get("picture") or "",
                          "channel": row.get("channel") or row.get("name") or ""}
    return out


# 人工看图里属于「这一帧不是节目」的四类；纯色与卡流不在其中——那两格在门禁侧本来就有单独的列，
# 口径是「只降分不删线」（一次淡入淡出给出纯黑不能算这条地址坏了），不该被并进占位页那一族。
CARD_LIKE = ("测试卡", "广告", "占位页", "推广页")


def review_cards(rows):
    """从人工看图台账里挑出「这一帧不是节目」那四类（键＝归一化地址）。"""
    return {c: r for c, r in rows.items() if (r.get("picture") or "").startswith(CARD_LIKE)}


def review_card(name, canon):
    """人工看图给这条地址的「占位页/广告」意见；没有或归属对不上就返回空串。"""
    row = REVIEW_PICTURE.get(canon) or {}
    label = row.get("picture") or ""
    if not label.startswith(CARD_LIKE):
        return ""
    owner = row.get("channel") or ""
    if owner:
        return label if owner == name else ""
    return "" if canon in AMBIGUOUS_LOGO else label


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


def load_retest(path):
    """读三轮回测汇总（retest3_summary.py）：canonical url -> {ok, fails, verdicts}。"""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("urls") or {}


def load_wshi_moves(path):
    """读「按台标改挂」的人工确认清单（默认 tools/wshi_logo_moves.json）：canonical url -> {from,to,evidence}。

    这份清单是两道闸之后的产物，构建层不再判定：探测器 `tools/.cache/wshi_mismatch.py` 的跨模板打分
    先挑出「本台台标没认出来、别的卫视台标认出来了」的格，再逐张单帧角标人工复核过一眼
    （10-25 我拿两列拼图肉眼看错一枚草书台标，差点摘掉一条能用的线 ⇒ 拼图不算复核）。
    清单缺文件／缺 moves 都返回 {}：这一条指令的默认状态是「没改挂任何线」，不是「没证据就乱挂」。
    """
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for row in data.get("moves") or []:
        canon = stream_rules.canon(row.get("url") or "")
        if not canon:
            print(f"[warn] 改挂清单里有一条没地址，跳过：{row}")
            continue
        frm, to = row.get("from"), row.get("to")
        if not frm or not to:
            print(f"[warn] 改挂 {canon} 缺 from/to，跳过")
            continue
        out[canon] = {"from": frm, "to": to, "evidence": row.get("evidence") or "",
                      # 严格解析：`bool("false")` 是 True，手写清单里 `"cross_group": "false"`
                      # 会被读成"允许跨组"⇒ 只认 1/true/yes 三种写法，其余一律按不允许。
                      "cross_group": str(row.get("cross_group")).strip().lower() in ("1", "true", "yes")}
    return out


CCTV3_KIND_LABEL = {
    "logo": "三轮都无台标（程序＋人工复核）",
    "no_frame": "三轮都没帧且无台标实证",
    # 用户 10-26 真机点档：这一页是源站拿来顶位的告警画面，出现过就把这条线摘掉并拉黑。
    "overload_qr": "服务器超负载…微信扫码告警页（任一轮命中，用户 10-26 指令删）",
    # 用户 10-27 点档甲：302 到一枚定长片子（不是清单、不是分片流），电视上"出了画"放的是那支片子。
    # 每一枚都是**逐条重放**抓到的（`tools/.cache/proxy_8138_gate_replay.json`），不是按主机连坐。
    "finite_clip_placeholder": "定长片占位（302 落地是一枚放得完的 mp4，用户 10-27 判为标签页、无台标）",
    # 用户 10-27 指认：东方卫视 线1 画面有本台台标、清单也是滑动的直播窗口（无 ENDLIST、MEDIA-SEQUENCE 在走），
    # 但分片全来自 `/material/<32位hash>/<存档日期>/video/<素材id>-NNNNN.ts` 这种**存好的素材**（那条日期是 2025/0107），
    # 所以它放的不是本台此刻的节目——台标尺与「有没有直播窗口」两把尺都抓不到它。证据见逐条现取的清单原文。
    "stale_material_replay": "非实时素材回放（滑动窗口但分片是 /material/<存档日期>/ 存好的素材，带本台台标却不是本台此刻节目，用户 10-27 指认）",
}


def load_cctv3(path):
    """读央视·卫视三轮回测的删除清单（final_deletions.json）：canonical url -> 理由。

    只收清单里 cut 那几条；每条都带着轮次、程序复核分数与人工复核结论（见同一份目录）。
    kind 决定理由文案，未知 kind 不收（宁可少删一条，不许把「说不清来路」的行当成删除凭据）。
    """
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for row in data.get("cut") or []:
        canon = stream_rules.canon(row.get("url") or "")
        if not canon:
            continue
        kind = row.get("kind") or "logo"
        label = CCTV3_KIND_LABEL.get(kind)
        if label is None:
            print(f"[warn] --cctv3 里有一条不认识的 kind={kind!r}，这条不收：{canon}")
            continue
        out[canon] = label
    return out


def compute_retest_cuts():
    """三轮都不通、并且没有任何本台台标实证的地址（台标证据与 `logo_verified` 同一批来源）。

    为什么要「同时」满足两条：用户的原话是「≥3 是无台标删除线路」——回测失败只是必要条件，
    有本台台标的那条按 10-24「保留有台标的线路」继续留着（广告时段也会连不上分片）。
    """
    cuts = set()
    for canon, row in RETEST.items():
        if int(row.get("fails") or 0) < RETEST_FAIL_LIMIT:
            continue
        if any(v in IDENTITY_GOOD for k, v in IDENTITY.items() if k.endswith("|" + canon)):
            continue
        if (REVIEW_LOGO.get(canon) or {}).get("logo", "").startswith("台标相符"):
            continue
        cuts.add(canon)
    return cuts


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

    用户 10-25 18:5x 点档「乙：清晰度优先」：`RES_FIRST_CATS`（央视·卫视、地方频道）这两组
    的次序只由清晰度决定，慢线**不再挪台尾**——否则一条 1920×1090 会被压到 1280 后面，
    与第 8 条指令字面冲突。其余组仍照旧挪（那几组本来就按证据档排，不按清晰度）。
    """
    moved: list[tuple[str, str]] = []
    skipped: list[str] = []
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
        if ch.get("category") in RES_FIRST_CATS:
            skipped.append(ch["name"])
            continue
        keep = [s for s in streams if s not in slow or idx[s] == n - 1]
        ch["streams"] = keep + to_move
        moved.extend((ch["name"], stream_rules.canon(s)) for s in to_move)
    if skipped:
        print(f"[slow] 清晰度优先组不挪慢线（用户 10-25 点档乙）：{len(skipped)} 台：{'、'.join(skipped)}")
    return channels, moved


def apply_demotions(channels, specs=None):
    """用户 10-26 点档「乙」：真死的首条线挪到台内末位——只换序，不删线、不进黑名单。

    名册 `tools/demote_lines.json` 由人工写：每条都带「三轮回测三轮 0 帧＋35 秒长窗 0 帧」的证据。
    构建侧只做搬运，不重新判定，也不替名册兜底：
      ①台名对不上、地址不在本台线路里 ⇒ 只报 `[check] demote`，跳过，不猜别的台；
      ②已经在末位的不动（名册只记真要挪的那批）；
      ③其余线路的相对次序原样保留（不许顺手再排一次）。
    位置放在 `move_slow_lines_back` 之后、目录组装之前：这是最后一步，前面的清晰度/证据排序不再覆盖它。
    """
    if specs is None:
        specs = json.loads(DEMOTE_FILE.read_text(encoding="utf-8")).get("lines") or []
    by_name = {row["name"]: row for row in channels}
    moved, missing = [], []
    for spec in specs:
        name, url = spec.get("channel"), spec.get("url")
        row = by_name.get(name)
        if row is None:
            missing.append(f"{name}（台不在目录）")
            continue
        canon = stream_rules.canon(url)
        hit = next((s for s in row["streams"] if stream_rules.canon(s) == canon), None)
        if hit is None:
            missing.append(f"{name}（地址不在本台线路里）")
            continue
        if row["streams"][-1] == hit:
            continue
        row["streams"] = [s for s in row["streams"] if s is not hit] + [hit]
        moved.append((name, canon, len(row["streams"])))
    return moved, missing


def strict_filter(channels):
    """删台/删线判据（用户 10-22 23:4x「此为最新判据」＋10-23 17:5x 卫视组删线指令）。

    三处作用域分开，不许互相串：
      ①必看台（央视 1-17＋5+＋4K 超高清＋辽宁体育）：台内**没有任何画面实证**的线路删掉；
        一条实证都没有的台**保留在目录里**并交给结尾 [check] no_picture 判红——
        不许它悄悄消失，也不许拿播不出来的线冒充可用（那种台要去公开源补线）。
        "已实证"按**累计**证据算（旧场次的抽帧/台标复核＋**设备实测播过**同样算实证），
        本轮没复核到只报数、不删线。
      ②卫视组＋地方/国际/港澳台三组（用户 10-23 17:5x「很多频道有多个不可用线路，已验证过的可用线路保留，
        不可用线路删除」＋18:5x「曾经出过真实画面的有台标的线路就留」＋**10-27 点档
        「3 从卫视组扩到地方/国际/港澳台」**，作用域见 `TIER3_PRUNE_CATS`）：整台没有一条有画面的线
        ⇒ 删除频道；活下来的台，台内**本机连不通**（档 3：连接超时/响应超时）的线路删掉——
        本机就是父母家那张网（10-23 网络口径），连不通的线只会让电视白等换线超时。
        保留档 0/1（抽帧认过台标／取到过分片）＋档 2（没测过，可能通）＋设备实测播过的线
        （「已播过的线路绝不自动换线」）＋**曾经出过真实画面的线**（has_picture：旧场次抽到过
        正常节目帧也算——天津 222.169.85.8、河南 111.59.139.82 就是这一类，现在连不通也留）。
        这一侧同样不吃设备实测那一档当**台级**证据。
      ③其余各组（央视名册与体育）：整台找不到一条有画面的线 ⇒ 删除频道；
        活下来的台，台内线路**一条都不动**（10-27 扩档只点到地方/国际/港澳台，没点央视与体育）。
      ④特色频道整组删除（用户 10-23 20:0x「删除特色频道」）：category 是 featured 的台
        不管有没有画面实证一律不进目录——影视轮播/NewTV/CHC/少儿那几台是用户点名不要的，
        与「没有可用源」不是一回事，计数单独一格（dropped_featured），不许并进 dropped_no_source。
      ⑤台级那一格多一条豁免（用户 10-24 #92 点档「认台标」）：②③那两侧「整台没有一条有画面的线
        ⇒ 删除频道」前面再加一问——这条地址有没有**为本台验过台标**（`logo_keeps_line`）。
        验过就留着台，单独计数 `logo_only_kept`。只管**台在不在**：`has_picture` / `has_picture_now`
        一行没动，这种台照样出现在报告的「没有可用画面」名单里（G2/G4 照旧判红），
        也不会替必看台那一侧说话——①没有放宽。
      ⑥台级那一格再多一条豁免（用户 10-27 #149 点档「甲」＝钉回）：名册 `demote_lines.json` 里
        `pin: true` 的那条地址，整台一条画面实证都没有时**台留着**，单独计数 `pin_kept`。
        与⑤同一处置口径——只管台在不在，`has_picture`/`has_picture_now` 一行没动，报告侧照旧判红。
        线那一侧由 `pin_rescues()` 守在 `usable()` 与②那一格的删除前面：只挡**按 health 判词**
        发起的删除（对端这一轮回的坏判词、以及②那一格的「本机连不通」），冻帧/告警页/三轮回测
        定案/容器不支持/占位页/台标是别的台 都钉不动（那几类是指纹级稳定坏）。
    """
    stats = {"must_total": len(MUST_PLAY_CHANNELS), "pruned_lines": 0,
             "must_no_picture": [], "dropped_no_source": 0, "dropped_featured": 0,
             "stale_only": [], "tier3_pruned": [], "logo_only_kept": [], "pin_kept": []}
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
            # 「整台换组」不许顺手改线路集：这台的剪枝规则按它**离开的那个组**算。
            # 10-27 试算现读：只把 subgroup 从 卫视 改成 local/山东 时，
            # `39.135.138.58:18890/.../3221225719/index.m3u8`（在库黑名单理由「连接超时」）
            # 因为不再是"卫视组"就绕过了这条剪枝 ⇒ 目录 472→473 条、黑名单 1561→1560，
            # 一条已判坏的线被换组这件事放了回来。用户指令只有"频道页放地方频道"，没有"把这条放回来"，
            # 所以按台名（在 卫视 名册 SAT_ORDER 里）继续适用原组规则。
            if (ch["subgroup"] == "卫视" or ch["category"] in TIER3_PRUNE_CATS
                    or (group_overridden(ch["name"]) and ch["name"] in SAT_ORDER)):
                kept, dropped = [], []
                for s in ch["streams"]:
                    if (evidence(s, ch["name"])[0] < 3 or device_proof(ch["name"], s)
                            or has_picture(ch["name"], s) or pin_rescues(ch["name"], s)):
                        kept.append(s)
                    else:
                        dropped.append(s)
                if kept and dropped:
                    ch["streams"] = kept
                    stats["tier3_pruned"].extend(
                        (ch["name"], stream_rules.canon(s),
                         (HEALTH.get(stream_rules.canon(s)) or {}).get("verdict", ""))
                        for s in dropped)
            survivors.append(ch)
        elif any(logo_keeps_line(ch["name"], s) for s in ch["streams"]):
            # 「台级删除也认本台台标」（用户 10-24 #92 点档「认台标」＝上面第 乙 档）：
            # 整台一条画面实证都没有、但有一条地址为本台验过台标 ⇒ 台留着。
            # 只放宽这一格：`proven`/`has_picture`/`has_picture_now` 一行没动，
            # 所以它照样进 G2/G4 的「没有可用画面」名单、报告侧照旧判红——是"台别消失"，不是"台算能用"。
            stats["logo_only_kept"].append(ch["name"])
            survivors.append(ch)
        elif any(pin_rescues(ch["name"], s) for s in ch["streams"]):
            # 「钉回目录」（用户 10-27 #149 点档「甲」＝上面第 ⑥ 格）：整台一条画面实证都没有，
            # 但名册里有一条地址是用户点名要留的 ⇒ 台留着。与⑤同一类处置——只管台在不在，
            # `has_picture`/`has_picture_now` 一行没动，这台照样进 G2/G4 的「没有可用画面」名单。
            stats["pin_kept"].append(ch["name"])
            survivors.append(ch)
        else:
            stats["dropped_no_source"] += 1
    return survivors, stats


def build(args):
    global MAX_STREAMS, HEALTH, IDENTITY, DROP_DEAD, DROP_SD, FRAME_RES, FRAME_PLACEHOLDER, FRESH_RES, DEVICE_PROOF
    global FRAME_STALLED, FROZEN, FROZEN_NO_SIBLING, REVIEW_LOGO, AMBIGUOUS_LOGO, REVIEW_PICTURE
    global RETEST, RETEST_CUT, CCTV3_CUT, PINNED
    check_group_override()
    # 「钉回目录」的名册要在第一条线路被判之前就位：usable() 与台级那一格都读这一格。
    PINNED = pin_specs() if DEMOTE_FILE.exists() else set()
    MAX_STREAMS = max(0, int(args.max_streams))
    DROP_DEAD = not args.keep_dead
    DROP_SD = not args.keep_sd
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
    review_paths = [Path(p) for p in (args.review or [])] or sorted(
        (CACHE / "gate").glob("round_*/review.json"))
    REVIEW_LOGO = load_review_logos(review_paths)
    REVIEW_PICTURE = review_cards(REVIEW_LOGO)
    print(f"[review] {len(review_paths)} 份人工看图台账、{len(REVIEW_LOGO)} 枚地址有台标结论"
          f"（其中「台标相符」{sum(1 for v in REVIEW_LOGO.values() if v['logo'].startswith('台标相符'))} 枚）")
    print(f"[review] 人工判为「这一帧不是节目」（测试卡/广告/占位页/推广页）的地址 {len(REVIEW_PICTURE)} 枚"
          f"——这一族在构建侧只降分、不摘线（摘线那一侧仍只认钉死的画面指纹）")
    RETEST = load_retest(args.retest) if args.retest else {}
    RETEST_CUT = compute_retest_cuts()
    if RETEST:
        print(f"[retest] {RETEST.get('__rounds__', '')}"
              f"{len(RETEST)} 条地址的回测汇总：三轮都不通且没有本台台标的 {len(RETEST_CUT)} 条"
              f"（有台标的按「保留有台标的线路」留着）")
    CCTV3_CUT = {}
    for _p in (args.cctv3 or []):
        CCTV3_CUT.update(load_cctv3(_p))
    if CCTV3_CUT:
        # 与 [retest] 那两把尺分开报：这一把判的是画面（程序复核＋人工复核），不是连通性。
        # 分档按理由现算：旧那行只分"扫码页 / 其余"两桶，加进"定长片占位"这一族后它把
        # 13 条占位片一起写成"三轮都无本台台标或三轮都没帧"＝分项之和≠总数那个老形状（10-27 现读）。
        _by = {}
        for _v in CCTV3_CUT.values():
            _k = _v.split("（")[0]
            _by[_k] = _by.get(_k, 0) + 1
        print(f"[cctv3] {len(args.cctv3 or [])} 份清单合并后 {len(CCTV3_CUT)} 条央视·卫视线路"
              f"（删线＋拉黑，设备端自动更新捞不回来）："
              + "，".join(f"{k} {v} 条" for k, v in sorted(_by.items(), key=lambda kv: -kv[1]))
              + f"｜分项之和 {sum(_by.values())} == {len(CCTV3_CUT)}"
              + (" ✓" if sum(_by.values()) == len(CCTV3_CUT) else " ✗"))
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
        # 用户 10-24 19:4x：「咪咕体育线路2 HLS 2409 响应超时，确认线路2是否正确」。
        # 现读：目录里体育组的 10 条 2409 IPv6 线（咪咕直播 1–8、4K-1/4K-2 的第 2 条）
        # 一轮 10/10 全部「连接超时」——这台电视走 IPv6 出网会抖，等于「线路2」永远是死的。
        # 先按台名摘掉，同时按地址拉黑（见 user_flagged_blocks），设备端自动更新不会再捞回来。
        "咪咕直播 1": ("http://[2409:8087:1a01:df::",),
        "咪咕直播 2": ("http://[2409:8087:1a01:df::",),
        "咪咕直播 3": ("http://[2409:8087:1a01:df::",),
        "咪咕直播 4": ("http://[2409:8087:1a01:df::",),
        "咪咕直播 5": ("http://[2409:8087:1a01:df::",),
        "咪咕直播 6": ("http://[2409:8087:1a01:df::",),
        "咪咕直播 7": ("http://[2409:8087:1a01:df::",),
        "咪咕直播 8": ("http://[2409:8087:1a01:df::",),
        "咪咕直播 4K-1": ("http://[2409:8087:1a01:df::",),
        "咪咕直播 4K-2": ("http://[2409:8087:1a01:df::",),
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
        # 用户 10-29 00:5x「卫视4K频道最低分辨率为全高清」：苏州 4K 的线2 抽帧原生 1280×738，
        # 低于全高清；线1 是 3840×2178 真 4K。按台名摘这一条，线1 不动（同台内按地址摘）。
        "苏州 4K": (
            "http://live-auth.51kandianshi.com/szgd/csztv4k_hd.m3u8",
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
        # 用户 10-28「资产统一」：设备目录上有、打进 APK 的目录里没有的线，按标准过了一遍，
        # 下面 6 条是 15:1x 现测通过（下载/码率 7.71–18.44、1280×720 或 1920×1080、
        # 抽帧在画面上认到本台台标）才补进来的，只追加、不动首条。
        # 证据：tools/.cache/unify_alive_probe.json ＋ tools/.cache/unify_frames/（拼图 unify_sheet.jpg）。
        # 未收的：苏州4K（抽到的是「4K频道」＝CCTV-4K，台名不符）、CGTN阿语（地址实为西语台）、
        # 东方卫视4K（与已有 https 线同源）、萧山生活频道/乐至综合频道（与已收那条重名同址）。
        "怀仁综合频道": [
            # 10.53×；1920×1080；左上「RTV 怀仁」
            "https://1yp7hc5p.live.sxmty.com/live/hls/bff8529922344209985b6e49baa9555a/efa2543628fc4a7ea93d3d6c975f77dc-1.m3u8",
        ],
        "诸暨新闻综合": [
            # 9.85×；1920×1080；左上蓝圈「新闻综合」
            "http://l.cztvcloud.com/channels/lantian/SXzhuji3/720p.m3u8?zzhed",
        ],
        "文成综合": [
            # 18.44×；1280×720；左上「文成综合」
            "http://l.cztvcloud.com/channels/lantian/SXwencheng1/720p.m3u8",
        ],
        "萧山生活": [
            # 14.50×；1280×720；左上「萧山电视台」
            "http://l.cztvcloud.com/channels/lantian/SXxiaoshan2/720p.m3u8",
        ],
        "广州综合": [
            # 7.71×；1280×720；左上「广州广播电视台」
            "http://tencentplay.gztv.com/live/zonghes.m3u8?txSecret=c8dbe86d6f86eb1a21999ab479fd5667&txTime=18d3c4690e8",
        ],
        "金川新闻综合": [
            # 10.43×；1920×1080；左上「JCTV」
            "http://live.jinchuanrmt.com:90/live/zhxw.m3u8?zsicd",
        ],
        "余杭未来E": [
            # 12.75×；1920×1080；左上「YMG 未来E频道」
            "http://l.cztvcloud.com/channels/lantian/SXyuhang3/720p.m3u8",
        ],
        "CGTN 西语": [
            # 7.55×；1920×1080；左上「CGTN ESPAÑOL」
            "http://espanol-liveali.cgtn.com/hls/LSveOGBaBw41Ea7ukkVAUdKQ220802LSTexu6xAuFH8VZNBLE1ZNEa220802cd.m3u8",
        ],
        "邢台综合": [
            # 2.2×；1280×720；左上蓝圈「新闻综合」
            "http://jwplay.hebyun.com.cn/live/xtsrmtzs/1500k/tzwj_video.m3u8",
        ],
        "缙云综合": [
            # 17.0×；1280×720；左上蓝圈「新闻综合」
            "http://l.cztvcloud.com/channels/lantian/SXjinyun1/720p.m3u8",
        ],
        "吉林卫视": [
            # 2.27×；1920×1080；左上「吉林卫视」（画面带补肺丸广告，按用户标准"有台标+广告=有台标"）
            "http://221.7.175.154:8445/tsfile/live/1014_1.m3u8?key=txiptv&playlive=1&authid=0",
        ],
        "怀仁综合": [
            # 单片清单；1920×1080；左上「RTV 怀仁」（两个写法同一份流，设备上都有，代码侧同批收齐）
            "http://1yp7hc5p.live.sxmty.com/live/hls/bff8529922344209985b6e49baa9555a/efa2543628fc4a7ea93d3d6c975f77dc-1.m3u8",
            "http://1yp7hc5p.live.sxmty.com/live/hls/bff8529922344209985b6e49baa9555a/efa2543628fc4a7ea93d3d6c975f77dc.m3u8",
        ],
        "乐至综合": [
            # 5.43×；1920×1080；左上「综合」＋右上「高清」
            "http://175.155.106.72:89/live1/live1.m3u8",
        ],
        # 用户 10-28「央视·卫视 继续找 vbsky/aptv 等列表，增加 MP2 高清线的替代源，需符合线路标准要求；加进各自台」：
        # 下面 6 条都过了「三轮回测 吞吐/码率」＋「ffprobe 分辨率」＋「台标」三关，**追加**在原线之后（不动首条）。
        # 三轮回测读数见 tools/.cache/mp2_alt_ratios{,2,3}.json；台标帧见 tools/.cache/mp2_alt/。
        # 用户 10-28 03:0x「复活两源 浙江 黑龙江」：这两条曾被 #86 乙 拉黑（「白天重扫仍判冻＋带二维码
        # 购物广告」／「解不出画面」），今天三轮回测都通（16/14/16×、7/5/7.6×，1080p，台标已认），
        # 按指令复活，并同批从 blockedLines 摘掉（目录里已摘，黑名单 1583→1581）。
        "浙江卫视": [
            # 三轮回测 16.07 / 14.20 / 16.14；1920×1080；有台标 0.738（左上「浙江卫视」＋「中国蓝新闻」）
            "https://ali-m-l.cztv.com/channels/lantian/channel001/1080p.m3u8",
        ],
        "黑龙江卫视": [
            # 三轮回测 6.97 / 5.01 / 7.62；1920×1080；有台标 0.61（官方 idclive）
            "https://idclive.hljtv.com:4430/live/hljws_own.m3u8",
        ],
        "东方卫视": [
            # 2.32 / 2.82 / 2.86；1920×1080；有台标 0.527（星标＋《危险关系》）
            "http://27.129.145.110:4433/hls/40/index.m3u8",
        ],
        # 10-28 20:2x：36.110 那台实测只给 4.4–5.9 Mbps（四种 UA 都一样），而内容是 8.0 Mbps，
        # 电视上就是「只有声音没画面」。同一批频道在 61.138.82.196:808 上有快源，逐帧认过台标：
        #   n26 = 东方卫视（左上红色「东方」书法标）、n24 = 山东卫视、n6 = CCTV-6（「CCTV 6 电影」）。
        # 实测（需要 8.0）：n26 20.1/14.4、n24 18.2/20.6、n6 21.4/10.2 Mbps，1920×1080 非标清。
        # 10-28 20:5x 用户指令：「分析 61.138.82.196:808/hls 的线路规则，1 到 100，把正常的线路按规则加到对应频道」。
        # 现读：该主机只有 1–40 号（41–100 全 404）；逐号抽帧认台标，且**只有 1080p 的号才收**（n20/27/28/31/36/38/39/40 是 720×576 标清，按标准不收）。
        # 索引↔频道：1-15=CCTV-1..15、16 浙江、17 江苏、18 湖北、19 广东、22 东方（红椭圆白星，与 27.129 那条同标，n26 的“东方”书法标是别的台，已撤）、
        # 23 辽宁、24 山东、25 吉林、29 安徽、30 北京、32 河北、33 天津、35 四川。
        # 实测样本（需要 8.0 Mbps）：n6 21.4/10.2、n24 18.2/20.6、n26 20.1/14.4、n30 10.5/24.0 Mbps ⇒ 余量 1.3–2.7 倍。
        # 全部插在各自第 2 位（首条不动）。
        "CCTV-1 综合": [
            "http://61.138.82.196:808/hls/1/index.m3u8",
        ],
        "CCTV-2 财经": [
            "http://61.138.82.196:808/hls/2/index.m3u8",
        ],
        "CCTV-3 综艺": [
            "http://61.138.82.196:808/hls/3/index.m3u8",
        ],
        "CCTV-4 中文国际": [
            "http://61.138.82.196:808/hls/4/index.m3u8",
        ],
        "CCTV-5 体育": [
            "http://61.138.82.196:808/hls/5/index.m3u8",
        ],
        "CCTV-6 电影": [
            "http://61.138.82.196:808/hls/6/index.m3u8",
        ],
        "CCTV-7 国防军事": [
            "http://61.138.82.196:808/hls/7/index.m3u8",
        ],
        "CCTV-8 电视剧": [
            "http://61.138.82.196:808/hls/8/index.m3u8",
        ],
        "CCTV-9 纪录": [
            "http://61.138.82.196:808/hls/9/index.m3u8",
        ],
        "CCTV-10 科教": [
            "http://61.138.82.196:808/hls/10/index.m3u8",
        ],
        "CCTV-11 戏曲": [
            "http://61.138.82.196:808/hls/11/index.m3u8",
        ],
        "CCTV-12 社会与法": [
            "http://61.138.82.196:808/hls/12/index.m3u8",
        ],
        "CCTV-13 新闻": [
            "http://61.138.82.196:808/hls/13/index.m3u8",
        ],
        "CCTV-14 少儿": [
            "http://61.138.82.196:808/hls/14/index.m3u8",
        ],
        "CCTV-15 音乐": [
            "http://61.138.82.196:808/hls/15/index.m3u8",
        ],
        "浙江卫视": [
            "http://61.138.82.196:808/hls/16/index.m3u8",
        ],
        "江苏卫视": [
            "http://61.138.82.196:808/hls/17/index.m3u8",
        ],
        "湖北卫视": [
            "http://61.138.82.196:808/hls/18/index.m3u8",
        ],
        "广东卫视": [
            "http://61.138.82.196:808/hls/19/index.m3u8",
        ],
        "东方卫视": [
            "http://61.138.82.196:808/hls/22/index.m3u8",
        ],
        "辽宁卫视": [
            "http://61.138.82.196:808/hls/23/index.m3u8",
        ],
        "山东卫视": [
            "http://61.138.82.196:808/hls/24/index.m3u8",
        ],
        "吉林卫视": [
            "http://61.138.82.196:808/hls/25/index.m3u8",
        ],
        "安徽卫视": [
            "http://61.138.82.196:808/hls/29/index.m3u8",
        ],
        "贵州卫视": [
            "http://61.138.82.196:808/hls/21/index.m3u8",
        ],
        # 用户 10-28 21:1x：n32=河北（目录原无此台，已新建频道）、n37=甘肃。两条都 1080p。
        "河北卫视": [
            "http://61.138.82.196:808/hls/32/index.m3u8",
        ],
        "甘肃卫视": [
            "http://61.138.82.196:808/hls/37/index.m3u8",
        ],
        # 用户 10-28 21:0x 指认：n26 那个红色书法标是**黑龙江卫视**（我先前判成“别的台”，已按用户改口）。
        # 实测 n26 需要 8.0 / 交付 20.1、14.4 Mbps。
        "黑龙江卫视": [
            "http://61.138.82.196:808/hls/26/index.m3u8",
        ],
        "北京卫视": [
            "http://61.138.82.196:808/hls/30/index.m3u8",
        ],
        "河北卫视": [
            "http://61.138.82.196:808/hls/32/index.m3u8",
        ],
        "天津卫视": [
            "http://61.138.82.196:808/hls/33/index.m3u8",
        ],
        "四川卫视": [
            "http://61.138.82.196:808/hls/35/index.m3u8",
        ],
        # 「山东卫视」那条 hznet 线 10-28 12:0x 摘掉：放大门禁帧左上角是 **HZTV（湖州台）**，
        # 画面是养生讲座＋二维码（「五脏同调祛慢病」），台标与频道名不符 ⇒ 按用户标准判红。
        # 当时登记写的"肉眼见左上红标"只认了颜色、没读字，是错的判据。
        # 摘后山东卫视剩 3 条（63.141 / 36.110 / 107.150），标清/高清见各线帧。
        "深圳卫视": [
            # 3.19 / 2.14 / 1.06；1920×1080；有台标 0.473（余量薄，且 38.75 这台主机此前有抖动，只当备用）
            "http://38.75.136.137:98/gslb/dsdqpub/szwshd.m3u8?auth=testpub",
        ],
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
            # 用户 10-28「加进各自台」：三轮回测 3.92 / 2.57 / 2.77；1920×1080；台标 0.542。
            # ⚠️来源是 douyin 第三方中继，长期可用性未证，只当备用。
            "https://cdn.ttvb.eu.org/douyin/cctv6cinema",
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
    # ---- 四台新主机的补充线路（同上，逐条 1080p＋台标相符）----
    # ---- 四台新主机的补充线路（同上，逐条 1080p＋台标相符）----
    _NEW_HOST_BACKUPS = {
        "CCTV-1 综合": [
            "http://ylt.7766.org:333/hls/1/index.m3u8",
            "http://ylt.7766.org:333/hls/29/index.m3u8",
            "http://27.129.145.110:4433/hls/1/index.m3u8",
            "http://1.24.39.180:808/hls/1/index.m3u8",
            "http://36.110.54.118:866/hls/1/index.m3u8",
        ],
        "CCTV-10 科教": [
            "http://ylt.7766.org:333/hls/10/index.m3u8",
            "http://27.129.145.110:4433/hls/10/index.m3u8",
            "http://1.24.39.180:808/hls/10/index.m3u8",
            "http://36.110.54.118:866/hls/10/index.m3u8",
        ],
        "CCTV-11 戏曲": [
            "http://ylt.7766.org:333/hls/11/index.m3u8",
            "http://27.129.145.110:4433/hls/11/index.m3u8",
            "http://1.24.39.180:808/hls/11/index.m3u8",
        ],
        "CCTV-12 社会与法": [
            "http://ylt.7766.org:333/hls/12/index.m3u8",
            "http://27.129.145.110:4433/hls/12/index.m3u8",
            "http://1.24.39.180:808/hls/12/index.m3u8",
            "http://36.110.54.118:866/hls/11/index.m3u8",
        ],
        "CCTV-13 新闻": [
            "http://27.129.145.110:4433/hls/13/index.m3u8",
            "http://1.24.39.180:808/hls/13/index.m3u8",
        ],
        "CCTV-14 少儿": [
            "http://ylt.7766.org:333/hls/14/index.m3u8",
            "http://27.129.145.110:4433/hls/14/index.m3u8",
            "http://1.24.39.180:808/hls/14/index.m3u8",
        ],
        "CCTV-15 音乐": [
            "http://27.129.145.110:4433/hls/15/index.m3u8",
            "http://1.24.39.180:808/hls/15/index.m3u8",
        ],
        "CCTV-16 奥林匹克": [
            "http://27.129.145.110:4433/hls/16/index.m3u8",
            "http://1.24.39.180:808/hls/60/index.m3u8",
        ],
        "CCTV-17 农业农村": [
            "http://ylt.7766.org:333/hls/17/index.m3u8",
            "http://27.129.145.110:4433/hls/17/index.m3u8",
            "http://1.24.39.180:808/hls/61/index.m3u8",
        ],
        "CCTV-2 财经": [
            "http://1.24.39.180:808/hls/2/index.m3u8",
            "http://36.110.54.118:866/hls/2/index.m3u8",
        ],
        "CCTV-3 综艺": [
            "http://ylt.7766.org:333/hls/3/index.m3u8",
            "http://27.129.145.110:4433/hls/3/index.m3u8",
            "http://1.24.39.180:808/hls/3/index.m3u8",
            "http://36.110.54.118:866/hls/3/index.m3u8",
        ],
        "CCTV-4 中文国际": [
            "http://ylt.7766.org:333/hls/4/index.m3u8",
            "http://27.129.145.110:4433/hls/4/index.m3u8",
            "http://1.24.39.180:808/hls/4/index.m3u8",
        ],
        "CCTV-5 体育": [
            "http://27.129.145.110:4433/hls/5/index.m3u8",
            "http://1.24.39.180:808/hls/5/index.m3u8",
            "http://36.110.54.118:866/hls/5/index.m3u8",
        ],
        "CCTV-5+ 体育赛事": [
            "http://36.110.54.118:866/hls/13/index.m3u8",
        ],
        "CCTV-6 电影": [
            "http://ylt.7766.org:333/hls/6/index.m3u8",
            "http://27.129.145.110:4433/hls/6/index.m3u8",
            "http://1.24.39.180:808/hls/6/index.m3u8",
        ],
        "CCTV-7 国防军事": [
            "http://ylt.7766.org:333/hls/7/index.m3u8",
            "http://27.129.145.110:4433/hls/7/index.m3u8",
            "http://1.24.39.180:808/hls/7/index.m3u8",
            "http://36.110.54.118:866/hls/7/index.m3u8",
        ],
        "CCTV-8 电视剧": [
            "http://ylt.7766.org:333/hls/8/index.m3u8",
            "http://27.129.145.110:4433/hls/8/index.m3u8",
            "http://1.24.39.180:808/hls/8/index.m3u8",
            "http://36.110.54.118:866/hls/8/index.m3u8",
        ],
        "CCTV-9 纪录": [
            "http://27.129.145.110:4433/hls/9/index.m3u8",
            "http://1.24.39.180:808/hls/9/index.m3u8",
            "http://36.110.54.118:866/hls/9/index.m3u8",
        ],
        "CGTN": [
            "http://ylt.7766.org:333/hls/16/index.m3u8",
        ],
        "CGTN 西语": [
            "http://36.110.54.118:866/hls/18/index.m3u8",
        ],
        "东南卫视": [
            "http://27.129.145.110:4433/hls/27/index.m3u8",
        ],
        "东方卫视": [
            "http://ylt.7766.org:333/hls/39/index.m3u8",
            "http://27.129.145.110:4433/hls/40/index.m3u8",
            "http://1.24.39.180:808/hls/44/index.m3u8",
            "http://36.110.54.118:866/hls/28/index.m3u8",
        ],
        "兵团卫视": [
            "http://ylt.7766.org:333/hls/34/index.m3u8",
        ],
        "内蒙古卫视": [
            "http://1.24.39.180:808/hls/16/index.m3u8",
            "http://1.24.39.180:808/hls/17/index.m3u8",
        ],
        "北京卫视": [
            "http://27.129.145.110:4433/hls/28/index.m3u8",
            "http://36.110.54.118:866/hls/14/index.m3u8",
        ],
        "吉林卫视": [
            "http://ylt.7766.org:333/hls/54/index.m3u8",
        ],
        "四川卫视": [
            "http://27.129.145.110:4433/hls/35/index.m3u8",
            "http://1.24.39.180:808/hls/39/index.m3u8",
        ],
        "天津卫视": [
            "http://ylt.7766.org:333/hls/40/index.m3u8",
            "http://27.129.145.110:4433/hls/29/index.m3u8",
            "http://1.24.39.180:808/hls/33/index.m3u8",
        ],
        "安徽卫视": [
            "http://27.129.145.110:4433/hls/26/index.m3u8",
            "http://1.24.39.180:808/hls/31/index.m3u8",
        ],
        "山东卫视": [
            "http://27.129.145.110:4433/hls/31/index.m3u8",
            "http://1.24.39.180:808/hls/35/index.m3u8",
            "http://36.110.54.118:866/hls/33/index.m3u8",
        ],
        "广东卫视": [
            "http://ylt.7766.org:333/hls/43/index.m3u8",
            "http://1.24.39.180:808/hls/48/index.m3u8",
        ],
        "广西卫视": [
            "http://ylt.7766.org:333/hls/56/index.m3u8",
        ],
        "江苏卫视": [
            "http://ylt.7766.org:333/hls/37/index.m3u8",
            "http://27.129.145.110:4433/hls/25/index.m3u8",
            "http://36.110.54.118:866/hls/27/index.m3u8",
        ],
        "江西卫视": [
            "http://ylt.7766.org:333/hls/52/index.m3u8",
            "http://27.129.145.110:4433/hls/36/index.m3u8",
        ],
        "河北卫视": [
            "http://ylt.7766.org:333/hls/22/index.m3u8",
            "http://27.129.145.110:4433/hls/18/index.m3u8",
            "http://1.24.39.180:808/hls/47/index.m3u8",
        ],
        "河北经济生活": [
            "http://27.129.145.110:4433/hls/19/index.m3u8",
        ],
        "河北都市": [
            "http://27.129.145.110:4433/hls/20/index.m3u8",
        ],
        "河南卫视": [
            "http://27.129.145.110:4433/hls/33/index.m3u8",
        ],
        "浙江卫视": [
            "http://1.24.39.180:808/hls/29/index.m3u8",
            "http://36.110.54.118:866/hls/29/index.m3u8",
        ],
        "深圳卫视": [
            "http://27.129.145.110:4433/hls/38/index.m3u8",
            "http://1.24.39.180:808/hls/34/index.m3u8",
            "http://36.110.54.118:866/hls/26/index.m3u8",
        ],
        "湖北卫视": [
            "http://27.129.145.110:4433/hls/39/index.m3u8",
            "http://1.24.39.180:808/hls/43/index.m3u8",
        ],
        "湖南卫视": [
            "http://ylt.7766.org:333/hls/35/index.m3u8",
            "http://27.129.145.110:4433/hls/23/index.m3u8",
            "http://1.24.39.180:808/hls/28/index.m3u8",
        ],
        "甘肃卫视": [
            "http://ylt.7766.org:333/hls/65/index.m3u8",
        ],
        "西藏卫视": [
            "http://ylt.7766.org:333/hls/63/index.m3u8",
        ],
        "贵州卫视": [
            "http://ylt.7766.org:333/hls/51/index.m3u8",
            "http://27.129.145.110:4433/hls/37/index.m3u8",
            "http://1.24.39.180:808/hls/32/index.m3u8",
        ],
        "重庆卫视": [
            "http://ylt.7766.org:333/hls/49/index.m3u8",
        ],
        "风云足球": [
            "http://ylt.7766.org:333/hls/80/index.m3u8",
        ],
        "黑龙江卫视": [
            "http://36.110.54.118:866/hls/25/index.m3u8",
        ],
    }
    for _n, _urls in _NEW_HOST_BACKUPS.items():
        manual_backups.setdefault(_n, []).extend(_urls)

    # 用户 10-29 10:3x：36.110 这 8 个号我这一场 VLC 漏抽，补抽后 7 个是 1920×1090
    # （n4 仍 0 帧、n21 的截图目录是空的）⇒ 按台名补线，首条一律不动。
    for _n, _urls in {
        "CCTV-6 电影": ["http://36.110.54.118:866/hls/6/index.m3u8"],
        "CCTV-14 少儿": ["http://36.110.54.118:866/hls/12/index.m3u8"],
        "CGTN 阿语": ["http://36.110.54.118:866/hls/22/index.m3u8"],
        "湖南卫视": ["http://36.110.54.118:866/hls/24/index.m3u8"],
        "湖北卫视": ["http://36.110.54.118:866/hls/30/index.m3u8"],
        "广东卫视": ["http://36.110.54.118:866/hls/32/index.m3u8"],
    }.items():
        manual_backups.setdefault(_n, []).extend(_urls)

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
    # 用户 10-24 目标「央视·卫视、体育频道增加有效线路源」：从盘上 62 份 health 台账里
    # 捞回 65 条「可取到分片、但此刻不在目录也不在黑名单」的旧候选，当面重探 60 条能取分片，
    # 每条抽一帧（tools/.cache/add7/frames/，总览图 sheet_01..09）逐张看左上角台标：
    #   - 只收**台标与频道名相符**的（26 条）；广告时段没照到台标的、画面是 CCTV+ 的、
    #     同一帧 md5 出现在两个不同频道的（那是同一台代理喂的广告，7a0848/000855/80a995/
    #     ba63c5/d79bdf/c4e3f1/5313b5 七组）一律不收；
    #   - 只收原生 ≥1280 宽的全高清档——标清那批（800×610/720×578）不往这里加，
    #     因为 10-23 19:1x 用户点过「删除标清源」（河北卫视 weishi_tingyun 就是这么摘的，
    #     它这次又被候选扫出来，仍不放回）。
    # 追加语义：现有第一条是验证过的（或设备播过的），新线只排在后面。
    for _name, _urls in (
        ("CCTV-1 综合", (
            "http://198.204.228.26/live/cctv1hd.m3u8",  # add7 #14 md5:af875f
        )),
        ("CCTV-2 财经", (
            "http://204.12.221.218:8181/3m1080p/cctv2.m3u8",  # add7 #39 md5:e4bda9
        )),
        ("CCTV-4 中文国际", (
            "http://173.208.212.130:8181/1080p/cctv4.m3u8",  # add7 #36 md5:126835
        )),
        ("CCTV-5+ 体育赛事", (
            "http://ylt.7766.org:333/hls/21/index.m3u8",  # add7 #32 md5:bb7cae
            "http://74.91.26.218:82/live/cctv5p.m3u8",  # add7 #53 md5:3eab12
        )),
        ("CCTV-6 电影", (
            "http://198.204.228.26/live/cctv6hd.m3u8",  # add7 #48 md5:945bcd
        )),
        ("CCTV-7 国防军事", (
            "http://107.150.60.122/live/cctv7hd.m3u8",  # add7 #42 md5:34215c
        )),
        ("CCTV-8 电视剧", (
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv8hd",  # add7 #50 md5:374730
            "http://192.151.150.154/live/cctv8hd.m3u8",  # add7 #51 md5:ca17d3
        )),
        ("CCTV-9 纪录", (
            "http://107.150.60.122/live/cctv9hd.m3u8",  # add7 #57 md5:2b1787
        )),
        ("CCTV-11 戏曲", (
            "http://107.150.60.122/live/cctv11hd.m3u8",  # add7 #13 md5:bab88b
            "http://38.75.136.137:98/gslb/dsdqpub/cctv11hd.m3u8?auth=testpub",  # add7 #15 md5:c64966
        )),
        ("CCTV-14 少儿", (
            "http://204.12.221.218:8181/3m1080p/cctv14.m3u8",  # add7 #17 md5:d9920a
        )),
        ("CCTV-15 音乐", (
            "http://204.12.221.218:8181/3m1080p/cctv15.m3u8",  # add7 #7 md5:cfb782
            "http://107.150.60.122/live/cctv15hd.m3u8",  # add7 #21 md5:523e9d
        )),
        ("CCTV-16 奥林匹克", (
            "http://38.75.136.137:98/gslb/dsdqpub/cctv16hd.m3u8?auth=testpub",  # add7 #26 md5:c41e7f
        )),
        ("CCTV-4K 超高清", (
            "http://192.151.150.154/live/cctv4k.m3u8",  # add7 #60 md5:1bae77
        )),
    ):
        manual_backups.setdefault(_name, []).extend(_urls)
    # 用户 10-25 新源（gitee/gly358617629、homjanon/tv、jisoypub/iptv）：
    # 候选先 health_probe 取到分片，再 VLC 原生抽帧，logo_check 对本台模板 ≥0.45 且目视相符；
    # 只收 .m3u8、且原生宽不超过该台现有第一条——追加在末尾，不动已播过／已验证的第一条。
    # 数据与逐条分数见 tools/new_source_backups_20261025.json（随仓库走，不依赖 .cache）。
    newsrc_path = BASE / "tools/new_source_backups_20261025.json"
    if newsrc_path.exists():
        newsrc = json.loads(newsrc_path.read_text(encoding="utf-8"))
        n_newsrc = 0
        for _name, _rows in (newsrc.get("channels") or {}).items():
            _urls = [r.get("url") for r in _rows if r.get("url")]
            if _urls:
                manual_backups.setdefault(_name, []).extend(_urls)
                n_newsrc += len(_urls)
        print(f"[newsrc] 新源备份线 {n_newsrc} 条（取分片＋原生抽帧＋台标程序复核 ≥0.45，只追加不换首条）")
    for ch in channels:
        backups = manual_backups.get(ch["name"])
        if backups:
            ch["streams"] = list(dict.fromkeys(list(ch["streams"]) + backups))
            MANUAL_LINES.update(backups)
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
            MANUAL_LINES.update(backups)

    # 台标证据的归属：`review.json` 那一份只按地址记结论（老行没有台名字段），
    # 所以先按「这条地址挂在几个台名下」算出歧义集，歧义的那批不许借证（见 logo_verified）。
    owners = {}
    for ch in channels:
        for s in ch["streams"]:
            owners.setdefault(stream_rules.canon(s), set()).add(ch["name"])
    AMBIGUOUS_LOGO = {c for c, ns in owners.items()
                      if len(ns) > 1 and not (REVIEW_LOGO.get(c) or {}).get("channel")}
    print(f"[review] 同一地址挂在 2 个以上台名下、结论又没写台名的：{len(AMBIGUOUS_LOGO)} 枚（这些不借证）")

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

    # 按台标改挂（用户 10-25「卫视频道，台标与频道不一致的，根据台标移入对应卫视频道」）。
    # 位置在标清闸门之前：搬过去的这条线要在新台名下重新过一遍标清／strict_filter 两道闸，
    # 而不是在旧台名下带着证据绕过去再原样塞进新台。
    logo_moves = remount_by_logo(channels, load_wshi_moves(args.wshi_moves))

    # 标清闸门（用户 10-25「全频道删除标清线路…删除没有线路的频道」）。
    # 位置就这一处：在手工备用线之后、strict_filter 之前，摘线只留一个出口，
    # 免得出现「这一处放行、那一处又判一遍」两套口径。
    SD_DROPPED.clear()
    SD_EXEMPT.clear()
    heads = {ch["id"]: (ch["streams"] or [""])[0] for ch in channels}
    if DROP_SD:
        channels, sd_empty = drop_sd_lines(channels)
        by_size = defaultdict(int)
        for _, _, size in SD_DROPPED:
            by_size["×".join(str(v) for v in size) if isinstance(size, tuple) else "标题写着标清"] += 1
        print(f"[definition] 标清线路删除 {len(SD_DROPPED)} 条（{len({n for n, _, _ in SD_DROPPED})} 台），"
              f"按尺寸分档：" + "、".join(f"{k} {v} 条" for k, v in sorted(by_size.items(), key=lambda kv: -kv[1]))
              + f"；必留名单里整台都是标清、原样保留 {len(SD_EXEMPT)} 条")
        if sd_empty:
            print(f"[definition] 因标清摘空、整台删除 {len(sd_empty)} 台："
                  + "、".join(sorted(sd_empty)[:15]) + (f"…另 {len(sd_empty) - 15} 台" if len(sd_empty) > 15 else ""))
        moved = [ch["name"] for ch in channels
                 if ch["category"] in RES_FIRST_CATS and ch["streams"] and ch["streams"][0] != heads.get(ch["id"])]
        print(f"[definition] 组内按清晰度由高到低重排（{'、'.join(CATEGORY_NAMES[c] for c in RES_FIRST_CATS)}）："
              f"{len(moved)} 台的第 1 条线换了，例：" + "、".join(moved[:8]))

    # 新判据三半，各自的作用域写清楚（见 strict_filter 的 docstring）：
    #   ①必看台「删除不能正常播放的线路」：台内只留有正常画面证据的线；
    #     一条都没有的台**先原样留着**，交给结尾 [check] 判红点名——不许让它悄悄消失，也不许拿坏线冒充可用。
    #   ②卫视组（用户 10-23 17:5x）：无画面证据的整台删掉；活下来的台删掉本机连不通的线。
    #   ③其余各组「无可用源删除频道」：整台没有任何可播源的直接删掉；活下来的台内线路一个都不删。
    # 「待验证」分组从这一版起不再产生：以前是"没画面也留在目录里挪个组"，新判据是直接删。
    survivors, strict_stats = strict_filter(channels)
    channels = survivors
    # 换组这一档的「名单数 vs 真落进目录的台数」两个数都要打：只报前者时，源里没这台、
    # 或被删台规则整台摘掉，都会伪装成"换组已生效"（命中 0 = 只有名单在守，没有真产物）。
    moved, missing = group_move_coverage(channels)
    for ch in moved:
        print(f"[换组] 目录现读 {ch['name']} -> {ch['category']}/{ch['subgroup']}"
              f" id={ch['id']} 线路 {len(ch['streams'])} 条"
              + ("（剪枝仍按它离开的 卫视 组算，线路集不换）" if ch["name"] in SAT_ORDER else
                 "（不在 卫视 名册 ⇒ 没有原组剪枝规则跟着走）"))
    if missing:
        print(f"[换组][警告] 名单 {len(CHANNEL_GROUP_OVERRIDE)} 台、目录里只落了 {len(moved)} 台："
              f"{'、'.join(missing)} 没进目录（源里没有这台，或被删台规则整台摘掉）")
    # 卫视 4K 只留 3840 宽的线（AGENTS「卫视4K频道线路分辨率3840x2160」＋用户 10-30 点档）。
    # 位置在 strict_filter 之后：先让每台过完删线/删台，再搬；搬进来的线跟着基础台重新排一次。
    channels, fourk_moved, fourk_dropped = enforce_sat_4k(channels)
    for fourk_name, base_name, want, got in fourk_moved:
        print(f"[4k] {fourk_name}：非 3840 线 {want} 条 ⇒ 搬进 {base_name} {got} 条"
              f"（其余与原有线路重复，按 canon 去重）")
    if fourk_dropped:
        print(f"[4k] 搬空后整台删除：{'、'.join(fourk_dropped)}")
    print(f"[4k] 卫视 4K 组现读 {sum(1 for ch in channels if ch.get('subgroup') == '4K 超高清')} 台"
          f"（每台线路都必须是 3840 宽）")
    # 尺寸这一份按最后存下来的那些线算（strict_filter 还会摘一批），报数才和目录里的条数自洽。
    line_quality = line_quality_map(channels)
    print(f"[resolution] 随目录打进 APK 的每条线尺寸 {len(line_quality)} 条"
          f"（选线面板按它标清晰度，没实测到的显示「未知」）")
    print(f"[strict] 必看台 {strict_stats['must_total']} 台：删掉没有画面证据的线路 {strict_stats['pruned_lines']} 条；"
          f"仍无可播画面的 {len(strict_stats['must_no_picture'])} 台 -> " +
          ("、".join(strict_stats['must_no_picture']) or "无"))
    if strict_stats['stale_only']:
        # 只报数、不删线：用户 10-22 23:5x「已实证有画面的线路不删除」——旧场次的实证照样算实证。
        print(f"[strict] 其中 {len(strict_stats['stale_only'])} 台靠的是旧场次的画面实证（本轮没复核到），"
              f"要靠模拟器截图定音：{('、'.join(strict_stats['stale_only']))}")
    tier3_names = sorted({name for name, _, _ in strict_stats['tier3_pruned']})
    tier3_by_verdict = {}
    for _, _, verdict in strict_stats['tier3_pruned']:
        key = verdict or "本机连不通"
        tier3_by_verdict[key] = tier3_by_verdict.get(key, 0) + 1
    print(f"[strict] 档 3 删线（作用域＝卫视组＋地方/国际/港澳台，用户 10-27 点档「3」）"
          f"{len(strict_stats['tier3_pruned'])} 条（{len(tier3_names)} 台；按判词："
          + "、".join(f"{k} {v}" for k, v in sorted(tier3_by_verdict.items())) + "）"
          f"，并按地址送进黑名单防设备端更新捞回")
    print(f"[strict] 其余各组无可用源、整台删除 {strict_stats['dropped_no_source']} 台（活下来的台内线路一条没动）")
    logo_only = sorted(strict_stats['logo_only_kept'])
    print(f"[strict] 台级「认台标」那一格救回的台 {len(logo_only)} 台"
          f"（整台没有画面实证、只有本台台标 ⇒ 台留着但报告照旧判红）："
          + ("、".join(logo_only[:12]) + (f"…另 {len(logo_only) - 12} 台" if len(logo_only) > 12 else "")
             if logo_only else "无"))
    pin_kept = sorted(strict_stats['pin_kept'])
    print(f"[strict] 台级「钉回目录」那一格救回的台 {len(pin_kept)} 台"
          f"（名册 tools/demote_lines.json 里 pin 那一格＝用户点档要留，只对端回了坏的这一档；"
          f"台留着但报告照旧判红）："
          + ("、".join(pin_kept) if pin_kept else "无")
          + f"｜名册共钉住 {len(PINNED)} 枚地址")
    print(f"[strict] 特色频道整组删除（用户 10-23 20:0x）{strict_stats['dropped_featured']} 台")
    must_no_picture = strict_stats['must_no_picture']

    channels, slow_moved = move_slow_lines_back(channels, HEALTH, device_proof)
    slow_names = sorted({name for name, _ in slow_moved})
    if slow_moved:
        print(f"[slow] 甲（用户 10-23 20:5x 点档）：首分片 >10 秒的慢线挪到台内最后 "
              f"{len(slow_moved)} 条（{len(slow_names)} 台）：{'、'.join(slow_names)}")
    else:
        print(f"[slow] 甲（用户 10-23 20:5x 点档）：没有要挪的慢线")

    # 用户 10-26 点档「乙」：真死的首条线降序。最后一步，前面的排序不再覆盖它。
    demoted, demote_missing = apply_demotions(channels)
    if demoted:
        print(f"[demote] 乙（用户 10-26 点档）：真死的首条线挪到台内末位 {len(demoted)} 条："
              + "、".join(f"{n}（本台 {k} 条线中排第 {k}）" for n, _, k in demoted))
    else:
        print("[demote] 乙（用户 10-26 点档）：名册里的线都已在末位或未命中")
    print("[check] demote 名册对不上:", "、".join(demote_missing) if demote_missing else "none")

    present = defaultdict(set)
    for ch in channels:
        present[ch["category"]].add(ch["subgroup"])

    categories = []
    for cat in CATEGORY_ORDER:
        subs = [s for s in SUBGROUP_ORDER[cat] if s in present[cat]]
        # 腾讯体育组由设备端手动挂的临时线填充，产物里通常是空的；
        # 分类定义必须留着，否则主页那一列没有这个组、挂上的场次也没有自己的分组。
        if subs or cat == "tencent":
            categories.append({"id": cat, "name": CATEGORY_NAMES[cat], "subgroups": subs})

    # 目录自己留下的线不能同时进黑名单：黑名单是给设备端「别再把它捞回来」用的，
    # 两边都从同一批读数算，但 usable() 是按「名字+地址」判的，身份档能把它救回来。
    kept = {stream_rules.canon(s) for ch in channels for s in ch["streams"]}
    blocked, exempted = blocked_lines(kept)
    # 卫视组刚删掉的那批「本机连不通」线：目录里没了，设备端自动更新还会从公开源一遍遍捞回来
    # （10-22 模拟器现读：一次开机后台更新就把缓存写成比内置目录多 561 条线）。
    # 和黑名单同一批交付，键仍排好序（这一份的顺序不许随证据文件的传法变）。
    for _, canon, verdict in strict_stats['tier3_pruned']:
        if canon:
            blocked[canon] = verdict or "本机连不通"
    # 用户点档的「内容漂移」线：证据还是好证据（可取到分片＋旧复核台标相符），按证据算它不坏，
    # 所以进不了上面的自动黑名单；但电视上现在放的不是本台画面（用户 10-23 18:5x 现读）。
    # 目录侧由 dead_stream_prefixes 按台名摘；这里按地址拉黑，防设备端更新从 vbsky 捞回。
    user_flagged_blocks = {
        "http://[2409:8087:1a01:df::4001]/PLTV/88888888/224/3221225655/index.m3u8": "IPv6 2409 在此网络连续超时（用户 10-24「咪咕体育线路2 响应超时」）",
        "http://[2409:8087:1a01:df::4005]/PLTV/88888888/224/3221225651/index.m3u8": "IPv6 2409 在此网络连续超时（用户 10-24「咪咕体育线路2 响应超时」）",
        "http://[2409:8087:1a01:df::4023]/PLTV/88888888/224/3221225611/index.m3u8": "IPv6 2409 在此网络连续超时（用户 10-24「咪咕体育线路2 响应超时」）",
        "http://[2409:8087:1a01:df::4005]/PLTV/88888888/224/3221225613/index.m3u8": "IPv6 2409 在此网络连续超时（用户 10-24「咪咕体育线路2 响应超时」）",
        "http://[2409:8087:1a01:df::4017]/PLTV/88888888/224/3221225619/index.m3u8": "IPv6 2409 在此网络连续超时（用户 10-24「咪咕体育线路2 响应超时」）",
        "http://[2409:8087:1a01:df::4058]/PLTV/88888888/224/3221225620/index.m3u8": "IPv6 2409 在此网络连续超时（用户 10-24「咪咕体育线路2 响应超时」）",
        "http://[2409:8087:1a01:df::4014]/PLTV/88888888/224/3221225617/index.m3u8": "IPv6 2409 在此网络连续超时（用户 10-24「咪咕体育线路2 响应超时」）",
        "http://[2409:8087:1a01:df::4015]/PLTV/88888888/224/3221225639/index.m3u8": "IPv6 2409 在此网络连续超时（用户 10-24「咪咕体育线路2 响应超时」）",
        "http://[2409:8087:1a01:df::4015]/PLTV/88888888/224/3221225641/index.m3u8": "IPv6 2409 在此网络连续超时（用户 10-24「咪咕体育线路2 响应超时」）",
        "http://[2409:8087:1a01:df::4011]/PLTV/88888888/224/3221225643/index.m3u8": "IPv6 2409 在此网络连续超时（用户 10-24「咪咕体育线路2 响应超时」）",

        stream_rules.canon("http://ali-m-l.cztv.com/channels/lantian/channel21/1080p.m3u8"):
            "画面是别的台（用户现读）",
        stream_rules.canon("http://bztv.tvbus.cc:8081/cdnlive/cctv8.m3u8"):
            "时有时无（近 6 轮连不上＋蓝屏帧，用户 10-23 22:5x 目标摘除）",
        # 与上面 dead_stream_prefixes 里 CCTV-2 那一条同批：目录侧摘掉，设备端自动更新也不许再捞回来。
        stream_rules.canon("http://8.138.7.223/tv/api.php?id=cctv2"):
            "真机现读是「服务器超负载…微信扫码支持」告警页（用户 10-24 11:0x）",
        # #97「补」：这条线抽到的帧左上角是 KTTV-1（不是新疆卫视），目录里已按「借来的台标」摘掉，
        # 但这台主机还在公开源里挂着新疆卫视的名字 ⇒ 设备端自动更新会原样捞回来并放行。
        # 键按台账里那一枚的字面串取（canon 不按分号切），与目录/台账里那条是同一个键。
        stream_rules.canon("http://218.84.12.186:8001/hls/main/playlist.m3u8"
                           "?zxinjd;http://218.84.12.186:8001/hls/main/playlist.m3u8zxinjd"):
            "画面是别的台（左上角 KTTV-1＋满屏「再见」，用户 10-24 点档 #97「补」）",
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
        "lineQuality": line_quality,
        # 改挂记录与黑名单同一用途：公开列表明天还会按「旧台名」把这条地址再送一遍，
        # 设备端合并时按它挡回，防止挂在错误台名下复活（用户 10-25「根据台标移入对应卫视频道」）。
        "lineMoves": logo_moves,
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
    # 两份手写名单要交叉对表：`dead_stream_prefixes` 按台名摘掉的线，后面的 `manual_backups`
    # 与 `sat_manual_backups` 又是"追加"语义，会把它原样加回来（10-24 现读：CCTV-4K 那条
    # 8.138.7.223 代理就是这么回到目录里的——摘它的依据是"模拟器两次没起画面"，
    # 而它在身份台账里是「台标相符」，两条规则在打架，谁赢要用户裁，不许构建替它选）。
    readded = [(ch["name"], s) for ch in channels for s in ch["streams"]
               if s.startswith(dead_stream_prefixes.get(ch["name"], ()))]
    print(f"[check] 按台名摘掉又被备份名单加回来的线: {len(readded)} 条"
          + ("" if not readded else "（两份名单口径不一，见待办 #94）"))
    for name, s in readded:
        print(f"[check]   {name} | {s}")
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
    ap.add_argument("--review", type=Path, action="append", default=None,
                    help="门禁各轮出的人工看图台账 review.json（台标结论）；不给就自动收 "
                         f"{CACHE / 'gate'} 下 round_*/review.json，后一轮覆盖前一轮")
    ap.add_argument("--frames", type=Path, action="append", default=None,
                    help=f"抽帧得到的原生画面尺寸与占位页结论（jsonl），可重复给多份；"
                         f"默认那一份 {DEFAULT_FRAMES} 始终参与，额外给的按线路覆盖它")
    ap.add_argument("--freeze", type=Path, action="append", default=None,
                    help="freeze_scan.py 的长窗冻帧结论（jsonl），可重复给多份；"
                         "只有分钟级确认「源没在给新画面」的那一档会删线＋拉黑")
    ap.add_argument("--retest", type=Path, default=None,
                    help="三轮全线路回测的汇总（tools/retest3_summary.py 的 --out 产物）")
    ap.add_argument("--cctv3", type=Path, action="append", default=None,
                    help="央视·卫视全线路三轮回测的删除清单（回测目录里的 final_deletions.json）："
                         "≥3 轮都无本台台标、三轮都没帧且无台标实证、"
                         "或任一轮抽出「服务器超负载…微信扫码」告警页的线路。"
                         "可重复给多份（每批复测一份）：blockedLines 是构建的产物、不回读上一版资产，"
                         "少传一份就等于把那一批删过的地址放回公开源捞回来的通道里")
    ap.add_argument("--wshi-moves", type=Path, default=BASE / "tools" / "wshi_logo_moves.json",
                    help="卫视「按台标改挂」的人工确认清单（json）：探测器 wshi_mismatch.py 挑出、"
                         "单张角标复核过的地址才写进来。默认那份在 tools/ 下随仓库走，"
                         "不放 tools/.cache/（那里被 .gitignore 收着，换机器就会把结论丢掉）")
    ap.add_argument("--keep-dead", action="store_true",
                    help="保留「对端明确答复了坏／画面是别的台」的线路（默认删掉，删空的频道整个消失）")
    ap.add_argument("--keep-sd", action="store_true",
                    help="保留标清线路（默认删掉：实测尺寸不到高清档、或清单标题写着「标清/SD」的线"
                         "都不进目录。用户 10-25「全频道删除标清线路，未来新增的线路也不添加标清线路」）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只算数不落盘：量统计与退出码时保护默认路径那份在库资产（给了 --out 也不写）")
    raise SystemExit(build(ap.parse_args()))
