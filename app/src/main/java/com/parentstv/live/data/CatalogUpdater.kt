package com.parentstv.live.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

class CatalogUpdater(private val context: Context) {
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }
    private val cacheFile = File(context.filesDir, CACHE_NAME)
    private val prefs = context.getSharedPreferences("catalog_updates", Context.MODE_PRIVATE)

    val lastUpdateAt: Long
        get() = prefs.getLong(KEY_LAST_UPDATE, 0L)

    fun isStale(now: Long = System.currentTimeMillis()): Boolean =
        now - lastUpdateAt >= UPDATE_INTERVAL_MS

    suspend fun refresh(force: Boolean): UpdateResult = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        if (!force && !isStale(now)) {
            return@withContext UpdateResult(
                addedLines = 0,
                successSources = 0,
                totalSources = SOURCES.size,
                lastUpdateAt = lastUpdateAt,
                skipped = true,
            )
        }

        val repository = CatalogRepository(context)
        val assets = repository.loadAssets()
        val cached = repository.loadCacheOrNull()
        // 应用更新后内置清单会比缓存新：这时要以内置清单为底重新拼，
        // 否则旧缓存会一直盖住新加的线路，清理和补充都落不到设备上。
        val base = if (cached != null && cached.generatedAt >= assets.generatedAt) cached else assets
        val fetched = coroutineScope {
            SOURCES.map { mirrors ->
                async(Dispatchers.IO) { fetchAny(mirrors) }
            }.awaitAll()
        }
        val success = fetched.count { it != null }
        if (success == 0) {
            return@withContext UpdateResult(
                addedLines = 0,
                successSources = 0,
                totalSources = SOURCES.size,
                lastUpdateAt = lastUpdateAt,
                skipped = false,
            )
        }

        val discovered = mutableMapOf<String, MutableList<String>>()
        fetched.filterNotNull().forEach { parseM3u(it, discovered) }

        var added = 0
        var changed = false
        val channels = base.channels.map { channel ->
            val fresh = discovered[normalize(channel.name)].orEmpty()
                .sortedBy { it.startsWith("http://[") }
            val merged = (channel.streams + fresh)
                .filterNot { isDeadStream(it) }
                .distinct()
                .take(MAX_STREAMS_PER_CHANNEL)
            added += (merged - channel.streams.toSet()).size
            if (merged == channel.streams) {
                channel
            } else {
                changed = true
                channel.copy(streams = merged)
            }
        }

        // 只删不增也要落盘，否则清理掉的失效线路会一直留在旧缓存里。
        if (added > 0 || changed) {
            val updated = base.copy(channels = channels)
            cacheFile.writeText(json.encodeToString(updated))
        }
        prefs.edit()
            .putLong(KEY_LAST_UPDATE, now)
            .putInt(KEY_LAST_ADDED, added)
            .apply()

        UpdateResult(
            addedLines = added,
            successSources = success,
            totalSources = SOURCES.size,
            lastUpdateAt = now,
            skipped = false,
        )
    }

    private fun fetchAny(mirrors: List<String>): String? {
        for (url in mirrors) {
            val text = fetch(url)
            if (!text.isNullOrBlank()) return text
        }
        return null
    }

    private fun fetch(url: String): String? {
        var connection: HttpURLConnection? = null
        return try {
            connection = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 8_000
                readTimeout = 15_000
                instanceFollowRedirects = true
                setRequestProperty(
                    "User-Agent",
                    "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36",
                )
            }
            if (connection.responseCode !in 200..299) return null
            val bytes = connection.inputStream.use { it.readBytes() }
            if (bytes.size < 100 || bytes.size > 4_000_000) return null
            String(bytes, Charsets.UTF_8)
        } catch (_: Throwable) {
            null
        } finally {
            connection?.disconnect()
        }
    }

    private fun parseM3u(text: String, out: MutableMap<String, MutableList<String>>) {
        var pendingName: String? = null
        for (raw in text.lineSequence()) {
            val line = raw.trim().removePrefix("\uFEFF")
            when {
                line.startsWith("#EXTINF", ignoreCase = true) -> {
                    val display = line.substringAfterLast(',', "").trim()
                    val tvgName = TVG_NAME.find(line)?.groupValues?.getOrNull(1).orEmpty()
                    pendingName = display.ifEmpty { tvgName }.takeIf { it.isNotEmpty() }
                }

                line.startsWith("http://", true) || line.startsWith("https://", true) -> {
                    val name = pendingName
                    if (name != null) {
                        val urls = out.getOrPut(normalize(name)) { mutableListOf() }
                        if (urls.size < MAX_DISCOVERED_PER_NAME && line !in urls) urls.add(line)
                    }
                    pendingName = null
                }

                line.isNotEmpty() && !line.startsWith("#") -> pendingName = null
            }
        }
    }

    private fun normalize(name: String): String =
        name.substringBefore('(')
            .substringBefore('（')
            .replace("高清", "")
            .replace("超清", "")
            .replace("频道", "")
            .replace(NAME_SEPARATORS, "")
            .lowercase()

    // 实测连不通（TCP 不通或清单有但分片 404）的线路，公开源会反复带回。按整串匹配，不做转义解码。
    private fun isDeadStream(stream: String): Boolean = stream.trim() in DEAD_STREAMS

    data class UpdateResult(
        val addedLines: Int,
        val successSources: Int,
        val totalSources: Int,
        val lastUpdateAt: Long,
        val skipped: Boolean,
    ) {
        val message: String
            get() = when {
                skipped -> "刚刚已检查过，稍后再试"
                successSources == 0 -> "公开源暂时都不可达，已保留现有线路"
                addedLines == 0 -> "检查了 $successSources/$totalSources 个公开源，没有发现新线路"
                else -> "已从 $successSources/$totalSources 个公开源补充 $addedLines 条新线路"
            }
    }

    private companion object {
        const val CACHE_NAME = "channels_live.json"
        const val KEY_LAST_UPDATE = "last_update"
        const val KEY_LAST_ADDED = "last_added"
        const val UPDATE_INTERVAL_MS = 12L * 60 * 60 * 1000
        const val MAX_STREAMS_PER_CHANNEL = 8
        const val MAX_DISCOVERED_PER_NAME = 8

        val TVG_NAME = Regex("tvg-name=\"([^\"]*)\"")
        val NAME_SEPARATORS = Regex("[\\s\\-_·•|]+")

        val DEAD_STREAMS = setOf(
            "http://59.44.102.18:8888/newlive/live/hls/45/live.m3u8",
            "http://42.176.185.28:9901/tsfile/live/1007_1.m3u8",
            "http://dassby.qqff.top:99/live/辽宁体育/index.m3u8",
            "http://dassby.qqff.top:99/live/%E8%BE%BD%E5%AE%81%E4%BD%93%E8%82%B2/index.m3u8",
        )

        val SOURCES = listOf(
            listOf(
                "https://cdn.jsdelivr.net/gh/XiaoZhang5656/xiaozhang-5656.github.io@main/iptv-live.txt",
                "https://raw.githubusercontent.com/XiaoZhang5656/xiaozhang-5656.github.io/main/iptv-live.txt",
            ),
            listOf("https://raw.githubusercontent.com/suxuang/myIPTV/main/ipv4.m3u"),
            listOf("https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/index.m3u"),
            listOf("https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u"),
            listOf("https://raw.githubusercontent.com/zbefine/iptv/main/iptv.m3u"),
            listOf("https://raw.githubusercontent.com/zhanghong1983/TVBOXZY/main/tv/live.txt"),
            listOf("https://raw.githubusercontent.com/YuanHsing/freed/master/tv/live.txt"),
        )
    }
}
