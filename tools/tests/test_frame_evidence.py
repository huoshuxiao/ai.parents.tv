#!/usr/bin/env python3
"""验一件事：抽帧抽到占位页的线路，在目录生成侧必须算「已确认坏」，不算「可用证据」。

这是「线路可用性优于频道数量」最容易被糊过去的一格——占位页也是画面，
只看「有没有抽到帧」会把测试卡顶到第一条。夹具是合成的，不联网：
正对照＝带 placeholder 字段的行必须被判坏且被 usable() 拒掉；
负对照＝同一行去掉 placeholder 字段必须判好（证明起作用的是那一格，不是别处的规则）。

后半批（10-23）验的是这条判据改软之后的**边界**：用户 10-23 02:14「CCTV-4K、CCTV 8K 有台标
就算真出画面」，报告侧 `prepack_report.card_is_picture` 必须与构建侧同一口径——
名单外的台、没人写过台标结论的帧、二维码推广页那三类都不许被顺手放行。

最后一批（10-24，用户「保留有台标的线路」）验的是**保留**那一侧：抽到广告/占位页时，
台标验过的线只降分不摘线，且台标证据有两个来源（手抄的身份台账 + 门禁各轮 review.json 的看图结论）；
容器解不开、长窗冻帧、结论写着别的台名这三类都不许借这条被救回来。
人工看图那一格接进构建时**只降分**：摘线那一侧仍只认钉死的画面指纹，一枚旧帧不按稳定坏处置。

跑法：python3 tools/tests/test_frame_evidence.py    （退出码 0 才算过）
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402

CLEAN = "http://1.2.3.4:9901/tsfile/live/0101_1.m3u8"
PLACEHOLDER = "http://5.6.7.8:9901/tsfile/live/0102_1.m3u8"
NAME = "某某卫视"


def rows(with_placeholder: bool):
    out = [{"channel": NAME, "line_index": 0, "url": CLEAN,
            "width": 1920, "height": 1080, "bytes": 200_000, "frame": "/tmp/a.jpg",
            "placeholder": ""}]
    if with_placeholder:
        out.append({"channel": NAME, "line_index": 1, "url": PLACEHOLDER,
                    "width": 1280, "height": 720, "bytes": 90_000, "frame": "/tmp/b.jpg",
                    "placeholder": "占位页：CCTV 测试卡"})
    else:
        out.append({"channel": NAME, "line_index": 1, "url": PLACEHOLDER,
                    "width": 1280, "height": 720, "bytes": 90_000, "frame": "/tmp/b.jpg"})
    return out


def load(with_placeholder: bool) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
        for row in rows(with_placeholder):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        path = Path(fh.name)
    try:
        bc.FRAME_RES, bc.FRAME_PLACEHOLDER, bc.FRAME_STALLED = bc.load_frames(path)
    finally:
        path.unlink()


def main() -> int:
    failures = []
    bc.HEALTH, bc.IDENTITY, bc.DROP_DEAD = {}, {}, True

    load(True)
    if bc.evidence(PLACEHOLDER, NAME)[0] != bc.BAD_TIER:
        failures.append(f"占位页线路判成 {bc.evidence(PLACEHOLDER, NAME)}，应为档 {bc.BAD_TIER}")
    if bc.usable(NAME, PLACEHOLDER):
        failures.append("占位页线路还被 usable() 放行 ⇒ 它会带着测试卡留在目录里")
    if bc.evidence(CLEAN, NAME)[0] != 2:
        failures.append(f"正常帧线路被判成 {bc.evidence(CLEAN, NAME)}，应为「没测过」档 2")
    if not bc.usable(NAME, CLEAN):
        failures.append("正常帧线路被拒 ⇒ 判据过牙")
    if bc.resolution_rank(PLACEHOLDER) == 0:
        failures.append("占位页的尺寸被丢了 ⇒ 负对照与正对照混在了一起")

    load(False)
    if bc.stream_rules.canon(PLACEHOLDER) in bc.FRAME_PLACEHOLDER:
        failures.append(f"没给 placeholder 字段却读出了占位页结论 {bc.FRAME_PLACEHOLDER}")
    if bc.FRAME_PLACEHOLDER.get(bc.stream_rules.canon(CLEAN)) != "":
        failures.append("空串＝「看过、画面正常」这条明确意见没被登记 ⇒ "
                        "多份证据累加时清不掉早先的判坏结论，会漏回坏线")
    if bc.evidence(PLACEHOLDER, NAME)[0] == bc.BAD_TIER:
        failures.append("去掉 placeholder 字段后仍判坏 ⇒ 抓到的不是那一格，是别处的规则")
    if not bc.usable(NAME, PLACEHOLDER):
        failures.append("去掉 placeholder 字段后被拒 ⇒ 这一格不是唯一变量")

    # ── 报告侧的同一把尺：prepack_report.card_is_picture（用户 10-23 02:14「有台标就算真出画面」）
    # 构建侧放宽、报告侧不放宽 = 同一件事两层读数互相打架（10-22 踩过）。这四格盯的是报告那一层。
    import prepack_report as pr  # noqa: E402
    OK4K = "http://gslb.example.com/cctv4k.m3u8"
    CARD = "占位页：CCTV 4K 超高清 测试卡（色条 +「3840×2160/50/P」）"
    AD = "占位页：epg.pw 二维码推广页（「Scan the QR code…download our latest APP」）"
    logo_ok = {OK4K: {"logo": "台标相符（左上角「CCTV 4K 超高清」）", "picture": "测试卡"}}
    note = pr.card_is_picture("CCTV-4K 超高清", CARD, OK4K, logo_ok)
    if not note:
        failures.append("报告侧没放行：必看台＋测试卡＋人工台标相符 ⇒ 它还会被 G2/G4 判成没画面")
    if pr.card_is_picture("河南卫视", CARD, OK4K, logo_ok):
        failures.append("报告侧放宽外溢到名单外的台了（用户那句只点 4K/8K，加必看台是我按新判据收的）")
    if pr.card_is_picture("CCTV-4K 超高清", CARD, OK4K,
                          {OK4K: {"logo": pr.PENDING, "picture": pr.PENDING}}):
        failures.append("没人写过台标结论也放行 ⇒ 这一格变成「有身份栏就通过」，判据没牙")
    if pr.card_is_picture("CCTV-4K 超高清", AD, OK4K, logo_ok):
        failures.append("推广页那一类被一起放行了 ⇒ 用户 10-22 那句「占位页赢」被顺手改软")

    # ── 「保留有台标的线路」（用户 10-24）：抽到广告/占位页只降分，摘线与拉黑要让位给台标证据。
    # 这一族最容易写成恒真：所以每一格都成对给「证据在＝保留」与「证据换成别的＝照旧摘」，
    # 并且把三条不该被放宽的路（容器解不开／长窗冻帧／结论是别的台的台标）单独钉住。
    AD_URL = "http://9.9.9.9/live/adline.m3u8"
    AD_CANON = bc.stream_rules.canon(AD_URL)
    CARD_LABEL = "占位页：CCTV 测试卡（本台色条卡）"
    AD_LABEL = "占位页：满屏购物广告＋二维码"
    bc.FRAME_RES = {AD_CANON: [1920, 1080]}
    bc.FRAME_PLACEHOLDER = {AD_CANON: AD_LABEL}
    bc.FRAME_STALLED, bc.FROZEN, bc.FROZEN_NO_SIBLING = {}, {}, {}
    bc.HEALTH = {AD_CANON: {"verdict": "可取到分片"}}
    bc.IDENTITY, bc.REVIEW_LOGO = {}, {}
    if bc.evidence(AD_URL, NAME)[0] != bc.BAD_TIER or bc.usable(NAME, AD_URL):
        failures.append("没有任何台标证据时这条线被保留了 ⇒ 这一格没牙，放宽成了「有帧就留」")
    bc.REVIEW_LOGO = {AD_CANON: {"logo": "台标相符（左上「某某卫视」）", "channel": NAME}}
    if bc.evidence(AD_URL, NAME)[0] != 1 or not bc.usable(NAME, AD_URL):
        failures.append("review.json 里台标相符仍被摘线 ⇒ 「保留有台标的线路」没接进构建侧")
    if bc.evidence(AD_URL, "隔壁卫视")[0] != bc.BAD_TIER:
        failures.append("看图结论写着 A 台，B 台借它说话也放行 ⇒ 按地址放宽过头")
    bc.REVIEW_LOGO = {AD_CANON: {"logo": "画面是别的台（左上「CCTV 13 新闻」）", "channel": ""}}
    if bc.usable(NAME, AD_URL):
        failures.append("「画面是别的台」被当成台标证据 ⇒ 错配的地址会被留在 B 台名下")
    bc.REVIEW_LOGO = {AD_CANON: {"logo": "台标相符（左上「某某卫视」）", "channel": NAME}}
    bc.HEALTH = {AD_CANON: {"verdict": "容器不支持"}}
    if bc.usable(NAME, AD_URL):
        failures.append("ExoPlayer 解不开容器的线被台标证据救回 ⇒ 电视上还是播不出来")
    bc.HEALTH = {AD_CANON: {"verdict": "可取到分片"}}
    bc.FROZEN = {AD_CANON: "冻帧：连续 13 帧字节相同"}
    if bc.usable(NAME, AD_URL):
        failures.append("长窗冻帧被台标证据救回 ⇒ 源在喂旧画面，这一族不在放宽范围内")
    bc.FROZEN = {}
    bc.REVIEW_LOGO = {AD_CANON: {"logo": "台标相符（左上「某某卫视」）", "channel": ""}}
    bc.AMBIGUOUS_LOGO = {AD_CANON}
    if bc.usable(NAME, AD_URL):
        failures.append("看图结论没写台名、地址又同时挂在两个台名下，仍被借证保留 ⇒ 改挂那一类会串台")
    bc.AMBIGUOUS_LOGO = set()
    if not bc.usable(NAME, AD_URL):
        failures.append("把歧义那一格摘掉后仍不保留 ⇒ 咬人的不是 AMBIGUOUS_LOGO 这一格（负对照）")
    bc.AMBIGUOUS_LOGO = set()
    bc.REVIEW_LOGO, bc.IDENTITY = {}, {}        # 这两格清空才是「没人验过台标」那两种形状
    bc.FRAME_PLACEHOLDER = {AD_CANON: CARD_LABEL}
    MUST = "CCTV-11 戏曲"        # 在 CARD_IS_PICTURE_CHANNELS 名单里（必看 20 台）
    if bc.has_picture(MUST, AD_URL):
        failures.append("名单内的台没验过台标也算画面 ⇒ 10-23 02:14 那条例外外溢了")
    if bc.has_picture(NAME, AD_URL):
        failures.append("名单外的台用测试卡冒充画面 ⇒ 10-23 02:14 那条例外外溢了")
    bc.REVIEW_LOGO = {**bc.REVIEW_LOGO, AD_CANON: {"logo": "台标相符（左上「CCTV 11 戏曲」）",
                                                   "channel": MUST}}
    if not bc.has_picture(MUST, AD_URL):
        failures.append("台标相符的本台测试卡仍不算画面 ⇒ 与 10-23 02:14 口径相反"
                        "（CCTV-11/14/15 三条就是这么被摘掉的）")
    bc.IDENTITY = {f"{MUST}|{AD_CANON}": "台标相符"}
    if not bc.has_picture(MUST, AD_URL):
        failures.append("身份台账里台标相符仍不算画面 ⇒ 旧那条路被改坏了")

    # ── 人工看图的「广告」只降分、不摘线（10-24 自查：把看图意见接进构建时差点多摘一条必看台的线）。
    # 这一族的边界：机器指纹＝钉死的稳定坏（照旧摘线＋拉黑），人工那一枚＝一次观测（只降到档 1）。
    # 现读证据：CCTV-6 那条 74.91.26.218 的药品广告帧在 7 份 review.json 里 md5 全是 456f0b637421
    # （10-23 03:26 抽的那一张，03:40 被证据搬运抄了 7 遍）——「7 轮都判广告」是同一张图的 7 个副本。
    HUMAN = "http://10.10.10.10/live/humanad.m3u8"
    HUMAN_CANON = bc.stream_rules.canon(HUMAN)
    bc.FRAME_PLACEHOLDER, bc.FRAME_RES = {}, {HUMAN_CANON: [1920, 1080]}
    bc.FRAME_STALLED, bc.FROZEN, bc.AMBIGUOUS_LOGO = {}, {}, set()
    bc.HEALTH = {HUMAN_CANON: {"verdict": bc.GOOD_VERDICT}}
    bc.IDENTITY, bc.REVIEW_LOGO = {}, {}
    bc.REVIEW_LOGO[HUMAN_CANON] = {"logo": "台标看不到（整帧没有角标）",
                                   "picture": "广告（满屏药品广告）", "channel": NAME}
    bc.REVIEW_PICTURE = bc.review_cards(bc.REVIEW_LOGO)
    if bc.review_card(NAME, HUMAN_CANON) == "":
        failures.append("台账里那枚「广告」根本没被读到 ⇒ 下面两格是空转（我造的前提没自证）")
    if bc.evidence(HUMAN, NAME)[0] != 1 or not bc.usable(NAME, HUMAN):
        failures.append("人工看图说「广告」就把线摘了 ⇒ 一次观测按稳定坏处置，必看台白少一条真源")
    if bc.blocked_lines()[0].get(HUMAN_CANON):
        failures.append("只降分的地址也进了黑名单 ⇒ 设备端「立即更新线路」会被永久挡回去")
    bc.FRAME_PLACEHOLDER = {HUMAN_CANON: "占位页：满屏购物广告＋二维码"}
    if bc.evidence(HUMAN, NAME)[0] != bc.BAD_TIER or bc.usable(NAME, HUMAN):
        failures.append("同一枚意见改由画面指纹给出时没摘线 ⇒ 上面那格放宽外溢到了稳定坏那一族")
    if not bc.blocked_lines()[0].get(HUMAN_CANON):
        failures.append("指纹占位页没进黑名单 ⇒ 摘了线还挡不住公开列表把它捞回来（负对照）")
    bc.FRAME_PLACEHOLDER = {}
    bc.REVIEW_LOGO = {HUMAN_CANON: {"logo": "台标相符（左上「某某卫视」）",
                                    "picture": "正常节目画面（《某某》）", "channel": NAME}}
    bc.REVIEW_PICTURE = bc.review_cards(bc.REVIEW_LOGO)
    if bc.review_card(NAME, HUMAN_CANON):
        failures.append("「正常节目画面」被当成占位页意见 ⇒ CARD_LIKE 那一族没拦住，判据反向咬人")

    # ── 「本台名下没有一枚帧认得出本台台标」⇒ 这条线不给画面分（用户 10-24 点档 #96＝乙）。
    # 触发形状是安多卫视那条线：11 枚唯一帧看过 9 枚＋16:44 复抽 3 枚全是西宁市台，
    # 旧口径「有画面」不问「是谁的画面」，于是它一直占着画面分、台也留着。
    BORROW = "http://11.22.33.44/live/borrowed.m3u8"
    BORROW_CANON = bc.stream_rules.canon(BORROW)
    bc.FRAME_PLACEHOLDER, bc.FRAME_RES = {}, {BORROW_CANON: [1920, 1080]}
    bc.FRAME_STALLED, bc.FROZEN, bc.AMBIGUOUS_LOGO = {}, {}, set()
    bc.HEALTH = {BORROW_CANON: {"verdict": bc.GOOD_VERDICT}}
    bc.REVIEW_PICTURE = {}
    # 正对照 1：台账与人工都说「是别的台」⇒ 不算本台画面（判定层两把尺都要变）
    bc.IDENTITY = {f"{NAME}|{BORROW_CANON}": "画面是别的台"}
    bc.REVIEW_LOGO = {BORROW_CANON: {"logo": "台标是别的台（画面写着「XNTV 西宁市广播电视台」）",
                                     "picture": "测试卡（别的台的色条卡）", "channel": NAME}}
    if not bc.no_own_logo(NAME, BORROW):
        failures.append("台账＋人工都点名别的台，新尺却没抓到 ⇒ 安多卫视那一格还会照样占画面分")
    if bc.has_picture(NAME, BORROW) or bc.has_picture_now(NAME, BORROW):
        failures.append("「是别的台的画面」仍算本台画面 ⇒ 判定层没接上新尺（父母打开看到的是别家台）")
    # 正对照 2：只有人工那一枚反证也成立（旧台账没写过这一对，10-21 那批手抄有漏）
    bc.IDENTITY = {}
    if not bc.no_own_logo(NAME, BORROW):
        failures.append("只有人工看图点名别的台时新尺不生效 ⇒ 手抄台账漏记的那批继续漏")
    # 正对照 3：生产里真实的那一形——台账与人工两格都在（人工 picture 写着「测试卡（别的台的色条卡）」）。
    # 这一形里 evidence() 走 human_card 那一支只降到档 1（线不摘、地址不拉黑），
    # 新尺只在判定层起作用：画面分不给，于是"整台只剩这一条线"时由 ②/③ 的无源删台收掉台。
    bc.REVIEW_PICTURE = bc.review_cards(bc.REVIEW_LOGO)
    if bc.review_card(NAME, BORROW_CANON) == "":
        failures.append("这一格没造出人工 picture 那一枚 ⇒ 变成空转（我造的前提没自证）")
    bc.REVIEW_LOGO = {BORROW_CANON: {"logo": "台标是别的台（画面写着「XNTV 西宁市广播电视台」）",
                                     "picture": "测试卡（别的台的色条卡）", "channel": NAME}}
    bc.REVIEW_PICTURE = bc.review_cards(bc.REVIEW_LOGO)
    if bc.evidence(BORROW, NAME)[0] != 1 or not bc.usable(NAME, BORROW):
        failures.append("新尺把线摘了 ⇒ 外溢到删除那一侧（乙 只管判定层，摘线仍只认钉死的画面指纹）")
    if bc.blocked_lines()[0].get(BORROW_CANON):
        failures.append("新尺把地址拉黑了 ⇒ 会把真源台（西宁新闻综合）的同一条地址一起挡掉")
    if bc.has_picture(NAME, BORROW):
        failures.append("这一形里画面分没扣掉 ⇒ 安多卫视在生产数据上还是留得住（正对照 1 是合成简化形）")
    # 负对照 1：漂移不算（延边卫视型——同一地址时而本台、时而别家）
    bc.REVIEW_LOGO = {BORROW_CANON: {"logo": "台标相符（左上「某某卫视」）",
                                     "picture": "正常节目画面", "channel": NAME}}
    if bc.no_own_logo(NAME, BORROW) or not bc.has_picture(NAME, BORROW):
        failures.append("有一枚「台标相符」还判无本台标 ⇒ 延边卫视/CCTV-7/CCTV-8 那三条漂移线会被误摘")
    # 负对照 2：「台标看不到」不是反证（河北卫视那条 live101 六轮都写看不到，第一次跑误杀两台）
    bc.IDENTITY = {}
    bc.REVIEW_LOGO = {BORROW_CANON: {"logo": "台标看不到（画面是户外公园外景，左上/右上都没有台标）",
                                     "picture": "正常节目画面", "channel": NAME}}
    if bc.no_own_logo(NAME, BORROW) or not bc.has_picture(NAME, BORROW):
        failures.append("「台标看不到」被当成反证 ⇒ 直播外景没角标的台会被整台删掉（收窄前实测多切 2 台）")
    # 负对照 3：没有任何观察就不发明
    bc.REVIEW_LOGO = {}
    if bc.no_own_logo(NAME, BORROW) or not bc.has_picture(NAME, BORROW):
        failures.append("台账与人工都没写过这一对却判「无本台标」 ⇒ 没观察也敢下结论")
    # 负对照 4：反证挂在别的台名下，不许替本台说话（与 logo_verified 同一套归属尺）
    bc.REVIEW_LOGO = {BORROW_CANON: {"logo": "台标是别的台（写着别家台名）",
                                     "picture": "正常节目画面", "channel": "另一个台"}}
    if bc.no_own_logo(NAME, BORROW) or not bc.has_picture(NAME, BORROW):
        failures.append("别人家的反证算进了本台头上 ⇒ 归属尺在两条路上口径不一")

    # ── 报告侧（门禁 G4）与构建侧共用同一个纯函数（10-24 17:0x 之后）：两层口径不许分叉。
    # 这一格用真函数、真台账路径无关的合成观察，证明"拼观察列表"这一步没把归属尺写歪：
    # 台账说别的台 ⇒ 报告侧也判不算；台账说相符 ⇒ 报告侧照旧算（漂移）。
    import prepack_report as pr
    pr.IDENTITY_LEDGER = {f"{NAME}|{BORROW_CANON}": "画面是别的台"}
    if not pr.other_station_frame(NAME, BORROW, {}):
        failures.append("报告侧没读到台账那一枚「别的台」 ⇒ G4 会继续把别家台画面算成本台画面")
    pr.IDENTITY_LEDGER = {f"{NAME}|{BORROW_CANON}": "台标相符"}
    if pr.other_station_frame(NAME, BORROW, {}):
        failures.append("报告侧把「台标相符」也判成别的台 ⇒ 漂移线在门禁那边会被打成红")
    pr.IDENTITY_LEDGER = {}
    if pr.other_station_frame(NAME, BORROW, {}):
        failures.append("报告侧在没有任何观察时也下结论 ⇒ 与构建侧第①条边界（没观察不发明）相反")
    pr.IDENTITY_LEDGER = None

    for line in failures:
        print(f"[frame-evidence] 不通过 {line}")
    print(f"[frame-evidence] 结论：{'通过' if not failures else f'{len(failures)} 格不通过'}"
          f"（正对照 16 格 + 负对照 25 格）")
    return 1 if failures else 0


if __name__ == "__main__":
    importlib.reload(bc)
    raise SystemExit(main())
