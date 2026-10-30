"""catalog.dat 的 Python 侧编解码——与 Kotlin `CatalogCodec` / Gradle `packCatalog` 同一套口径。

用户 10-29：「本地开发 channels.json 还是现状，打包运行时转码，读 url 时解码」。
源码 `app/src/main/assets/channels.json` 保持明文；APK 里只有 `assets/catalog.dat`
（gzip → 逐字节异或 → 前置 magic "PTV1"）。构建闸要拿包内目录跟仓库目录比，
所以这里必须能把包内那份解回明文再比，而不是比密文。

用法：
  python3 tools/catalog_codec.py decode <in.dat> <out.json>
  python3 tools/catalog_codec.py sha256 <apk>      # 打印包内目录「解码后」的 sha256
  python3 tools/catalog_codec.py compare <dat|apk> <明文.json>   # 解回明文逐字节比，不等再给差量
"""
from __future__ import annotations

import gzip
import json
import hashlib
import sys
import zipfile
from pathlib import Path

MAGIC = b"PTV1"
ASSET_NAME = "assets/catalog.dat"
LEGACY_ASSET_NAME = "assets/channels.json"
KEY = bytes([
    0x37, 0x9A, 0xC1, 0x4E, 0x88, 0x2D, 0x6B, 0xF0,
    0x15, 0xA7, 0x3C, 0xE2, 0x59, 0x06, 0xBD, 0x71,
])


def looks_encoded(data: bytes) -> bool:
    return len(data) > len(MAGIC) and data[: len(MAGIC)] == MAGIC


def decode_bytes(data: bytes) -> bytes:
    """有 magic 就解，没有就当明文原样返回（兼容旧包与开发期）。"""
    if not looks_encoded(data):
        return data
    body = bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(data[len(MAGIC):]))
    return gzip.decompress(body)


def encode_bytes(raw: bytes) -> bytes:
    """给自检/夹具用的参考实现。

    ⚠️ 与 Gradle `packCatalog` **不保证逐字节相同**：gzip 的压缩实现不同（deflevel/头部字段），
    所以产物 sha 只用于「解码后内容相同」的比对，不要拿两边密文比 sha。
    """
    gz = gzip.compress(raw, mtime=0)
    return MAGIC + bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(gz))


def apk_catalog_bytes(apk: Path) -> bytes:
    """从 APK 里取目录并解码成明文；新包读 catalog.dat，旧包退回 channels.json。"""
    with zipfile.ZipFile(apk) as zf:
        names = set(zf.namelist())
        if ASSET_NAME in names:
            return decode_bytes(zf.read(ASSET_NAME))
        if LEGACY_ASSET_NAME in names:
            return zf.read(LEGACY_ASSET_NAME)
        raise SystemExit(f"[缺件] {apk} 里既没有 {ASSET_NAME} 也没有 {LEGACY_ASSET_NAME}")


def apk_catalog_sha(apk: Path) -> str:
    return hashlib.sha256(apk_catalog_bytes(apk)).hexdigest()


def compare(src: Path, ref: Path) -> int:
    """把 .dat（或 APK 里的那份）解回明文，跟参考 JSON 逐字节比；不等时再给一份人话差量。

    用户 10-29 问「有没有解码 dist/catalog.dat 和 channels.json 比较的工具」——这就是那一条。
    """
    if src.suffix == ".apk":
        got, where = apk_catalog_bytes(src), f"{src} 内的 {ASSET_NAME}"
    else:
        got, where = decode_bytes(src.read_bytes()), str(src)
    want = ref.read_bytes()
    sg, sw = hashlib.sha256(got).hexdigest(), hashlib.sha256(want).hexdigest()
    print(f"[比较] {where}  {len(got)} B  sha256 {sg[:16]}…")
    print(f"        {ref}  {len(want)} B  sha256 {sw[:16]}…")
    if got == want:
        print("[结果] 逐字节相同")
        return 0
    print("[结果] 不相同")
    diff = next((i for i, (x, y) in enumerate(zip(got, want)) if x != y), min(len(got), len(want)))
    print(f"        首个不同字节偏移 {diff}")
    try:
        a, b = json.loads(got.decode("utf-8")), json.loads(want.decode("utf-8"))
        ca = {c["id"]: c for c in a["channels"]}
        cb = {c["id"]: c for c in b["channels"]}
        print(f"        台数 {len(ca)} vs {len(cb)}｜线数 "
              f"{sum(len(c['streams']) for c in ca.values())} vs {sum(len(c['streams']) for c in cb.values())}")
        only_a = [ca[i]["name"] for i in ca.keys() - cb.keys()]
        only_b = [cb[i]["name"] for i in cb.keys() - ca.keys()]
        if only_a:
            print(f"        只在前者：{only_a[:10]}{' …' if len(only_a) > 10 else ''}")
        if only_b:
            print(f"        只在后者：{only_b[:10]}{' …' if len(only_b) > 10 else ''}")
        changed = [ca[i]["name"] for i in ca.keys() & cb.keys() if ca[i] != cb[i]]
        if changed:
            print(f"        内容不同：{len(changed)} 台 {changed[:10]}{' …' if len(changed) > 10 else ''}")
        for k in ("generatedAt", "blockedLines", "lineQuality", "lineMoves"):
            if a.get(k) != b.get(k):
                print(f"        {k} 不同")
    except Exception as exc:  # noqa: BLE001 - 解不出 JSON 就只报字节层
        print(f"        （JSON 层没比成：{type(exc).__name__}: {exc}）")
    return 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "decode" and len(argv) == 4:
        Path(argv[3]).write_bytes(decode_bytes(Path(argv[2]).read_bytes()))
        print(f"[decode] {argv[2]} → {argv[3]}")
        return 0
    if cmd == "compare" and len(argv) == 4:
        return compare(Path(argv[2]), Path(argv[3]))
    if cmd == "sha256" and len(argv) == 3:
        print(apk_catalog_sha(Path(argv[2])))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
