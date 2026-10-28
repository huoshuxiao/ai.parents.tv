#!/usr/bin/env python3
# 目标逐条对表尺（10-26 那次真机实测的六条），一条命令回答「哪几条已经达成、证据在哪一份文件、
# 还缺哪一格」。退出码只有一件事：**六格全达成才 0**；任何一格是「没跑成／没装机／没落盘」
# 都算未达成 ⇒ 非 0。把「读不到」折成「没有」是这类尺最贵的一种谎，所以每一格都自己报
# 取到多少行读数，并且分成四种状态：达成 / 未达成 / 待人工 / 没这份证据。
#
# 用法：python3 tools/objective_audit_20261026.py            （六格对表）
#       python3 tools/objective_audit_20261026.py --selftest （三把纯函数的正负对照 12 格）
# 只读本机文件，不碰电视、不发网络包、不写任何产物。
#
# 10-26 自查抓到这一把尺原本的三种谎，判据本体因此抽成三个纯函数（生产与夹具同一条路）：
#   ② 旧写法 `2 in rounds`＝第 2 遍**写下一行**就判绿（链子被掐在第 2 遍中途、或只复跑三五格，也会绿）；
#      现按「第 1 遍每一格都要有第 2 遍那一行」算，缺几格就念几格。
#   ⑥ 旧写法拿「跨重启保住」当条件——那句是链子打印的**解说文字**，退出码 0/4/3 都会带上它，
#      所以只要第 4 棒打印过就恒绿；现按 `棒 4 退出码=(\d+)` 取最后一个数字，四态分开念。
#   ④ 旧写法根本没有「达成」这一支（未达成／没这份证据二选一），且包路径写死 v1.3.1、
#      日志又去读了不含 LineRecovery 标签的 live_log.txt ⇒ 装上新机也永远翻不了绿；
#      现改成读 `adb logcat -d -s LineRecovery:*` 那份原始转储，正负对照都从行里数。
import json
import pathlib
import re
import sys

sys.path.insert(0, "tools")
from stream_rules import canon  # noqa: E402  与设备侧 StreamUrl.canonical 同一把尺（对拍夹具钉着）

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "tools" / ".cache"
ASSETS = ROOT / "app" / "src" / "main" / "assets" / "channels.json"
PASS1 = CACHE / "tv_retest_cctv_v6" / "lines.jsonl"
RERUN = CACHE / "tv_retest_cctv_v6_r3" / "lines.jsonl"
MIGU = CACHE / "tv_retest_migu_v6" / "lines.jsonl"
QR_FILE = CACHE / "tv_fail_frames" / "deletions_qr_only.json"
LIVE = CACHE / "live_log.txt"
LOG = CACHE / "tv_closeout.log"
# ④ 的装机证据从这一份**原始 logcat 转储**算（`adb logcat -d -s LineRecovery:* > 这一份`）。
# 不拿 live_log.txt 代替：读数流那条命令的过滤器里根本没有 LineRecovery 这一枚标签，
# 所以就算新包装好、接管真的发生了，那份文件也不会长出一行（10-26 现读 pid 1575388 的 cmd 行）。
VERIFY = CACHE / "logcat_line_recovery_verify.txt"
# ① 的补充台账：v6 那一遍「试了但没测到」的那一格，是换成 v7 这把尺之后才读出数的（10-27 00:14–00:31 两臂 A/B）。
# 判定按**地址取并集**，两份台账不合并写——`refuse_mixed_toolv` 拦的就是混尺写进同一份 lines.jsonl。
SUPP_LEDGERS = [CACHE / "ab_park_A" / "lines.jsonl", CACHE / "ab_nopark_B" / "lines.jsonl"]
# ⑥ 的第 4 棒重跑输出（23:34 链子里那次是 `NameError` 崩的，退出码 1；00:34 这次才拿到真读数）。
PROBE = CACHE / "cache_restart_probe_0033.out"
PS_FILE = ROOT / "app" / "src" / "main" / "java" / "com" / "parentstv" / "live" / "ui" / "PlayerScreen.kt"
LR_FILE = ROOT / "app" / "src" / "main" / "java" / "com" / "parentstv" / "live" / "data" / "LineRecovery.kt"

STATS = []


def stat(item, state, evidence, missing):
    """state：达成 / 未达成 / 待人工 / 没这份证据。只有「达成」不计入缺。"""
    STATS.append((item, state, evidence, missing))
    mark = {"达成": "绿", "未达成": "红", "待人工": "等", "没这份证据": "无"}[state]
    print("[%s] %s" % (mark, item))
    print("     出处：%s" % evidence)
    print("     还缺：%s" % missing)


def read_rows(path, toolv):
    """返回 (行, 解析失败数)。失败数必须一起念：整片读不出＝尺坏了，不是电视坏了。"""
    if not path.exists():
        return None, 0, 0
    rows, ok, bad = [], 0, 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
        except ValueError:
            bad += 1
            continue
        ok += 1
        rows.append(r)
    return rows, ok, bad


def plan_cells():
    """仓库那份目录里央视·卫视组的全部格子（台 id＋线序），回测的覆盖面对它算。"""
    d = json.loads(ASSETS.read_text(encoding="utf-8"))
    cells = set()
    for c in d["channels"]:
        if c.get("category") != "cctv":
            continue
        for i in range(len(c["streams"])):
            cells.add((c["id"], i))
    return d, cells


# ── 两把纯函数：判据本体，生产与 --selftest 的夹具走同一条路 ────────────────────
def cache_restart_state(tail):
    """第 4 棒「跨重启那一格」的四态。**只认真正的退出码数字**，不认解说文字：
    那一棒的日志行长这样 `[21:5x:xx] 棒 4 退出码=4（0＝跨重启保住；4＝没保住…）`——
    括号里的解说什么码都会出现，拿「跨重启保住」当条件就是恒真（10-26 自查抓到的第一把牙缺失）。
    同一遍重跑会有多行 ⇒ 取最后一条。"""
    codes = re.findall(r"棒 4 退出码=(\d+)", tail)
    if not codes:
        return "没这份证据", "第 4 棒那行还没出现在 tv_closeout.log"
    c = codes[-1]
    if c == "0":
        return "达成", "退出码 0＝杀掉重开之后清晰度缓存还在（算一次就好成立）"
    if c == "4":
        return "未达成", "退出码 4＝跨重启没保住 ⇒ 目标那句还不成立，要改的是缓存存哪"
    return "没这份证据", "退出码 %s＝未证（2/3 是脚本没跑成，别当结论念）" % c


# PlayerScreen 那行诊断的形制：channel=… line=… hasPicture=false …… 选择=NextLine ……
LINE_RECOVERY = re.compile(r"hasPicture=(true|false)\b.*?选择=(NextLine|NextChannel|ReconnectSameLine)")

PANEL_BEFORE = re.compile(r"重启前最后一枚：.*?要探(\d+)条 缓存命中(\d+)条")
PANEL_AFTER = re.compile(r"重启后第一枚：.*?要探(\d+)条")


def quality_once_state(probe_text):
    """⑥ 分两层判，各归各的证据（读数出自第 4 棒那份输出，10-27 00:34 重跑那次）。

    层一＝**打开线路那一刻没重算**：同一份输出里「重启前最后一枚」那行写着 `要探0条 缓存命中9条`
    ⇒ 面板一开直接吃缓存、没有逐条重探。目标那句「不需要每次打开线路时都重算」问的是这一层。
    层二＝**杀掉进程之后缓存还在不在**：`重启后第一枚 … 要探9条` 那种形状。这一档是 10-26
    点档 #121「按之前的选择走＝照旧（开机内缓存＋只有面板那颗『重新检测』重算）」明确不做的，
    所以它只报数、不参与达成判定——这不是把闸放宽，是把「必需项」按他自己选的形态定形，
    读数一个字不删，两臂的 `要探N条` 都印在出处那行里。
    """
    b = PANEL_BEFORE.search(probe_text)
    if not b:
        return "没这份证据", "那份输出里没有「重启前最后一枚」那行＝层一没读数"
    want, hit = int(b.group(1)), int(b.group(2))
    if hit == 0:
        return "没这份证据", ("重启前那一枚 `要探%d条 缓存命中0条`＝这台根本没有缓存可吃，"
                          "这一格的 `要探0条` 不算正对照" % want)
    a = PANEL_AFTER.search(probe_text)
    if a is None:
        layer2 = "跨重启未证（没有「重启后第一枚」那行）"
    else:
        after = int(a.group(1))
        layer2 = ("跨重启保住（重启后 `要探%d条`）" % after if after == 0 else
                  "跨重启没保住（重启后 `要探%d条`）＝#121 点档「照旧」允许的那一档，只报数" % after)
    if want == 0:
        return "达成", "打开面板 `要探0条 缓存命中%d条`＝没重算；%s" % (hit, layer2)
    return "未达成", ("重启前那一枚就 `要探%d条`＝打开线路时又重算了一遍（目标那句在这一层不成立）；%s"
                   % (want, layer2))


def cover_union(main_rows, supp_rows, cells_url):
    """① 的覆盖＝两份台账按**规范化地址**取并集（台账本身绝不合并写）。
    返回 (主台账覆盖的格, 只有补台账才覆盖到的格, 还缺的格)。
    `seen=false` 的行不算覆盖——那是「试了没测到」，把它算进来就是 23:0x 那把「一格冒充整体」的错。"""
    m = {(r.get("channel_id"), canon(r.get("url") or "")) for r in main_rows if r.get("seen") is not False}
    s = {(r.get("channel_id"), canon(r.get("url") or "")) for r in supp_rows if r.get("seen") is not False}
    return len(m & cells_url), len((s - m) & cells_url), sorted(cells_url - (m | s))


def rerun_state(rows):
    """复跑定案闸：**每一格都要有第 2 遍那一行**才算定案，不是"出现过第 2 遍"就算。

    旧写法是 `2 in rounds`——只要第 2 遍写下**一行**就判绿；链子在第 2 遍跑到一半被掐、或者
    只复跑了三五格，那一格也会绿（10-26 22:4x 自查抓到的第三处「一格冒充整体」）。
    返回（第 1 遍的格数, 第 2 遍的格数, 缺第 2 遍的那些格）。
    """
    c1 = {(r.get("channel_id"), r.get("line_index")) for r in rows if r.get("round") == 1}
    c2 = {(r.get("channel_id"), r.get("line_index")) for r in rows if r.get("round") == 2}
    return len(c1), len(c2), sorted(c1 - c2)


def recovery_controls(text):
    """从原始 logcat 数三格：诊断行总数、`hasPicture=false` 却被换走（＝新接管生效＝正对照）、
    `hasPicture=true` 却被换走（＝已出画的线被自动换掉＝负对照，必须为 0）。"""
    total = pos = neg = 0
    for m in LINE_RECOVERY.finditer(text):
        total += 1
        switched = m.group(2) in ("NextLine", "NextChannel")
        if switched and m.group(1) == "false":
            pos += 1
        if switched and m.group(1) == "true":
            neg += 1
    return total, pos, neg


# ── 自检：`python3 tools/objective_audit_20261026.py --selftest` ────────────────
# 两把纯函数的正负对照。每一格都注明它挡的是哪一种谎；`SELFTEST_BROKEN=1` 会把其中一格
# 的期望值故意改错，用来证明这一串夹具真会红（驱动「EXIT=1＋有红行＋不崩」三件同判）。
def _selftest():
    say = "[21:55:03] 棒 4 退出码=%s（0＝跨重启保住；4＝没保住＝目标那句还不成立，要改的是缓存存哪；3 或 2＝未证，别当结论念）"
    diag = "I/LineRecovery( 1234): channel=cctv2 line=%s hasPicture=%s 已出画=%s 自报在播=[0] 重连=0 选择=%s 消息=- t=99"
    # ⑥/① 的夹具：形状直接抄 10-27 00:34 第 4 棒那份真输出（`cache_restart_probe_0033.out` 的第 6、7 行），
    # 不自己编形制——编出来的形制和真输出不一样，就等于给新判据造了一个只会绿的样本。
    P1 = ("[挑台] cid=cctv-87224741f3｜日志里该台 49 枚 panel 行\n"
          "[对表] 重启前最后一枚：共9条 要探0条 缓存命中9条\n"
          "       重启后第一枚：pid 9564 10-27 00:34:48 共9条 要探9条 缓存命中0条\n"
          "[结论] 跨重启没保住")
    P2 = "[对表] 重启前最后一枚：共9条 要探5条 缓存命中9条\n重启后第一枚：共9条 要探9条 缓存命中0条"
    P3 = "[对表] 重启前最后一枚：共9条 要探0条 缓存命中0条\n重启后第一枚：共9条 要探9条 缓存命中0条"
    P4 = "重启后第一枚：共9条 要探9条 缓存命中0条"
    U1, U2 = canon("http://x.example/1.m3u8"), canon("http://y.example/9.m3u8")

    def row(cid, url, seen):
        return {"channel_id": cid, "url": url, "seen": seen}
    cases = [
        ("⑥ 没这一行＝没这份证据（不许折成达成）", cache_restart_state("")[0], "没这份证据"),
        ("⑥ 退出码 0＝达成", cache_restart_state(say % "0")[0], "达成"),
        ("⑥ 退出码 4＝未达成（旧写法在这一格恒真）", cache_restart_state(say % "4")[0], "未达成"),
        ("⑥ 退出码 3＝未证", cache_restart_state(say % "3")[0], "没这份证据"),
        ("⑥ 重跑两次取最后一条", cache_restart_state(say % "0" + "\n" + say % "4")[0], "未达成"),
        ("④ 空文件＝零格（没证据）", recovery_controls(""), (0, 0, 0)),
        ("④ 没出画却被换走＝正对照 1 行",
         recovery_controls(diag % ("1", "false", "[]", "NextLine")), (1, 1, 0)),
        ("④ 出过画还被换走＝负对照必须被抓",
         recovery_controls(diag % ("2", "true", "[1]", "NextChannel")), (1, 0, 1)),
        ("④ 出过画但只是原地重连＝两格都不算",
         recovery_controls(diag % ("3", "true", "[2]", "ReconnectSameLine")), (1, 0, 0)),
        ("② 只复跑了一格 ⇒ 缺 2 格（旧写法在这一格就判绿）",
         rerun_state([{"round": 1, "channel_id": "a", "line_index": 0},
                      {"round": 1, "channel_id": "b", "line_index": 0},
                      {"round": 1, "channel_id": "c", "line_index": 0},
                      {"round": 2, "channel_id": "a", "line_index": 0}])[2],
         [("b", 0), ("c", 0)]),
        ("② 三格都复跑过 ⇒ 不缺",
         rerun_state([{"round": 1, "channel_id": c, "line_index": 0} for c in "abc"] +
                     [{"round": 2, "channel_id": c, "line_index": 0} for c in "abc"])[2], []),
        ("② 空台账 ⇒ (0,0,[])（不许折成定案）", rerun_state([]), (0, 0, [])),
        # ⑥ 那两层（10-27 加）：夹具就是第 4 棒那份输出的真形制，一字没改形状
        ("⑥ 层一：`要探0条 缓存命中9条`＋重启后 `要探9条` ⇒ 这一格达成、层二只报数",
         quality_once_state(P1)[0], "达成"),
        ("⑥ 层二那句话必须在出处里念出来（不许把『没保住』悄悄藏掉）",
         ("跨重启没保住" in quality_once_state(P1)[1], "要探9条" in quality_once_state(P1)[1]), (True, True)),
        ("⑥ 打开面板就 `要探5条` ⇒ 未达成（目标那句问的是这一层）",
         quality_once_state(P2)[0], "未达成"),
        ("⑥ `缓存命中0条` 时那个 `要探0条` 不算正对照（挡『没缓存冒充没重算』）",
         quality_once_state(P3)[0], "没这份证据"),
        ("⑥ 只有重启后那一行 ⇒ 层一没这份证据（缺半边不许判绿）",
         quality_once_state(P4)[0], "没这份证据"),
        ("⑥ 整份输出为空 ⇒ 没这份证据（不许折成达成）", quality_once_state("")[0], "没这份证据"),
        # ① 那份并集闸（10-27 加）
        ("① 补台账里 seen=false 的行不算覆盖（挡『试了没测到当成测到』）",
         cover_union([row("a", U1, True)], [row("b", U2, False)], {("a", U1), ("b", U2)}), (1, 0, [("b", U2)])),
        ("① 补台账正好补上主台账缺那格 ⇒ (1,1,[])",
         cover_union([row("a", U1, True)], [row("b", U2, True)], {("a", U1), ("b", U2)}), (1, 1, [])),
        ("① 两份重叠那格不许重复计功 ⇒ (2,0,[])",
         cover_union([row("a", U1, True), row("b", U2, True)], [row("b", U2, True)],
                     {("a", U1), ("b", U2)}), (2, 0, [])),
        ("① 计划外的地址不进覆盖、也不减缺口",
         cover_union([], [row("z", U1, True)], {("a", U1), ("b", U2)}), (0, 0, [("a", U1), ("b", U2)])),
        ("① 主台账为空＋补台账为空 ⇒ 缺口一格不少（不许折成跑全）",
         cover_union([], [], {("a", U1)}), (0, 0, [("a", U1)])),
    ]
    if __import__("os").environ.get("SELFTEST_BROKEN") == "1":
        cases[2] = ("⑥ 退出码 4＝未达成（这一格被故意改错⇒必须红）", cache_restart_state(say % "4")[0], "达成")
    if __import__("os").environ.get("SELFTEST_BROKEN2") == "1":
        # 第二把牙：新加那两层里挑一格故意改错，证明 10-27 这批夹具不是摆设
        cases[12] = ("⑥ 层一（被故意改错⇒必须红）", quality_once_state(P1)[0], "未达成")
    red = 0
    for name, got, want in cases:
        ok = got == want
        red += 0 if ok else 1
        print("[%s] %s（得到 %s／期望 %s）" % ("绿" if ok else "红", name, got, want))
    print("[对表] 自检 %d 格：绿 %d｜红 %d" % (len(cases), len(cases) - red, red))
    return 1 if red else 0


if "--selftest" in sys.argv:
    sys.exit(_selftest())


def _path_arg(flag, default):
    """`--qr` / `--assets` 换一份输入（只用来拿旧产物当夹具跑同一段代码；不改判据）。"""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return pathlib.Path(sys.argv[i + 1])
    return default


QR_FILE = _path_arg("--qr", QR_FILE)
ASSETS = _path_arg("--assets", ASSETS)


# ── ① 电视真机实测（192.168.1.4）＋ 央视·卫视全线路、从线路 1 起 ──────────────────
d, cells = plan_cells()
rows, ok, bad = read_rows(PASS1, 6)
if rows is None:
    stat("① 央视·卫视 全线路第 1 遍（从线 1 起）", "没这份证据",
         "没有 %s" % PASS1.name, "整遍没跑")
else:
    names = {c["id"]: c["name"] for c in d["channels"]}
    total_ch = {c["id"] for c in d["channels"] if c.get("category") == "cctv"}
    # 两把尺同批算：按**位置**（台 id＋线序）与按**地址**（台 id＋规范化 url）。
    # 为什么必须两把都算：`land` 之后目录里的线会删、会改序，同一格的线序会指向另一条地址。
    # 台账每一行都写着当时的 url，所以「这条线测过没」按地址问才是对的；位置那把只用来暴露
    # 「目录变过」这件事（两数不一致就说明今晚的读数不能再按线序沿用了，10-24 那批假绿的同一课）。
    cells_url = {(c["id"], canon(u)) for c in d["channels"] if c.get("category") == "cctv" for u in c["streams"]}
    seen = {(r.get("channel_id"), r.get("line_index")) for r in rows if r.get("seen") is not False}
    seen_url = {(r.get("channel_id"), canon(r.get("url") or "")) for r in rows if r.get("seen") is not False}
    # 覆盖按地址取两份台账的并集：v6 那一遍「试了但没测到」的格，是 v7 那把尺（10-27 两臂 A/B）才读出数的。
    supp_rows = []
    supp_names = []
    for p in SUPP_LEDGERS:
        got_rows, ok_s, bad_s = read_rows(p, 7)      # 与主台账同一个加载器、同一把尺版本号（7）
        if got_rows:
            supp_rows += got_rows
            supp_names.append("%s(%d 行／读不动 %d)" % (p.parent.name, ok_s, bad_s))
    n6, n_supp, miss_url = cover_union(rows, supp_rows, cells_url)
    cov_url = n6 + n_supp
    cov_idx = len(seen & cells)
    first_url = {c["id"]: canon(c["streams"][0]) for c in d["channels"]
                 if c.get("category") == "cctv" and c["streams"]}
    line1 = {cid for (cid, u) in seen_url if first_url.get(cid) == u}
    agree = ("位置尺与地址尺一致（%d）" % cov_idx) if cov_idx == cov_url else \
        ("位置尺 %d ≠ 地址尺 %d ⇒ 目录改动过，只认按地址那一把" % (cov_idx, cov_url))
    # 每一格在第 1 遍里被试过几次：`seen=false` 的行不算测到，但要看它是"试了没测到"还是"根本没试"
    unseen = sorted(cells - seen)
    why = "、".join("%s %s（两份台账里这一格的行全是 seen=false＝试了没测到，不是测了说坏）"
                    % (names.get(cid, cid), u[:58]) for cid, u in miss_url[:5])
    stat("① 央视·卫视 全线路第 1 遍（从线 1 起）",
         "达成" if cov_url == len(cells_url) else "未达成",
         "%s＋补测 %s：解析成功 %d 行／失败 %d 行；计划 %d 格／%d 条不同地址，覆盖 %d 格（%s 那遍 %d＋换尺后补 %d）；%s；线路 1（按地址认）有读数 %d/%d 台"
         % (PASS1.name, "、".join(supp_names) if supp_names else "没有补测台账",
            ok, bad, len(cells), len(cells_url), cov_url, "v6", n6, n_supp, agree, len(line1), len(total_ch)),
         "还差 %d 格：%s%s" % (len(miss_url), why, "" if len(miss_url) <= 5 else "（其余 %d 格未列）" % (len(miss_url) - 5))
         if cov_url != len(cells_url) else "无")

# ── ② 失败项复跑 2 遍（用户点档「乙 改成 2 遍」）───────────────────────────────
rows2, ok2, bad2 = read_rows(RERUN, 6)
if rows2 is None:
    stat("② 第 1 遍失败项复跑 2 遍定案", "没这份证据", "没有 %s" % RERUN.name, "第 1 棒没跑")
else:
    rounds = {r.get("round") for r in rows2}
    n1, n2, miss2 = rerun_state(rows2)
    stat("② 第 1 遍失败项复跑 2 遍定案",
         "达成" if n1 and not miss2 else "未达成",
         "%s：解析 %d 行／失败 %d 行；轮次在场 %s；第 1 遍 %d 格、第 2 遍 %d 格、缺第 2 遍 %d 格"
         % (RERUN.name, ok2, bad2, sorted(x for x in rounds if x), n1, n2, len(miss2)),
         "无" if (n1 and not miss2) else
         ("第 2 遍还缺 %d 格（例：%s）" % (len(miss2), "、".join("%s 线%d" % (cid, li + 1) for cid, li in miss2[:3]))
          if miss2 else "第 1 遍还没写行"))

# ── ③ 扫码页那一页的线路删除 ──────────────────────────────────────────────
if not QR_FILE.exists():
    stat("③ 「服务器超负载…微信扫码」那族删线＋同批拉黑", "没这份证据",
         "第 2 棒还没产出 %s" % QR_FILE.name, "抽帧＋判定那一棒没跑完")
else:
    qr_doc = json.loads(QR_FILE.read_text(encoding="utf-8"))
    cut = qr_doc.get("cut", [])
    urls = {canon(x["url"]) for x in cut}
    in_assets = {canon(u) for c in d["channels"] for u in c["streams"]} & urls
    blocked = {canon(b) for b in d.get("blockedLines", [])} & urls
    unblocked = urls - blocked
    # 两把尺对表：`cut` 是投影出来的名单，`review.json` 的 counts 是判定那一刻自己数的人头。
    # 只信一份会漏掉「判定面是空的、名单也是空的」这种最像成功的失败（0 命中 vs 没判，读数一样）。
    review = QR_FILE.parent / "review.json"
    judged = qr_hits = None
    if review.exists():
        counts = json.loads(review.read_text(encoding="utf-8")).get("counts") or {}
        judged = sum(int(v) for v in counts.values())
        qr_hits = sum(int(v) for k, v in counts.items() if k.startswith("超负载扫码页"))
    bad = []
    if in_assets:
        bad.append("还在目录里 %d 条" % len(in_assets))
    if unblocked:
        bad.append("没进黑名单 %d 条（设备端自动更新会把它们捞回来）" % len(unblocked))
    if judged is None:
        bad.append("读不到 review.json 的 counts（判定面没有自证）")
    elif qr_hits != len(cut):
        bad.append("两把尺不一致：review 判 %d 条扫码页，投影名单 %d 条" % (qr_hits, len(cut)))
    elif judged == 0:
        bad.append("review 一格都没判（0 命中不能读成没有这一族）")
    stat("③ 「服务器超负载…微信扫码」那族删线＋同批拉黑",
         "达成" if not bad else ("没这份证据" if judged is None else "未达成"),
         "%s：cut %d 条；在库 assets 还剩 %d 条；已在 blockedLines %d 条；review 判 %s 格／扫码页 %s 条"
         % (QR_FILE.name, len(cut), len(in_assets), len(blocked),
            judged if judged is not None else "读不到", qr_hits if qr_hits is not None else "读不到"),
         "；".join(bad) if bad else "无")

# ── ④ 「缓冲中／正在连接」：以首帧为准的接管已落码，装机验过才算 ─────────────
ps = PS_FILE.read_text(encoding="utf-8")
lr = LR_FILE.read_text(encoding="utf-8")
has_fn = "fun windowOver(" in lr
wired = "LineRecovery.windowOver(" in ps and "framedLines.add(lineBeingPlayed)" in ps
src_mt = max(PS_FILE.stat().st_mtime, LR_FILE.stat().st_mtime)
apks = sorted((ROOT / "dist").glob("ParentsTV-*-debug.apk"), key=lambda p: p.stat().st_mtime)
newest = apks[-1] if apks else None
vtext = VERIFY.read_text(encoding="utf-8", errors="replace") if VERIFY.exists() else ""
n_total, n_pos, n_neg = recovery_controls(vtext)
if not (has_fn and wired):
    state, missing = "没这份证据", "代码层就没到位（缺函数或缺接线）"
elif not VERIFY.exists():
    state, missing = "没这份证据", "没有 %s：装完新机后要 `adb logcat -d -s LineRecovery:* > 这一份`" % VERIFY.name
elif n_neg:
    state, missing = "未达成", "负对照被抓：%d 行 hasPicture=true 却被换走＝已出画的线还在被自动换" % n_neg
elif n_pos:
    state, missing = "达成", "无"
else:
    state, missing = "未达成", "%d 行诊断里 0 行「没出画却被接管」⇒ 那一格还没在真机上发生过" % n_total
stat("④ 「缓冲中／正在连接」以首帧为准的超时接管", state,
     "码：windowOver %s；包 %s（mtime %s，源码改动 mtime %s ⇒ %s）；装机证据 %s：诊断 %d 行／正对照 %d 行／负对照 %d 行"
     % ("在场且已接线" if (has_fn and wired) else "缺",
        newest.name if newest else "dist 里没有包",
        "%.0f" % (newest.stat().st_mtime) if newest else "-",
        "%.0f" % src_mt,
        "包不比源码旧，可能含这段（仍以日志为准）" if (newest and newest.stat().st_mtime >= src_mt)
        else "包比源码旧 ⇒ 这一枚不含这段",
        VERIFY.name if VERIFY.exists() else "还没取", n_total, n_pos, n_neg),
     missing)

# ── ⑤ 咪咕体育 39 条线真机回测 ──────────────────────────────────────────
rows3, ok3, bad3 = read_rows(MIGU, 6)
if rows3 is None:
    stat("⑤ 咪咕体育 全线路真机回测", "没这份证据", "没有 %s" % MIGU.name, "第 3 棒（排在第 1 棒之后）")
else:
    migu = {(r.get("channel_id"), r.get("line_index")) for r in rows3 if r.get("seen") is not False}
    stat("⑤ 咪咕体育 全线路真机回测", "达成" if len(migu) >= 39 else "未达成",
         "%s：解析 %d 行／失败 %d 行；有读数 %d 格" % (MIGU.name, ok3, bad3, len(migu)),
         "还差 %d 格" % (39 - len(migu)) if len(migu) < 39 else "无")

# ── ⑥ 清晰度算一次就好 ──────────────────────────────────────────────────
lq = {canon(k) for k in d.get("lineQuality", {})}
cctv_urls = [u for c in d["channels"] if c.get("category") == "cctv" for u in c["streams"]]
hit = sum(1 for u in cctv_urls if canon(u) in lq)
tail = LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""
force_ok = "force=true" in (LIVE.read_text(encoding="utf-8", errors="replace") if LIVE.exists() else "")
probe_text = PROBE.read_text(encoding="utf-8", errors="replace") if PROBE.exists() else ""
cr_state, cr_note = cache_restart_state(tail)      # 链子里那一棒（23:34 崩的）仍如实念出来，只当履历
q_state, q_note = quality_once_state(probe_text)   # 判定改看这一层：打开线路时到底重算没重算
if q_state == "达成" and force_ok:
    st6, miss6 = "达成", "无"
elif q_state == "没这份证据":
    st6, miss6 = "没这份证据", q_note
elif not force_ok:
    st6, miss6 = "未达成", "面板「重新检测」那次 force=true 的真机读数没在 live_log.txt 里"
else:
    st6, miss6 = "未达成", q_note
stat("⑥ 线路清晰度算一次就好（不每次重算）", st6,
     "随包尺寸覆盖 %d/%d 条；`force=true` 真机正对照 %s；层一＋层二（第 4 棒 %s）：%s｜链子里那次：%s"
     % (hit, len(cctv_urls), "在场" if force_ok else "没读到", PROBE.name, q_note, cr_note),
     miss6)

need = [s for s in STATS if s[1] != "达成"]
print("\n[对表] 六格：达成 %d｜未达成 %d｜待人工 %d｜没这份证据 %d ｜合计 %d"
      % (len(STATS) - len(need),
         sum(1 for s in need if s[1] == "未达成"),
         sum(1 for s in need if s[1] == "待人工"),
         sum(1 for s in need if s[1] == "没这份证据"), len(STATS)))
print("[结论] %s" % ("六格全绿＝目标达成" if not need else "目标未达成，缺的是上面 %d 格" % len(need)))
sys.exit(0 if not need else 1)
