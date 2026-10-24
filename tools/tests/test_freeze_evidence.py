#!/usr/bin/env python3
"""验的是冻帧这一档：长窗冻帧才删线，7 秒窗口的卡流只降分，且删线要有同台活线对照。

这批夹具盯的是五种会说谎的情形：
  1) 阈值恒真——判据写成「有几帧相同就判冻」，那 CCTV-8《枫桥警事》里两个警察站定
     7 秒的固定镜头就会被当成坏线删掉。A4/A5 盯这一格，A6 盯它的孪生形状：
     「整窗全同」那一支若走在阈值前面，只抽到 2 帧也能凑成"全同"。
  2) 字段恒盲——load_freeze 收了文件却没人读，或收了「不作判」当「没冻」。B 组盯这一格。
  3) 降分与删线串档——把 7 秒卡流也接进删除那一侧，等于用最短的窗口下最重的结论；
     反过来把长窗只接排序不接删除，冻帧线会继续排第一。C 组两头各钉一遍。
  4) 把「整台半夜没节目」当成「这条线路坏了」——一台的所有线在同一场里一起冻住时，
     删线既修不好上游、单线台还会被删没。D 组盯这道「要有同台活线对照才许删」的闸。
  5) 黑名单按「抽过帧的那几条」收，而不是按地址族收——同一族纯音频地址台账里 20 条，
     抽过帧的 4 条进了黑名单、另外 16 条敞口，设备端「立即更新线路」一捞就全回来。
     E 组盯这一格。

每格都配一条负对照：把被试那一格摘掉，结论必须翻回原样。摘了还不变的，说明起作用的是别处。

跑法：python3 tools/tests/test_freeze_evidence.py    （退出码 0 才算过）
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402
import freeze_scan as fs  # noqa: E402
import stream_rules  # noqa: E402

NAME = "某某卫视"
FROZEN_URL = "http://9.9.9.9:8080/gslb/frozen.m3u8?auth=x"
STALLED_URL = "http://8.8.8.8:8080/gslb/stalled.m3u8?auth=x"
GOOD_URL = "http://7.7.7.7:8080/gslb/good.m3u8?auth=x"
FR = [1920, 1080]

canon = stream_rules.canon
failures: list[str] = []
ran = 0


def check(label: str, got, want) -> None:
    """每调一次计一次数：总数现算，不写死条数（加一格判据时那条计数不会过期）。"""
    global ran
    ran += 1
    if got != want:
        failures.append(f"{label}: 得到 {got!r}，期望 {want!r}")


def make_frames(tmp: Path, prefix: str, contents: list[bytes]) -> list[Path]:
    """按 VLC 的命名法造帧文件：文件名里的数字就是解码帧号。"""
    paths = []
    for i, blob in enumerate(contents):
        path = tmp / f"{prefix}{i * 250 + 1:05d}.jpg"
        path.write_bytes(blob)
        paths.append(path)
    return paths


def shot(frames: list[Path], elapsed_ms: int = 150_000) -> dict:
    return {"frames": frames, "elapsed_ms": elapsed_ms, "timed_out": False, "exit_code": 0}


def part_a() -> None:
    """判据本体：按**解码帧数**判，不按秒判。

    并发软解时 VLC 的解码吞吐只有 8-12fps，比流的真实帧率低，拿「末帧号/墙钟秒」反推
    出来的秒数会虚高（实测 CCTV-3 那条按吞吐算 42 秒、按 25fps 折只有 20 秒）——
    用它当删除阈值会朝漏判偏。帧数（(N-1)×scene-ratio 个解码帧）与本机快慢无关。
    默认阈值 6 帧 × ratio 250 ⇒ 1250 个解码帧没有新内容，按 50fps 上界折也有 ≥25 秒节目。
    """
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        a, b, c, d = b"A" * 4000, b"B" * 4000, b"C" * 4000, b"D" * 4000
        # A1 整窗六帧全同 ⇒ 判冻（distinct==1 那一支）
        row = fs.judge(FROZEN_URL, NAME, shot(make_frames(tmp, "a1", [a] * 6)), 250, 6)
        check("A1 整窗全同应判冻", row["verdict"].startswith("冻帧"), True)
        check("A1 帧数", row["frames"], 6)
        # A1b 前面在动、尾段六帧相同 ⇒ 也判冻（走的是「连续段 ≥ 阈值」那一支，不是整窗全同）
        row = fs.judge(FROZEN_URL, NAME, shot(make_frames(tmp, "a1b", [b, c, a, a, a, a, a, a])), 250, 6)
        check("A1b 尾段 6 帧相同应判冻", row["verdict"].startswith("冻帧"), True)
        check("A1b distinct", row["distinct"], 3)
        # A1c 秒数只当信息，且报的是按 50fps 上界折的**下限**，不是反推吞吐
        check("A1c 秒数下限字段", row["run_seconds_min"], (6 - 1) * 250 / 50.0)
        # A2 帧帧不同 ⇒ 正常推进
        row = fs.judge(GOOD_URL, NAME, shot(make_frames(tmp, "a2", [a, b, c, a, b, c])), 250, 6)
        check("A2 交替帧应判正常", row["verdict"], "画面正常推进")
        # A3 只有一帧不作判（VLC 没解出来＝没意见，不是判坏）
        row = fs.judge(GOOD_URL, NAME, shot(make_frames(tmp, "a3", [a])), 250, 6)
        check("A3 单帧不作判", "不作判" in row["verdict"], True)
        row = fs.judge(GOOD_URL, NAME, shot([]), 250, 6)
        check("A3b 零帧不作判", "不作判" in row["verdict"], True)
        # A4 差一帧到阈值（5 帧相同）：静止字幕/慢镜头那一档，不许判冻
        row = fs.judge(STALLED_URL, NAME, shot(make_frames(tmp, "a4", [a, a, a, a, a, b])), 250, 6)
        check("A4 五帧相同不判冻", row["verdict"].startswith("冻帧"), False)
        check("A4 最长相同段", row["longest_run"], 5)
        # A4b 两帧相同（《枫桥警事》那种固定双人镜头的形状）⇒ 更不判冻
        row = fs.judge(STALLED_URL, NAME, shot(make_frames(tmp, "a4b", [a, a, b, c, a, b])), 250, 6)
        check("A4b 两帧相同不判冻", row["verdict"].startswith("冻帧"), False)
        check("A4b 最长相同段", row["longest_run"], 2)
        # A5 负对照：A4b 同一批帧，只把阈值从 6 降到 2 ⇒ 必须翻成判冻，
        #     证明挡在它前面的是阈值那一格，不是别的分支
        row = fs.judge(STALLED_URL, NAME, shot(make_frames(tmp, "a4b", [a, a, b, c, a, b])), 250, 2)
        check("A5 阈值摘到 2 应判冻", row["verdict"].startswith("冻帧"), True)
        # A6 「整窗全同」那一支以前走在阈值前面：只抽到 2 帧、恰好字节相同，就被写成冻帧
        #    （10-24 山西卫视 63.141 sxwshd 真撞上这个形状：2 帧全同＝一条电视购物广告的两个采样）。
        #    现在它必须与 A4b 同一口径——样本短到 2 帧，判不出「源没喂新画面」和「这一镜头本来就不动」。
        row = fs.judge(STALLED_URL, NAME, shot(make_frames(tmp, "a6", [a, a])), 250, 6)
        check("A6 两帧全同不判冻", row["verdict"].startswith("冻帧"), False)
        check("A6 要说清是帧数不够", f"{6} 帧" in row["verdict"], True)
        check("A6 distinct 仍是 1", row["distinct"], 1)
        # A6b 负对照：同一批两帧，只把阈值摘到 2 ⇒ 必须翻回判冻，
        #     证明这一格改的是「够不够帧数」这道闸，不是把 distinct==1 那一支整个删掉了
        row = fs.judge(STALLED_URL, NAME, shot(make_frames(tmp, "a6", [a, a])), 250, 2)
        check("A6b 阈值摘到 2 应判冻", row["verdict"].startswith("冻帧"), True)
        # A6c 六帧全同仍判冻（放宽只放到 2~5 帧那一档，没放到够长的那一档）
        row = fs.judge(FROZEN_URL, NAME, shot(make_frames(tmp, "a6c", [a] * 6)), 250, 6)
        check("A6c 六帧全同仍判冻", row["verdict"].startswith("冻帧"), True)
        # A6d 真跑形状：浙江卫视 cztv 1080p 那一条 13 帧／distinct 3／连续 11 帧相同
        #     （帧号 501→3001，见 tools/.cache/freeze/scan_all_20261024.jsonl）
        thirteen = [a, b] + [d] * 11
        row = fs.judge(FROZEN_URL, NAME, shot(make_frames(tmp, "a6d", thirteen)), 250, 6)
        check("A6d 浙江卫视形状应判冻", row["verdict"].startswith("冻帧"), True)
        check("A6d 最长相同段", row["longest_run"], 11)
        check("A6d distinct", row["distinct"], 3)


def write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def part_b() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        frozen = {"url": FROZEN_URL, "canonical": canon(FROZEN_URL), "verdict": "冻帧：连续 6 帧字节相同（约 75 秒无新画面）"}
        ok = {"url": GOOD_URL, "canonical": canon(GOOD_URL), "verdict": "画面正常推进"}
        undecided = {"url": STALLED_URL, "canonical": canon(STALLED_URL), "verdict": "帧数不足（0 帧），不作判"}
        path = tmp / "line_freeze.jsonl"
        write_rows(path, [frozen, ok, undecided])
        got = bc.load_freeze(path)
        check("B1 判冻那格收录", got.get(canon(FROZEN_URL)) is not None, True)
        check("B2 正常推进不收录", canon(GOOD_URL) in got, False)
        check("B3 不作判不算意见", canon(STALLED_URL) in got, False)
        # B4 累加：后一份说没冻 ⇒ 前面的判冻结论要被清掉（换底）
        write_rows(path, [ok])
        got2 = bc.load_freeze(path)
        check("B4 后来者说没冻应清掉", canon(FROZEN_URL) in got2, False)
        # B5 反向：先没冻后判冻 ⇒ 收录
        write_rows(path, [ok, frozen])
        got3 = bc.load_freeze(path)
        check("B5 后判冻应收录", got3.get(canon(FROZEN_URL)) is not None, True)


def reset_evidence() -> None:
    bc.HEALTH = {canon(u): {"verdict": "可取到分片", "segment_ms": 100} for u in (FROZEN_URL, STALLED_URL, GOOD_URL)}
    bc.IDENTITY = {f"{NAME}|{canon(u)}": "台标相符" for u in (FROZEN_URL, STALLED_URL, GOOD_URL)}
    bc.DROP_DEAD = True
    bc.FRAME_RES = {canon(u): FR for u in (FROZEN_URL, STALLED_URL, GOOD_URL)}
    bc.FRAME_PLACEHOLDER = {}
    bc.FRAME_STALLED = {}
    bc.FROZEN = {}


def part_c() -> None:
    reset_evidence()
    check("C0 基线：三条都是档 0", bc.evidence(FROZEN_URL, NAME), (0, 0))
    # C1 长窗冻帧 ⇒ 档 4、usable 拒（删线）
    bc.FROZEN = {canon(FROZEN_URL): "冻帧：连续 6 帧字节相同（约 75 秒无新画面）"}
    check("C1 长窗冻帧应到档 4", bc.evidence(FROZEN_URL, NAME), (4, 0))
    check("C1 usable 应拒", bc.usable(NAME, FROZEN_URL), False)
    check("C1 has_picture 应 False", bc.has_picture(NAME, FROZEN_URL), False)
    bc.FRESH_RES = {canon(FROZEN_URL): FR}
    check("C1 has_picture_now 应 False", bc.has_picture_now(NAME, FROZEN_URL), False)
    # C2 负对照：摘掉 FROZEN 那一格，同一条线必须翻回档 0
    bc.FROZEN = {}
    check("C2 摘掉判冻格应回档 0", bc.evidence(FROZEN_URL, NAME), (0, 0))
    check("C2 usable 应放行", bc.usable(NAME, FROZEN_URL), True)
    # C3 7 秒窗口卡流 ⇒ 只降分（档 1），不删线
    bc.FRAME_STALLED = {canon(STALLED_URL): "卡流：3 帧字节完全相同（抽帧窗口内没有新画面）"}
    check("C3 卡流应压到档 1", bc.evidence(STALLED_URL, NAME), (1, 0))
    check("C3 卡流不许删线", bc.usable(NAME, STALLED_URL), True)
    check("C3 卡流仍算有画面", bc.has_picture(NAME, STALLED_URL), True)
    # C4 负对照：摘掉 stalled 那格 ⇒ 回到档 0，证明压档的是那一格
    bc.FRAME_STALLED = {}
    check("C4 摘掉卡流格应回档 0", bc.evidence(STALLED_URL, NAME), (0, 0))
    # C5 黑名单：长窗冻帧要随目录打进 APK，公开列表再捞回来时挡得住
    bc.FROZEN = {canon(FROZEN_URL): "冻帧：连续 6 帧字节相同（约 75 秒无新画面）"}
    blocked, _ = bc.blocked_lines()
    check("C5 冻帧地址应进黑名单", blocked.get(canon(FROZEN_URL)), "长窗冻帧")
    check("C5 卡流地址不该进黑名单", canon(STALLED_URL) in blocked, False)
    bc.FROZEN = {}
    blocked, _ = bc.blocked_lines()
    check("C5b 负对照：摘掉后黑名单里没有它", canon(FROZEN_URL) in blocked, False)


def part_d() -> None:
    """D 组：长窗冻帧只在「同一台至少有一条线在同一场里正常推进」时才删线。

    10-24 凌晨那一场 427 条判出 16 条冻帧，帧逐张看过：浙江卫视是 3 条里冻 1 条
    （另外两条同一时刻在动＝这条地址专属坏，该删），而平湖／开化是 2 条全冻、
    兰溪／永嘉／普陀／武义／余姚是单线台——帧里都是节目画面（新闻游走字幕、电视剧），
    不是休台测试卡。整台一起冻住是这台半夜自动播出卡住，删一条线既修不好它、
    对单线台等于把整台删没。所以删线那一侧要一条同台的活读数当对照。
    """
    zj_frozen = {"channel": "浙江卫视", "url": FROZEN_URL, "canonical": canon(FROZEN_URL),
                 "verdict": "冻帧：连续 11 帧字节相同（≥50 秒节目无新画面）"}
    zj_ok = {"channel": "浙江卫视", "url": GOOD_URL, "canonical": canon(GOOD_URL),
             "verdict": "画面正常推进"}
    ph_a = {"channel": "平湖新闻综合", "url": FROZEN_URL, "canonical": canon(FROZEN_URL),
            "verdict": "冻帧：连续 7 帧字节相同（≥30 秒节目无新画面）"}
    ph_b = {"channel": "平湖新闻综合", "url": STALLED_URL, "canonical": canon(STALLED_URL),
            "verdict": "冻帧：整窗 16 帧字节全同（≥75 秒节目无新画面）"}
    lx = {"channel": "兰溪新闻综合", "url": FROZEN_URL, "canonical": canon(FROZEN_URL),
          "verdict": "冻帧：连续 11 帧字节相同（≥50 秒节目无新画面）"}
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        p1 = tmp / "scan_1.jsonl"
        # D1 浙江卫视形状：3 条里冻 1 条，另外两条在动 ⇒ 该删
        write_rows(p1, [zj_frozen, zj_ok])
        frozen, owner, live = bc.load_freeze_all([p1])
        delete, hold = bc.gate_frozen_by_sibling(frozen, owner, live)
        check("D1 有活线对照的冻帧进删除档", list(delete), [canon(FROZEN_URL)])
        check("D1 不进记账档", hold, {})
        check("D1 活线台名收录", live, {"浙江卫视"})
        # D2 平湖形状：两条线（一条带 ?zzhed）全冻 ⇒ 谁都不删，但要记账
        write_rows(p1, [ph_a, ph_b])
        frozen, owner, live = bc.load_freeze_all([p1])
        delete, hold = bc.gate_frozen_by_sibling(frozen, owner, live)
        check("D2 整台都冻不删线", delete, {})
        check("D2 两条都进记账档", sorted(hold.values()), ["平湖新闻综合", "平湖新闻综合"])
        # D3 兰溪形状：单线台，全场只有它自己一条读数 ⇒ 没有对照，不删
        write_rows(p1, [lx])
        frozen, owner, live = bc.load_freeze_all([p1])
        delete, hold = bc.gate_frozen_by_sibling(frozen, owner, live)
        check("D3 单线台冻帧不删线", delete, {})
        check("D3 记账档留着它", hold, {canon(FROZEN_URL): "兰溪新闻综合"})
        # D4 换底：同台的活读数只来自上一场，这一场那条改成「不作判」⇒ 对照消失
        write_rows(p1, [zj_frozen, zj_ok])
        p2 = tmp / "scan_2.jsonl"
        write_rows(p2, [{"channel": "浙江卫视", "url": GOOD_URL, "canonical": canon(GOOD_URL),
                         "verdict": "帧数不足（0 帧），不作判"}])
        frozen, owner, live = bc.load_freeze_all([p1, p2])
        delete, hold = bc.gate_frozen_by_sibling(frozen, owner, live)
        check("D4 旧活读数被新场改判后不再当对照", delete, {})
        check("D4 冻帧本身还在台账里", list(frozen), [canon(FROZEN_URL)])
        # D5 牙齿：不接这道闸，D2 那两条会被直接删掉＋拉黑（证明删与不删确实是闸在管）
        reset_evidence()
        write_rows(p1, [ph_a, ph_b])
        frozen, owner, live = bc.load_freeze_all([p1])
        bc.FROZEN = frozen
        check("D5 摘闸基线：档 4", bc.evidence(FROZEN_URL, "平湖新闻综合"), (4, 0))
        blocked, _ = bc.blocked_lines()
        check("D5 摘闸基线：进了黑名单", canon(FROZEN_URL) in blocked, True)
        delete, hold = bc.gate_frozen_by_sibling(frozen, owner, live)
        bc.FROZEN = delete
        # 接闸后回到「取到过分片」那一档（夹具给这条线的是档 1 的读数，不是档 0）
        check("D5 接闸后不再是档 4", bc.evidence(FROZEN_URL, "平湖新闻综合")[0], 1)
        check("D5 接闸后 usable 放行", bc.usable("平湖新闻综合", FROZEN_URL), True)
        blocked, _ = bc.blocked_lines()
        check("D5 接闸后不进黑名单", canon(FROZEN_URL) in blocked, False)
        check("D5 接闸后另一条也不进黑名单", canon(STALLED_URL) in blocked, False)
        bc.FROZEN = {}


def part_e() -> None:
    """E 组：黑名单里「按前缀收」那一档——纯音频族一条都不许漏。

    10-24 现读：台账里 piccpndali.v.myalicdn.com/audio/ 这一族 20 条，health 全报「可取到分片」，
    但只有抽过帧的 4 条因为「解不出画面」进了黑名单，另外 16 条是敞口的——
    设备端「立即更新线路」会从公开列表把它们再捞一次，电视上永远不出画面。
    规则要按前缀收，不能按「抽过帧的那几条」收，否则明天换个台名又漏一批。
    """
    a1 = "https://piccpndali.v.myalicdn.com/audio/cctv2_2.m3u8"
    a2 = "http://piccpndali.v.myalicdn.com/audio/cctv17_2.m3u8"
    other = "https://piccpndali.v.myalicdn.com/video/cctv2_2.m3u8"
    reset_evidence()
    bc.HEALTH = {canon(u): {"verdict": "可取到分片", "segment_ms": 300} for u in (a1, a2, other)}
    bc.IDENTITY = {}
    bc.FROZEN = {}
    blocked, _ = bc.blocked_lines()
    check("E1 https 那条进黑名单", blocked.get(canon(a1)), "纯音频无视频轨")
    check("E1 http 那条也进（canon 不抹 scheme）", blocked.get(canon(a2)), "纯音频无视频轨")
    check("E2 负对照：同主机 /video/ 那条不收", canon(other) in blocked, False)
    # E3 牙齿：把前缀摘掉，这两条必须一起消失——证明是这一档在收，不是别的读数顺手收的
    saved = bc.AUDIO_ONLY_PREFIXES
    bc.AUDIO_ONLY_PREFIXES = ()
    blocked, _ = bc.blocked_lines()
    check("E3 摘掉前缀后两条都不在黑名单", [canon(a1) in blocked, canon(a2) in blocked], [False, False])
    bc.AUDIO_ONLY_PREFIXES = saved
    # E4 留下与拉黑不能同时成立：目录按名字救回来的地址要从黑名单里摘出去
    blocked, exempted = bc.blocked_lines(keep=frozenset({canon(a1)}))
    check("E4 豁免的那条不进黑名单", canon(a1) in blocked, False)
    check("E4 没豁免的那条仍在", blocked.get(canon(a2)), "纯音频无视频轨")
    check("E4 豁免名单里记着它", canon(a1) in exempted, True)


def main() -> int:
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    for line in failures:
        print(f"[红] {line}")
    print(f"[freeze 夹具] {ran - len(failures)}/{ran} 过 · 红 {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
