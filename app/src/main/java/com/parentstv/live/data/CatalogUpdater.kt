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
        val base = if (cached != null && notOlderThan(cached.generatedAt, assets.generatedAt)) cached else assets
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
        val merged = base.channels.map { channel ->
            val fresh = discovered[normalize(channel.name)].orEmpty()
            // 台标实测属于本台、却常被公开源挂到别名下的线，改挂进来并排在最前；
            // 新线路之间按 IPv6 优先排（见 CatalogMerge）。
            val lines = CatalogMerge.streams(channel.name, channel.streams, fresh)
            added += lines.count { kept ->
                channel.streams.none { StreamUrl.sameLine(it, kept) }
            }
            if (lines == channel.streams) {
                channel
            } else {
                changed = true
                channel.copy(streams = lines)
            }
        }

        // 用户 2026-10-22 要求：「立即更新」不只补地址，还要把线路可用性探一遍，
        // 探到能出画面的那条就排到最前。后台自动更新（force=false）不探，那是要在电视上默默跑的。
        // 探测范围只覆盖央视/卫视/辽宁体育：全目录 1515 条按 8 路并行要 20 分钟以上，
        // 这个按钮是按下去要看到结果的那一下，不能挂那么久。
        val outcomes = if (force) probeAvailability(merged.filter { isMustWatch(it) } ) else null
        val channels = if (outcomes == null) {
            merged
        } else {
            val probed = outcomes.associateBy { it.channel.id }
            merged.map { probed[it.id]?.channel ?: it }
        }

        // 只删不增也要落盘，否则清理掉的失效线路会一直留在旧缓存里。
        if (added > 0 || changed || outcomes != null) {
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
            probedLines = outcomes?.sumOf { it.tried },
            playableChannels = outcomes?.count { it.playable },
            probedChannels = outcomes?.size,
        )
    }

    /**
     * 逐频道探线路：一个频道内**串行**、第一条探通就停手；频道之间按 8 个一批并行。
     *
     * 频道内串行是必须的——并发探测会把慢源读成超时（10-21 实测同一台 25 分钟内三次读数会翻转），
     * 那样反而会把本来能看的线排到后面去。
     */
    private suspend fun probeAvailability(channels: List<TvChannel>): List<ProbeOutcome> =
        channels.chunked(PROBE_BATCH).flatMap { batch ->
            coroutineScope {
                batch.map { channel -> async(Dispatchers.IO) { probeChannel(channel) } }.awaitAll()
            }
        }

    private suspend fun probeChannel(channel: TvChannel): ProbeOutcome {
        var good = -1
        for (position in channel.streams.indices) {
            if (StreamProbe.probe(channel.streams[position]).playable) {
                good = position
                break
            }
        }
        if (good < 0) return ProbeOutcome(channel, channel.streams.size, false, false)
        val ordered = listOf(channel.streams[good]) +
            channel.streams.filterIndexed { position, _ -> position != good }
        return ProbeOutcome(channel.copy(streams = ordered), good + 1, true, ordered != channel.streams)
    }

    private data class ProbeOutcome(
        val channel: TvChannel,
        val tried: Int,
        val playable: Boolean,
        val reordered: Boolean,
    )

    /**
     * 「立即更新」这一档要探的频道范围。
     *
     * 口径与 `tools/prepack_report.py` 里的 `is_target()` 一致（央视 + 卫视 + 辽宁体育），
     * 两边不一致的话，设备上看到的线路次序和打包前那份报告就对不上表。
     */
    private fun isMustWatch(channel: TvChannel): Boolean =
        channel.subgroup == "央视" || channel.subgroup == "卫视" || channel.name == "辽宁体育"

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
                        if (StreamRules.neverPlayable(line) == null &&
                            urls.none { StreamUrl.sameLine(it, line) }
                        ) {
                            urls.add(line)
                        }
                    }
                    pendingName = null
                }

                line.isNotEmpty() && !line.startsWith("#") -> pendingName = null
            }
        }
    }

    private fun normalize(name: String): String = ChannelName.normalize(name)

    /** 两份目录的 generatedAt 是 ISO 串，写法可能一个是 Z、一个是 +08:00，按字典序比会反。
     *  能解析就换成毫秒比，解析不了才退回字典序。 */
    private fun notOlderThan(candidate: String, reference: String): Boolean {
        val left = BeijingTime.parseIso(candidate)
        val right = BeijingTime.parseIso(reference)
        return if (left != null && right != null) left >= right else candidate >= reference
    }

    data class UpdateResult(
        val addedLines: Int,
        val successSources: Int,
        val totalSources: Int,
        val lastUpdateAt: Long,
        val skipped: Boolean,
        val probedLines: Int? = null,
        val playableChannels: Int? = null,
        val probedChannels: Int? = null,
    ) {
        val message: String
            get() {
                val head = when {
                    skipped -> "刚刚已检查过，稍后再试"
                    successSources == 0 -> "公开源暂时都不可达，已保留现有线路"
                    addedLines == 0 -> "检查了 $successSources/$totalSources 个公开源，没有发现新线路"
                    else -> "已从 $successSources/$totalSources 个公开源补充 $addedLines 条新线路"
                }
                // 探测只在「立即更新」那一次跑，把探到多少条、多少个频道确认能出画面报出来，
                // 不然按现在的判据，界面上「可用」和「清单通」这两种绿是同一个颜色。
                val tail = if (probedChannels == null || probedLines == null) {
                    null
                } else {
                    "已检测央视/卫视/辽宁体育 $probedChannels 个频道、$probedLines 条线路，" +
                        "$playableChannels 个频道确认能取到画面并排到最前"
                }
                return if (tail == null) head else "$head · $tail"
            }
    }

    private companion object {
        const val CACHE_NAME = "channels_live.json"
        const val KEY_LAST_UPDATE = "last_update"
        const val KEY_LAST_ADDED = "last_added"
        const val UPDATE_INTERVAL_MS = 12L * 60 * 60 * 1000

        /** 「立即更新」里同时探线路时的并行频道数：太小时整轮要等十几分钟，太大慢源会被读成超时。 */
        const val PROBE_BATCH = 8

        val TVG_NAME = Regex("tvg-name=\"([^\"]*)\"")

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

/**
 * 单个频道的线路合并，抽成不依赖 Context 的纯函数：
 * 改挂规则是设备端自动更新里最后才生效的一环，光在真机上等一次「8 个源全不可达」是验不出来的。
 */
internal object CatalogMerge {

    /**
     * 改挂进本台的线排最前（台标实测过的那条），再依次是内置线路和公开源新发现的；
     * 新发现的这一批内部按 IPv6 优先（用户 2026-10-22 指令：家里网络有 IPv6 出口，
     * 而本机没有 v6 出口，v6 线路在本机永远只能读到「连不通」，不该按 IPv4 的次序压在后面）。
     */
    fun streams(channelName: String, packaged: List<String>, fresh: List<String>): List<String> =
        (RelabeledStreams.linesFor(channelName) + packaged +
            fresh.sortedByDescending { StreamUrl.isIpv6(it) })
            .filterNot { DeadStreams.isDead(it) }
            .filterNot { StreamRules.neverPlayable(it) != null }
            .filterNot { RelabeledStreams.belongsElsewhere(channelName, it) }
            .distinctBy { StreamUrl.canonical(it) }
}
