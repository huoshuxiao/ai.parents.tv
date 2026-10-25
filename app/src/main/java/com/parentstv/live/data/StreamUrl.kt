package com.parentstv.live.data

/**
 * 线路地址的比较键。公开 IPTV 列表里同一条线路常有多种写法：中文路径有裸写和百分号转义、
 * 转义后的十六进制有大小写、主机名大小写不一、默认端口 :80/:443 可省、结尾多一个斜杠。
 * 按整串比较会把同一条地址当成两条，所以比较前先归一。
 * 归一只用于比较，请求时仍然用原始串。
 */
object StreamUrl {

    fun canonical(url: String): String {
        val raw = url.trim().removePrefix("\uFEFF")
        val schemeEnd = raw.indexOf("://")
        if (schemeEnd <= 0) return raw.lowercase()
        val scheme = raw.substring(0, schemeEnd).lowercase()
        var rest = raw.substring(schemeEnd + 3)
        val fragment = rest.indexOf('#')
        if (fragment >= 0) rest = rest.substring(0, fragment)

        val authorityEnd = if (rest.startsWith("[")) {
            val close = rest.indexOf(']')
            if (close < 0) return raw.lowercase()
            val next = rest.indexOfAny(charArrayOf('/', '?'), close)
            if (next < 0) rest.length else next
        } else {
            val next = rest.indexOfAny(charArrayOf('/', '?'))
            if (next < 0) rest.length else next
        }
        val authority = rest.substring(0, authorityEnd)
        val tail = rest.substring(authorityEnd)

        val at = authority.lastIndexOf('@')
        val userInfo = if (at >= 0) authority.substring(0, at + 1).lowercase() else ""
        val hostPort = if (at >= 0) authority.substring(at + 1) else authority

        val host: String
        val port: String?
        if (hostPort.startsWith("[")) {
            val close = hostPort.indexOf(']')
            if (close < 0) return raw.lowercase()
            host = hostPort.substring(0, close + 1)
            val after = hostPort.substring(close + 1)
            port = when {
                after.isEmpty() -> null
                after.length > 1 && after[0] == ':' && after.substring(1).all { it.isDigit() } -> after.substring(1)
                else -> return raw.lowercase()
            }
        } else {
            val colon = hostPort.indexOf(':')
            if (colon < 0) {
                host = hostPort
                port = null
            } else {
                host = hostPort.substring(0, colon)
                val declared = hostPort.substring(colon + 1)
                if (declared.isEmpty()) {
                    port = null
                } else if (!declared.all { it.isDigit() }) {
                    return raw.lowercase()
                } else {
                    port = declared
                }
            }
        }
        if (host.isEmpty()) return raw.lowercase()

        val defaultPort = when (scheme) {
            "http" -> "80"
            "https" -> "443"
            else -> null
        }
        val portText = if (port != null && port != defaultPort) ":$port" else ""
        val queryAt = tail.indexOf('?')
        val path = decodeEscapes(if (queryAt >= 0) tail.substring(0, queryAt) else tail)
            .removeTrailingSlash()
        // 空的 '?' 不携带任何值，带它和不带它是同一条线路；有值才保留（鉴权串靠它区分线路）。
        val query = if (queryAt >= 0) decodeEscapes(tail.substring(queryAt + 1)).takeIf { it.isNotEmpty() } else null
        return buildString {
            append(scheme).append("://").append(userInfo).append(host.lowercase().removeSuffix("."))
            append(portText).append(path)
            if (query != null) append('?').append(query)
        }
    }

    fun isIpv6(url: String): Boolean {
        val canonical = canonical(url)
        return canonical.startsWith("http://[") || canonical.startsWith("https://[")
    }

    fun sameLine(first: String, second: String): Boolean = canonical(first) == canonical(second)

    private fun String.removeTrailingSlash(): String =
        if (length > 1 && endsWith("/")) dropLast(1) else this

    /** 只解 %XX，不动 '+'（'+' 在路径里是字面量，在查询串里也按字面量比较更稳）。 */
    private fun decodeEscapes(text: String): String {
        if (!text.contains('%')) return text
        val out = StringBuilder(text.length)
        val pending = ArrayList<Byte>(4)
        var i = 0
        while (i < text.length) {
            val c = text[i]
            if (c == '%' && i + 2 < text.length && hexValue(text[i + 1]) >= 0 && hexValue(text[i + 2]) >= 0) {
                pending.add((hexValue(text[i + 1]) shl 4 or hexValue(text[i + 2])).toByte())
                i += 3
            } else {
                if (pending.isNotEmpty()) {
                    out.append(pending.toByteArray().toString(Charsets.UTF_8))
                    pending.clear()
                }
                out.append(c)
                i += 1
            }
        }
        if (pending.isNotEmpty()) out.append(pending.toByteArray().toString(Charsets.UTF_8))
        return out.toString()
    }

    private fun hexValue(c: Char): Int = when (c) {
        in '0'..'9' -> c - '0'
        in 'a'..'f' -> c - 'a' + 10
        in 'A'..'F' -> c - 'A' + 10
        else -> -1
    }
}

/**
 * 标清这一档的名单外那道闸（用户 10-25 指令「全频道删除标清线路，未来新增的线路也不添加标清线路」）。
 *
 * 分工：构建侧（tools/build_catalog.py 的 `is_sd_line`）按**实测尺寸**＋清单标题摘，摘完的地址随
 * `blockedLines` 打进 APK；设备端两道——
 * ① [isSdTitle] 在解析公开列表时就挡「标题自己写着标清／SD」的那一进目录；
 * ② [isSdResolution] 在「立即更新」探测那一刻挡「清单标题没写、但清单里的 RESOLUTION 只有标清档」的那一条
 *   （列表明天还会把这些地址换个台名再送一遍，光靠标题挡不住）。
 * 两处都只认独立一档的档名，不从 URL 里的 480/576 猜（那可能是端口或节目号）；
 * 尺寸那一道的阈值与构建侧 `SD_MAX_HEIGHT`/`SD_MIN_WIDTH`、屏幕上 `qualityText` 同一把尺。
 */
object Definition {

    private val sdTitle = Regex("""(^|[\s\-_/·(（\[])(标清|SD)($|[\s\-_/·)）\]])""", RegexOption.IGNORE_CASE)
    private val res = Regex("""(\d+)\s*[x×]\s*(\d+)""")

    /** 与 `tools/build_catalog.py` 的 SD_MAX_HEIGHT 同值：高到这一档以下算标清。 */
    private const val SD_MAX_HEIGHT = 699

    /** 与 `tools/build_catalog.py` 的 SD_MIN_WIDTH 同值：宽过这一档就不算标清（横屏高清优先）。 */
    private const val SD_MIN_WIDTH = 1200

    /** 清单标题里写着「标清／SD」这一档 ⇒ 这条线不往目录里加。 */
    fun isSdTitle(name: String): Boolean = sdTitle.containsMatchIn(name)

    /** 探测读到的最高一档是标清 ⇒ 这条线不留着。读不到尺寸（null）不算标清，别拿"没量到"当判坏。 */
    fun isSdResolution(resolution: String?): Boolean {
        val m = resolution?.let { res.find(it) } ?: return false
        val w = m.groupValues[1].toIntOrNull() ?: return false
        val h = m.groupValues[2].toIntOrNull() ?: return false
        return h in 1..SD_MAX_HEIGHT && w < SD_MIN_WIDTH
    }
}

/**
 * 实测连不通、公开源又会反复带回的线路。写原始写法即可，匹配走 [StreamUrl.canonical]，
 * 同一地址的转义/大小写/默认端口变体不用再各列一条。
 * 只按整条地址拉黑，不做主机级前缀拉黑：同主机其他节目号可能在不同网络上可用。
 *
 * 这一份是**证据黑名单盖不住**的两条（辽宁移动单播，10-21 现读是清单 404，目录里另有按名字的前缀剔除）：
 * 10-22 现读全部读数文件，对这两条只剩「连接超时」一种读数，而「本机连不通」按 10-21 选项A 是不拉黑的，
 * 所以它们只能继续手抄。目录生成时按实测攒出来的那一大份走 [ChannelCatalog.blockedLines]，
 * 合并时两处一起挡（见 CatalogMerge.streams）。
 */
object DeadStreams {

    private val keys: Set<String> = listOf(
        "http://59.44.102.18:8888/newlive/live/hls/45/live.m3u8",
        "http://42.176.185.28:9901/tsfile/live/1007_1.m3u8",
    ).mapTo(mutableSetOf()) { StreamUrl.canonical(it) }

    fun isDead(stream: String): Boolean = StreamUrl.canonical(stream) in keys
}

/**
 * 挂在 A 台名下、抽帧认台标其实是 B 台画面的线。公开源会反复把它带回错的频道，
 * 只改内置目录挡不住下一次自动更新（设备上 00:04 现读：这条线仍在「西藏卫视」第 1 条），
 * 所以合并时按规范地址改挂到真正的频道，并放在最前面。
 *
 * 这里只留 10-21/10-24 手抄的那几条历史；新的「按台标改挂」结论随目录走
 * （[ChannelCatalog.lineMoves]，构建层按台标打分＋人工看单帧复核后写入），查的时候那份优先。
 */
object RelabeledStreams {

    /** 原始写法 → 台标实测真正的频道名。匹配走 [StreamUrl.canonical]，转义/查询串变体一并认。 */
    private val targets: List<Pair<String, String>> = listOf(
        // 10-21 21:31 与 22:31 两次抽帧，画面左上角都是「四川卫视」台标。
        "http://112.27.5.218:9901/tsfile/live/0111_1.m3u8?key=txiptv&playlive=1&authid=0" to "四川卫视",
        // 10-24 16:5x「同一枚帧被两个台名认领」全量扫出来的三对（用户点档 #96 乙 之后从目录里删了错的那台，
        // 这里补上改挂表：公开源下次把它们带回来时，错的台名摘掉、对的台名接住）。
        // ① 该址 11 枚唯一帧看过 9 枚＋16:44 复抽 3 枚全是西宁市台（XNTV 色卡／「新闻综合」旋标／《夏都零距离》）。
        "https://liveout.xntv.tv/a65jur/96iln2.m3u8" to "西宁新闻综合",
        // ② 该址 4 枚帧是井研的橙色火焰「新闻综合」标（域名 jyrmt.cn＝井研融媒体中心）。
        "http://tvfile.jyrmt.cn/nmip-media/channellive/channel104452/playlist.m3u8" to "井研综合",
        // ③ 该址唯一一枚帧左上角写「营山」（19 处登记 md5 全同＝一次观测）；`#` 后那半截是列表粘串，canonical 会剥掉。
        "http://file.ysxtv.cn/cms/videos/nmip-media/channellive/channel4/playlist.m3u8?zsicd" to "营山电视台",
    )

    private fun keyOf(url: String) = StreamUrl.canonical(url)

    private val byKey: Map<String, String> = targets.associate { (url, channel) -> keyOf(url) to channel }

    /** 这条地址真身属于哪个频道；null 表示没有改挂记录。
     *
     *  目录里那份（`lineMoves`，构建按台标证据算出来的）优先于下面手抄的 `targets`：
     *  用户 10-25「卫视频道，台标与频道不一致的，根据台标移入对应卫视频道」之后，
     *  新的改挂结论一律随 channels.json 走，`targets` 只留 10-21/10-24 那几条历史。
     */
    fun trueChannel(stream: String, catalogMoves: Map<String, LineMove> = emptyMap()): String? =
        catalogMoves[StreamUrl.canonical(stream)]?.to ?: byKey[keyOf(stream)]

    /** 在 [channelName] 名下看到它就该摘掉——它出的是别的台的画面。 */
    fun belongsElsewhere(channelName: String, stream: String,
                         catalogMoves: Map<String, LineMove> = emptyMap()): Boolean {
        val owner = trueChannel(stream, catalogMoves) ?: return false
        return ChannelName.normalize(owner) != ChannelName.normalize(channelName)
    }

    /** 该改挂进 [channelName] 的线路（保留原始写法，排在频道线路列表最前）。 */
    fun linesFor(channelName: String): List<String> = targets
        .filter { ChannelName.normalize(it.second) == ChannelName.normalize(channelName) }
        .map { it.first }
}

/**
 * 不联网也能断定播不出来的三类：ExoPlayer 没有对应解复用器的容器、鉴权参数绑死在
 * 别人的机顶盒或手机号上、URL 里写死的过期时刻已经过去。
 * 判据与 tools/stream_rules.py 逐条对齐，对拍夹具是 app/src/test/resources/stream_rules.json。
 */
object StreamRules {

    private val unsupportedExt = setOf("flv", "rmvb", "asf", "wmv", "avi", "mkv")
    private val unboundValues = setOf("unknown", "0", "")

    private val deviceKey = Regex(
        "[?&]([a-zA-Z_]*(?:mac|msisdn|serialnumber|devid|deviceid)[a-zA-Z_]*)=([^&;]*)",
        RegexOption.IGNORE_CASE,
    )
    private val stampTtl = Regex("[^0-9a-zA-Z](1[6-9]\\d{8})_(\\d{1,9})(?:[^0-9]|$)")
    private val expireKey = Regex("(?:expire|expired|deadline|end_time|endtime|validuntil|valid)=?(1[6-9]\\d{8})")

    /** 返回不行的原因；null 表示这条地址值得真去探一次。 */
    fun neverPlayable(url: String, nowEpochSeconds: Long = System.currentTimeMillis() / 1000): String? = when {
        unsupportedContainer(url) -> "容器不支持"
        deviceBound(url) -> "设备绑定"
        tokenExpired(url, nowEpochSeconds) -> "鉴权已过期"
        else -> null
    }

    fun unsupportedContainer(url: String): Boolean {
        val path = url.substringBefore('?').substringBefore('#').lowercase()
        val dot = path.lastIndexOf('.')
        return dot >= 0 && dot < path.length - 1 && path.substring(dot + 1) in unsupportedExt
    }

    fun deviceBound(url: String): Boolean =
        deviceKey.findAll(url).any { it.groupValues[2].trim().lowercase() !in unboundValues }

    fun tokenExpired(url: String, nowEpochSeconds: Long): Boolean {
        stampTtl.findAll(url).forEach {
            val expiresAt = it.groupValues[1].toLong() + it.groupValues[2].toLong()
            if (expiresAt < nowEpochSeconds) return true
        }
        expireKey.findAll(url).forEach {
            if (it.groupValues[1].toLong() < nowEpochSeconds) return true
        }
        return false
    }
}
