#!/usr/bin/env python3
"""验的是用户 10-26 点档「乙」那一格：真死的首条线挪到台内末位——只换序，不删线、不进黑名单。

名册 `tools/demote_lines.json` 是人工写的（每条带三轮回测＋35 秒长窗证据），构建侧只搬运。
本夹具盯六种会说谎的情形：

  A) 挪了但顺序被搅乱——目标线沉底，**其余线路的相对次序必须原样**（不许顺手再排一次）。
  B) 已在末位又"挪"一次——名单计数虚增，用户按数看不出真实动作。
  C) 台名/地址对不上就猜一台——只许报 missing 并跳过，绝不许挂到别的台名下。
  D) 删线混进来——「乙」的边界是只换序；条数一变就是比用户指令更硬。
  E) 同一地址不同写法（默认端口/大小写/尾斜杠）认不出——canon 那把尺必须同用。
  F) 空名册也动手——负对照：`specs=[]` 时任何台的线路都不许变。

跑法：python3 tools/tests/test_demote.py    （退出码 0 才算过）
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "tools"))
import build_catalog as bc  # noqa: E402
import stream_rules  # noqa: E402

NAME = "某卫视"
DEAD = "http://1.1.1.1:8080/live/dead.m3u8"
B = "http://2.2.2.2:8080/live/b.m3u8"
C = "http://3.3.3.3:8080/live/c.m3u8"
OTHER_DEAD = "http://4.4.4.4:8080/live/other.m3u8"

failures: list[str] = []
ran = 0


def check(label: str, got, want) -> None:
    global ran
    ran += 1
    if got != want:
        failures.append(f"{label}: 得到 {got!r}，期望 {want!r}")


def channel(name: str, streams: list[str]) -> dict:
    return {"name": name, "streams": list(streams)}


def spec(name: str, url: str) -> dict:
    return {"channel": name, "url": url, "evidence": "夹具"}


def part_a() -> None:
    """目标线沉底，其余相对次序不变。"""
    ch = channel(NAME, [DEAD, B, C])
    moved, missing = bc.apply_demotions([ch], [spec(NAME, DEAD)])
    check("A1 目标线到末位", ch["streams"], [B, C, DEAD])
    check("A2 其余保持原序", ch["streams"][:2], [B, C])
    check("A3 不删线（条数不变）", len(ch["streams"]), 3)
    check("A4 moved 记账一条", [(n, k) for n, _, k in moved], [(NAME, 3)])
    check("A5 没有 missing", missing, [])


def part_b() -> None:
    """已在末位：不动、不记账。"""
    ch = channel(NAME, [B, C, DEAD])
    moved, missing = bc.apply_demotions([ch], [spec(NAME, DEAD)])
    check("B1 末位原样", ch["streams"], [B, C, DEAD])
    check("B2 不虚增计数", moved, [])
    check("B3 也不算 missing", missing, [])


def part_c() -> None:
    """名册对不上只报不猜。"""
    ch = channel(NAME, [DEAD, B])
    moved, missing = bc.apply_demotions([ch], [spec("查无此台", DEAD)])
    check("C1 台名对不上：线路原样", ch["streams"], [DEAD, B])
    check("C2 台名对不上：进 missing", len(missing), 1)
    moved, missing = bc.apply_demotions([ch], [spec(NAME, OTHER_DEAD)])
    check("C3 地址不在本台：线路原样", ch["streams"], [DEAD, B])
    check("C4 地址不在本台：进 missing", len(missing), 1)
    # 负对照：换成真地址同一台，必须真的动
    moved, missing = bc.apply_demotions([ch], [spec(NAME, DEAD)])
    check("C5 负对照（真地址会动）", ch["streams"], [B, DEAD])


def part_d() -> None:
    """只换序：条数、地址集合都不许变。"""
    ch = channel(NAME, [DEAD, B, C])
    before = set(ch["streams"])
    bc.apply_demotions([ch], [spec(NAME, DEAD)])
    check("D1 地址集合不变", set(ch["streams"]), before)
    check("D2 条数不变", len(ch["streams"]), len(before))


def part_e() -> None:
    """同一地址不同写法也要认出来（canon）。"""
    variants = [
        "http://1.1.1.1:8080/live/dead.m3u8/",   # 尾斜杠
        "HTTP://1.1.1.1:8080/live/dead.m3u8",    # 大小写
    ]
    for v in variants:
        ch = channel(NAME, [DEAD, B])
        bc.apply_demotions([ch], [spec(NAME, v)])
        check(f"E 变体 {v} 认出并沉底", ch["streams"], [B, DEAD])


def part_f() -> None:
    """负对照：空名册一行都不动。"""
    ch = channel(NAME, [DEAD, B, C])
    moved, missing = bc.apply_demotions([ch], [])
    check("F1 空名册不动线", ch["streams"], [DEAD, B, C])
    check("F2 空名册不记账", (moved, missing), ([], []))


def part_g() -> None:
    """真名册（specs=None 那条路）能解析、能命中目录里的台。"""
    specs = json.loads(bc.DEMOTE_FILE.read_text(encoding="utf-8")).get("lines") or []
    check("G1 真名册非空", len(specs) > 0, True)
    # 造一份只含名册里三台的目录：每条都从第 1 位挪到末位
    chans = [channel(s["channel"], [s["url"], B, C]) for s in specs]
    moved, missing = bc.apply_demotions(chans, None)
    check("G2 真名册全部命中", missing, [])
    check("G3 真名册全部挪到末位", [c["streams"][-1] for c in chans], [s["url"] for s in specs])
    # 负对照：把名字改掉一个字，同一份名册必须报 missing 而不是猜
    chans = [channel(s["channel"] + "X", [s["url"], B]) for s in specs]
    moved, missing = bc.apply_demotions(chans, None)
    check("G4 负对照（名字改一字）", (moved, len(missing)), ([], len(specs)))


def part_h():
    """#149 甲「钉回目录」那一格：只救「对端这一轮回了一个坏判词」，别的一律钉不动。

    放宽类判据按规矩交六格：放行 1 格（P1）＋判红 5 格（P2 没钉住／P3 冻帧／P4 容器不支持／
    P5 台标是别的台／P7 不许顺手把画面那一侧也放宽）。`evidence`/`usable`/`pin_rescues` 走真码，
    只把画面那几把尺接成夹具读数（与 part_a 同一手法）。
    """
    name = "某国际台"
    pinned = "http://stream.mcquack.net/131/index.m3u8"
    other = "http://9.9.9.9/a.m3u8"
    live = B
    specs = [{"channel": name, "url": pinned, "pin": True, "evidence": "夹具"}]

    @contextlib.contextmanager
    def env(verdict="清单404", frozen=False, placeholder=False, identity=None, pin=True):
        keep = {n: getattr(bc, n) for n in ("HEALTH", "PINNED", "FROZEN", "FRAME_PLACEHOLDER",
                                            "IDENTITY", "has_picture", "has_picture_now",
                                            "device_proof", "logo_keeps_line", "DROP_DEAD")}
        bc.HEALTH = {stream_rules.canon(pinned): {"url": pinned, "verdict": verdict},
                     stream_rules.canon(live): {"url": live, "verdict": bc.GOOD_VERDICT},
                     stream_rules.canon(other): {"url": other, "verdict": "清单404"}}
        bc.PINNED = bc.pin_specs(specs) if pin else set()
        bc.FROZEN = {stream_rules.canon(pinned): "长窗冻帧"} if frozen else {}
        bc.FRAME_PLACEHOLDER = {stream_rules.canon(pinned): "二维码推广页"} if placeholder else {}
        bc.IDENTITY = identity or {}
        bc.DROP_DEAD = True
        bc.has_picture = lambda n, u: stream_rules.canon(u) == stream_rules.canon(live)
        bc.has_picture_now = lambda n, u: stream_rules.canon(u) == stream_rules.canon(live)
        bc.device_proof = lambda n, u: False
        bc.logo_keeps_line = lambda n, u: False
        try:
            yield
        finally:
            for n, v in keep.items():
                setattr(bc, n, v)

    def chan(sub, streams):
        return {"name": name, "category": "international", "subgroup": sub,
                "streams": list(streams)}

    with env():
        check("P1 放行：钉住的那条线 usable 为真", bc.usable(name, pinned), True)
        check("P1b 放行：档位仍是 4（钉住只挡删除，不改判分）", bc.evidence(pinned, name)[0], 4)
        surv, st = bc.strict_filter([chan("新闻", [pinned])])
        check("P1c 放行：整台没一条画面实证，台仍留着",
              ([c["name"] for c in surv], st["pin_kept"], st["dropped_no_source"]),
              ([name], [name], 0))
        check("P1d 放行：这条线沉在台内末位（它本来就是唯一一条）",
              surv[0]["streams"], [pinned])
        check("P1e 放行：黑名单不许同时收这条地址",
              stream_rules.canon(pinned) in bc.blocked_lines(
                  {stream_rules.canon(s) for s in surv[0]["streams"]})[0], False)

    with env(pin=False):
        check("P2 判红（牙齿）：不钉住就整台消失",
              bc.strict_filter([chan("新闻", [pinned])])[1]["dropped_no_source"], 1)
        check("P2b 判红：不钉住时这条线 usable 为假", bc.usable(name, pinned), False)

    with env(frozen=True):
        check("P3 判红：长窗冻帧钉不动", bc.usable(name, pinned), False)
        check("P3b 判红：冻帧台照样消失",
              bc.strict_filter([chan("新闻", [pinned])])[1]["dropped_no_source"], 1)

    with env(verdict="容器不支持"):
        check("P4 判红：播放器解不开的容器钉不动", bc.usable(name, pinned), False)

    with env(identity={f"{name}|{stream_rules.canon(pinned)}": "画面是别的台"}):
        check("P5 判红：台标认成别的台时钉不动", bc.usable(name, pinned), False)

    with env():
        surv, st = bc.strict_filter([chan("新闻", [live, pinned])])
        check("P7 判红：画面那一侧没被顺手放宽（这台仍算没有画面实证→G2/G4 照旧）",
              bc.has_picture(name, pinned), False)
        check("P7b 放行：台内活线仍在第 1 位，钉住的那条沉末位",
              surv[0]["streams"], [live, pinned])
        check("P7c 判红：pin_kept 不替「有活线的台」记账", st["pin_kept"], [])

    with env():
        # ②那一格（档 3 删线）也要吃 pin：本组是 international，活线之外那条坏线不许被剪掉
        surv, st = bc.strict_filter([chan("新闻", [live, pinned])])
        check("P8 放行：档 3 剪枝不吃钉住的那条",
              (len(surv[0]["streams"]), len(st["tier3_pruned"])), (2, 0))
    with env(verdict="连接超时"):
        surv, st = bc.strict_filter([chan("新闻", [live, pinned])])
        check("P8b 放行：钉住的这条即便本轮判成「本机连不通」也不被②那一格剪掉",
              (len(surv[0]["streams"]), len(st["tier3_pruned"])), (2, 0))
    with env(verdict="连接超时", pin=False):
        surv, st = bc.strict_filter([chan("新闻", [live, pinned])])
        check("P8c 判红（牙齿）：同一份读数不钉住时，②那一格照旧摘它",
              (len(surv[0]["streams"]), len(st["tier3_pruned"])), (1, 1))

    with env():
        check("P6 判红：名册只认它自己那条地址，别的一条不救", bc.usable(name, other), False)
        check("P6b 放行：canon 一致才算命中（带默认端口/尾斜杠的写法）",
              bc.usable(name, "http://stream.mcquack.net:80/131/index.m3u8"), True)
    check("P9 真名册只吃 pin 那一格（6 条里 1 条钉住）",
          len(bc.pin_specs(json.loads(bc.DEMOTE_FILE.read_text(encoding="utf-8"))["lines"])), 1)


def main() -> int:
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    part_f()
    part_g()
    part_h()
    for line in failures:
        print(f"[红] {line}")
    print(f"[demote 夹具] {ran - len(failures)}/{ran} 过 · 红 {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
