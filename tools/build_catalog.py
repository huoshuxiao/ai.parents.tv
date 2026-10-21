#!/usr/bin/env python3
"""Build the bundled Android TV channel catalog from public IPTV playlists.

The output is app/src/main/assets/channels.json. Stream URLs are third-party
and can expire; every channel carries several fallback URLs when available.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.request
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
CACHE = BASE / "tools" / ".cache"
OUT = BASE / "app" / "src" / "main" / "assets" / "channels.json"

SOURCES = {
    "fanmingming": "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/index.m3u",
    "vbsky": "https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u",
    "zbefine": "https://raw.githubusercontent.com/zbefine/iptv/main/iptv.m3u",
    "bigbig": "https://raw.githubusercontent.com/BigBigGrandG/IPTV-URL/release/Gather.m3u",
    "yang": "https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u",
    "epg_hk": "https://epg.pw/test_channels_hong_kong.m3u",
    "epg_tw": "https://epg.pw/test_channels_taiwan.m3u",
    "epg_mo": "https://epg.pw/test_channels_macau.m3u",
    "aptv": "https://raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u",
    "iptv_org": "https://iptv-org.github.io/iptv/index.m3u",
    "pca": "https://raw.githubusercontent.com/modood/Administrative-divisions-of-China/master/dist/pca-code.json",
}

# Lower value = stream is tried earlier.
SOURCE_PRIORITY = {
    "fanmingming": 0,
    "iptv_org": 1,
    "zbefine": 2,
    "bigbig": 3,
    "vbsky": 4,
    "epg": 5,
    "yang": 6,
    "aptv": 7,
}

PROVINCE_ORDER = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
]
PROVINCE_GROUPS = set(PROVINCE_ORDER)

CATEGORY_ORDER = ["cctv", "local", "hktw", "featured", "sports", "international"]
CATEGORY_NAMES = {
    "cctv": "央视·卫视",
    "local": "地方频道",
    "hktw": "港澳台",
    "featured": "特色频道",
    "sports": "体育频道",
    "international": "国际频道",
}
SUBGROUP_ORDER = {
    "cctv": ["央视", "卫视"],
    "local": PROVINCE_ORDER + ["其他"],
    "hktw": ["香港", "台湾", "澳门", "海外华人"],
    "featured": ["影视轮播", "NewTV", "CHC", "少儿"],
    "sports": ["国内体育", "咪咕直播", "港台体育"],
    "international": ["新闻", "纪实", "综合"],
}

CCTV_NAMES = {
    "1": "CCTV-1 综合",
    "2": "CCTV-2 财经",
    "3": "CCTV-3 综艺",
    "4": "CCTV-4 中文国际",
    "5": "CCTV-5 体育",
    "5+": "CCTV-5+ 体育赛事",
    "6": "CCTV-6 电影",
    "7": "CCTV-7 国防军事",
    "8": "CCTV-8 电视剧",
    "9": "CCTV-9 纪录",
    "10": "CCTV-10 科教",
    "11": "CCTV-11 戏曲",
    "12": "CCTV-12 社会与法",
    "13": "CCTV-13 新闻",
    "14": "CCTV-14 少儿",
    "15": "CCTV-15 音乐",
    "16": "CCTV-16 奥林匹克",
    "17": "CCTV-17 农业农村",
    "4K": "CCTV-4K 超高清",
}
CCTV_ORDER = {v: i for i, v in enumerate(CCTV_NAMES.values())}
CCTV_ORDER.update({
    "CGTN": 90, "CGTN 纪录": 91, "CGTN 法语": 92,
    "CGTN 西语": 93, "CGTN 阿语": 94, "CGTN 俄语": 95,
})

SAT_ORDER = [
    "北京卫视", "东方卫视", "天津卫视", "重庆卫视", "河北卫视", "山西卫视",
    "内蒙古卫视", "辽宁卫视", "吉林卫视", "黑龙江卫视", "江苏卫视",
    "浙江卫视", "安徽卫视", "东南卫视", "江西卫视", "山东卫视",
    "山东教育卫视", "河南卫视", "湖北卫视", "湖南卫视", "广东卫视",
    "深圳卫视", "南方卫视", "广西卫视", "海南卫视", "三沙卫视",
    "四川卫视", "康巴卫视", "贵州卫视", "云南卫视", "西藏卫视",
    "陕西卫视", "农林卫视", "甘肃卫视", "青海卫视", "安多卫视",
    "宁夏卫视", "新疆卫视", "兵团卫视", "延边卫视",
]

TR2CN = str.maketrans({
    "鳳": "凤", "視": "视", "東": "东", "綜": "综", "藝": "艺", "財": "财",
    "經": "经", "體": "体", "聞": "闻", "電": "电", "劇": "剧", "灣": "湾",
    "歡": "欢", "樂": "乐", "華": "华", "衛": "卫", "龍": "龙", "爾": "尔",
    "愛": "爱", "達": "达", "網": "网", "亞": "亚", "歐": "欧", "陸": "陆",
    "廣": "广", "蘇": "苏", "遼": "辽", "雲": "云", "貴": "贵", "陝": "陕",
    "寧": "宁", "瓊": "琼", "閩": "闽", "贛": "赣", "魯": "鲁", "豫": "豫",
    "粵": "粤", "趙": "赵", "錢": "钱", "孫": "孙", "劉": "刘", "張": "张",
    "榮": "荣", "訊": "讯", "線": "线", "頻": "频", "道": "道", "數": "数",
    "據": "据", "齊": "齐", "內": "内", "麥": "麦", "點": "点", "鏡": "镜",
    "頭": "头", "環": "环", "球": "球", "紀": "纪", "錄": "录",
    "專": "专", "業": "业", "師": "师", "優": "优", "選": "选", "節": "节",
    "場": "场", "開": "开", "關": "关", "將": "将", "軍": "军", "動": "动",
    "畫": "画", "兒": "儿", "童": "童", "國": "国", "際": "际", "會": "会",
    "員": "员", "歸": "归", "傳": "传", "統": "统", "歷": "历", "史": "史",
    "門": "门", "運": "运", "車": "车", "馬": "马", "鳥": "鸟", "魚": "鱼",
    "說": "说", "語": "语", "讓": "让", "時": "时", "間": "间", "陳": "陈",
    "黃": "黄", "楊": "杨", "吳": "吴", "葉": "叶", "澤": "泽", "與": "与",
})


def clean_name(raw: str) -> str:
    n = unicodedata.normalize("NFKC", str(raw or ""))
    n = re.sub(r"\[[^\]]*(?:geo|24/7|BD|HD|SD|VGA|UHD|4K)[^\]]*\]", "", n, flags=re.I)
    n = re.sub(r"\((?:1080|720|576|540|480|2160|1080i)[pi]?\)", "", n, flags=re.I)
    n = re.sub(r"「[^」]*」", "", n)
    n = re.sub(r"\s*\[[A-Za-z0-9 ]+\]\s*", " ", n)
    n = re.sub(r"\s+(?:HD|SD|UHD)$", "", n, flags=re.I)
    n = re.sub(r"(?:高清|超清|标清)$", "", n)
    n = re.sub(r"\s+", " ", n).strip(" -|·")
    return n.strip()


def norm_key(raw: str) -> str:
    n = clean_name(raw).translate(TR2CN).lower()
    n = re.sub(r"[\s\-_·•]+", "", n)
    return n


def match_key(raw: str) -> str:
    n = clean_name(raw).translate(TR2CN).lower()
    n = re.sub(r"\b(hd|sd|uhd|fhd)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def is_ipv6(url: str) -> bool:
    m = re.match(r"https?://\[", url)
    return bool(m)


def is_geo(raw: str) -> bool:
    return "geo" in raw.lower()


def parse_m3u(path: Path, source: str):
    entries = []
    cur = None
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if line.startswith("#EXTINF"):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            name = line.rsplit(",", 1)[-1].strip()
            cur = {
                "name": name,
                "raw": line,
                "group": attrs.get("group-title", ""),
                "logo": attrs.get("tvg-logo", ""),
                "source": source,
                "url": None,
            }
        elif line.startswith("#EXTGRP:") and cur:
            cur["group"] = line.split(":", 1)[1].strip()
        elif line and not line.startswith("#") and cur:
            if line.startswith(("http://", "https://")) or line.startswith("rtmp://"):
                cur["url"] = line
                entries.append(cur)
                cur = None
    return entries


def strip_admin(name: str) -> str:
    for suffix in (
        "特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区", "自治区",
        "自治州", "自治县", "地区", "林区", "新区", "省", "市", "区", "县", "盟", "旗",
    ):
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            return name[: -len(suffix)]
    return name


class ProvinceIndex:
    def __init__(self, pca_path: Path):
        data = json.loads(pca_path.read_text(encoding="utf-8"))
        candidates = defaultdict(set)
        for prov in data:
            pname = strip_admin(prov["name"])
            candidates[prov["name"]].add(pname)
            candidates[pname].add(pname)
            for city in prov.get("children", []):
                if city["name"] in ("市辖区", "省直辖县级行政区划", "自治区直辖县级行政区划"):
                    continue
                cname = strip_admin(city["name"])
                candidates[city["name"]].add(pname)
                candidates[cname].add(pname)
                for area in city.get("children", []):
                    aname = strip_admin(area["name"])
                    candidates[area["name"]].add(pname)
                    candidates[aname].add(pname)
        self.mapping = {k: next(iter(v)) for k, v in candidates.items() if len(v) == 1}
        self.manual = {
            "南国都市": "海南", "中国蓝新闻": "浙江", "中国交通": "海南", "三沙": "海南",
            "康巴": "四川", "安多": "青海", "农林": "陕西", "兵团": "新疆",
            "延边": "吉林", "深圳": "广东", "厦门": "福建", "青岛": "山东",
            "大连": "辽宁", "宁波": "浙江", "苏州": "江苏", "无锡": "江苏",
            "温州": "浙江", "佛山": "广东", "东莞": "广东", "珠海": "广东",
            "泉州": "福建", "洛阳": "河南", "唐山": "河北", "保定": "河北",
        }

    def detect(self, name: str):
        n = clean_name(name)
        for p in PROVINCE_ORDER:
            if n.startswith(p):
                return p
        for length in range(min(len(n), 8), 1, -1):
            prefix = n[:length]
            if prefix in self.mapping:
                return self.mapping[prefix]
            if prefix in self.manual:
                return self.manual[prefix]
        return None


HK_CHANNELS = [
    ("翡翠台", ["翡翠台", "翡翠", "jade", "tvb翡翠", "tvb翡翠台"]),
    ("明珠台", ["明珠台", "明珠", "pearl"]),
    ("TVB J2", ["j2", "tvbj2"]),
    ("TVB Plus", ["tvbplus"]),
    ("无线新闻", ["无线新闻", "tvb新闻"]),
    ("无线财经体育资讯台", ["无线财经体育资讯台", "无线财经"]),
    ("凤凰中文", ["凤凰中文", "凤凰卫视中文台", "凤凰卫视", "凤凰中文台",
                  "phoenixchinesechannel", "phoenixchinese"]),
    ("凤凰资讯", ["凤凰资讯", "凤凰资讯台", "phoenixinfonewschannel", "phoenixinfonews"]),
    ("凤凰香港", ["凤凰香港", "phoenixhongkong"]),
    ("HOY TV", ["hoytv"]),
    ("HOY资讯台", ["hoy资讯台", "hoyinfotainment"]),
    ("ViuTV", ["viutv"]),
    ("ViuTV6", ["viutv6"]),
    ("香港国际财经", ["香港国际财经", "hkinternationalbusinesschannel"]),
    ("RTHK 31", ["rthk31", "rhk31", "港台电视31"]),
    ("RTHK 32", ["rthk32", "rhk32", "港台电视32"]),
    ("星河台", ["星河台", "tvb星河"]),
    ("美亚电影", ["美亚电影", "meiahmoviechannel"]),
]

TW_CHANNELS = [
    ("TVBS", ["tvbs", "tvbs综合"]),
    ("TVBS新闻", ["tvbs新闻", "tvbsnews"]),
    ("TVBS欢乐台", ["tvbs欢乐台", "tvbs欢乐"]),
    ("TVBS亚洲", ["tvbs亚洲", "tvbs-asia", "tvbsasia"]),
    ("中天新闻", ["中天新闻", "ctinews"]),
    ("中天综合", ["中天综合", "ctivariety"]),
    ("中天娱乐", ["中天娱乐", "ctientertainment"]),
    ("东森新闻", ["东森新闻", "ebcnews"]),
    ("东森财经新闻", ["东森财经新闻", "ebcfinancialnews"]),
    ("东森综合", ["东森综合"]),
    ("东森电影", ["东森电影"]),
    ("东森洋片", ["东森洋片"]),
    ("三立新闻", ["三立新闻", "setnews"]),
    ("三立台湾", ["三立台湾"]),
    ("三立戏剧", ["三立戏剧"]),
    ("民视", ["民视", "ftv"]),
    ("民视新闻", ["民视新闻"]),
    ("台视", ["台视", "ttv"]),
    ("台视新闻", ["台视新闻"]),
    ("中视", ["中视", "ctv"]),
    ("中视经典", ["中视经典"]),
    ("中视菁采", ["中视菁采"]),
    ("华视", ["华视", "cts"]),
    ("公视", ["公视", "pts"]),
    ("大爱一台", ["大爱", "大爱电视", "daai1"]),
    ("大爱二台", ["大爱2", "daai2"]),
    ("八大第一台", ["八大第一台"]),
    ("八大戏剧", ["八大戏剧"]),
    ("八大综合", ["八大综合"]),
    ("纬来日本", ["纬来日本"]),
    ("卫视中文台", ["卫视中文台", "卫视中文"]),
    ("非凡新闻", ["非凡新闻"]),
    ("好莱坞电影", ["好莱坞电影", "hollywoodmovies"]),
    ("AXN台湾", ["axn台湾", "axn"]),
    ("人间卫视", ["人间卫视"]),
    ("番薯台", ["番薯台"]),
    ("靖天卡通", ["靖天卡通"]),
    ("大立电视", ["大立电视"]),
]

MO_CHANNELS = [
    ("澳视澳门", ["澳视澳门", "tdmoumun", "tdmoumum"]),
    ("澳视资讯", ["澳视资讯", "tdminformation"]),
    ("澳视综艺", ["澳视综艺", "tdmentertainment"]),
    ("澳视葡文", ["澳视葡文", "tdmportuguese"]),
    ("澳门莲花", ["澳门莲花", "lotustv", "莲花卫视"]),
    ("澳门MACAU", ["澳门macau", "canalmacau"]),
]

OVERSEAS_CHANNELS = [
    ("美国中文电视", ["美国中文电视", "sinovision"]),
    ("华语电视", ["华语电视"]),
]

REGION_LISTS = {
    "香港": HK_CHANNELS,
    "台湾": TW_CHANNELS,
    "澳门": MO_CHANNELS,
    "海外华人": OVERSEAS_CHANNELS,
}

REGION_ALIAS = {}
for _region, _channels in REGION_LISTS.items():
    for _idx, (_display, _aliases) in enumerate(_channels):
        for _alias in _aliases:
            REGION_ALIAS[norm_key(_alias)] = (_region, _display, _idx)
REGION_SUFFIX = sorted(REGION_ALIAS.items(), key=lambda kv: len(kv[0]), reverse=True)


INTL_CHANNELS = [
    ("新闻", "BBC News BBC新闻", [r"^bbc news\b", r"^bbc world news$"], 0),
    ("新闻", "CNN 国际新闻", [r"^cnn$", r"^cnn international$"], 1),
    ("新闻", "FOX News 福克斯新闻", [r"^fox news channel\b", r"^fox news\b"], 2),
    ("新闻", "Al Jazeera 半岛电视台", [r"^al jazeera english\b", r"^半岛英语$",
                                        r"^半岛新闻.*英文", r"^半岛新闻.*英语"], 3),
    ("新闻", "DW 德国之声", [r"^dw english\b"], 4),
    ("新闻", "France 24 法国24", [r"^france 24 english\b"], 5),
    ("新闻", "Euronews 欧洲新闻", [r"^euronews english\b", r"^euronews$"], 6),
    ("新闻", "NHK World NHK世界", [r"^nhk world.?japan\b"], 7),
    ("新闻", "Bloomberg 彭博财经", [r"^bloomberg tv$", r"^bloomberg tv asia\b",
                                     r"^bloomberg news asia$", r"^bloomberg$"], 8),
    ("新闻", "CNBC 财经", [r"^cnbc asia\b", r"^cnbc$"], 9),
    ("新闻", "CNA 亚洲新闻台", [r"^亚洲新闻$"], 10),
    ("纪实", "Discovery 探索频道", [r"^discovery channel\b", r"^探索频道$"], 20),
    ("纪实", "Discovery Asia 探索亚洲", [r"^discovery asia\b", r"^探索亚洲$"], 21),
    ("纪实", "National Geographic 国家地理", [r"^national geographic($| )"], 22),
    ("纪实", "Nat Geo Wild 国家地理野生", [r"^national geographic wild\b",
                                           r"^国家地理野生$"], 23),
    ("纪实", "BBC Earth BBC地球", [r"^bbc earth\b"], 24),
    ("纪实", "Animal Planet 动物星球", [r"^animal planet\b", r"^动物星球$"], 25),
    ("纪实", "History 历史频道", [r"^history$", r"^history channel$", r"^历史频道$"], 26),
    ("纪实", "求索纪录", [r"^求索记录$"], 27),
    ("纪实", "求索科学", [r"^求索科学$"], 28),
    ("纪实", "求索动物", [r"^求索动物$"], 29),
    ("纪实", "求索生活", [r"^求索生活$"], 30),
    ("综合", "Arirang TV 阿里郎", [r"^arirang tv\b"], 40),
    ("综合", "NHK World Premium", [r"^nhk world premium\b"], 41),
]


THEMED_MOVIES = [
    "刘德华电影", "周星驰电影", "成龙电影", "李连杰电影", "林正英电影",
    "沈腾电影", "古天乐电影", "强森电影", "武侠电影", "战争电影",
    "枪战电影", "功夫电影", "怪兽电影", "恐怖电影", "喜剧电影",
    "经典香港电影", "黑莓电影",
]

KIDS_ALIASES = [
    "金鹰卡通", "卡酷少儿", "嘉佳卡通", "优漫卡通", "哈哈炫动",
    "浙江少儿", "福州少儿", "黑龙江少儿", "海南少儿", "靖天卡通",
    "宝宝巴士", "宝宝课堂", "中国教育1台", "中国教育2台",
]

NEWTV_ORDER = [
    "超级电影", "超级电视剧", "超级综艺", "超级体育", "精品大剧",
    "家庭剧场", "欢乐剧场", "军旅剧场", "古装剧场", "东北热剧",
    "热播精选", "精品综合", "精品纪录", "精品体育", "金牌综艺",
    "明星大片", "中国功夫", "武博世界", "军事评论", "潮妈辣婆",
    "怡伴健康", "农业致富", "炫舞未来",
]


class Builder:
    def __init__(self):
        self.channels = {}
        self.stats = defaultdict(int)

    def add(self, display, category, subgroup, url, *, logo="", source="", order=999,
            raw="", key_name=None):
        if not url or not url.startswith(("http://", "https://")):
            return
        display = unicodedata.normalize("NFKC", str(display or "")).strip()
        if not display:
            return
        key = (category, subgroup, norm_key(key_name or display))
        ch = self.channels.get(key)
        if ch is None:
            ch = {
                "name": display,
                "category": category,
                "subgroup": subgroup,
                "logo": logo or "",
                "order": order,
                "streams": [],
                "_seen": set(),
            }
            self.channels[key] = ch
        if logo and not ch["logo"]:
            ch["logo"] = logo
        ch["order"] = min(ch["order"], order)
        if url not in ch["_seen"]:
            ch["_seen"].add(url)
            ch["streams"].append({
                "url": url,
                "source": source,
                "ipv6": is_ipv6(url),
                "geo": is_geo(raw),
                "seq": len(ch["streams"]),
            })

    def final_channels(self):
        rows = []
        for (category, subgroup, _), ch in self.channels.items():
            streams = sorted(
                ch["streams"],
                key=lambda s: (
                    s["ipv6"],
                    s["geo"],
                    SOURCE_PRIORITY.get(s["source"], 50),
                    s["seq"],
                ),
            )
            urls = []
            for s in streams:
                if s["url"] not in urls:
                    urls.append(s["url"])
                if len(urls) >= 6:
                    break
            if not urls:
                continue
            ch_id = f"{category}-{hashlib.sha1(('|'.join([category, subgroup, ch['name']])).encode()).hexdigest()[:10]}"
            rows.append({
                "id": ch_id,
                "name": ch["name"],
                "category": category,
                "subgroup": subgroup,
                "logo": ch["logo"],
                "streams": urls,
                "_order": ch["order"],
            })
        cat_idx = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        rows.sort(key=lambda r: (
            cat_idx.get(r["category"], 99),
            SUBGROUP_ORDER.get(r["category"], []).index(r["subgroup"])
            if r["subgroup"] in SUBGROUP_ORDER.get(r["category"], []) else 99,
            r["_order"], r["name"],
        ))
        for row in rows:
            row.pop("_order", None)
        return rows


def canonical_cctv(name):
    n = clean_name(name)
    up = n.upper().replace(" ", "")
    if "CCTV+" in up or "CCTV＋" in n:
        return None
    if "欧洲" in n or "美洲" in n:
        return None
    if re.search(r"CCTV[-\s]*4K", n, re.I):
        return CCTV_NAMES["4K"]
    m = re.search(r"CCTV[-\s]*(\d+)(\+)?", n, re.I)
    if m:
        num = str(int(m.group(1)))
        key = num + ("+" if m.group(2) else "")
        return CCTV_NAMES.get(key)
    if up.startswith("CGTN"):
        if "纪录" in n or "DOC" in up:
            return "CGTN 纪录"
        if "法语" in n or "FRENCH" in up:
            return "CGTN 法语"
        if "西语" in n or "SPANISH" in up:
            return "CGTN 西语"
        if "阿语" in n or "ARABIC" in up:
            return "CGTN 阿语"
        if "俄语" in n or "RUSSIAN" in up:
            return "CGTN 俄语"
        return "CGTN"
    return None


def canonical_satellite(name):
    n = clean_name(name)
    if not n.endswith("卫视"):
        return None
    if any(x in n for x in ("凤凰", "香港", "澳门", "台湾", "星空")):
        return None
    if n.startswith("CCTV"):
        return None
    if norm_key(n) in REGION_ALIAS:
        return None
    return n


def sat_order(name):
    try:
        return SAT_ORDER.index(name)
    except ValueError:
        return 200


def add_cctv(builder, entry):
    name = canonical_cctv(entry["name"])
    if not name:
        return False
    builder.add(name, "cctv", "央视", entry["url"], logo=entry["logo"],
                source=entry["source"], order=CCTV_ORDER.get(name, 150),
                raw=entry["raw"], key_name=name)
    return True


def add_satellite(builder, entry):
    name = canonical_satellite(entry["name"])
    if not name:
        return False
    builder.add(name, "cctv", "卫视", entry["url"], logo=entry["logo"],
                source=entry["source"], order=sat_order(name),
                raw=entry["raw"], key_name=name)
    return True


def add_region(builder, entry):
    n = norm_key(entry["name"])
    hit = REGION_ALIAS.get(n)
    if not hit:
        for alias, value in REGION_SUFFIX:
            if len(alias) >= 3 and n.endswith(alias):
                hit = value
                break
    if not hit:
        return False
    region, display, idx = hit
    builder.add(display, "hktw", region, entry["url"], logo=entry["logo"],
                source=entry["source"], order=idx, raw=entry["raw"],
                key_name=display)
    return True


def add_intl(builder, entry):
    n = match_key(entry["name"])
    if not n:
        return False
    for subgroup, display, patterns, order in INTL_CHANNELS:
        if any(re.search(p, n) for p in patterns):
            builder.add(display, "international", subgroup, entry["url"],
                        logo=entry["logo"], source=entry["source"],
                        order=order, raw=entry["raw"], key_name=display)
            return True
    return False


def add_chc(builder, entry):
    n = norm_key(entry["name"])
    if "chc" not in n:
        return False
    if "家庭" in n:
        display = "CHC家庭影院"
    elif "动作" in n:
        display = "CHC动作电影"
    elif "影迷" in n:
        display = "CHC影迷电影"
    else:
        display = "CHC高清电影"
    order = ["CHC家庭影院", "CHC动作电影", "CHC高清电影", "CHC影迷电影"].index(display)
    builder.add(display, "featured", "CHC", entry["url"], logo=entry["logo"],
                source=entry["source"], order=order, raw=entry["raw"],
                key_name=display)
    return True


def add_newtv(builder, entry):
    n = clean_name(entry["name"])
    if "newtv" not in n.lower() and entry.get("group") != "NewTV":
        return False
    base = re.sub(r"(?i)^newtv\s*", "", n)
    base = re.sub(r"^超级", "超级", base)
    if base in NEWTV_ORDER:
        idx = NEWTV_ORDER.index(base)
    else:
        # Names such as 超级综艺 / 精品体育 arrive without the NewTV prefix.
        idx = 100 + len(builder.channels)
    display = f"NewTV {base}" if base else "NewTV"
    builder.add(display, "featured", "NewTV", entry["url"], logo=entry["logo"],
                source=entry["source"], order=idx, raw=entry["raw"],
                key_name=display)
    return True


def add_kids(builder, entry):
    n = norm_key(entry["name"])
    for display in KIDS_ALIASES:
        if norm_key(display) == n:
            builder.add(display, "featured", "少儿", entry["url"],
                        logo=entry["logo"], source=entry["source"],
                        order=list(KIDS_ALIASES).index(display),
                        raw=entry["raw"], key_name=display)
            return True
    return False


def add_sports(builder, entry, subgroup=None, display=None, order=None):
    n = clean_name(entry["name"]).translate(TR2CN)
    nn = norm_key(n)
    if "CCTV" in n.upper():
        return False
    lower = n.lower()
    if not subgroup:
        if "咪咕" in n or "睛彩竞技" in n or "睛彩篮球" in n:
            subgroup = "咪咕直播"
        elif any(k in n for k in ("纬来体育", "爱尔达", "博斯", "澳视体育", "澳门体育")) or \
                any(k in lower for k in ("eleven", "tdm sports")):
            subgroup = "港台体育"
        elif "体育" in n:
            subgroup = "国内体育"
        else:
            return False
    if not display:
        display = n
        if "咪咕直播" in n:
            m = re.search(r"咪咕直播\s*(\d+)\s*K?\s*-?\s*(\d+)?", n, re.I)
            if "4K" in n.upper():
                num = re.search(r"4K\s*-?\s*(\d+)", n, re.I)
                display = f"咪咕直播 4K-{num.group(1) if num else '1'}"
            elif m:
                display = f"咪咕直播 {m.group(1)}"
        # Keep the sports category useful: 34 numbered event feeds crowd out real channels.
        if "咪咕直播" in display:
            m2 = re.match(r"^咪咕直播\s*(4K)?\s*-?\s*(\d+)$", display, re.I)
            if m2:
                display = f"咪咕直播 {'4K-' if m2.group(1) else ''}{int(m2.group(2))}"
        if display.startswith("咪咕直播") and "4K" not in display:
            num = re.search(r"(\d+)$", display)
            if num and int(num.group(1)) > 8:
                return True
    if order is None:
        special = {
            "五星体育": 0, "足球频道": 10, "精品体育": 11,
            "广东体育": 20, "上海体育": 21, "北京体育": 22, "天津体育": 23,
            "辽宁体育": 24, "山东体育": 25, "江苏体育": 26, "湖北体育": 27,
            "四川体育": 28, "福建体育": 29,
        }
        if display.startswith("咪咕直播 4K"):
            order = 40
        elif display.startswith("咪咕直播"):
            order = 60
        elif subgroup == "港台体育":
            order = 100
        else:
            order = special.get(display, 80)
    display = display.translate(TR2CN)
    display = {
        "爱尔达体育2": "爱尔达体育2台",
        "爱尔达体育3": "爱尔达体育3台",
        "博斯運动2": "博斯运动2",
        "博斯运动2": "博斯运动2",
        "ELEVEN 体育1台": "Eleven Sports 1",
        "ELEVEN 体育2台": "Eleven Sports 2",
        "ELEVEN 体育3台": "Eleven Sports 3",
    }.get(display, display)
    if "澳视体育" in display or "澳门体育" in display:
        display = "澳视体育"
    builder.add(display, "sports", subgroup, entry["url"], logo=entry["logo"],
                source=entry["source"], order=order, raw=entry["raw"],
                key_name=display)
    return True


def add_local(builder, entry, province):
    name = clean_name(entry["name"])
    if not name:
        return False
    if canonical_cctv(name) or canonical_satellite(name):
        return False
    if add_chc(builder, entry) or add_newtv(builder, entry):
        return True
    if add_kids(builder, entry):
        return True
    if "体育" in name or "咪咕" in name:
        return add_sports(builder, entry)
    priority = 10
    for kw, val in (("新闻综合", 0), ("综合", 1), ("都市", 2), ("经济", 3),
                    ("影视", 4), ("公共", 5), ("生活", 6), ("少儿", 7),
                    ("文旅", 8), ("科教", 9)):
        if kw in name:
            priority = val
            break
    builder.add(name, "local", province, entry["url"], logo=entry["logo"],
                source=entry["source"], order=priority * 1000 + len(builder.channels) % 1000,
                raw=entry["raw"], key_name=name)
    return True


def process_fanmingming(builder, entries):
    for e in entries:
        if add_cctv(builder, e) or add_satellite(builder, e):
            continue


def process_vbsky(builder, entries, provinces):
    for e in entries:
        g = e["group"]
        if g == "央视频道":
            add_cctv(builder, e)
        elif g == "卫视频道":
            add_satellite(builder, e)
        elif g in ("地方频道", "数字频道"):
            prov = provinces.detect(e["name"])
            if prov:
                add_local(builder, e, prov)
            elif g == "地方频道":
                add_local(builder, e, "其他")
        elif g == "电影频道":
            if add_chc(builder, e):
                continue
            name = clean_name(e["name"])
            if name in THEMED_MOVIES:
                builder.add(name, "featured", "影视轮播", e["url"], logo=e["logo"],
                            source=e["source"], order=THEMED_MOVIES.index(name),
                            raw=e["raw"], key_name=name)
        elif g == "儿童频道":
            add_kids(builder, e)
        elif g == "体育频道":
            add_sports(builder, e)


def process_zbefine(builder, entries):
    for e in entries:
        g = e["group"]
        if add_chc(builder, e) or add_newtv(builder, e):
            continue
        if g == "央视":
            add_cctv(builder, e)
        elif strip_admin(g) in PROVINCE_GROUPS:
            if add_satellite(builder, e) or add_cctv(builder, e):
                continue
            add_local(builder, e, strip_admin(g))
        elif g == "网络":
            name = clean_name(e["name"])
            if name in ("黑莓电影", "纯享4K", "纯享超清"):
                display = "纯享4K" if "纯享" in name else name
                builder.add(display, "featured", "影视轮播", e["url"],
                            logo=e["logo"], source=e["source"], order=60,
                            raw=e["raw"], key_name=display)


def process_bigbig(builder, entries):
    province_groups = {
        "北京频道": "北京", "上海频道": "上海", "浙江频道": "浙江",
        "海南频道": "海南", "湖南频道": "湖南", "广东频道": "广东",
    }
    for e in entries:
        g = e["group"]
        if g == "央视频道":
            if add_chc(builder, e):
                continue
            add_cctv(builder, e)
        elif g == "卫视频道":
            add_satellite(builder, e)
        elif g in ("香港频道", "港澳台「P」", "•港澳台"):
            if not add_region(builder, e):
                name = clean_name(e["name"]).translate(TR2CN)
                if any(k in name for k in ("体育", "爱尔达", "博斯", "Eleven")):
                    add_sports(builder, e)
        elif g in province_groups:
            add_local(builder, e, province_groups[g])
        elif g in ("NewTV系列",):
            add_newtv(builder, e)
        elif g in ("IPV6·咪咕", "咪咕「移动」"):
            add_sports(builder, e)
        elif g == "IHOT":
            display = "iHOT·" + clean_name(e["name"]).replace("iHOT", "").strip()
            if display == "iHOT·":
                continue
            builder.add(display, "featured", "影视轮播", e["url"],
                        logo=e["logo"], source=e["source"], order=20,
                        raw=e["raw"], key_name=display)
        elif g == "•影视":
            name = clean_name(e["name"])
            if "解说" in name:
                display = name.replace("•", " ")
                builder.add(display, "featured", "影视轮播", e["url"],
                            logo=e["logo"], source=e["source"], order=50,
                            raw=e["raw"], key_name=display)
        elif g == "•少儿":
            add_kids(builder, e)
        elif g in ("求索系列",):
            name = clean_name(e["name"])
            if name.startswith("求索"):
                add_intl(builder, e)
        elif g in ("•国际", "国际频道"):
            add_intl(builder, e)
        elif g == "超清频道":
            name = clean_name(e["name"])
            if "纯享" in name:
                builder.add("纯享4K", "featured", "影视轮播", e["url"],
                            logo=e["logo"], source=e["source"], order=60,
                            raw=e["raw"], key_name="纯享4K")


def process_yang(builder, entries):
    for e in entries:
        g = e["group"]
        if g == "•咪咕「移动」":
            name = clean_name(e["name"])
            if "咪咕直播" in name or "睛彩竞技" in name or "睛彩篮球" in name:
                add_sports(builder, e)
        elif g == "•影视「轮播」":
            name = clean_name(e["name"])
            display = name.replace("「", "").replace("」", "").strip()
            if not display:
                continue
            builder.add(display, "featured", "影视轮播", e["url"],
                        logo=e["logo"], source=e["source"], order=55,
                        raw=e["raw"], key_name=display)


def process_epg(builder, entries):
    for e in entries:
        if add_region(builder, e):
            continue
        name = clean_name(e["name"]).translate(TR2CN)
        if "体育" in name or "爱尔达" in name or "博斯" in name or "Eleven" in name:
            add_sports(builder, e)


def process_iptv_org(builder, entries):
    for e in entries:
        if add_region(builder, e):
            continue
        if add_intl(builder, e):
            continue
        add_cctv(builder, e)


def process_aptv(builder, entries):
    for e in entries:
        if add_region(builder, e):
            continue
        if add_chc(builder, e):
            continue
        if "五星体育" in e["name"]:
            add_sports(builder, e, subgroup="国内体育", display="五星体育", order=0)


def build(args):
    if not args.offline:
        ensure_sources()
    entries = {}
    for key in ("fanmingming", "vbsky", "zbefine", "bigbig", "yang",
                "epg_hk", "epg_tw", "epg_mo", "aptv", "iptv_org"):
        path = CACHE / f"{key}.m3u"
        if not path.exists():
            print(f"[warn] missing {path}, skipping", file=sys.stderr)
            entries[key] = []
            continue
        entries[key] = parse_m3u(path, key)
        print(f"[load] {key}: {len(entries[key])} entries")
    provinces = ProvinceIndex(CACHE / "pca-code.json")

    builder = Builder()
    process_fanmingming(builder, entries["fanmingming"])
    process_vbsky(builder, entries["vbsky"], provinces)
    process_zbefine(builder, entries["zbefine"])
    process_bigbig(builder, entries["bigbig"])
    process_yang(builder, entries["yang"])
    process_epg(builder, entries["epg_hk"] + entries["epg_tw"] + entries["epg_mo"])
    process_iptv_org(builder, entries["iptv_org"])
    process_aptv(builder, entries["aptv"])

    channels = builder.final_channels()
    if not channels:
        raise SystemExit("catalog is empty")

    # 公开列表缺少稳定来源的地方台，用人工实测线路补齐。
    # 首条为已实测可播；其余为待用户在电视上验证的候选（本机构络测不通，多为省内运营商单播）。
    manual_streams = {
        "辽宁体育": [
            "http://120.76.248.139/live/bfgd/4200000611.m3u8",
            "http://httplive.slave.bfgd.com.cn:14311/playurl?playtype=live&protocol=http"
            "&accesstoken=R5F2408FEU3198804BK78052214IE73560DFP2BF4M340CE68V0Z339CBW1626D4D261E46FEA"
            "&playtoken=31608RMZHGPUCXX10&programid=4200000611",
            "http://39.134.61.218/PLTV/88888888/224/3221226916/index.m3u8",
            "http://hwottcdn.ln.chinamobile.com/PLTV/11/224/3221226764/index.m3u8",
            "http://39.135.129.162/hwottcdn.ln.chinamobile.com/PLTV/3/224/3221226764/index.m3u8",
        ],
    }
    # 本机实测端口不通/分片 404 的主机，公开列表里仍会带回来，按前缀剔除。
    dead_stream_prefixes = {
        "辽宁体育": (
            "http://59.44.102.18:8888/",
            "http://42.176.185.28:9901/",
            "http://dassby.qqff.top:99/",
        ),
    }
    for ch in channels:
        extra = manual_streams.get(ch["name"])
        if extra:
            ch["streams"] = list(dict.fromkeys(extra + ch["streams"]))[:8]
        dead = dead_stream_prefixes.get(ch["name"])
        if dead:
            ch["streams"] = [s for s in ch["streams"] if not s.startswith(dead)]

    present = defaultdict(set)
    for ch in channels:
        present[ch["category"]].add(ch["subgroup"])

    categories = []
    for cat in CATEGORY_ORDER:
        subs = [s for s in SUBGROUP_ORDER[cat] if s in present[cat]]
        if subs:
            categories.append({"id": cat, "name": CATEGORY_NAMES[cat], "subgroups": subs})

    catalog = {
        "schemaVersion": 1,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sourceNote": "频道与线路来自互联网公开 IPTV 列表，仅用于学习与个人测试；线路可能随时失效，应用会自动尝试备用线路。",
        "categories": categories,
        "channels": channels,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n[write] {OUT} ({OUT.stat().st_size / 1024:.0f} KiB)")
    print(f"[stats] {len(channels)} channels")
    for cat in CATEGORY_ORDER:
        counts = defaultdict(int)
        for ch in channels:
            if ch["category"] == cat:
                counts[ch["subgroup"]] += 1
        if counts:
            print(f"  {CATEGORY_NAMES[cat]}: {sum(counts.values())} -> " +
                  ", ".join(f"{k}:{v}" for k, v in counts.items()))

    required = [
        "CCTV-1 综合", "CCTV-5 体育", "CCTV-13 新闻", "CCTV-4K 超高清",
        "北京卫视", "东方卫视", "湖南卫视", "广东卫视", "深圳卫视",
        "翡翠台", "明珠台", "凤凰中文", "凤凰资讯", "TVBS新闻", "澳视澳门",
        "NewTV 超级电影", "CHC家庭影院", "CHC动作电影",
        "五星体育", "咪咕直播 4K-1", "纬来体育", "辽宁体育",
        "Discovery 探索频道", "BBC News BBC新闻", "FOX News 福克斯新闻",
        "CNN 国际新闻", "National Geographic 国家地理",
    ]
    names = {ch["name"] for ch in channels}
    missing = [n for n in required if n not in names]
    print("[check] missing:", missing if missing else "none")
    if missing:
        return 1
    return 0


def ensure_sources():
    CACHE.mkdir(parents=True, exist_ok=True)
    for key, url in SOURCES.items():
        name = "pca-code.json" if key == "pca" else f"{key}.m3u"
        path = CACHE / name
        if path.exists() and path.stat().st_size > 100:
            continue
        print(f"[fetch] {key}")
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                if len(data) > 100:
                    path.write_bytes(data)
                    break
            except Exception as exc:
                print(f"  attempt {attempt + 1} failed: {exc}", file=sys.stderr)
                time.sleep(2)
        else:
            print(f"[warn] could not download {key}", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use only cached playlists")
    raise SystemExit(build(ap.parse_args()))
