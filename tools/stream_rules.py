"""线路地址的判定规则：目录生成、健康探测、App 端合并共用同一把尺。

Python 侧与 Kotlin 侧（app/src/main/java/com/sun/tv/parents/data/StreamUrl.kt）
必须给出同样的结论，tools/tests/test_rules_parity.py 会逐条对拍。
"""

import re
import time
from urllib.parse import urlsplit

HEX = set("0123456789abcdefABCDEF")

# ExoPlayer 没有解复用器的容器后缀，命中即注定播不出来，不必再花探测预算。
UNSUPPORTED_EXT = ("flv", "rmvb", "asf", "wmv", "avi", "mkv")

# 绑定到具体机顶盒/手机号的参数，别的设备拿到也是 403。值为 unknown/空 的不算绑定。
UNBOUND_VALUES = ("unknown", "0", "")

# 内嵌过期时间：…_<unix>_<ttl> 或 …?<expire>=<unix>
TS_TTL = re.compile(r"[^0-9a-zA-Z](1[6-9]\d{8})_(\d{1,9})(?:[^0-9]|$)")
KV_EXPIRE = re.compile(r"(?:expire|expired|deadline|end_time|endtime|validuntil|valid)=?(1[6-9]\d{8})")
KV_DEVICE = re.compile(r"[?&]([a-z_]*(?:mac|msisdn|serialnumber|devid|deviceid)[a-z_]*)=([^&;]*)", re.I)


def _unhex(text):
    out = bytearray()
    i = 0
    while i < len(text):
        if text[i] == "%" and i + 3 <= len(text) and text[i + 1] in HEX and text[i + 2] in HEX:
            out.append(int(text[i + 1:i + 3], 16))
            i += 3
            continue
        out += text[i].encode("utf-8", "surrogatepass")
        i += 1
    return out.decode("utf-8", "replace")


def canon(url):
    """把同一地址的不同写法归成一个比较键。"""
    raw = (url or "").strip().lstrip("\ufeff")
    if "://" not in raw:
        return raw.lower()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower()
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return raw.lower()
    try:
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return raw.lower()
    if ":" in host and not host.startswith("["):
        # urlsplit 会把 IPv6 字面量的方括号剥掉，归一键里必须补回来，否则地址非法。
        host = "[" + host + "]"
    if scheme == "http" and port == 80:
        port = None
    if scheme == "https" and port == 443:
        port = None
    userinfo = ""
    if parts.username is not None:
        userinfo = parts.username.lower()
        if parts.password is not None:
            userinfo += ":" + parts.password.lower()
        userinfo += "@"
    path = _unhex(parts.path or "")
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = _unhex(parts.query) if parts.query else ""
    net = userinfo + host + ((":" + str(port)) if port is not None else "")
    return scheme + "://" + net + path + (("?" + query) if query else "")


def is_ipv6(url):
    key = canon(url)
    return key.startswith("http://[") or key.startswith("https://[")


def same_line(first, second):
    return canon(first) == canon(second)


def unsupported_container(url):
    path = re.sub(r"[?#].*$", "", url.lower())
    tail = path.rsplit(".", 1)
    return len(tail) == 2 and tail[1] in UNSUPPORTED_EXT


def device_bound(url):
    for match in KV_DEVICE.finditer(url):
        value = match.group(2).strip().lower()
        if value not in UNBOUND_VALUES:
            return True
    return False


def token_expired(url, now=None):
    """URL 里写死的过期时刻已经过去，任何设备都取不到。只在证据明确时才判过期。"""
    now = now if now is not None else time.time()
    for match in TS_TTL.finditer(url):
        if int(match.group(1)) + int(match.group(2)) < now:
            return True
    for match in KV_EXPIRE.finditer(url):
        if int(match.group(1)) < now:
            return True
    return False


def never_playable(url, now=None):
    """不联网就能断定播不出来的三类。返回原因串，可用则返回 None。"""
    if unsupported_container(url):
        return "容器不支持"
    if device_bound(url):
        return "设备绑定"
    if token_expired(url, now):
        return "鉴权已过期"
    return None
