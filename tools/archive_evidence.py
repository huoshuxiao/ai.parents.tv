#!/usr/bin/env python3
"""把「认台标」的两份证据归档进 dist/，交付时与包同批带走（用户 10-24 点档 #95＝乙）。

为什么是这个形状：台账 `tools/.cache/linecheck/identity_review.jsonl` 与门禁每轮 `review.json` 都在
`.gitignore` 的 `tools/.cache/` 下，不进版本库；换机或 clone 之后「这条线为什么被保留」就没人答得出。
乙 的取舍＝不动判据读路径（构建照旧从 `tools/.cache/` 读），只多一步人工归档 ⇒ 归档必须能自证，
所以默认只算数不落盘，`--write` 才写，`--verify` 逐文件对 sha，`--selfcheck` 用「删掉台账一行」的负对照
证明这把尺看得见差异（什么异常都算抓到的尺＝没牙）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

R = Path(__file__).resolve().parent.parent
LEDGER = R / "tools/.cache/linecheck/identity_review.jsonl"
REVIEW_GLOB = "tools/.cache/gate/round_*/review.json"
CATALOG = R / "app/src/main/assets/channels.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ledger_counts(path: Path) -> dict:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    keys = {}
    for r in rows:
        keys[f"{r.get('name', '')}|{r.get('url', '')}"] = r.get("verdict", "")
    return {"行数": len(rows), "键数": len(keys),
            "台标相符": sum(1 for v in keys.values() if v == "台标相符")}


def review_files() -> list[Path]:
    return sorted(R.glob(REVIEW_GLOB))


def review_counts(paths: list[Path]) -> dict:
    logo = 0
    for p in paths:
        for r in json.loads(p.read_text(encoding="utf-8")).get("reviews", []):
            if (r.get("logo") or "").startswith("台标相符"):
                logo += 1
    return {"份数": len(paths), "台标相符枚数(按轮次计,含重复)": logo}


def build(out_dir: Path, title: str, write: bool) -> Path:
    lp, rp = ledger_counts(LEDGER), review_counts(review_files())
    members = {"identity_review.jsonl": LEDGER}
    for i, p in enumerate(review_files()):
        members[f"review/{p.parent.name}.json"] = p
    manifest = {
        "title": title,
        "catalog_sha256": sha(CATALOG)[:16] if CATALOG.exists() else "",
        "台账": lp, "门禁看图": rp,
        "文件": {name: sha(path) for name, path in members.items()},
    }
    print(f"[收集] 台账 {lp['行数']} 行/{lp['键数']} 键/台标相符 {lp['台标相符']} 枚；"
          f"看图 {rp['份数']} 份、台标相符 {rp['台标相符枚数(按轮次计,含重复)']} 枚（含跨轮重复）")
    print(f"[收集] 对表的在库目录 sha256 前 16：{manifest['catalog_sha256'] or '（在库目录不存在）'}")
    print(f"[收集] 归档成员 {len(members)} 个文件 =="
          f" 台账 1 + 看图 {rp['份数']}:", "分项之和==总数:", 1 + rp["份数"] == len(members))
    # 归档一旦发出去就是履历，同 title 重跑不许把旧的覆掉（两份证据读数会混成一份）
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"evidence-{title}"
    stamp = datetime.now().strftime("%H%M")
    tar_path = Path(f"{stem}-{stamp}.tar.gz")
    n = 0
    while tar_path.exists():
        n += 1
        tar_path = Path(f"{stem}-{stamp}-{n}.tar.gz")
    if not write:
        print(f"[dry-run] 将写 {tar_path}（加 --write 才落盘）")
        return tar_path
    tmp = Path(tempfile.mkdtemp())
    for name, src in members.items():
        dst = tmp / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    with tarfile.open(tar_path, "w:gz") as tf:
        for p in sorted(tmp.rglob("*")):
            if p.is_file():
                tf.add(p, arcname=p.relative_to(tmp))
    shutil.rmtree(tmp)
    print(f"[写盘] {tar_path} {tar_path.stat().st_size:,} B sha256 前 16 {sha(tar_path)[:16]}")
    return tar_path


def verify(tar_path: Path) -> int:
    """逐文件对 manifest 的 sha；少文件/多文件/内容变了都算红。"""
    tmp = Path(tempfile.mkdtemp())
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(tmp)
    manifest = json.loads((tmp / "manifest.json").read_text(encoding="utf-8"))
    bad = []
    for name, want in manifest["文件"].items():
        p = tmp / name
        if not p.is_file():
            bad.append(f"缺 {name}")
        elif sha(p) != want:
            bad.append(f"sha 不符 {name}")
    extra = [str(p.relative_to(tmp)) for p in tmp.rglob("*")
             if p.is_file() and p.name != "manifest.json"
             and str(p.relative_to(tmp)) not in manifest["文件"]]
    for x in extra:
        bad.append(f"多出 {x}")
    lp = ledger_counts(tmp / "identity_review.jsonl")
    if lp != manifest["台账"]:
        bad.append(f"台账读数变了 {manifest['台账']} -> {lp}")
    shutil.rmtree(tmp)
    if bad:
        for b in bad:
            print("   [红]", b)
        print(f"[verify] 不通过：{len(bad)} 处 —— {tar_path.name}")
        return 1
    print(f"[verify] 通过：{len(manifest['文件'])} 个文件逐 sha 相符、台账读数 {lp} 与清单一致 —— {tar_path.name}")
    return 0


def selfcheck(tar_path: Path) -> int:
    """负对照：把归档里的台账删掉一行再 verify，必须判红——否则这把尺根本没在看内容。"""
    work = Path(tempfile.mkdtemp())
    (work / "src").mkdir()
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(work / "src")
    led = work / "src/identity_review.jsonl"
    lines = led.read_text(encoding="utf-8").splitlines()
    (work / "src/identity_review.jsonl").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    tampered = work / "tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as tf:
        for p in sorted((work / "src").rglob("*")):
            if p.is_file():
                tf.add(p, arcname=p.relative_to(work / "src"))
    rc_tamper = verify(tampered)
    shutil.rmtree(work)
    rc_real = verify(tar_path)
    print(f"[selfcheck] 负对照（台账少一行）verify 退出码 = {rc_tamper}，判红才算抓到:", rc_tamper != 0)
    print(f"[selfcheck] 真归档 verify 退出码 = {rc_real}，判绿才算交付:", rc_real == 0)
    return 0 if (rc_tamper != 0 and rc_real == 0) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=R / "dist")
    ap.add_argument("--title", default="v1.2.0-1024")
    ap.add_argument("--write", action="store_true", help="真落盘；不给就只算数")
    ap.add_argument("--verify", type=Path, default=None)
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.verify:
        return verify(args.verify)
    if args.selfcheck:
        # 自检不许依赖调用方先跑过 --write（17:4x 那一跑就是这么崩的：dry-run 只算了路径、
        # 文件根本没生成，随后 tarfile.open 直接 FileNotFound）。自己造一份、两臂跑完就删。
        tmp = Path(tempfile.mkdtemp())
        try:
            return selfcheck(build(tmp, "selfcheck", True))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    tar_path = build(args.out, args.title, args.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
