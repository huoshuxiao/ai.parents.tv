#!/usr/bin/env bash
# 真机回测收口链（只在电视空闲时按序跑，产证据，不改目录、不删线、不打包、不 commit）
#
# 为什么要这一份（10-26）：第 1 遍还要约 1.8 小时，后面排着三棒都要用**同一台电视**——
# 失败项各跑 2 遍定案（原计划 3 遍，10-26 20:2x 点档「乙 改成 2 遍」）、主持机抽帧判扫码页那一页（约 2 分钟）、咪咕 39 格（约 29 分钟）。
# 靠我一轮一轮盯会被会话轮次切断，所以把顺序与闸写死在这里：每一棒开工前都要过闸，过不去就停手报数。
#
# 这一份 `_r2` 是 10-26 20:2x 用户点档「乙 改成 2 遍」的产物：与 `tv_closeout_chain.sh` 逐字节相同，
# **只改棒 1 的轮次数**（`--rounds 3` → `--rounds 2`）以及提到「3 遍」的那三行字。作用域要念全：
#   · 只降电视上那 55 个失败格的复跑遍数；定案强度从「3 轮里 ≥2 轮一致」降到 2 轮，抖动格更容易定错。
#   · 棒 2 主持机抽帧的 `--rounds 3` **一个字没动**（那不在电视上，也不是这条点档的对象）。
#   · 台账按 `(轮, 台 id, 线序, url)` 断点续测 ⇒ 切过去不浪费已测的格；旧那份 `lines.jsonl` 原样留着。
#
# 硬边界（这份脚本里一条都不会做）：不写 `app/src/main/assets/channels.json`、不写 `blockedLines`、
# 不打 APK、不装机、不 commit、不 push、不 kill 别人的进程。删线与拉黑要等主持机帧定案后由人点档。
#
# ⚠️两处例外是用户点档授权过的动作，写清楚免得日后被当成违约：
#   · 棒 4（#129 甲）会 force-stop **本应用**再冷启动——只为取证，判据与 toolv 一字不动；
#   · 棒 5（#126 甲）会改设备侧 `shared_prefs/parents_tv.xml` 里 `line_<频道 id>` 那批键，
#     改前把整份文件备份到 `tools/.cache/prefs_before_restore_*.xml` 并记 sha，写完逐枚回读对表、
#     并且数一遍"没该动却动了的键"。对不上就报红退出，不装作成功。
#
# 跑法：
#   CHAIN_DRY=1 bash tools/tv_closeout_chain.sh     # 空跑：只念它会不会如实拒绝，不起任何一棒
#   bash tools/tv_closeout_chain.sh                 # 正式起（后台 nohup，日志 tools/.cache/tv_closeout.log）
#   touch tools/.cache/tv_closeout.CANCEL           # 下一棒开工前会自己停（在跑的那一棒不被打断）
#
# ⚠️今天再拿这份起（10-27 尺升到 TOOLV=8 之后）会有的两件事，先写在这里免得日后被当成读数可信：
#   ① 棒 1／棒 3 的 `--out` 指向的是 10-26 那批已经在盘的目录，新写的行会**追加**在同一份 `lines.jsonl` 尾部，
#      一份文件里混 6 与 8 两种版本（`rows_from_ledger` 按版本过滤，所以每一版各自还数得清）；
#   ② 棒 2／棒 5／读数板那几腿仍写死 `--toolv 6`——它们读的就是 10-26 那批的行，
#      对本把新写的行会打 `[跳过] 这份台账里没有 toolv=6 的行`（`prefs_restore_lines.py:89`）或把尺版本印在自己那行表头里，
#      不会把「新行没数到」伪装成「新行没问题」。要把这一把的读数当结论，就得把那几腿也改成 `--toolv "$TOOLV_NOW"`。
set -u
REPO=/home/sunwenkun/Developer/agent-workspace/ai.parents.tv.git
cd "$REPO" || exit 2
CACHE=tools/.cache
PASS1=$CACHE/tv_retest_cctv_v6/lines.jsonl
PASS1_PID=1581031
FEEDER_PID=1575388
LIVE=$CACHE/live_log.txt
LOG=$CACHE/tv_closeout.log
ADB=/usr/bin/adb
SERIAL=192.168.1.4:5555
PKG=com.parentstv.live
# 尺版本现读，不写死（10-27）：`tv_retest_lines.py` 的 TOOLV 已从 6 升到 8（多读一格 LiveWatch）。
# `--toolv` 是**读侧过滤器**，写进台账的版本永远是当前 TOOLV；数错版本会把刚写的台账数成「0 行→没跑全」
# （13:5x 实测：同一份 v6 台账按默认 8 数＝0 行 0 格，按 6 数＝223 行 208 格）。
# 所以「audit 这一把新写的台账」用 $TOOLV_NOW，「audit 10-26 那批写好的 v6 台账」继续写死 6。
TOOLV_NOW=$(python3 -c "import sys; sys.path.insert(0, 'tools'); import tv_retest_lines as m; print(m.TOOLV)")
PASS1_INCOMPLETE=""   # 第 1 遍还剩几格没测就被掐了；非空＝这一批读数的作用域要带上它

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }
cancelled() { [ -e "$CACHE/tv_closeout.CANCEL" ]; }

# 每一棒开工前都要再看一眼读数流：链子要跑四个小时，那条 logcat 中途死了的话，
# `--live` 那份文件就不再长，每一格都会读成「按键没落地」——宁可当场停手，不许交出假故障的台账。
#
# 为什么先问 pid、不把「文件多久没新行」当唯一的闸：电视停在首页时应用本来就不打日志，
# 那一档 mtime 也会超过 120 秒——拿它单独定音会把好端端的一棒拒掉。所以
# pid 活着＝放行（把 age 如实念出来当证据）；pid 不在了才用 age 分辨
# 「真的断了」还是「有别人接着写同一份文件」。
gate_feeder() {
  local tag="$1" age now
  if feeder_ok; then
    if [ ! -e "$LIVE" ]; then
      say "[拒绝] 起「$tag」之前 $LIVE 不在了——文件没了尺子读不到任何一行，每一格都会记成「按键没落地」。"
      exit 5
    fi
    now=$(date +%s)
    age=$(( now - $(stat -c %Y "$LIVE") ))
    say "闸过（$tag）：读数流 pid $FEEDER_PID 活着；$LIVE 最近 ${age} 秒前有新行"
    return 0
  fi
  if [ ! -e "$LIVE" ]; then
    say "[拒绝] 起「$tag」之前读数流（pid $FEEDER_PID）已经不在了，$LIVE 也不在了。"
    exit 5
  fi
  now=$(date +%s)
  age=$(( now - $(stat -c %Y "$LIVE") ))
  if [ "$age" -gt 120 ]; then
    say "[拒绝] 起「$tag」之前读数流（pid $FEEDER_PID）已不在，且 $LIVE 已经 ${age} 秒没新行（>120 秒）＝日志流断了。"
    say "[拒绝] 不停手就会把这一整遍记成「按键没落地」——那是我的尺子的病，不是电视的病。不顺手再起一条 logcat（那会动别人窗口的读数），请人工确认后再起。"
    exit 5
  fi
  say "[注意] pid $FEEDER_PID 已经不在了，但 $LIVE 最近 ${age} 秒仍在长＝有别人接着写同一份文件，这一棒照跑。"
}

# 闸 1：读数流（--live 那份文件）还得活着，否则按键落地与否读不到、整遍会记成"没首帧"
feeder_ok() { ps -p "$FEEDER_PID" -o pid= >/dev/null 2>&1; }

# 闸 2：第 1 遍必须真跑完（进程没了 + 这把尺自己说跑全了）
#
# 光看"进程没了"不够：进程被掐死与跑完，在 `ps` 那一层是同一个读数。少了这一格，
# 后面几棒会只在"已经测过的那些失败格"上工作，而目标里那句「全线路」就会被当成已达成。
# 所以进程停了之后必须再问一次 `--audit` 缺几格，缺得多就在日志里写死一行「没跑全」，
# 并让链子末尾那一行也带着它——收尾引用读数时想漏也漏不掉。
wait_pass1() {
  while ps -p "$PASS1_PID" -o pid= >/dev/null 2>&1; do
    if cancelled; then say "已取消：第 1 遍还在跑，链子退出（没动任何东西）"; exit 3; fi
    sleep 60
  done
  say "第 1 遍进程已停，取最后读数"
  local out rc miss=""
  out=$(python3 -u tools/tv_retest_lines.py --audit "$PASS1" --group cctv --toolv 6 2>&1); rc=$?
  printf '%s\n' "$out" >>"$LOG"
  say "audit 退出码=$rc"
  if [[ "$out" =~ 没跑全[[:space:]]+([0-9]+)[[:space:]]+格 ]]; then miss="${BASH_REMATCH[1]}"; fi
  if [[ "$out" =~ 计划[[:space:]]+([0-9]+)[[:space:]]+格 ]]; then say "计划面：覆盖对表读到的那一行是「计划 ${BASH_REMATCH[1]} 格」"; fi
  if [ -n "$miss" ] && [ "$miss" -gt 0 ]; then
    PASS1_INCOMPLETE="$miss"
    say "[没跑全] 第 1 遍还剩 $miss 格没测，进程就没了＝这不是跑完。下面几棒只在已经测过的那些失败格上工作 ⇒ 目标里那句「全线路」还不成立，引用这一批读数必须连本行一起念。"
  else
    say "[跑全了] audit 没报缺格（缺 0 或这一遍把计划的格都覆盖了）"
  fi
}

run_retest() {  # 失败项各跑 2 遍定案（10-26 点档「乙 改成 2 遍」；原 3 遍）
  gate_feeder "棒 1"
  say "棒 1：失败项复跑 2 遍（按格子筛）"
  python3 -u tools/tv_retest_lines.py --adb "$ADB" --serial "$SERIAL" --pkg "$PKG" \
      --group cctv --only-shipped --retest-failures "$PASS1" --rounds 2 --after 20 \
      --live "$LIVE" --out tools/.cache/tv_retest_cctv_v6_r3 >>"$LOG" 2>&1
  say "棒 1 退出码=$?"
  python3 -u tools/tv_retest_lines.py --audit tools/.cache/tv_retest_cctv_v6_r3/lines.jsonl \
      --group cctv --toolv "$TOOLV_NOW" >>"$LOG" 2>&1
  say "棒 1 的 audit 退出码=$?（这份新台账是复跑那 2 遍自己的覆盖度，与第 1 遍那份分开设）"
}

run_frames() {  # 主持机抽帧：电视空闲之后才允许，否则同一张网会污染起播/卡顿读数
  say "棒 2：主持机抽帧判「服务器超负载…微信扫码」那一页（只针对真机没成的格子）"
  python3 -u tools/retest_flat.py --only-tv-failures "$PASS1" --tv-toolv 6 \
      --rounds 3 --workers 4 --out tools/.cache/tv_fail_frames >>"$LOG" 2>&1
  say "棒 2 退出码=$?（帧与台账落 tools/.cache/tv_fail_frames）"
  # 抽完帧必须紧接着出判定：`retest_flat.py` 只出帧，「服务器超负载…微信扫码」那一页是不是命中
  # 由 `retest_flat_review.py` 里那把 dHash 尺定音（它自己算 `PR.fingerprint_hit(帧)`）。
  # 不接这一棒，"此线路删除"那句就只有一堆图、没有名单。全部只读本机文件：不联网、不碰电视、
  # 不写 assets／blockedLines（它只往 `tools/.cache/tv_fail_frames/` 里落四份产物）。
  python3 -u tools/retest_flat_review.py --out tools/.cache/tv_fail_frames >>"$LOG" 2>&1
  say "棒 2 的判定退出码=$?（名单在 tools/.cache/tv_fail_frames/：deletion_candidates.json／final_deletions*.json／human_queue.json／review.json）"
  python3 - <<'PY2'
import json, pathlib
# 形制是现读的（10-26 19:22 拿两份旧产物对过）：`final_deletions.json`／`deletion_candidates.json`
# 顶层是 {generatedAt, kind, cut:[{url, kind, channel, line_index, evidence, scores}]}，
# `human_queue.json` 是 {generatedAt, note, count, lines:[…]}。列表键认不出来就报「读不到」，
# **不许折成 0 枚**——把"读不到"念成"事实为零"是最贵的一种谎。
d = pathlib.Path("tools/.cache/tv_fail_frames")
KNOWN = {"final_deletions.json": "cut", "deletion_candidates.json": "cut", "human_queue.json": "lines"}
for name, key in KNOWN.items():
    f = d / name
    if not f.exists():
        print(f"  [没这份] {name}")
        continue
    try:
        v = json.loads(f.read_text())
    except Exception as e:
        print(f"  [读失败] {name}：{e}")
        continue
    items = v.get(key)
    if not isinstance(items, list):
        print(f"  [读不到] {name} 里没有列表键 {key!r}；顶层键＝{list(v)}")
        continue
    extra = ""
    if isinstance(v.get("count"), int):
        extra = f"｜它自己写的 count={v['count']} {'==' if v['count'] == len(items) else '!='} 实际 {len(items)}"
    qr = [x for x in items if isinstance(x, dict) and str(x.get("kind")) in ("overload_qr", "overload")]
    print(f"  {name}：{key} {len(items)} 枚｜其中 kind=overload_qr {len(qr)} 枚{extra}")
    for x in qr[:12]:
        print(f"    扫码页命中：{x.get('channel')} 线{x.get('line_index')} url {str(x.get('url'))[:64]}")
PY2'
import json, pathlib
d = pathlib.Path("tools/.cache/tv_fail_frames")
for name in ("final_deletions.json", "deletion_candidates.json", "human_queue.json"):
    f = d / name
    if not f.exists():
        print(f"  [没这份] {name}")
        continue
    try:
        v = json.loads(f.read_text())
    except Exception as e:
        print(f"  [读失败] {name}：{e}")
        continue
    items = v.get("items") or v.get("candidates") or v.get("queue") or []
    qr = [x for x in items if "超负载" in json.dumps(x, ensure_ascii=False)]
    print(f"  {name}：条目 {len(items)} 枚｜其中扫码页命中 {len(qr)} 枚")
PY2
}

run_migu() {  # 咪咕 39 格
  gate_feeder "棒 3"
  say "棒 3：咪咕体育 39 条线真机回测（v6、新目录）"
  python3 -u tools/tv_retest_lines.py --adb "$ADB" --serial "$SERIAL" --pkg "$PKG" \
      --group migu --rounds 1 --after 20 --live "$LIVE" \
      --out tools/.cache/tv_retest_migu_v6 >>"$LOG" 2>&1
  say "棒 3 退出码=$?"
  python3 -u tools/tv_retest_lines.py --audit tools/.cache/tv_retest_migu_v6/lines.jsonl \
      --group migu --toolv "$TOOLV_NOW" >>"$LOG" 2>&1
  say "棒 3 的 audit 退出码=$?"
}

run_cache_probe() {  # 「算一次就好」还差的最后一格：杀掉重开之后那份缓存还在不在
  gate_feeder "棒 4"
  say "棒 4：跨重启那一格（force-stop 本应用＋冷启动＋开一次选线面板，只发按键与读日志，不写任何东西）"
  python3 -u tools/tv_cache_restart_probe.py --adb "$ADB" --serial "$SERIAL" --pkg "$PKG" \
      --live "$LIVE" >>"$LOG" 2>&1
  say "棒 4 退出码=$?（0＝跨重启保住；4＝没保住＝目标那句还不成立，要改的是缓存存哪；3 或 2＝未证，别当结论念）"
}

run_prefs_restore() {  # #126 点档＝甲：把每台起点恢复成这一遍真播过的那条（会 force-stop 一次电视）
  say "棒 5：恢复设备侧「记住的线路」（先空跑出自觉，再落盘；备份与回读对表都在那一支脚本里）"
  python3 -u tools/prefs_restore_lines.py --ledger "$PASS1" \
      --ledger tools/.cache/tv_retest_cctv_v6_r3/lines.jsonl \
      --ledger tools/.cache/tv_retest_migu_v6/lines.jsonl --toolv 6 \
      > "$CACHE/prefs_restore_plan_final.out" 2>&1
  say "棒 5 的空跑退出码=$?（计划全文在 $CACHE/prefs_restore_plan_final.out，四档分项之和要==台数）"
  grep -q "^\[计划\]" "$CACHE/prefs_restore_plan_final.out" || { say "[拒绝] 空跑没出计划行，不落盘"; return 0; }
  python3 -u tools/prefs_restore_lines.py --ledger "$PASS1" \
      --ledger tools/.cache/tv_retest_cctv_v6_r3/lines.jsonl \
      --ledger tools/.cache/tv_retest_migu_v6/lines.jsonl --toolv 6 --write \
      > "$CACHE/prefs_restore_write_final.out" 2>&1
  say "棒 5 落盘退出码=$?（0＝回读逐字对上了；1＝有没对上或动了不该动的键；3/4＝拒绝或回读失败）"
  say "⚠️ 这一棒会 `am force-stop` 本应用一次：电视上正在看的人会被退出。不想让它在没人时改，就在它开工前 touch $CACHE/tv_closeout.CANCEL。"
}

run_boards() {  # 三张读数 + prefs 污染终值（全只读）
  say "棒 4：读数板与对表"
  for f in "$PASS1" tools/.cache/tv_retest_cctv_v6_r3/lines.jsonl tools/.cache/tv_retest_migu_v6/lines.jsonl; do
    [ -e "$f" ] || { say "跳过（还没这份台账）：$f"; continue; }
    printf '\n### %s\n' "$f" >>"$LOG"
    python3 -u tools/retest_stall_board.py --ledger "$f" --toolv 6 >>"$LOG" 2>&1 || say "读数板在这一份上报错（不中断）"
  done
  "$ADB" -s "$SERIAL" exec-out "run-as $PKG cat shared_prefs/parents_tv.xml" \
      > "$CACHE/prefs_after_all_passes.xml" 2>/dev/null
  say "prefs 终值快照取出：$(grep -c 'name="line_' "$CACHE/prefs_after_all_passes.xml") 枚 line_ 键"
  python3 -u tools/.cache/prefs_pollution_diff.py >>"$LOG" 2>&1 || say "对表脚本按现值跑（不中断）"
  if [ -n "$PASS1_INCOMPLETE" ]; then
    say "链子跑完：证据都在 tools/.cache/，没有改目录、没有删线、没有打包——⚠️但第 1 遍还剩 $PASS1_INCOMPLETE 格没测就停了，这一批读数**答不了**目标里那句「全线路」，别当验收用"
  else
    say "链子跑完：证据都在 tools/.cache/，没有改目录、没有删线、没有打包"
  fi
}

if [ "${CHAIN_DRY:-0}" = "1" ]; then
  LOG=/dev/null      # 空跑不去碰正式日志：那一份是给人读"哪一棒真起过"的，掺进试跑行就分不清了
  echo "[dry-run] 这一档不起任何一棒，只证明这些闸会如实判断："
  echo "  第 1 遍进程 $PASS1_PID 现在活着吗：$(ps -p $PASS1_PID -o pid= >/dev/null 2>&1 && echo 活着 || echo 已停)"
  gate_feeder "dry-run"          # 真的走一遍闸：健康＝闸过并退 0，读数流断了＝这里就 [拒绝] 退 5
  echo "  取消文件在吗：$([ -e "$CACHE/tv_closeout.CANCEL" ] && echo 在 || echo 不在)"
  echo "  它会依序跑：等第 1 遍 → audit 闸（还会如实报「没跑全」） → 失败项×2（10-26 点档「乙 改成 2 遍」） → 抽帧＋扫码页判定 → 咪咕 39 → 跨重启那一格 → 恢复 prefs 起点 → 读数板与 prefs 终值"
  echo "  它不会做：写 assets/blockedLines、打包、装机、commit、kill 别人的进程"
  echo "  它会做（都已被点档）：棒 4 force-stop 本应用一次做跨重启取证；棒 5 改设备 prefs 的 line_ 起点（有备份＋回读对表）"
  exit 0
fi

say "收口链起跑（pid=$$）；取消＝touch $CACHE/tv_closeout.CANCEL"
gate_feeder "起跑"
wait_pass1
if cancelled; then say "已取消：第 1 遍之后没起任何一棒"; exit 3; fi
run_retest
if cancelled; then say "已取消：抽帧与咪咕两棒没起"; exit 3; fi
run_frames
if cancelled; then say "已取消：咪咕那一棒没起"; exit 3; fi
run_migu
if cancelled; then say "已取消：跨重启那一格与读数板没跑"; exit 3; fi
run_cache_probe
run_prefs_restore
run_boards
exit 0
