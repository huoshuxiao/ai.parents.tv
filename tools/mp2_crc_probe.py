#!/usr/bin/env python3
"""从真 TS 里把音轨 ES 拼出来，读它**自己声明**的 protection_bit（也就是 CRC 那一位）。

为什么要有这把尺：应用侧 MP2 软解补回去的 4 字节帧头一律写「无 CRC」
（`Mp2PcmPump.synthesizeHeader` 里 CRC 位固定补 1）。源若真带 CRC，帧总长里就多出 2 字节校验，
泵的同步字找帧界会一路错位——症状从「没声音」变成「噪声/断续」。所以这一位不能猜。

判据（每条各挡一种说谎）：
  ① TS 188 字节周期性自检：`0x47` 起点若周期不对，整份 ES 就是垃圾 ⇒ 直接 `[拒绝]`，不输出任何结论。
  ② PAT→PMT 按规范取（PUS 是 byte1 的 0x40，**不是** 0x80；0x80 是 transport_error_indicator；
     PAT 节目项从 tab[8] 起，PMT 的 program_info_length 在 tab[10..11]）：
     找不到 PMT 就报「未定位」，不许退化成"整段裸扫"当作证据。PMT 声明的 stream_type 与 Media3
     报的 `audio/mpeg-L2` 是两条独立来源，要能相互对表。
  ③ **整除**（主判据）：每条音频 PES 的净载荷长度必须是整数个 Layer II 帧（整除率 ≥0.9）。
     源若在帧头后塞 2 字节 CRC，载荷就会留下余数 ⇒ 整除塌掉。
     这一档是替掉旧判据的：旧的拿"连续同步帧数 ≥6"当唯一判据，而这条流每段载荷只有 3 帧（2298/768），
     把「容器粒度」读成了「没有 MP2」——阈值说谎，不是流说谎。
  ④ 负对照（内置自证）：给真音频 PES 的每个帧头后塞 2 字节假 CRC，整除率必须塌到 <0.9 ⇒
     说明这一读数真的对 CRC 敏感；塌不动＝这把尺没牙，当场判红。
  ⑤ 视频 ES 必须被拒：`pes_frames` 只认 0xC0–0xDF 的音频 PES；实测视频 pid 的字节能被
     `decode_head` 误读成 "Layer II"（现读 `ffe45a30`），所以只靠同步字不作数，整除＋PES 类型才是两臂。
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

PACKET = 188
CH_MIN = 6          # 连续对齐帧数下限：5 帧以下视作巧合
MAX_BYTES = 6_000_000

BITRATES = {
    "mpeg1": [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    "mpeg2": [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
RATES = {
    "mpeg1": {0: 44100, 1: 48000, 2: 32000},
    "mpeg2": {0: 22050, 1: 24000, 2: 16000},
    "mpeg25": {0: 11025, 1: 12000, 2: 8000},
}


def fetch(url: str, timeout: float = 25.0) -> bytes:
    """HLS 清单取第一段分片；直连 .ts 取前 MAX_BYTES。"""
    headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 9) ParentsTV/1.3.1"}
    if ".m3u8" not in url:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return r.read(MAX_BYTES)
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        text = r.read(400_000).decode("utf-8", "replace")
    segs = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    # 直播清单的第一行往往已被源自己删掉（三沙 21:2x 那条 404 就是这么来的），取最后一段。
    seg = segs[-1] if segs else None
    if not seg:
        raise RuntimeError("清单里没有分片行")
    base = url.rsplit("/", 1)[0] + "/"
    seg_url = seg if re.match(r"^https?://", seg) else base + seg.lstrip("/")
    with urllib.request.urlopen(urllib.request.Request(seg_url, headers=headers), timeout=timeout) as r:
        return r.read(MAX_BYTES)

def ts_offset(data: bytes) -> int:
    for start in range(0, min(len(data) - 1, 4096)):
        if data[start] == 0x47 and data[start + PACKET] == 0x47 and data[start + 2 * PACKET] == 0x47:
            return start
    return -1


def packets(data: bytes, off: int):
    n = off
    while n + PACKET <= len(data):
        pk = data[n:n + PACKET]
        if pk[0] != 0x47:
            return
        yield pk
        n += PACKET


def elementary_streams(data: bytes, off: int):
    """返回 (audio_pids 的 ES 字节字典, PMT 里的 stream_type, 诊断行)。"""
    pat_pes = bytearray()
    pmt_pes = bytearray()
    pmt_pid = None
    pes_buf: dict[int, bytearray] = {}
    for pk in packets(data, off):
        pid = ((pk[1] & 0x1F) << 8) | pk[2]
        afc = (pk[3] >> 4) & 0x03
        pus = bool(pk[1] & 0x40)          # 规范里 PUSI 在这一位；0x80 是 transport_error_indicator
        payload = pk[4:] if afc in (0x01, 0x03) else b""
        if afc in (0x02, 0x03):          # 带 adaptation field，要先跳过去
            alen = pk[4] if afc in (0x02, 0x03) else 0
            payload = pk[5 + alen:] if afc == 0x03 else b""
        if not payload:
            continue
        if pus:
            pes_buf.setdefault(pid, bytearray())
        buf = pes_buf.setdefault(pid, bytearray())
        buf += payload
        if pid == 0 and len(pat_pes) == 0:
            pat_pes += payload[2:] if payload[0] == 0x00 else payload
        if pmt_pid is not None and pid == pmt_pid and len(pmt_pes) < 4096:
            pmt_pes += buf[-2048:] if len(buf) > 2048 else b""
        if pmt_pid is None and pid == 0 and len(buf) > 16:
            pointer = buf[0]
            tab = bytes(buf[1 + pointer:])
            # PAT 段头 8 字节（table_id、section_length、TS_id、version、section/last_section），
            # 节目项从 tab[8] 起、每项 4 字节：program_number(16) + reserved(3) + PID(13)。
            # program_number==0 那项是 net_PID，不能当 PMT 用。
            if len(tab) >= 12 and tab[0] == 0x00:
                for j in range(8, len(tab) - 3, 4):
                    program = (tab[j] << 8) | tab[j + 1]
                    if program == 0:
                        continue
                    pmt_pid = ((tab[j + 2] & 0x1F) << 8) | tab[j + 3]
                    break
    es: dict[int, bytearray] = {}
    for pid, buf in pes_buf.items():
        if pid in (0, pmt_pid) or pid == 0x1FFF or len(buf) < 64:
            continue
        es[pid] = buf
    types = {}
    if pmt_pid is not None:
        for pid, buf in pes_buf.items():
            if pid != pmt_pid or len(buf) < 16:
                continue
            pointer = buf[0]
            tab = bytes(buf[1 + pointer:])
            if len(tab) < 12 or tab[0] != 0x02:
                continue
            sect_len = ((tab[1] & 0x0F) << 8) | tab[2]
            # PMT：tab[3..4]=program_number，tab[5]=version，tab[6..7]=current_next+section_number…，
            # tab[8..9]=reserved+PCR_PID，tab[10..11]=reserved(4)+program_info_length(12) ⇒ 长度在 10/11，不在 8/9。
            pilen = ((tab[10] & 0x0F) << 8) | tab[11]
            i = 12 + pilen
            end = 3 + sect_len - 4
            while i + 5 <= min(end, len(tab) - 1):
                stype = tab[i]
                epid = ((tab[i + 1] & 0x1F) << 8) | tab[i + 2]
                elen = ((tab[i + 3] & 0x0F) << 8) | tab[i + 4]
                types[epid] = stype
                i += 5 + elen
            break
    return es, types, {"pmt_pid": pmt_pid, "pat_found": pmt_pid is not None, "pes_groups": len(pes_buf)}


def decode_head(buf: bytes, i: int):
    if i + 4 > len(buf):
        return None
    b0, b1, b2, b3 = buf[i], buf[i + 1], buf[i + 2], buf[i + 3]
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None
    version = (b1 >> 3) & 0x03
    layer = (b1 >> 1) & 0x03
    if layer != 0b10:                     # 只要 Layer II
        return None
    br = (b2 >> 4) & 0x0F
    sr = (b2 >> 2) & 0x03
    pad = (b2 >> 1) & 0x01
    if sr == 0b11:
        return None
    if version == 0b00:
        fam, vname = "mpeg25", "mpeg25"
    elif version == 0b10:
        fam, vname = "mpeg2", "mpeg2"
    elif version == 0b11:
        fam, vname = "mpeg1", "mpeg1"
    else:
        return None
    table = BITRATES["mpeg1"] if vname == "mpeg1" else BITRATES["mpeg2"]
    if br in (0, 15) or br >= len(table):
        return None
    rate = RATES[fam][sr]
    kbps = table[br]
    divisor = 144 if vname == "mpeg1" else 72
    total = divisor * kbps * 1000 // rate + pad * 4
    if total < 24:
        return None
    return {
        "at": i, "total": total, "protection": b1 & 0x01,
        "version": vname, "kbps": kbps, "rate": rate, "pad": pad,
        "mode": (b3 >> 6) & 0x03, "head": bytes(buf[i:i + 4]).hex(),
    }


def longest_chain(buf: bytes, start: int, limit: int = 400):
    n, i = 0, start
    heads = []
    while n < limit:
        h = decode_head(buf, i)
        if not h or h["at"] + h["total"] > len(buf):
            break
        heads.append(h)
        n += 1
        i += h["total"]
    return n, heads


def scan_align(buf: bytes):
    best = (0, -1, [])
    i = 0
    while i < len(buf) - 4:
        if buf[i] == 0xFF and (buf[i + 1] & 0xE0) == 0xE0:
            n, heads = longest_chain(buf, i)
            if n > best[0]:
                best = (n, i, heads)
                if n >= 40:
                    break
        i += 1
    return best


def pes_frames(buf: bytes):
    """按 PES 包边界量 CRC —— 这一格的关键不是"链有多长"，而是**整除**。

    每条音频 PES 的净载荷若正好是整数个 Layer II 帧（`payload % total == 0`），说明源没在帧头后塞
    2 字节 CRC（塞了的话该按 `total + 2` 整除，链长那一档 3 帧就会露出 6 字节的余数）。
    这是算术层的第二臂：与帧头里 protection 位那一直读相互独立。

    ⚠️曾经的坑：拿"连续同步帧数 ≥6"当唯一判据 ⇒ 这条流每段载荷只有 3 帧（2298/768），
    尺子把「容器粒度」读成「没有 MP2」，差点把已确认的读数判错。
    """
    n = len(buf)
    i = 0
    segs = []
    heads: dict[str, int] = {}
    while i + 10 <= n:
        if buf[i] != 0 or buf[i + 1] != 0 or buf[i + 2] != 1:
            break
        sid = buf[i + 3]
        if not (0xC0 <= sid <= 0xDF):
            return None               # 不是音频 PES（视频 ES 走这条路就该被拒）
        plen = (buf[i + 4] << 8) | buf[i + 5]
        # PES：byte6 才是 '10'+scrambling+priority+alignment+copyright+original，
        # byte7 是 flags2，byte8 是 header_data_length（我先前拿 byte7 的 0x80 判存在、又读了 byte9 ⇒ 载荷起点偏 1 字节）。
        hdr_len = buf[i + 8]
        start = i + 9 + hdr_len
        end = i + 6 + plen if plen else n
        if end > n or end <= start:
            break
        payload = buf[start:end]
        h = decode_head(payload, 0)
        if not h:
            break
        heads[h["head"]] = heads.get(h["head"], 0) + 1
        segs.append((len(payload), h["total"], len(payload) % h["total"], h))
        i = end
    if not segs:
        return None
    exact = sum(1 for _, total, rem, _ in segs if rem == 0)
    return {
        "段数": len(segs), "整除段数": exact, "载荷字节": sum(s[0] for s in segs),
        "帧长": segs[0][1], "protection位集合": sorted({s[3]["protection"] for s in segs}),
        "头集合": {k: v for k, v in sorted(heads.items(), key=lambda kv: -kv[1])[:4]},
        "版本": segs[0][3]["version"], "kbps": segs[0][3]["kbps"], "采样率": segs[0][3]["rate"],
        "声道模式": segs[0][3]["mode"], "整除率": round(exact / len(segs), 4),
    }


def inject_fake_crc(buf: bytes, pad: int = 2) -> bytes:
    """负对照用：在每条音频 PES 的每个帧头后面塞 `pad` 字节，看整除是否塌掉。"""
    n = len(buf)
    i = 0
    out = bytearray()
    while i + 10 <= n:
        if buf[i] != 0 or buf[i + 1] != 0 or buf[i + 2] != 1:
            break
        sid = buf[i + 3]
        if not (0xC0 <= sid <= 0xDF):
            break
        plen = (buf[i + 4] << 8) | buf[i + 5]
        hdr_len = buf[i + 8]
        start = i + 9 + hdr_len
        end = i + 6 + plen if plen else n
        if end > n or end <= start:
            break
        payload = buf[start:end]
        h = decode_head(payload, 0)
        if not h:
            break
        rebuilt = bytearray()
        j = 0
        frames = 0
        while j + h["total"] <= len(payload):
            rebuilt += payload[j:j + 4] + b"\x00" * pad + payload[j + 4:h["total"]]
            j += h["total"]
            frames += 1
        # 关键：注入把载荷变长了，`PES_packet_length` 必须同步改写，否则读尺那边照旧长度截断、
        # 整除率不动 ⇒ 负对照空转、翻成正对照（10-27 实测到这一形状）。
        new_len = (start - (i + 6)) + len(rebuilt)
        out += buf[i:i + 4] + bytes([(new_len >> 8) & 0xFF, new_len & 0xFF]) + buf[i + 6:start] + rebuilt
        i = end
    return bytes(out)


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--fixture":
        path = Path(args[1])
        data = path.read_bytes()
        n, at, heads = scan_align(data)
        print(f"[裸MP2夹具] {path.name} 链长={n} 起点={at} protection={heads[0]['protection'] if heads else '-'} head={heads[0]['head'] if heads else '-'}")
        return 0 if n >= CH_MIN else 1
    catalog = Path("app/src/main/assets/channels.json")
    want = args[0] if args else None
    chs = json.loads(catalog.read_text(encoding="utf-8"))["channels"]
    if want is None:
        targets = [c for c in chs if c["name"] in ("三沙卫视", "四川卫视")]
    else:
        targets = [c for c in chs if c["name"] == want]
        if not targets:
            print(f"[拒绝] 目录里没有台名 {want!r}（先现读台名，别把取不到台折成零结论）")
            return 1
    rc = 0
    for ch in targets:
        for idx, url in enumerate(ch["streams"], 1):
            if want is None and ch["name"] == "四川卫视" and idx != 3:
                continue
            try:
                data = fetch(url)
            except Exception as e:  # noqa: BLE001
                print(f"[拒绝] {ch['name']} 线{idx} 取不到字节：{type(e).__name__}: {e}")
                rc = 1
                continue
            off = ts_offset(data)
            if off < 0:
                print(f"[拒绝] {ch['name']} 线{idx} 不是 188 字节 TS（前 4096 找不到 0x47 周期）⇒ 不给 CRC 结论")
                rc = 1
                continue
            es, types, diag = elementary_streams(data, off)
            row = []
            for pid, buf in sorted(es.items(), key=lambda kv: -len(kv[1])):
                read = pes_frames(bytes(buf))
                if read and read["整除率"] >= 0.9:
                    read["pid"] = pid
                    read["stream_type"] = hex(types.get(pid, -1)) if pid in types else "PMT未声明"
                    row.append(read)
            print(f"{ch['name']} 线{idx} TS偏移={off} 字节={len(data)} PMT定位={diag['pmt_pid']} "
                  f"声明={ {hex(k): hex(v) for k, v in sorted(types.items())} } 判为音频轨={len(row)}")
            for r in row:
                print("   ", json.dumps(r, ensure_ascii=False))
            # 负对照：给第一条音频轨每个帧头后塞 2 字节假 CRC，整除率必须塌到 0
            if row:
                buf = bytes(es[row[0]["pid"]])
                n2, _, _ = scan_align(inject_fake_crc(buf))
                read2 = pes_frames(inject_fake_crc(buf))
                rate2 = read2["整除率"] if read2 else -1
                verdict = "有牙（整除塌了）" if rate2 < 0.9 else "没牙！这把尺看不见 CRC ⇒ 结论不作数"
                print(f"    [负对照] 塞 2 字节假 CRC 后 整除率={rate2}（原 {row[0]['整除率']}）"
                      f" 裸链长={n2} ⇒ {verdict}")
                if rate2 >= 0.9:
                    rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
