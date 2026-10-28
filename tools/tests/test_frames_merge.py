#!/usr/bin/env python3
"""#54 那把换底缺陷的尺：`--rerender` 之后，台账里「本轮目录没有的地址」必须还在。

判据是纯函数 `merge_frame_rows`，另加一格真落盘（字节层），因为纯函数绿了不等于文件没被截短。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from prepack_report import merge_frame_rows, write_frame_rows  # noqa: E402


def row(url, width=1920, frame="f.jpg"):
    return json.dumps({"channel": "X", "line_index": 1, "url": url, "width": width,
                       "height": 1080, "bytes": 100, "frame": frame,
                       "placeholder": "", "blank": "", "stalled": ""}, ensure_ascii=False)


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{(' :: ' + detail) if detail and not cond else ''}")
    return cond


def main() -> int:
    ok = True
    old = [row("http://a/keep.m3u8"), row("http://b/gone.m3u8")]

    # F1 本轮行全部落盘，且排在前面
    fresh = [row("http://a/keep.m3u8", width=1280)]
    merged = merge_frame_rows(old, fresh)
    ok &= check("F1 本轮行在结果里且排第一", merged[0] == fresh[0])
    ok &= check("F2 台账里本轮没有的地址还在（换底被抓）", row("http://b/gone.m3u8") in merged)
    # 牙齿：把本轮行为直接当全文＝旧口径，那一行必然消失
    ok &= check("F2b 旧口径（只写本轮）确实会丢掉那一行＝上面那格有牙", row("http://b/gone.m3u8") not in fresh)
    ok &= check("F3 同一 url 只留本轮那行", sum(1 for ln in merged if "a/keep" in ln) == 1)
    ok &= check("F3b 本轮那行的新读数没被旧行覆盖", json.loads(merged[0])["width"] == 1280)

    # F4 读不懂的行保留
    junk = ["{不是 json"]
    merged4 = merge_frame_rows(old + junk, fresh)
    ok &= check("F4 坏行原样留着不悄悄扔", "{不是 json" in merged4)

    # F5 本轮为空 ⇒ 台账一字不变
    merged5 = merge_frame_rows(old, [])
    ok &= check("F5 本轮零行时台账原样", merged5 == old)

    # F6 真落盘：文件里的行数与字节内容 == 合并结果（不是纯函数自证）
    tmp = ROOT / "tools" / ".cache" / "test_frames_merge.jsonl"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        tmp.unlink()
    write_frame_rows(tmp, [row("http://b/gone.m3u8", width=3840)])          # 第一轮：只有 b
    tmp.write_text("\n".join(old) + "\n", encoding="utf-8")                  # 摆一份带 a/b 的台账
    write_frame_rows(tmp, fresh)                                             # 第二轮：本轮只跑 a
    on_disk = [ln for ln in tmp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    ok &= check("F6 落盘文件仍含那条没跑过的地址", row("http://b/gone.m3u8") in on_disk,
                f"实际 {len(on_disk)} 行")
    ok &= check("F6b 落盘行数 == 合并行数", len(on_disk) == 2, f"实际 {len(on_disk)}")
    tmp.unlink(missing_ok=True)

    print("EXIT", 0 if ok else 1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
