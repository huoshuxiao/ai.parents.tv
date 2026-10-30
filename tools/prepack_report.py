#!/usr/bin/env python3
"""打包前线路检测报告：央视 / 卫视 / 辽宁体育。

「能看」按三级证据逐层记，不合并成一句「可用」：
  1) 清单可达 —— HTTP 2xx 且正文真是 #EXTM3U（302 跟随后仍要是清单）；
  2) 分片可取 —— 真的拉到 >1KB 媒体字节（复用 tools/health_probe.py 的判定）；
  3) 画面出帧 —— VLC 抽到 jpg（复用 tools/probe_channels.py 的 capture_frames）。
台标是不是这个台仍由人工看缩略图后写 `<out>/review.json`，脚本只标「待人工复核」。

    python3 tools/prepack_report.py --apk dist/ParentsTV-v1.1.5-debug.apk
    python3 tools/prepack_report.py --use-cache      # 沿用本次缓存里的清单/分片读数
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "tools"))
import health_probe  # noqa: E402
import probe_channels  # noqa: E402
import review_carry  # noqa: E402
import stream_rules  # noqa: E402
import build_catalog  # noqa: E402   # 必看台名单只这一份（MUST_PLAY_CHANNELS）

DEFAULT_OUT = BASE / "tools" / ".cache" / "prepack"
DEFAULT_DIST = BASE / "dist"
FIXTURES = BASE / "tools" / "fixtures"
# 帧目录旁记一笔「这一帧是哪条地址抽来的」：帧是按线路号命名的，目录一改号线就挂错。
SOURCE_NOTE = "frame_source.json"
UA = health_probe.UA
FRAME_OK_BYTES = 4_000
# 出帧结论里，只有这两种算「正常画面」；其余（广告、占位页、二维码、别的台）都不许进包。
GOOD_LOGO = "台标相符"
GOOD_PICTURE = "正常节目画面"
# review.json 里没写结论的帧一律按「待人工复核」计，不折成通过。
PENDING = "待人工复核"
# 一张帧的四态：门禁（G2/G3）与复核队列都只认这一个函数算出来的结果，不许各写一套。
# 10-26 用户点档：「只被自动指纹认成占位页、台标那一半从没人写过」的帧算「待人工复核」，
# 不许算成「画面异常」——没人写过台标结论就不算判过（r58 那 15 张红里就有 2 张是这种情况）。
STATE_JUDGED = "已判"
STATE_CARRIED = "沿用"
STATE_PENDING = PENDING
STATE_RED = "判红"
CARRIED_MARK = "（沿用 "

# 已知「不是节目画面」的画面指纹：dHash（8×8 相邻像素梯度，64 位）+ 一句话判词。
# 用户 2026-10-22 要求「必须排除广告画面」「画面不能有二维码」，全频道口径下几百张帧逐张看不过来，
# 所以把亲眼判过的推广页/测试卡按指纹钉住，同图的直接自动判红。
# 阈值 8 是量出来的（10-22 02:45，本批 35 张正常画面）：正常帧到这三枚的最小汉明距离
# 分别是 16 / 24 / 23，而两张不同频道的 epg.pw 推广页之间距离是 0。
FRAME_FINGERPRINTS = (
    (0x188F8E8C4ECDCF0F, "占位页：epg.pw 二维码推广页（「Scan the QR code…download our latest APP」）"),
    (0x2F69686860603DE3, "占位页：CCTV 测试卡（色条卡家族：各台的卡面同一族，归属看左上角台标）"),
    (0x2F6F222120203DE3, "占位页：CCTV 测试卡（少儿）（色条卡家族：这一枚是从少儿那张钉的，"
                        "别的台端出自己的色条卡也会落在这里，归属看台标不看这一行）"),
    # 以下 7 枚来自 10-22 13:31 那场全频道跑的人工看图结论（`<out>/review.json`）：
    # 那 7 张帧离上面三枚 25–32 格，库里不收，只能各钉一枚——它们彼此也相距 >12，并不成一枚。
    # 钉之前量过咬合余量：这 7 张离人工判为「正常节目画面」的 26 张最近 18 格，阈值 8 留 10 格。
    (0x4D4F1F3F37337170, "占位页：西宁市广播电视台测试卡（挂在安多卫视名下）"),
    (0x6E1F4F793D7DCDCC, "占位页：KRTV-1 测试卡（挂在新疆卫视名下）"),
    (0x4C4C5C5818991909, "占位页：CCTV-7 国防军事测试卡"),
    (0x4A10114543010506, "占位页：CCTV-11 戏曲测试卡"),
    (0xF273231397192979, "占位页：CCTV-14 少儿测试卡"),
    (0x9D9D030303020292, "占位页：CCTV UHD 测试卡（挂在 CCTV-4K 超高清名下）"),
    (0xDE3A3614944ACF0F, "广告：诊室画面 + 两处「咨询热线 400-179-9565」+「27 天专家会诊」"
                         "（挂在 CCTV-15 音乐名下）"),
    # 10-23 01:01 那场（tools/.cache/suspect_probe）抽到的：CCTV-9 纪录 第 2 条线
    # （74.91.26.218/live/cctv9hd.m3u8）给的不是纪录片，是一整屏商品小图加两行
    # 「订购电话：400-689-3979」。它离库里最近的坏帧 19 格、离两张节目画面 22/33 格，
    # 所以只能单独钉一枚；阈值 8 对最近的正常帧留 14 格。
    (0x4A63851103838303, "广告：满屏商品小图 + 两处「订购电话：400-689-3979」"
                         "（挂在 CCTV-9 纪录名下）"),
    # 10-23 01:47 第二批 4K 候选（tools/.cache/cand4k2）里 line0 抽到的：左上角写着
    # 「CCTV 4K 超高清」＋「3840×2160/50/P」＋「CCTV UHD」，整帧是色条卡，中间那块小图在换素材
    # （鸟笼／水果／泳池…）。它离库里那枚 00:13 的「CCTV UHD 测试卡」28 格——中间小图一换，
    # dHash 就散开，所以这一类卡**一枚钉不死**，只能一张一张收；这里收的是鸟笼那一帧。
    # 咬合余量：离库内最近一枚 18 格，离两张人工判为正常节目画面的夹具 33 / 38 格。
    (0x0B4F8E8E0F1BDB83, "占位页：CCTV 4K 超高清 测试卡（色条 +「3840×2160/50/P」，中间在换素材）"),
    # 10-24 11:3x 钉的：`8.138.7.223/tv/api.php?id=cctv16` 在 03:50、04:04、11:3x 三次都端出同一页
    # ——「看到此画面不要着急 是因为服务器超负载所致…可以微信扫码支持一下」＋右边一枚微信收款码
    # （「熬夜更新，请赏杯咖啡~」）。用户 10-24 在真机上看到的 CCTV-2 线路 5 就是这一页，
    # 而冻帧那把尺**看不见它**：这一页会自己刷新，字节一直在变，长窗读的是「画面正常推进」。
    # 咬合余量（11:3x 现量）：离库内最近一枚指纹 21 格、离本轮 20 张「收」的帧最近 18 格，阈值 8 留 10 格。
    (0x2525252527270703, "占位页：源站「服务器超负载…微信扫码支持」告警页（右侧微信收款码，无任何台标）"),
    # 10-25 21:2x 钉的：用户逐张复核 CCTV-4K 超高清 L02/L06 的 R2 判「左右无台标、是广告」——
    # 诊室画面（老先生坐诊、药柜）＋上下两行「订购电话：400-689-3979」，左下「麦可通」小标。
    # 同一支广告在两帧的 dHash 只差 4 格；离库内最近一枚指纹 20 格；离同台 R3 真画面（左上「CCTV 4K」）31 格。
    # 阈值 8 ⇒ 两帧互相覆盖，与真画面留 23 格。
    (0x161E1E19D899FD1F, "广告：诊室画面 + 两处「订购电话：400-689-3979」（挂在 CCTV-4K 超高清名下）"),
)
FINGERPRINT_MAX_DISTANCE = 8
# 纯色帧（整帧没有明暗变化：蓝屏／黑屏）。它证明不了这台在播节目，但也不像测试卡那样
# 是源稳定端出来的页面——抽帧只取第一帧，一次淡入淡出、一次台标转场就能给出纯黑。
# 所以这一格只让「有正常画面」不加分，不写进 placeholder 那一列（写进去会让目录生成侧
# 把这条线判到档 4 直接删掉，代价是把好线删了）。
BLANK_FINGERPRINTS = ((0x0000000000000000, "纯色画面：整帧无明暗变化（蓝屏/黑屏）"),)
# health_probe 嗅探出的容器判定：ExoPlayer 没有对应解复用器，电视上出不了画面（VLC 能解）。
PLAYER_UNDEMUXABLE = "容器不支持"


def dhash(path, size: int = 8) -> int:
    """把画面缩成 8×9 灰度，逐行比较相邻像素的明暗，得到 64 位指纹。"""
    from PIL import Image

    with Image.open(path) as image:
        small = image.convert("L").resize((size + 1, size), Image.BILINEAR)
    pixels = small.load()
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (1 if pixels[x, y] > pixels[x + 1, y] else 0)
    return bits


def hamming(first: int, second: int) -> int:
    return bin(first ^ second).count("1")


def fingerprint_hit(path) -> str | None:
    """画面与某枚已知指纹足够像就返回它的判词；读不出图的按未命中处理。"""
    if not path:
        return None
    try:
        digest = dhash(path)
    except Exception:  # noqa: BLE001 - 坏图交给「抽不到帧」那一格处理
        return None
    for known, verdict in FRAME_FINGERPRINTS:
        if hamming(digest, known) <= FINGERPRINT_MAX_DISTANCE:
            return verdict
    return None


def card_is_picture(name: str, placeholder: str, url: str, review: dict | None) -> str | None:
    """报告这一层的同一把尺：用户 10-23 02:14「CCTV-4K、CCTV 8K 有台标就算真出画面，算正常频道线路」。

    三个条件缺一不可，少一个就照旧按占位页判红：
      ① 台在 `build_catalog.CARD_IS_PICTURE_CHANNELS` 那份名单里（必看 20 台＋CCTV-8K；
         名单只有一份，构建侧与报告侧不许各写一遍——10-22 就是两处各写一份才漏的）；
      ② 这一帧命中的是**测试卡**那类指纹（二维码推广页／满屏商品广告不是这台的内容）；
      ③ `<out>/review.json` 里有人为**这一枚**写过台标结论、且以「台标相符」开头。
    满足就返回一句要跟着指纹一起写进报告的话（这一行是给读报告的人看的，不是给我自己打勾的）。
    """
    if "测试卡" not in (placeholder or ""):
        return None
    if name not in build_catalog.CARD_IS_PICTURE_CHANNELS:
        return None
    logo = ((review or {}).get(url) or {}).get("logo") or ""
    if not logo.startswith("台标相符"):
        return None
    return (f"｜{name} 自己的测试卡、台标人工看过相符（{logo}）"
            "——按用户 10-23 02:14 口径算这一台的画面，不当占位页判红")


IDENTITY_LEDGER: dict[str, str] | None = None


def identity_ledger() -> dict[str, str]:
    """手抄的身份台账（「台名|地址」→ 结论）只读一次：报告侧与构建侧必须用同一批证据。

    为什么要接进来：G4 以前只判到「抽到帧、且不是已知推广页/测试卡同图」这一层，不问这一帧是谁的台标；
    于是安多卫视那条线（画面是西宁市台「新闻综合」）一直给它算「有正常画面」。
    用户 10-24 点档 #96 乙 之后，构建侧已经有这把尺（`build_catalog.no_own_logo`），
    报告侧不同步就是同一件事两处口径不一 —— 门禁绿了、目录里也留着，等于没查。
    """
    global IDENTITY_LEDGER
    if IDENTITY_LEDGER is None:
        path = build_catalog.CACHE / "linecheck" / "identity_review.jsonl"
        IDENTITY_LEDGER = build_catalog.load_identity(path) if path.exists() else {}
    return IDENTITY_LEDGER


def other_station_frame(name: str, url: str, review: dict | None) -> bool:
    """这一 (台名,地址) 的观察里没有一枚认得出本台台标、且有点名别的台的反证 ⇒ 不算本台画面。

    判定本体在 `build_catalog.logo_contradicts_station`（纯函数，两层共用）；
    这里只负责按报告侧的数据拼观察：①身份台账那一枚；②本轮 `review.json` 那一枚（归属要对得上本台名）。
    与构建侧的已知差别：构建侧读**各轮** review（后一轮覆盖前一轮），报告侧只有**本轮**这一份。
    """
    canon = build_catalog.stream_rules.canon(url)
    observations = []
    ident = identity_ledger().get(f"{name}|{canon}")
    if ident:
        observations.append(("台账", ident))
    row = (review or {}).get(url) or {}
    logo = (row.get("logo") or "").strip()
    if logo and logo != PENDING:
        owner = row.get("channel") or row.get("name") or ""
        if owner == name or not owner:
            observations.append(("人工", logo))
    return build_catalog.logo_contradicts_station(observations)


def frame_counts(name: str, line: dict, logo: str, picture: str) -> bool:
    """这一帧算不算「这台能播的画面」——门禁那一层与 card_is_picture 同一把尺。

    正常节目画面当然算；另一类是用户 10-23 02:14 点名的测试卡（名单在
    `build_catalog.CARD_IS_PICTURE_CHANNELS`，必看 20 台＋CCTV-8K，只有一份）。
    推广页／购物广告／别的台的卡都不算，那两类照旧一票否决。
    """
    if picture.startswith(GOOD_PICTURE):
        return True
    url = line.get("url") or ""
    return bool(card_is_picture(name, line.get("placeholder") or picture, url,
                                {url: {"logo": logo}}))


def frame_state(name: str, line: dict, logo: str, picture: str) -> str:
    """这一帧的四态：已判／沿用／待人工复核／判红。**只在这里算**（G2/G3 与复核队列共用）。

    待人工复核：台标或画面那一半没人写过。自动指纹只填「画面不是节目内容」那一半，
    它不算判过——10-26 用户点档，r58 那 2 张「只被指纹认成占位页、台标没人写过」的
    CCTV-4K·线2／CCTV-8K·线3 从「画面异常」挪到这一档。
    沿用：review_carry 按 url 跨轮补的结论（台账在 tools/.cache/gate/review_carry.json）。
    """
    if logo == PENDING or picture == PENDING:
        return STATE_PENDING
    if not (logo.startswith(GOOD_LOGO) and frame_counts(name, line, logo, picture)):
        return STATE_RED
    if CARRIED_MARK in logo or CARRIED_MARK in picture:
        return STATE_CARRIED
    return STATE_JUDGED


def frames_stalled(shots) -> str | None:
    """同一条线抽到的几帧字节完全相同 ⇒ 这段窗口里没有新画面（卡流）。少于两帧不作判。

    为什么用字节而不是像素差：真在动的画面即使镜头很慢，编码器每帧的码流也不会一字不差
    （实测 平湖新闻综合 的慢航拍：4 帧 md5 全不同、像素差 0.002，那是节目不是卡住）；
    而 Bloomberg 那条 5 帧 md5 全同、画面里却带着每秒都在跳的行情时钟 ⇒ 只能是没解出新帧。
    """
    paths = [Path(p) for p in (shots or []) if p and Path(p).exists()]
    if len(paths) < 2:
        return None
    try:
        digests = {hashlib.md5(p.read_bytes()).hexdigest() for p in paths}
    except OSError:
        return None
    if len(digests) == 1:
        return f"卡流：{len(paths)} 帧字节完全相同（抽帧窗口内没有新画面）"
    return None


def blank_hit(path) -> str | None:
    """纯色帧（蓝屏/黑屏）判定：与 fingerprint_hit 同一把尺，但结论只用于「不加分」。"""
    if not path:
        return None
    try:
        digest = dhash(path)
    except Exception:  # noqa: BLE001
        return None
    for known, verdict in BLANK_FINGERPRINTS:
        if hamming(digest, known) <= FINGERPRINT_MAX_DISTANCE:
            return verdict
    return None


def log(message: str) -> None:
    print(message, flush=True)


def is_target(channel: dict, must: tuple[str, ...]) -> bool:
    """必看台判定，与构建侧共用同一份名单（用户 10-22 23:4x 新判据：央视 1-17＋4K 超高清＋辽宁体育；10-23 追加 CCTV-5+ 体育赛事）。

    以前这里只认 subgroup 是央视/卫视，「4K 超高清」那一档根本不在闸门口径里——
    CCTV-4K 超高清抽到测试卡也没人拦。名单从 build_catalog 取，不再手写第二份。
    """
    if channel.get("subgroup") in ("央视", "卫视", "4K 超高清"):
        return True
    name = channel.get("name")
    return name in must or name in build_catalog.MUST_PLAY_CHANNELS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


from catalog_codec import apk_catalog_sha  # noqa: E402  (tools/ 同目录)


def apk_asset_sha(apk: Path, asset: str) -> str:
    with zipfile.ZipFile(apk) as zf:
        return hashlib.sha256(zf.read(asset)).hexdigest()


def run_health(catalog: Path, out_dir: Path, workers: int, use_cache: bool,
               skip_probe: bool = False) -> dict[str, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "line_health.jsonl"
    if skip_probe:
        if not results.exists():            raise SystemExit(f"--rerender 要沿用旧读数，但 {results} 不存在")
        log(f"[health] 沿用旧读数 {results.name}（这一轮一条都没重新探测）")
    else:
        if results.exists() and not use_cache:
            results.unlink()
        command = [
            sys.executable, str(BASE / "tools" / "health_probe.py"),
            "--catalog", str(catalog), "--out", str(out_dir), "--workers", str(workers),
        ]
        log("[health] " + " ".join(command))
        proc = subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        if proc.returncode != 0 and not results.exists():
            raise SystemExit(f"health_probe 失败 EXIT={proc.returncode}，且没有产出读数文件")
    rows: dict[str, dict] = {}
    for line in results.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rows[row["url"]] = row
    return rows


def recheck_must_watch(targets: list[dict], health: dict[str, dict], results_path: Path,
                       rounds: int) -> dict[str, int]:
    """必看台若首轮一条都没取到分片，就串行复测几轮。

    首轮是 32 并发，慢源会被读成「连接超时」；实测 dassby 的辽宁体育首轮超时、
    单发 0.04 秒就 200。复测结果照样追加进 line_health.jsonl，读数可回溯。
    """
    tally = {"复测线路": 0, "复测后转好": 0}
    if rounds < 1:
        return tally
    for channel in targets:
        if any(health.get(u, {}).get("verdict") == "可取到分片" for u in channel["streams"]):
            continue
        for url in channel["streams"]:
            tally["复测线路"] += 1
            for _ in range(rounds):
                row = health_probe.probe_one(url, 12.0, 15.0)
                with results_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                health[url] = row
                if row["verdict"] == "可取到分片":
                    tally["复测后转好"] += 1
                    break
                time.sleep(2)
    return tally


def grab_frame(url: str, work_dir: Path, timeout: int, reuse: bool = False,
               no_capture: bool = False) -> dict:
    """抽一帧原生尺寸的画面：jpg 的宽高就是这条流的真实分辨率。

    reuse=True 时沿用上一轮已经落盘的那一帧——看完图再重跑门禁不必再花十几分钟重抽。
    沿用**必须先证这一帧本来就是这条地址抽来的**：帧目录是按线路号命名的
    （`frames/<频道id>/line<序号>`），目录一改（删线/重排）同一个 line3 就成了另一条地址，
    旧帧会被挂到别的线上当证据。来源记在 `frame_source.json` 里，
    对不上或没记录的都拒绝沿用（记成「抽帧未出画面」并写明原因）。
    no_capture=True 时一行网络请求都不发：上一轮没抽到的这一轮仍然记「没画面」，
    只用来换门禁口径重出报告（--rerender）。
    """
    keep = work_dir / "frame_first.jpg"
    source = work_dir / SOURCE_NOTE
    key = stream_rules.canon(url)

    def shots() -> list[Path]:
        # frame_first.jpg 是 frames[0] 的副本，算卡流时要排除，否则永远「两帧相同」。
        return sorted(p for p in work_dir.glob("frame_*.jpg") if p.name != "frame_first.jpg")

    def refused(reason: str) -> dict:
        return {"frame": None, "bytes": 0, "width": 0, "height": 0,
                "timed_out": False, "elapsed_ms": 0, "shots": [], "reason": reason}

    if reuse and keep.exists() and keep.stat().st_size > FRAME_OK_BYTES:
        stored = None
        if source.exists():
            try:
                stored = json.loads(source.read_text(encoding="utf-8")).get("canonical")
            except ValueError:
                stored = None
        if stored is None:
            return refused("旧帧没有来源记录（这一帧只按线路号存着，证不了是本条地址抽的），拒绝沿用")
        if stored != key:
            return refused(f"这一格的旧帧来自 {stored}，不是本条地址，拒绝沿用")
        width, height = frame_size(keep)
        return {"frame": keep, "bytes": keep.stat().st_size, "width": width, "height": height,
                "timed_out": False, "elapsed_ms": 0, "shots": shots()}
    if no_capture:
        return refused("--rerender 不发网络请求")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    captured = probe_channels.capture_frames(url, work_dir, timeout, native=True)
    frames = [f for f in captured["frames"] if f.stat().st_size > FRAME_OK_BYTES]
    if not frames:
        return {"frame": None, "bytes": 0, "width": 0, "height": 0,
                "timed_out": captured["timed_out"], "elapsed_ms": captured["elapsed_ms"], "shots": []}
    shutil.copyfile(frames[0], keep)
    work_dir.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps({"url": url, "canonical": key}, ensure_ascii=False),
                      encoding="utf-8")
    width, height = frame_size(keep)
    return {"frame": keep, "bytes": keep.stat().st_size, "width": width, "height": height,
            "timed_out": captured["timed_out"], "elapsed_ms": captured["elapsed_ms"],
            "shots": shots()}


def frame_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:  # noqa: BLE001 - 读不出尺寸就记 0，让门禁按「未查」处理
        return 0, 0


def probe_channel(channel: dict, health: dict[str, dict], frames_dir: Path,
                  timeout: int, stop_at_first_frame: bool, reuse: bool = False,
                  no_capture: bool = False, review: dict | None = None) -> dict:
    """逐线路记三级证据。

    必看台（stop_at_first_frame=False）要把**每一条**线路都抽一遍帧——
    用户 2026-10-22 的要求是「全线路检测全部有画面才算通过」，一台里只要有一条抽不到帧就判红。
    其他台（True）只要求「至少一条有正常画面」，第一条出帧就停，省下几十分钟机时。
    """
    lines = []
    got_frame = False
    for index, url in enumerate(channel["streams"]):
        row = health.get(url)
        lines.append({
            "index": index, "url": url,
            "verdict": row["verdict"] if row else "未测",
            "http_status": (row or {}).get("http_status"),
            "seg_bytes": (row or {}).get("seg_bytes", 0),
            "resolution": (row or {}).get("resolution"),
            "frame": None, "bytes": 0, "width": 0, "height": 0,
        })
        if got_frame and stop_at_first_frame:
            lines[-1]["frame_skipped"] = "本机已有出帧线路，按「至少一条」口径不再抽帧"
            continue
        info = grab_frame(url, frames_dir / channel["id"] / f"line{index}", timeout, reuse,
                          no_capture)
        if info["frame"]:
            lines[-1].update(frame=info["frame"], bytes=info["bytes"],
                             width=info["width"], height=info["height"])
            # 抽到帧 ≠ 抽到能看的画面：占位页/推广页长得再稳也不是节目，
            # 容器解不了的线在电视上是黑屏。这两种都不算「找到能播的线」，继续往下探。
            placeholder = fingerprint_hit(info["frame"])
            blank = None if placeholder else blank_hit(info["frame"])
            stalled = None if (placeholder or blank) else frames_stalled(info.get("shots"))
            if placeholder:
                note = card_is_picture(channel["name"], placeholder, url, review)
                lines[-1]["placeholder"] = placeholder + (note or "")
                if note:
                    # 台标相符的测试卡：这一帧算这台的画面（见 card_is_picture 那三条件）
                    lines[-1]["placeholder_ok"] = True
                    got_frame = True
            elif blank:
                lines[-1]["blank"] = blank
            elif stalled:
                lines[-1]["stalled"] = stalled
            elif lines[-1]["verdict"] == PLAYER_UNDEMUXABLE:
                lines[-1]["undemuxable"] = PLAYER_UNDEMUXABLE
            else:
                got_frame = True
        else:
            lines[-1]["frame_skipped"] = info.get("reason") or "抽帧未出画面"
    segment_ok = sum(1 for line in lines if line["verdict"] == "可取到分片")
    framed = [line for line in lines if line["frame"]]
    # VLC 能解 FLV，ExoPlayer 没有对应的解复用器：这类线在电视上是黑屏。
    # 「至少一条线路显示正常画面」里的"显示"是在父母那台电视上显示，不是在抽帧机上显示。
    demuxable = [line for line in framed if line["verdict"] != PLAYER_UNDEMUXABLE]
    # 「画面是别人家的节目」不算这一台能播（用户 10-24 #96 乙，与构建侧同一个纯函数）：
    # 安多卫视那条线抽到的是西宁市台「新闻综合」，以前在这一格里算「有正常画面」。
    borrowed = [line for line in demuxable
                if other_station_frame(channel["name"], line.get("url") or "", review)]
    clean = [line for line in demuxable
             if (line.get("placeholder_ok") or not line.get("placeholder"))
             and not line.get("blank") and not line.get("stalled")
             and line not in borrowed]
    return {
        "id": channel["id"], "name": channel["name"], "subgroup": channel.get("subgroup"),
        "category": channel.get("category"),
        "must_watch": not stop_at_first_frame,
        "lines": lines, "segment_ok": segment_ok, "frame_ok": len(framed),
        "undemuxable": len(framed) - len(demuxable),
        "clean_frame_ok": len(clean),
        "borrowed_frame": len(borrowed),
        "frame": clean[0] if clean else (framed[0] if framed else None),
        "frame_line": clean[0] if clean else (framed[0] if framed else None),
        "all_lines_framed": bool(lines) and len(framed) == len(lines),
    }


def load_review(path: Path) -> dict[str, dict]:
    """人工看图结论：URL → {logo, picture}。

    `logo` 是台标对不对得上，`picture` 是这一帧算不算正常节目画面（广告/占位页/带二维码都不算）。
    两个字段各自独立，缺任何一个都按「待人工复核」计，不默认成通过。
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for row in data.get("reviews", []):
        out[row["url"]] = {
            "logo": row.get("logo", PENDING),
            "picture": row.get("picture", PENDING),
        }
    return out


def line_verdict(review: dict[str, dict], line: dict) -> tuple[str, str]:
    """人工结论优先；没有人写过的那一帧先过一遍已知指纹。

    指纹只自动判「这一帧不是节目内容」这半件事，台标对不对仍然要人看。
    """
    entry = review.get(line["url"]) or {}
    logo = entry.get("logo", PENDING)
    picture = entry.get("picture", PENDING)
    if picture == PENDING:
        hit = fingerprint_hit(line.get("frame"))
        if hit:
            picture = hit + "（自动比对已知图）"
    return logo, picture


def write_line_frames(path: Path, results: list[dict]) -> None:
    """把每条线路的原生画面尺寸落成 jsonl，供 build_catalog.py 按分辨率给线路排名。

    只记本次真抽到帧的那几条；没出帧的线路不写尺寸（不拿 0 冒充「标清」）。
    """
    rows: list[str] = []
    for result in results:
        for line in result["lines"]:
            if not line["frame"]:
                continue
            rows.append(json.dumps({
                    "channel": result["name"], "line_index": line["index"], "url": line["url"],
                    "width": line["width"], "height": line["height"],
                    "bytes": line["bytes"], "frame": str(line["frame"]),
                    # 占位页要跟着尺寸一起落盘：目录生成那边靠这一格把「抽到画面」和
                    # 「抽到节目」分开，不然测试卡会被当成可用证据排到第一条。
                    "placeholder": line.get("placeholder", ""),
                    # 纯色帧（蓝屏/黑屏）单独一列：它不该被当成「抽到节目」的证据，
                    # 但也不进 placeholder 那一列——那一列在目录侧是一票否决（档 4 删线），
                    # 一次淡入淡出就给出纯黑，拿它删线会误伤能播的线。
                    "blank": line.get("blank", ""),
                    # 卡流（抽帧窗口内几帧字节全同）单独一列：它同样只让「有正常画面」不加分，
                    # 不进 placeholder 那一列——卡流是时刻性的，不是这条线永久坏了。
                    "stalled": line.get("stalled", ""),
            }, ensure_ascii=False))
    write_frame_rows(path, rows)


def merge_frame_rows(existing: list[str], rows: list[str]) -> list[str]:
    """台账里「本轮目录没有的地址」原样留着——#54 那把换底缺陷挡的就是这个。

    本轮同一个 url 的新读数顶掉旧行；读不懂的行（不是 json）保留下来等人工看，不悄悄扔。
    证据文件按「累加」算，不按「本轮跑过哪些」算：字段缺失不等于这条地址的意见是干净的。
    """
    fresh_urls = set()
    for row in rows:
        try:
            fresh_urls.add(json.loads(row)["url"])
        except (ValueError, KeyError, TypeError):
            continue
    kept: list[str] = []
    for line in existing:
        if not line.strip():
            continue
        try:
            url = json.loads(line).get("url")
        except ValueError:
            kept.append(line)
            continue
        if url in fresh_urls:
            continue
        kept.append(line)
    return rows + kept


def write_frame_rows(path: Path, rows: list[str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    merged = merge_frame_rows(existing, rows)
    path.write_text("".join(line + "\n" for line in merged), encoding="utf-8")
    log(f"[frames] {path}（本轮 {len(rows)} 行＋沿用台账 {len(merged) - len(rows)} 行）")


def render(report: dict) -> str:
    channels = report["channels"]
    groups: dict[tuple[str, str], list[dict]] = {}
    for result in channels:
        groups.setdefault((result.get("category") or "?", result["subgroup"] or "其他"), []).append(result)
    lines_total = sum(len(r["lines"]) for r in channels)

    def head_line(r: dict):
        return r["lines"][0] if r["lines"] else None

    framed_total = sum(r["frame_ok"] for r in channels)
    out = [
        f"# 打包前线路检测报告 · {report['title']}",
        "",
        f"- 生成时刻：{report['generated_at']}",
        f"- 目录文件：`{report['catalog']}`（{report['catalog_bytes']:,} B）",
        f"- 目录 sha256：`{report['catalog_sha256']}`",
        f"- 待打包 APK：{report['apk'] or '（本次未指定，报告只描述目录，不声明包体一致）'}",
        f"- APK 内 `assets/catalog.dat`（解码后）sha256：`{report['apk_sha256'] or '未取'}`",
        f"- 覆盖：{len(report['channels'])} 台 / {lines_total} 条线路 · 抽到画面 {framed_total} 条"
        f"（口径：{'央视/卫视/辽宁体育 全线路逐条抽帧' if report['scope'] == 'must' else '全目录；必看台逐条、其他台探到第一条画面即停'}）",
        f"- 必看台串行复测：{report['recheck']['复测线路']} 条首轮全灭的线路被复测，"
        f"其中 {report['recheck']['复测后转好']} 条复测后取到分片",
        "",
        "> 三级证据各自独立成列：清单可达（HTTP 2xx + 正文 #EXTM3U）→ 分片可取（>1KB 媒体字节）"
        "→ 画面出帧（VLC 抽到原生尺寸 jpg）。台标是否相符、画面是不是广告/带二维码，"
        "仍要人工看帧，脚本只把「有没有人判过、判的是什么」如实列出来。",
        "",
    ]
    out += [
        "## 各组可用性：能播的台数 vs 台数",
        "",
        "「线路可用性优于频道数量」（用户 2026-10-22 指令）——这一张表就是这条的尺："
        "台数再多，能播的台数不上去就是虚数。",
        "",
        "| 分组 | 台数 | 有出帧线路的台 | 有正常画面的台 | 首条就出帧的台 | 线路条数 | 出帧线路 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    # 分组表按目录里的分组次序走（先大类、再子类），不然「cctv / 卫视」会插在一堆
    # local 组中间，读的人以为漏了组。
    CATEGORY_SEQ = ["cctv", "local", "hktw", "featured", "sports", "international"]
    SUBGROUP_SEQ = ["央视", "卫视", "4K 超高清", "国内体育", "咪咕直播", "港台体育",
                    "香港", "台湾", "澳门", "海外华人", "影视轮播", "NewTV", "CHC", "少儿",
                    "新闻", "纪实", "综合"]

    def group_key(item: tuple[str, str]) -> tuple[int, int, str]:
        cat, sub = item
        return (CATEGORY_SEQ.index(cat) if cat in CATEGORY_SEQ else 99,
                SUBGROUP_SEQ.index(sub) if sub in SUBGROUP_SEQ else 99, sub)

    ordered_groups = sorted(groups.items(), key=lambda kv: group_key(kv[0]))
    for (cat, sub), results in ordered_groups:
        out.append(
            f"| {cat} / {sub} | {len(results)} "
            f"| {sum(1 for r in results if r['frame_ok'])} "
            f"| {sum(1 for r in results if r['clean_frame_ok'])} "
            f"| {sum(1 for r in results if (head_line(r) or {}).get('frame'))} "
            f"| {sum(len(r['lines']) for r in results)} "
            f"| {sum(r['frame_ok'] for r in results)} |"
        )
    out += [
        f"| **合计** | **{len(channels)}** "
        f"| **{sum(1 for r in channels if r['frame_ok'])}** "
        f"| **{sum(1 for r in channels if r['clean_frame_ok'])}** "
        f"| **{sum(1 for r in channels if (head_line(r) or {}).get('frame'))}** "
        f"| **{lines_total}** | **{framed_total}** |",
        "",
    ]
    for (cat, sub), results in ordered_groups:
        lines = sum(len(r["lines"]) for r in results)
        framed = sum(r["frame_ok"] for r in results)
        full = any(r["must_watch"] for r in results)
        out += [
            f"## {cat} / {sub}：{len(results)} 台 / {lines} 条线路 · 出帧 {framed} 条",
            "",
            ("| 频道 | 线路 | 清单 2xx | 分片可取 | 出帧 | 逐条明细 |" if full
             else "| 频道 | 线路 | 清单 2xx | 分片可取 | 出帧 | 首条 |"),
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in sorted(results, key=lambda x: x["name"]):
            playlist_ok = sum(1 for line in r["lines"]
                              if isinstance(line["http_status"], int) and 200 <= line["http_status"] < 300)
            if not full:
                head = head_line(r)
                if (head or {}).get("placeholder"):
                    first = "占位页（不是节目画面）"
                elif (head or {}).get("frame"):
                    first = "出帧"
                else:
                    first = (head or {}).get("verdict", "—")
                out.append(
                    f"| {r['name']} | {len(r['lines'])} | {playlist_ok}/{len(r['lines'])} "
                    f"| {r['segment_ok']}/{len(r['lines'])} | {r['frame_ok']}/{len(r['lines'])} | {first} |"
                )
                continue
            detail = []
            for line in r["lines"]:
                logo, picture = line_verdict(report["review"], line)
                if line["frame"]:
                    size = f"{line['width']}×{line['height']}" if line["height"] else "?"
                    frame_path = Path(line["frame"])
                    try:
                        # 报告写在 dist/ 下，帧路径要退一层；不在仓库内的（--out 指到别处）就原样写。
                        rel = Path("..") / frame_path.resolve().relative_to(BASE)
                    except ValueError:
                        rel = frame_path
                    detail.append(f"[线{line['index'] + 1} 出帧 {size}]({rel})·{logo}·{picture}")
                else:
                    detail.append(f"线{line['index'] + 1} 无画面（{line['verdict']}）")
            out.append(
                f"| {r['name']} | {len(r['lines'])} | {playlist_ok}/{len(r['lines'])} "
                f"| {r['segment_ok']}/{len(r['lines'])} | {r['frame_ok']}/{len(r['lines'])} "
                f"| {'；'.join(detail)} |"
            )
        out.append("")
    dead = [r for r in report["channels"] if not r["clean_frame_ok"]]
    out += [
        "## 没有一条正常画面的台（含一条画面都没抽到的）",
        "",
        f"- 共 {len(dead)} 台：" + "、".join(r["name"] for r in dead) if dead else "- 无",
        "",
        "## 判定",
        "",
    ]
    for gate in report["gates"]:
        out.append(f"- {gate['name']}：**{gate['status']}** —— {gate['evidence']}")
    out += [
        "",
        "## 复现",
        "",
        "```bash",
        report["command"],
        "```",
        "",
    ]
    return "\n".join(out)


def selfcheck() -> int:
    """验画面判据会不会咬人：正对照要抓到自己那一枚，负对照要放过，重编码后还要抓得到。

    夹具是 tools/fixtures/ 下的 18 张真帧，两把尺各验一遍：
      占位页/广告那把（FRAME_FINGERPRINTS，14 枚）——16 格：13 张坏帧正对照 + 3 张节目画面负对照；
      纯色帧那把（BLANK_FINGERPRINTS，1 枚）——4 格：蓝屏/黑屏两张正对照 + 同样那两张节目画面负对照
      （暗场那张近纯色，距纯色 16 格，是这一格最容易被咬到的东西）。
    缺夹具直接算不通过——不许把「没验成」念成「过了」。
    正对照按「命中的必须是它自己那一枚」判，不是「命中任意一枚占位页」：
    钉错 digest 时后者会绿着漏过去。
    """
    failures: list[str] = []
    expectations = (
        ("frame_qr_placeholder.jpg", "epg.pw"),
        ("frame_test_card.jpg", "CCTV 测试卡"),
        ("card_anduo.jpg", "西宁市广播电视台"),
        ("card_xinjiang.jpg", "KRTV-1"),
        ("card_cctv7.jpg", "CCTV-7 国防军事测试卡"),
        ("card_cctv11.jpg", "CCTV-11 戏曲测试卡"),
        ("card_cctv14.jpg", "CCTV-14 少儿测试卡"),
        ("card_cctv4k.jpg", "CCTV UHD"),
        ("ad_cctv15.jpg", "广告：诊室画面"),
        ("ad_cctv9_shopping.jpg", "订购电话"),
        ("ad_cctv4k_400689.jpg", "广告：诊室画面"),
        ("card_cctv4k_gslb.jpg", "CCTV 4K 超高清 测试卡"),
        ("ad_overload_qr.jpg", "服务器超负载"),
        ("frame_program.jpg", None),
        ("frame_cctv4k_program.jpg", None),
        ("frame_dark_program.jpg", None),
    )
    import tempfile
    from PIL import Image

    for name, expect in expectations:
        path = FIXTURES / name
        if not path.exists():
            failures.append(f"{name}: 夹具缺失 ⇒ 这一格没验成")
            continue
        hit = fingerprint_hit(path)
        distances = [hamming(dhash(path), known) for known, _ in FRAME_FINGERPRINTS]
        # 库空时 min() 会抛 ValueError：那等于「判据被人整库摘掉」这件最该报的事，
        # 变成一次崩溃而不是一次判红。
        nearest = min(distances) if distances else None
        near_text = f"{nearest} 格" if nearest is not None else "库是空的"
        if expect is None:
            if hit:
                failures.append(f"{name}: 正常节目画面被判成「{hit}」（离最近指纹 {near_text}）⇒ 判据过牙")
            else:
                log(f"[selfcheck] 放过 {name}（离最近指纹 {near_text}，阈值 {FINGERPRINT_MAX_DISTANCE}）")
            continue
        if not hit or expect not in hit:
            failures.append(f"{name}: 该命中含「{expect}」的那枚，实际「{hit}」（离最近指纹 {near_text}）"
                            f"⇒ 判据没牙或钉错了 digest")
        else:
            log(f"[selfcheck] 抓到 {name} → {hit}")
        # 同一张画面在现网会被重新编码，压一遍再判：抓不到就说明指纹只认字节、不认画面。
        with tempfile.TemporaryDirectory() as tmp:
            again = Path(tmp) / "reencoded.jpg"
            with Image.open(path) as image:
                image.save(again, "JPEG", quality=55)
            reread = fingerprint_hit(again)
            if not reread or expect not in reread:
                failures.append(f"{name}: 重编码副本（quality=55）判成「{reread}」，没含「{expect}」⇒ 阈值太紧")
    # 纯色帧那一格单独验：它只让「有正常画面」不加分，所以判红条件是「抓到／放过」，
    # 而不是「命中哪一枚」。暗场节目画面（距纯色 16 格）是这一格最容易被咬到的东西。
    for name, expect in (("frame_blue_screen.jpg", "纯色"), ("frame_black_screen.jpg", "纯色"),
                         ("frame_dark_program.jpg", None), ("frame_program.jpg", None)):
        path = FIXTURES / name
        if not path.exists():
            failures.append(f"{name}: 纯色帧夹具缺失 ⇒ 这一格没验成")
            continue
        dist = min(hamming(dhash(path), known) for known, _ in BLANK_FINGERPRINTS)
        hit = blank_hit(path)
        if expect is None:
            if hit:
                failures.append(f"{name}: 正常节目画面被判成纯色（距纯色 {dist} 格）⇒ 纯色判据过牙")
            else:
                log(f"[selfcheck] 纯色判据放过 {name}（距纯色 {dist} 格，阈值 {FINGERPRINT_MAX_DISTANCE}）")
        elif not hit:
            failures.append(f"{name}: 该判成纯色却没判（距纯色 {dist} 格）⇒ 纯色判据没牙")
        else:
            log(f"[selfcheck] 纯色判据抓到 {name} → {hit}（距纯色 {dist} 格）")
    for line in failures:
        log(f"[selfcheck] 不通过 {line}")
    log(f"[selfcheck] 结论：{'通过' if not failures else f'{len(failures)} 格不通过'}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=BASE / "app/src/main/assets/channels.json")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--apk", type=Path, default=None)
    parser.add_argument("--title", default="v?")
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--frame-timeout", type=int, default=25)
    parser.add_argument("--use-cache", action="store_true", help="沿用上次清单/分片读数，不重新探测")
    parser.add_argument("--skip-frames", action="store_true", help="只出清单/分片两级证据")
    parser.add_argument("--must", default="辽宁体育", help="必看台，逗号分隔；首轮全灭会串行复测")
    parser.add_argument("--recheck", type=int, default=3, help="必看台每条线路的复测轮数，0=不复测")
    parser.add_argument("--scope", choices=("must", "all"), default="must",
                        help="must=只查央视/卫视/辽宁体育（每条线路都要有画面）；"
                             "all=再加上其他频道（每台探到一条画面即停）")
    parser.add_argument("--frame-workers", type=int, default=6, help="抽帧并发数")
    parser.add_argument("--reuse-frames", action="store_true",
                        help="沿用上一轮已落盘的帧（看完图重跑门禁时用，省十几分钟重抽）")
    parser.add_argument("--selfcheck", action="store_true",
                        help="只验画面指纹判据有没有牙（不联网、不探测）")
    parser.add_argument("--rerender", action="store_true",
                        help="一行网络请求都不发：沿用 --out 目录里上一次的清单/分片读数和已落盘的帧，"
                             "只按当前门禁口径重出报告（改判据、补看图结论时用）")
    parser.add_argument("--no-carry", action="store_true",
                        help="关掉「G3 判定按 url 跨轮沿用」（默认开：同一地址上一轮判过的结论"
                             "在本轮帧上仍成立就沿用，明细见 tools/.cache/gate/review_carry.json）")
    args = parser.parse_args()
    if args.selfcheck:
        return selfcheck()
    args.out = args.out.resolve()
    if args.apk and not args.apk.exists():
        raise SystemExit(f"--apk 指向的文件不存在：{args.apk}")

    catalog = args.catalog.resolve()
    must = tuple(name.strip() for name in args.must.split(",") if name.strip())
    data = json.loads(catalog.read_text(encoding="utf-8"))
    must_watch = [c for c in data["channels"] if is_target(c, must)]
    others = [c for c in data["channels"] if not is_target(c, must)]
    if not must_watch:
        raise SystemExit(f"目录里没匹配到央视/卫视/{args.must}：{catalog}")
    targets = must_watch + (others if args.scope == "all" else [])

    args.out.mkdir(parents=True, exist_ok=True)
    target_catalog = args.out / "target_catalog.json"
    target_catalog.write_text(json.dumps({"channels": targets}, ensure_ascii=False, indent=1),
                              encoding="utf-8")

    health = run_health(target_catalog, args.out, args.workers, args.use_cache,
                        skip_probe=args.rerender)
    recheck = recheck_must_watch(
        [c for c in targets if c["name"] in must],
        health, args.out / "line_health.jsonl", 0 if args.rerender else args.recheck)
    if recheck["复测线路"]:
        log(f"[recheck] 必看台复测 {recheck['复测线路']} 条，转好 {recheck['复测后转好']} 条")
    frames_dir = args.out / "frames"
    # 人工看图结论要在**抽帧之前**就读到：占位页那一格现在有一条例外（card_is_picture），
    # 它得问 review.json「这一枚的台标是谁」。以前这份表只在渲染那一步读，抽帧阶段看不见它。
    review_path = args.out / "review.json"
    review = load_review(review_path)
    if review:
        log(f"[review] 读到 {len(review)} 枚人工看图结论（{review_path}）")
    must_ids = {c["id"] for c in must_watch}
    results: list[dict] = []
    started = time.time()
    if args.skip_frames:
        for channel in targets:
            results.append({
                "id": channel["id"], "name": channel["name"], "subgroup": channel.get("subgroup"),
                "category": channel.get("category"),
                "must_watch": channel["id"] in must_ids,
                "lines": [{"index": i, "url": u,
                           "verdict": health.get(u, {}).get("verdict", "未测"),
                           "http_status": health.get(u, {}).get("http_status"),
                           "seg_bytes": health.get(u, {}).get("seg_bytes", 0),
                           "resolution": health.get(u, {}).get("resolution"),
                           "frame": None, "bytes": 0, "width": 0, "height": 0}
                          for i, u in enumerate(channel["streams"])],
                "segment_ok": sum(1 for u in channel["streams"]
                                  if health.get(u, {}).get("verdict") == "可取到分片"),
                "frame_ok": 0, "clean_frame_ok": 0,
                "frame": None, "frame_line": None, "all_lines_framed": False,
            })
    else:
        with ThreadPoolExecutor(max_workers=args.frame_workers) as pool:
            results = list(pool.map(
                lambda c: probe_channel(c, health, frames_dir, args.frame_timeout,
                                        stop_at_first_frame=c["id"] not in must_ids,
                                        reuse=args.reuse_frames or args.rerender,
                                        no_capture=args.rerender, review=review),
                targets))
        # 必看台在并发场里一条帧都没抽到 → 串行重抽一遍再定它红不红。
        # 并发会把慢源读成超时（10-21 同一台 25 分钟内三次读数翻转；10-22 13:31 那场
        # 辽宁体育记「抽帧 0 张」判红，13:35/13:41/13:46 串行三次全部出画面、原生 1920x1090）。
        retry = [(pos, chan) for pos, (chan, res) in enumerate(zip(targets, results))
                 if res["must_watch"] and not res["frame_ok"]]
        if retry and not args.rerender:
            log(f"[frames] {len(retry)} 个必看台并发场一条帧都没抽到，串行重抽")
            for done, (pos, chan) in enumerate(retry, start=1):
                results[pos] = probe_channel(chan, health, frames_dir, args.frame_timeout,
                                             stop_at_first_frame=False, review=review)
                log(f"[frames] 串行复测 {done}/{len(retry)} {chan['name']}："
                    f"{len(chan['streams'])} 条线路抽到 {results[pos]['frame_ok']} 张")
        log(f"[frames] {len(results)} 台，用时 {time.time() - started:.0f}s")

    order = {c["id"]: i for i, c in enumerate(targets)}
    results.sort(key=lambda r: (r["subgroup"] or "", order[r["id"]]))
    write_line_frames(args.out / "line_frames.jsonl", results)

    # G3 判定按 url 跨轮沿用（用户 10-26 点档乙）：只补「本轮待人工复核」的那几格，
    # 本轮 review.json 已有的结论一律不动；被内容自证挡住的会退回「待人工复核」。
    carry_summary = None
    if not args.no_carry and not args.skip_frames:
        review, carry_summary = review_carry.apply_carry(
            review, results, current_review_path=args.out / "review.json",
            prior_globs=(str(BASE / "tools/.cache/gate/round_*/review.json"),),
            ledger_path=BASE / "tools/.cache/gate/review_carry.json", log=log)

    apk_path = args.apk.resolve() if args.apk else None
    apk_sha = None
    if apk_path:
        apk_sha = apk_catalog_sha(apk_path)   # 读 assets/catalog.dat 并解码后再算——比的是明文
    review_path = args.out / "review.json"
    report = {
        "title": args.title,
        "scope": args.scope,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "catalog": str(catalog.relative_to(BASE)),
        "catalog_bytes": catalog.stat().st_size,
        "catalog_sha256": sha256_file(catalog),
        "apk": str(apk_path.relative_to(BASE)) if apk_path else None,
        "apk_sha256": apk_sha,
        "channels": results,
        "recheck": recheck,
        "review": review,
        "carry": carry_summary,
        "dist_dir": args.dist,
        "command": "python3 tools/prepack_report.py " + " ".join(sys.argv[1:]),
    }

    def gate(name: str, status: str, evidence: str) -> dict:
        return {"name": name, "status": status, "evidence": evidence,
                "ok": status == "通过", "checked": status != "未查"}

    gates = []
    watch = [r for r in results if r["must_watch"]]
    lns = next((r for r in watch if r["name"] == "辽宁体育"), None)
    if lns is None:
        gates.append(gate("G1 辽宁体育必须有出帧线路", "不通过", "目录里没找到「辽宁体育」这一台"))
    elif args.skip_frames:
        gates.append(gate("G1 辽宁体育必须有出帧线路", "未查",
                          f"--skip-frames 只出了清单/分片两级：分片可取 {lns['segment_ok']}"
                          f"/{len(lns['lines'])} 条，没抽帧"))
    elif lns["frame_ok"]:
        gates.append(gate("G1 辽宁体育必须有出帧线路", "通过",
                          f"辽宁体育 {len(lns['lines'])} 条线路里 {lns['frame_ok']} 条抽到画面"))
    else:
        gates.append(gate("G1 辽宁体育必须有出帧线路", "不通过",
                          f"辽宁体育 {len(lns['lines'])} 条线路，分片可取 {lns['segment_ok']} 条，抽帧 0 张"))

    watch_lines = sum(len(r["lines"]) for r in watch)
    watch_framed = sum(r["frame_ok"] for r in watch)
    # 门禁口径＝用户 2026-10-22 点的「选项B」：一条线路都不删，只看每台第 1 条——
    # 首条必须抽到帧，且判过是正常节目画面（台标相符）。第 2 条往后只如实列进报告，不卡包。
    # （原来这一格按「每条线路都要有画面」判，68 台里 66 台含没画面的线路。）
    first_missing, first_pending, first_bad = [], [], []
    for r in watch:
        head = r["lines"][0] if r["lines"] else None
        if head is None or not head["frame"]:
            first_missing.append(r["name"])
            continue
        logo, picture = line_verdict(report["review"], head)
        if logo == PENDING or picture == PENDING:
            first_pending.append(r["name"])
        elif not (logo.startswith(GOOD_LOGO) and frame_counts(r["name"], head, logo, picture)):
            first_bad.append(f"{r['name']}（{logo}／{picture}）")
    G2_LABEL = "G2 必看台（央视1-17＋5+＋4K超高清＋辽宁体育，含卫视）每台第 1 条线路必须有正常画面"
    if args.skip_frames:
        gates.append(gate(G2_LABEL, "未查", "--skip-frames 没抽帧，这一格无从判起"))
    elif not (first_missing or first_pending or first_bad):
        gates.append(gate(G2_LABEL, "通过",
                          f"{len(watch)} 台首条线路全部出帧并判为正常节目画面"
                          f"（共 {watch_lines} 条线路、抽到 {watch_framed} 张，一条没删）"))
    else:
        parts = []
        if first_missing:
            parts.append(f"首条没画面 {len(first_missing)} 台：" + "、".join(first_missing[:20])
                         + ("…" if len(first_missing) > 20 else ""))
        if first_pending:
            parts.append(f"首条待人工复核 {len(first_pending)} 台：" + "、".join(first_pending[:10])
                         + ("…" if len(first_pending) > 10 else ""))
        if first_bad:
            parts.append(f"首条画面不正常 {len(first_bad)} 台：" + "、".join(first_bad[:10]))
        gates.append(gate(G2_LABEL, "不通过",
                          f"{len(watch)} 台 / {watch_lines} 条线路，"
                          f"{len(first_missing) + len(first_pending) + len(first_bad)} 台首条不合格；"
                          + "；".join(parts)))

    bad_logo, bad_picture, unreviewed = [], [], []
    n_judged, n_carried = 0, 0
    for r in watch:
        for line in r["lines"]:
            if not line["frame"]:
                continue
            logo, picture = line_verdict(report["review"], line)
            tag = f"{r['name']}·线{line['index'] + 1}"
            # 四态只在 frame_state 里算（用户 10-26 点档）：台标那一半没人写过就先进
            # 「待人工复核」——自动指纹只填画面那一半，它不算判过。
            state = frame_state(r["name"], line, logo, picture)
            if state == STATE_PENDING:
                unreviewed.append(tag)
            elif state == STATE_RED:
                if picture != PENDING and not frame_counts(r["name"], line, logo, picture):
                    bad_picture.append(f"{tag}（{picture}）")
                else:
                    bad_logo.append(f"{tag}（{logo}）")
            elif state == STATE_CARRIED:
                n_carried += 1
            else:
                n_judged += 1
    carry_note = ""
    if carry_summary and carry_summary["carried"]:
        counts = carry_summary["counts"]
        carry_note = (f"；沿用历史结论 {carry_summary['carried']} 张"
                      f"（强 {counts['strong']}／旧红 {counts['red']}，"
                      f"明细 {carry_summary['ledger']}）")
    if args.skip_frames:
        gates.append(gate("G3 每张画面都判过：台标相符且非广告/无二维码（与已知推广页/测试卡同图的自动判红）", "未查",
                          "--skip-frames 没抽帧，没有可判的图" + carry_note))
    elif unreviewed:
        gates.append(gate("G3 每张画面都判过：台标相符且非广告/无二维码（与已知推广页/测试卡同图的自动判红）", "不通过",
                          f"{len(unreviewed)} 张待人工复核：" + "、".join(unreviewed[:15])
                          + ("…" if len(unreviewed) > 15 else "") + carry_note))
    elif bad_logo or bad_picture:
        gates.append(gate("G3 每张画面都判过：台标相符且非广告/无二维码（与已知推广页/测试卡同图的自动判红）", "不通过",
                          f"判红 {len(bad_logo) + len(bad_picture)} 张："
                          f"台标不符 {len(bad_logo)} 张：" + "、".join(bad_logo[:10])
                          + f"；画面异常 {len(bad_picture)} 张：" + "、".join(bad_picture[:10])
                          + carry_note))
    else:
        gates.append(gate("G3 每张画面都判过：台标相符且非广告/无二维码（与已知推广页/测试卡同图的自动判红）", "通过",
                          f"{watch_framed} 张画面全部判为台标相符 + 正常节目画面"
                          f"（本轮人工判过 {n_judged} 张、沿用 {n_carried} 张）" + carry_note))

    rest = [r for r in results if not r["must_watch"]]
    if args.scope != "all":
        gates.append(gate("G4 其他频道至少一条线路有正常画面", "未查",
                          f"--scope must 只覆盖了必看组；另外 {len(others)} 台没探，"
                          "要查就加 --scope all"))
    else:
        # 「有正常画面」在这一档只能自动判到「抽到帧、且不是已知推广页/测试卡同图」这一层：
        # 一千多张帧逐张人工看不过来，所以把没见过的广告页漏过去是这一格的已知盲点，写进结论里。
        no_frame, unseen = [], 0
        for r in rest:
            if r["clean_frame_ok"]:
                framed = [line for line in r["lines"] if line["frame"]]
                reviewed = any((report["review"].get(line["url"]) or {}).get("picture")
                               for line in framed)
                if not reviewed:
                    unseen += 1
            elif r["frame_ok"]:
                if r.get("borrowed_frame"):
                    no_frame.append(f"{r['name']}（抽到的帧是别的台的画面，台标结论点名别的台）")
                else:
                    no_frame.append(f"{r['name']}（抽到的帧都是已知推广页/测试卡）")
            else:
                no_frame.append(r["name"])
        verdict_text = (f"{len(rest)} 台全部至少一条出帧" if not no_frame
                        else f"{len(no_frame)}/{len(rest)} 台没有可用画面："
                             + "、".join(no_frame[:20]) + ("…" if len(no_frame) > 20 else ""))
        gates.append(gate("G4 其他频道至少一条线路有正常画面",
                          "通过" if not no_frame else "不通过",
                          verdict_text + f"；其中 {unseen} 台的画面只过了自动指纹、没人看过"))

    missing = [r for r in results if any(line["verdict"] == "未测" for line in r["lines"])]
    gates.append(gate(
        "G5 每一台每条线路都有本次读数", "通过" if not missing else "不通过",
        f"{sum(len(r['lines']) for r in results)} 条线路全部有本次读数" if not missing
        else f"{len(missing)} 台含「未测」线路：" + "、".join(r["name"] for r in missing)))
    if args.apk:
        gates.append(gate(
            "G6 报告描述的目录 == APK 内的目录",
            "通过" if apk_sha == report["catalog_sha256"] else "不通过",
            f"APK `{report['apk']}` 内 sha256 `{(apk_sha or '')[:16]}…` vs 目录 "
            f"`{report['catalog_sha256'][:16]}…`"))
    else:
        gates.append(gate("G6 报告描述的目录 == APK 内的目录", "未查", "没传 --apk，这一格没查"))
    report["gates"] = gates

    (args.out / "prepack_report.json").write_text(
        json.dumps({**report, "dist_dir": str(report["dist_dir"])}, ensure_ascii=False, indent=1,
                   default=str), encoding="utf-8")

    args.dist.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^0-9A-Za-z._-]+", "-", args.title)
    md_path = args.dist / f"prepack_report-{slug}-{time.strftime('%Y%m%d-%H%M')}.md"
    md_path.write_text(render(report), encoding="utf-8")
    log(f"[report] {md_path}")
    failed = [g["name"] for g in gates if g["status"] == "不通过"]
    unchecked = [g["name"] for g in gates if g["status"] == "未查"]
    for gate_row in gates:
        log(f"[gate] {gate_row['status']:<4} {gate_row['name']} —— {gate_row['evidence']}")
    if failed:
        log(f"[verdict] {len(failed)} 格不通过：{'、'.join(failed)}"
            + (f"；另有 {len(unchecked)} 格未查" if unchecked else ""))
        return 1
    log(f"[verdict] 已查的 {len(gates) - len(unchecked)} 格全部通过"
        + (f"；{len(unchecked)} 格未查：{'、'.join(unchecked)}" if unchecked else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
