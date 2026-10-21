package com.parentstv.live.data

import android.content.Context
import android.util.Xml
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.xmlpull.v1.XmlPullParser
import java.io.File
import java.io.InputStream
import java.io.InputStreamReader
import java.io.PushbackInputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.Calendar
import java.util.TimeZone
import java.util.zip.GZIPInputStream

@Serializable
data class EpgProgram(val title: String, val start: Long, val stop: Long)

/**
 * 节目单快照。键是频道显示名的归一结果，值按开始时间排好序。
 * 只保留落进 [windowStart, windowEnd] 的节目：整份 xmltv 解开有 6 万多条，
 * 全塞进内存和缓存文件都没有意义，父母只需要「现在演什么＋今天还有什么」。
 */
@Serializable
data class EpgSnapshot(
    val fetchedAt: Long = 0L,
    val windowStart: Long = 0L,
    val windowEnd: Long = 0L,
    val entries: Map<String, List<EpgProgram>> = emptyMap(),
) {
    val isEmpty: Boolean
        get() = entries.isEmpty()

    fun covers(now: Long): Boolean = now in windowStart until windowEnd

    fun programsFor(name: String, now: Long = System.currentTimeMillis()): List<EpgProgram> {
        if (entries.isEmpty()) return emptyList()
        val key = ChannelName.lookupKeys(name).firstOrNull { entries.containsKey(it) } ?: return emptyList()
        return entries.getValue(key).filter { it.stop > now }
    }

    fun onAir(name: String, now: Long = System.currentTimeMillis()): EpgProgram? =
        programsFor(name, now).firstOrNull { it.start <= now }
}

object ChannelName {

    private val STRIP_WORDS = Regex("高清|超清|标清|准高清|频道|直播")
    private val SEPARATORS = Regex("[\\s\\-_·•|:：]")
    private val QUALITY_SUFFIXES = listOf("hd", "sd", "uhd", "fhd", "4k", "8k", "16k")
    private val CCTV_PATTERN = Regex("^cctv-?(\\d+)(.*)$")

    fun normalize(name: String): String =
        name.substringBefore('(')
            .substringBefore('（')
            .replace(STRIP_WORDS, "")
            .replace(SEPARATORS, "")
            .lowercase()

    /**
     * 一个频道名可能对应节目单里的多种写法，按优先级给出候选键。
     * 央视是主要场景：目录里叫「CCTV-1 综合」，节目单里多半只写「CCTV1」。
     */
    fun lookupKeys(name: String): List<String> {
        val normalized = normalize(name)
        val keys = LinkedHashSet<String>()
        keys.add(normalized)
        val match = CCTV_PATTERN.find(normalized)
        if (match != null) {
            val digits = match.groupValues[1].trimStart('0').ifEmpty { "0" }
            keys.add("cctv$digits")
            if (match.groupValues[2].isNotBlank()) keys.add("cctv$digits${match.groupValues[2]}")
        }
        keys.add(stripQualitySuffix(normalized))
        return keys.filter { it.isNotEmpty() }
    }

    private fun stripQualitySuffix(text: String): String =
        QUALITY_SUFFIXES.fold(text) { value, suffix ->
            if (value.length > suffix.length && value.endsWith(suffix)) value.removeSuffix(suffix) else value
        }
}

/** xmltv 时间形如 20261021193000 +0800，也可能省略偏移段（这时按北京时间读）。 */
object XmltvTime {

    fun parse(text: String?): Long? {
        val value = text?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        val digits = value.takeWhile { it.isDigit() }
        if (digits.length < 14) return null
        val offsetText = value.removePrefix(digits).trim()
        val offsetMinutes = if (offsetText.length == 5) {
            val sign = if (offsetText.startsWith("-")) -1 else 1
            val hours = offsetText.substring(1, 3).toIntOrNull() ?: return null
            val minutes = offsetText.substring(3, 5).toIntOrNull() ?: return null
            sign * (hours * 60 + minutes)
        } else {
            DEFAULT_OFFSET_MINUTES
        }
        val year = digits.substring(0, 4).toIntOrNull() ?: return null
        val month = digits.substring(4, 6).toIntOrNull() ?: return null
        val day = digits.substring(6, 8).toIntOrNull() ?: return null
        val hour = digits.substring(8, 10).toIntOrNull() ?: return null
        val minute = digits.substring(10, 12).toIntOrNull() ?: return null
        val second = digits.substring(12, 14).toIntOrNull() ?: return null
        return runCatching {
            val calendar = Calendar.getInstance(TimeZone.getTimeZone("UTC")).apply {
                isLenient = false
                clear()
                set(year, month - 1, day, hour, minute, second)
                set(Calendar.MILLISECOND, 0)
            }
            calendar.timeInMillis - offsetMinutes * 60_000L
        }.getOrNull()
    }

    private const val DEFAULT_OFFSET_MINUTES = 480
}

class EpgRepository(private val context: Context) {
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }
    private val cacheFile = File(context.filesDir, CACHE_NAME)
    private val prefs = context.getSharedPreferences("epg_updates", Context.MODE_PRIVATE)

    suspend fun snapshot(force: Boolean = false): EpgSnapshot = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        val cached = readCache()
        val stale = now - prefs.getLong(KEY_LAST_UPDATE, 0L) >= REFRESH_INTERVAL_MS
        val expired = cached == null || !cached.covers(now + LOOKAHEAD_MIN_HOURS * 60_000L)
        if (!force && cached != null && !stale && !expired) return@withContext cached

        val fresh = runCatching { fetchAndParse(now) }.getOrNull()
        when {
            fresh != null && fresh.entries.isNotEmpty() -> {
                writeCache(fresh)
                prefs.edit()
                    .putLong(KEY_LAST_UPDATE, now)
                    .putInt(KEY_PROGRAM_COUNT, fresh.entries.values.sumOf { it.size })
                    .apply()
                fresh
            }

            cached != null -> cached
            else -> {
                // 一次都没成功过也要记下时间，免得每次进播放器都重试一个当前网络拿不到的源。
                prefs.edit().putLong(KEY_LAST_UPDATE, now).apply()
                EpgSnapshot()
            }
        }
    }

    val programCount: Int
        get() = prefs.getInt(KEY_PROGRAM_COUNT, 0)

    private fun fetchAndParse(now: Long): EpgSnapshot? {
        val connection = (URL(SOURCE).openConnection() as HttpURLConnection).apply {
            connectTimeout = 10_000
            readTimeout = 30_000
            instanceFollowRedirects = true
            setRequestProperty(
                "User-Agent",
                "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36",
            )
        }
        return try {
            if (connection.responseCode !in 200..299) return null
            connection.inputStream.use { parse(it, now) }
        } finally {
            connection.disconnect()
        }
    }

    /**
     * 逐事件流式解析：整份 xmltv 解开 gzip 有 12 MB 左右，一次性读成字符串会在电视上占住内存。
     * channel 段先把「显示名 → 候选键」建好，programme 段按 channel 属性回填到每个键上。
     */
    private fun parse(stream: InputStream, now: Long): EpgSnapshot? {
        val windowStart = now - LOOKBACK_MS
        val windowEnd = now + LOOKAHEAD_MS
        val keysById = HashMap<String, MutableList<String>>()
        val programs = HashMap<String, MutableList<EpgProgram>>()

        val parser = Xml.newPullParser().apply {
            // 第三方源不给 DTD/外部实体处理开口子，避免解析器去联网取外部内容。
            setFeature(XmlPullParser.FEATURE_PROCESS_DOCDECL, false)
            setInput(InputStreamReader(openPossiblyGzipped(stream), Charsets.UTF_8))
        }

        var channelId = ""
        var programChannel = ""
        var programStart = 0L
        var programStop = 0L
        var inProgramme = false
        var title = ""
        var event = parser.eventType

        while (event != XmlPullParser.END_DOCUMENT) {
            when (event) {
                XmlPullParser.START_TAG -> when (parser.name) {
                    "channel" -> channelId = parser.getAttributeValue(null, "id").orEmpty()

                    "display-name" -> if (channelId.isNotEmpty()) {
                        val text = runCatching { parser.nextText() }.getOrNull()?.trim().orEmpty()
                        if (text.isNotEmpty()) {
                            val keys = keysById.getOrPut(channelId) { mutableListOf() }
                            ChannelName.lookupKeys(text).forEach { key ->
                                if (key.isNotEmpty() && key !in keys) keys.add(key)
                            }
                        }
                    }

                    "programme" -> {
                        inProgramme = true
                        programChannel = parser.getAttributeValue(null, "channel").orEmpty()
                        programStart = XmltvTime.parse(parser.getAttributeValue(null, "start")) ?: 0L
                        programStop = XmltvTime.parse(parser.getAttributeValue(null, "stop")) ?: 0L
                        title = ""
                    }

                    "title" -> if (inProgramme && title.isEmpty()) {
                        title = runCatching { parser.nextText() }.getOrNull()?.trim().orEmpty()
                    }
                }

                XmlPullParser.END_TAG -> if (parser.name == "programme") {
                    inProgramme = false
                    if (programStop > windowStart && programStart < windowEnd && title.isNotEmpty()) {
                        keysById[programChannel].orEmpty().forEach { key ->
                            programs.getOrPut(key) { mutableListOf() }
                                .add(EpgProgram(title, programStart, programStop))
                        }
                    }
                }
            }
            event = parser.next()
        }

        if (programs.isEmpty()) return null
        val entries = programs.mapValues { (_, list) ->
            list.sortedWith(compareBy({ it.start }, { it.title })).distinctBy { it.start to it.title }
        }
        return EpgSnapshot(now, windowStart, windowEnd, entries)
    }

    /** 服务端有时已经透明解压、有时直发 .gz，靠前两字节魔数决定要不要套一层 gunzip。 */
    private fun openPossiblyGzipped(stream: InputStream): InputStream {
        val pushed = PushbackInputStream(stream.buffered(), 2)
        val first = pushed.read()
        val second = pushed.read()
        if (second >= 0) pushed.unread(second)
        if (first >= 0) pushed.unread(first)
        return if (first == GZIP_MAGIC_0 && second == GZIP_MAGIC_1) GZIPInputStream(pushed) else pushed
    }

    private fun readCache(): EpgSnapshot? = cacheFile
        .takeIf { it.isFile && it.length() > 2 }
        ?.let { file ->
            runCatching { json.decodeFromString(EpgSnapshot.serializer(), file.readText()) }.getOrNull()
        }

    private fun writeCache(snapshot: EpgSnapshot) {
        val temp = File(context.filesDir, "$CACHE_NAME.tmp")
        temp.writeText(json.encodeToString(snapshot))
        runCatching { cacheFile.delete() }
        if (!temp.renameTo(cacheFile)) {
            temp.copyTo(cacheFile, overwrite = true)
            runCatching { temp.delete() }
        }
    }

    private companion object {
        const val SOURCE = "https://iptv.yang-1989.xyz/epg.xml.gz"
        const val CACHE_NAME = "epg_snapshot.json"
        const val KEY_LAST_UPDATE = "epg_last_update"
        const val KEY_PROGRAM_COUNT = "epg_program_count"
        const val REFRESH_INTERVAL_MS = 6L * 60 * 60 * 1000
        const val LOOKBACK_MS = 2L * 60 * 60 * 1000
        const val LOOKAHEAD_MS = 26L * 60 * 60 * 1000
        const val LOOKAHEAD_MIN_HOURS = 6L
        const val GZIP_MAGIC_0 = 0x1f
        const val GZIP_MAGIC_1 = 0x8b
    }
}
