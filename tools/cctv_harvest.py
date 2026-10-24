#!/usr/bin/env python3
"""从公开列表里给央视各台挑「还没进目录」的候选线路，产出一份能直接喂 health_probe 的小目录。

为什么要单独一个采集器：目录生成那条链只认它自己那套来源与合并规则，跑完就把没通过证据闸的
地址丢了；要「增加可用线路」得反过来看——哪些地址公开列表里有、目录里没有、也还没被拉黑。
这一层只做「挑出来」，不做「判能用」，判定还是交给生产那两把尺（health_probe 量分片、
freeze_scan 量冻帧、VLC 抽帧认台标），别在这里再写第三份判据。

台号是从**已发布目录自己的台名**反推的，不在这里手写第二份 CCTV 名单：
目录里叫什么，候选就归到什么名下，免得两边名字长岔。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stream_rules
from build_catalog import CACHE, SOURCES, parse_m3u

BASE = Path(__file__).resolve().parents[1]
SHIPPED = BASE / "app" / "src" / "main" / "assets" / "channels.json"
LIST_KEYS = ("fanmingming", "vbsky", "zbefine", "bigbig", "yang", "aptv", "iptv_org")
# 官方「央视频」免费 CDN 只出现在 `名字,URL` 这种 txt 列表里（doubwing 那份有 31 条
# tlivectfree-cdn.ysp.cctv.cn），m3u 那几个上游一条都没有——只读 m3u 就等于把最该用的来源漏光。
LIST_TXT = ("doubwing_list.txt", "xz5656_live.txt", "gitee_直播20240313.txt")

# 台号 → 目录里的正式台名，靠目录自己填；这一份只是「台号 → 名字」的解析结果，不是名单。
NUM_RE = re.compile(r"CCTV[\s\-_]*(\d{1,2})(\+)?", re.I)
K_RE = re.compile(r"CCTV[\s\-_]*([48])K", re.I)


def channel_number(name: str) -> str:
    """把台名归一成台号键：'CCTV-5+ 体育赛事'→'5+'，'CCTV-4K 超高清'→'4K'，其余 '1'..'17'。"""
    hit = K_RE.search(name)
    if hit:
        return f"{hit.group(1)}K"
    hit = NUM_RE.search(name)
    if not hit:
        return ""
    return f"{int(hit.group(1))}{'+' if hit.group(2) else ''}"


def target_names(catalog: dict) -> dict[str, str]:
    """目录里的央视台：台号 -> 正式台名。"""
    out = {}
    for channel in catalog.get("channels", []):
        name = channel.get("name") or ""
        if not name.upper().startswith("CCTV"):
            continue
        number = channel_number(name)
        if number:
            out[number] = name
    return out


def refresh_lists(force: bool) -> list[str]:
    fetched = []
    for key in LIST_KEYS:
        path = CACHE / f"{key}.m3u"
        if path.exists() and path.stat().st_size > 100 and not force:
            continue
        url = SOURCES[key]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=90).read()
        except Exception as exc:  # noqa: BLE001  单份列表拉不到不影响其余，报出来就行
            print(f"[warn] {key} 刷新失败：{type(exc).__name__} {exc}", file=sys.stderr)
            continue
        if len(data) > 100:
            path.write_bytes(data)
            fetched.append(key)
            print(f"[fetch] {key} {len(data)} B")
    return fetched


def parse_txt(path: Path) -> list[dict]:
    """`显示名,URL` 一行一条。段头（`央视频道,#genre#`）和空 URL 的行会被 URL 前缀那道筛掉。"""
    out = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or "," not in line:
            continue
        name, _, url = line.rpartition(",")
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            continue
        out.append({"name": name.strip(), "url": url, "source": path.stem})
    return out


def harvest(catalog: dict, repos: bool = False) -> tuple[dict[str, list[str]], dict]:
    known = {stream_rules.canon(u) for ch in catalog.get("channels", []) for u in ch.get("streams", [])}
    blocked = {stream_rules.canon(u) for u in catalog.get("blockedLines", [])}
    numbers = target_names(catalog)
    stats = {"seen": 0, "dup_known": 0, "blocked": 0, "never": 0, "unmatched": 0}
    candidates: dict[str, list[str]] = {}
    got: set[str] = set()
    for entry in all_entries(repos):
        name = entry.get("name") or ""
        url = entry.get("url") or ""
        if not url or "cctv" not in name.lower().replace(" ", ""):
            continue
        stats["seen"] += 1
        canon = stream_rules.canon(url)
        if not canon or canon in known:
            stats["dup_known"] += 1
            continue
        if canon in blocked:
            stats["blocked"] += 1
            continue
        if stream_rules.never_playable(url):
            stats["never"] += 1
            continue
        number = channel_number(name)
        target = numbers.get(number)
        if not target:
            stats["unmatched"] += 1
            continue
        if canon in got:
            continue
        got.add(canon)
        candidates.setdefault(target, []).append(url)
    return candidates, stats


def parse_any_repo(path: Path) -> list[dict]:
    """克隆下来的仓库列表形状很杂（#EXTINF、`名字,URL`、裸 URL 一行、yml 里嵌 URL 都有）。

    这里只认「一行里能同时拿到名字和 http 地址」的两种形状，其余一律不猜：
    猜错了会把别的台的地址记到 CCTV 名下，那种错在下游抽帧认台标时才暴露，代价最大。
    """
    out = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    pending = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            pending = line.rsplit(",", 1)[-1].strip()
            continue
        hit = re.search(r"https?://\S+", line)
        if not hit:
            continue
        url = hit.group(0).rstrip('"\'')
        head = line[:hit.start()].strip().rstrip(",").strip()
        name = head if head else (pending or "")
        if name:
            out.append({"name": name, "url": url, "source": path.parent.name})
        pending = None
    return out


def all_entries(repos: bool = False):
    """m3u 与 txt 两类列表都进来；缺哪份只报一声，不拦。

    repos=True 时再扫 tools/.cache/repos 下克隆下来的第三方列表——那批量比上面几个上游大一个
    数量级（现读 4677 行 CCTV 相关地址），但形状杂、腐坏也快，所以默认关：
    主链那条 rebuild 不读这些文件，别把它们悄悄变成默认输入。
    """
    for key in LIST_KEYS:
        path = CACHE / f"{key}.m3u"
        if not path.exists():
            print(f"[warn] 缺列表 {path}", file=sys.stderr)
            continue
        yield from parse_m3u(path, key)
    for fname in LIST_TXT:
        path = CACHE / fname
        if not path.exists():
            print(f"[warn] 缺列表 {path}", file=sys.stderr)
            continue
        yield from parse_txt(path)
    if repos:
        for path in sorted((CACHE / "repos").rglob("*")):
            if not path.is_file() or path.suffix not in (".m3u", ".m3u8", ".txt"):
                continue
            yield from parse_any_repo(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="央视候选线路采集")
    parser.add_argument("--catalog", type=Path, default=SHIPPED)
    parser.add_argument("--out", type=Path, default=CACHE / "cctv_cand" / "cand_catalog.json")
    parser.add_argument("--refresh", action="store_true", help="强制重拉公开列表（默认只用已在库的那几份）")
    parser.add_argument("--repos", action="store_true",
                    help="连 tools/.cache/repos 下克隆的第三方列表一起扫（量大得多，默认关）")
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    refresh_lists(args.refresh)
    candidates, stats = harvest(catalog, args.repos)

    mini = {"channels": [{"name": name, "streams": urls} for name, urls in sorted(candidates.items())]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(mini, ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(len(v) for v in candidates.values())
    print(f"[harvest] 列表里 CCTV 条目 {stats['seen']} 条：已在目录 {stats['dup_known']}、"
          f"已拉黑 {stats['blocked']}、扩展名就不可能播 {stats['never']}、"
          f"台号对不上目录 {stats['unmatched']}")
    print(f"[harvest] 新候选 {total} 条，覆盖 {len(candidates)} 个央视台")
    for name, urls in sorted(candidates.items(), key=lambda kv: -len(kv[1])):
        print(f"  {name}: {len(urls)} 条")
    print(f"[落盘] {args.out}")
    print(f"下一步：python3 tools/health_probe.py --catalog {args.out} --out {args.out.parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
