#!/usr/bin/env python3
"""真机 prefs 与「今天落盘的那份目录」逐条对表：这台电视记住的线路，落点还在不在。

只读三份现成文件（设备 prefs 的转存 + 落盘前后两份目录），不写盘、不碰设备。
问的是：升级后父母打开某一台，App 会回到"它自己记得的那条线"——那条线要是不在了或被拉黑了，
这一台的第一屏就变了，属于「装机前该知道」的代价，不能拿模拟器的读数代替真机。
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "tools"))
import stream_rules as sr  # noqa: E402

PREFS = BASE / "tools/.cache/real_tv_prefs.xml"
OLD = BASE / "tools/.cache/channels_before_land_1634.json"
NEW = BASE / "app/src/main/assets/channels.json"

if not PREFS.exists():
    print("[缺件] 真机 prefs 转存不在 ⇒ 这一格红着念，不许折成「没有问题」")
    raise SystemExit(2)

prefs = PREFS.read_text(encoding="utf-8")
kv = dict(re.findall(r'name="(line_[^"]+)"[^>]*>([^<]+)<', prefs))
m_last = re.search(r'name="last_channel"[^>]*>([^<]+)<', prefs)
last_channel = m_last.group(1) if m_last else "(没有这个键)"
m_switch = re.search(r'name="line_switch_seconds" value="(\d+)"', prefs)

old = json.loads(OLD.read_text(encoding="utf-8"))
new = json.loads(NEW.read_text(encoding="utf-8"))
o_by = {c["id"]: c for c in old["channels"]}
n_by_name = {}
for c in new["channels"]:
    n_by_name.setdefault(c["name"], c)
blocked = {sr.canon(k) for k in new["blockedLines"]}

print(f"[设备] line_* 键 {len(kv)} 枚 ｜favorites 键在不在：{'favorites' in prefs}"
      f" ｜换线等待现读 {m_switch.group(1) if m_switch else '未读到'} 秒"
      f" ｜last_channel={last_channel}")
print(f"[目录] 落盘前 {len(old['channels'])} 台 → 落盘后 {len(new['channels'])} 台")

moved, vanished_id, kept = [], [], 0
for key, url in kv.items():
    cid = key[len("line_"):]
    ch = o_by.get(cid)
    if ch is None:
        vanished_id.append((cid, url[:52]))
        continue
    now = n_by_name.get(ch["name"])
    if now is None:
        moved.append((ch["name"], url[:52], "整台已不在新目录"))
    elif sr.canon(url) not in {sr.canon(u) for u in now["streams"]}:
        tag = "已被拉黑" if sr.canon(url) in blocked else "本台换/摘了这条"
        moved.append((ch["name"], url[:52], tag))
    else:
        kept += 1
        old_pos = [i + 1 for i, u in enumerate(ch["streams"]) if sr.canon(u) == sr.canon(url)]
        new_pos = [i + 1 for i, u in enumerate(now["streams"]) if sr.canon(u) == sr.canon(url)]
        if old_pos and new_pos and old_pos[0] == 1 and new_pos[0] != 1:
            moved.append((ch["name"], url[:52], f"线还在但不再是首条（{old_pos[0]}→{new_pos[0]}）"))
        elif old_pos and new_pos and old_pos[0] != new_pos[0]:
            moved.append((ch["name"], url[:52], f"线内位次变了（{old_pos[0]}→{new_pos[0]}）"))

print(f"[对表] 记住的线路【地址还在】= {kept} 枚 ｜地址没了或换组整台消失 = {len(moved)} 枚 ｜指向不存在的 id = {len(vanished_id)} 枚")
for row in moved:
    print("   ", row)
sd_old = [c["id"] for c in old["channels"] if c["name"] == "山东教育卫视"]
if sd_old:
    hits = sum(1 for k in kv if k.endswith(sd_old[0]))
    print(f"[换组那台] 旧 id {sd_old[0]} 在这台电视 prefs 里出现 {hits} 次；"
          f"新 id local-eb5ff905e9 出现 {sum(1 for k in kv if 'eb5ff905e9' in k)} 次"
          f"（命中 0 次＝这台在电视上没记住过线路，换 id 不丢东西）")
raise SystemExit(0)
