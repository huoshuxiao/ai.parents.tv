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
        val repository = CatalogRepository(context)
        val assets = repository.loadAssets()
        val cached = repository.loadCacheOrNull()
        // 应用更新后内置清单会比缓存新：这时要以内置清单为底重新拼，
        // 否则旧缓存会一直盖住新加的线路，清理和补充都落不到设备上。
        // 这一步必须在 12 小时节流**之前**做：装 APK 不会重置 lastUpdateAt，
        // 10-23 13:46 装机后首页仍显示旧目录 44/15，就是被这个节流整个跳过了。
        val assetsNewer = cached == null || !notOlderThan(cached.generatedAt, assets.generatedAt)
        val stale = isStale(now)
        if (!force && !stale && !assetsNewer) {
            return@withContext UpdateResult(
                addedLines = 0,
                successSources = 0,
                totalSources = SOURCES.size,
                lastUpdateAt = lastUpdateAt,
                skipped = true,
            )
        }

        val base = if (cached != null && !assetsNewer) cached else assets
        // 只是内置清单变新、又没到自动更新时间：只做本地换底落盘，不发网络请求。
        val localOnly = !force && !stale
        val fetched = if (localOnly) {
            emptyList()
        } else {
            coroutineScope {
                SOURCES.map { mirrors ->
                    async(Dispatchers.IO) { fetchAny(mirrors) }
                }.awaitAll()
            }
        }
        val success = fetched.count { it != null }
        if (fetched.isNotEmpty() && success == 0 && !assetsNewer) {
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
        val settings = TvPrefs(context)
        // 目录生成时实测判坏的那些地址（随 channels.json 一起装进 APK）。
        // 公开源下一次还会把它们带回来，合并时按黑名单挡掉——这是「可用性优于数量」在设备端的那一环。
        val blocked = base.blockedLines.keys
        val merged = base.channels.mapNotNull { channel ->
            val fresh = discovered[normalize(channel.name)].orEmpty()
            // 台标实测属于本台、却常被公开源挂到别名下的线，改挂进来并排在最前；
            // 新线路之间按 IPv6 优先排；这台电视真出过画面的那条线顶到第一条（见 CatalogMerge）。
            val lines = CatalogMerge.streams(channel.name, channel.streams, fresh,
                settings.lineFor(channel.id), blocked)
            added += lines.count { kept ->
                channel.streams.none { StreamUrl.sameLine(it, kept) }
            }
            when {
                // 挡完一条都不剩：目录侧的规则是「线路清空＝整个台消失」，设备端要一致，
                // 否则 files/channels_live.json 里会留下一个界面看不见、却占着一个台位的空壳
                //（10-22 模拟器现读：番禺综合 落盘时连 streams 键都没有，界面数 773、文件数 774）。
                lines.isEmpty() -> null
                lines == channel.streams -> channel
                else -> {
                    changed = true
                    channel.copy(streams = lines)
                }
            }
        }

        // 用户 2026-10-22 要求：「立即更新」不只补地址，还要把线路可用性探一遍，
        // 探到能出画面的那条就排到最前。后台自动更新（force=false）不探，那是要在电视上默默跑的。
        // 预算按「这台缺不缺能播的线」分配（用户 2026-10-22：线路可用性优于频道数量）：
        // 必看台先探，接着探**这台电视上从没出过画面**的台，看过的台不占预算；仍然限时，
        // 超预算的台留到下一次按按钮，不然这一下要挂十几分钟。
        val targets = probeTargets(merged, settings)
        val outcomes = if (force) probeAvailability(targets) { channel -> settings.lineFor(channel.id) } else null
        val probeSkipped = outcomes?.let { targets.size - it.size }
        val channels = if (outcomes == null) {
            merged
        } else {
            val probed = outcomes.associateBy { it.channel.id }
            merged.map { probed[it.id]?.channel ?: it }
        }

        // 只删不增也要落盘，否则清理掉的失效线路会一直留在旧缓存里。
        // 「以内置清单为底重拼过」（`base === assets`：装了新版本、内置清单比缓存新）也必须落盘——
        // 界面读的是缓存文件，光在内存里换底不换文件，旧缓存里那些新版本已经判坏的线路一条都没被清掉。
        // （10-22 现读到的形状：升级后若公开源这一轮没补新线、次序也没变，以前这一格直接跳过写盘，
        //   脏缓存原样留在设备上，界面上的坏线一根没少。）
        if (added > 0 || changed || outcomes != null || base === assets) {
            val updated = base.copy(channels = channels)
            cacheFile.writeText(json.encodeToString(updated))
        }
        val replaced = base === assets
        val editor = prefs.edit().putInt(KEY_LAST_ADDED, added)
        // 只做内置目录换底时不算一次「公开源更新」，别把 12 小时计时器一起重置，
        // 否则装了新包就把本该到点的联网更新往后推 12 小时。
        if (!localOnly) editor.putLong(KEY_LAST_UPDATE, now)
        editor.apply()

        UpdateResult(
            addedLines = added,
            successSources = success,
            totalSources = SOURCES.size,
            lastUpdateAt = if (localOnly) lastUpdateAt else now,
            skipped = false,
            probedLines = outcomes?.sumOf { it.tried },
            playableChannels = outcomes?.count { it.playable },
            probedChannels = outcomes?.size,
            probeSkipped = probeSkipped,
            catalogChanged = replaced || added > 0 || changed,
            localOnly = localOnly,
        )
    }

    /**
     * 逐频道探线路：一个频道内**串行**、第一条探通就停手；频道之间按 8 个一批并行。
     *
     * 频道内串行是必须的——并发探测会把慢源读成超时（10-21 实测同一台 25 分钟内三次读数会翻转），
     * 那样反而会把本来能看的线排到后面去。
     */
    private suspend fun probeAvailability(channels: List<TvChannel>,
                                          played: (TvChannel) -> String?): List<ProbeOutcome> {
        val deadline = System.currentTimeMillis() + PROBE_BUDGET_MS
        val done = ArrayList<ProbeOutcome>()
        for (batch in channels.chunked(PROBE_BATCH)) {
            // 第一批无条件跑（不然一条都没探就成了「没探」）；之后每批起头看一眼预算。
            if (done.isNotEmpty() && System.currentTimeMillis() >= deadline) break
            done += coroutineScope {
                batch.map { channel -> async(Dispatchers.IO) { probeChannel(channel, played(channel)) } }
                    .awaitAll()
            }
        }
        return done
    }

    /**
     * 「立即更新」这一档要探的频道，按**缺不缺能播的线**排，不按分类排：
     * 必看台（央视/卫视/辽宁体育）先探，然后是这台电视上从没出过画面的台，
     * 已经看过的台（[TvPrefs.lineFor] 有记录）不占预算。上限 [PROBE_LIMIT] 个。
     */
    private fun probeTargets(channels: List<TvChannel>, prefs: TvPrefs): List<TvChannel> {
        val must = channels.filter { isMustWatch(it) }
        val blind = channels.filter { !isMustWatch(it) && prefs.lineFor(it.id).isNullOrBlank() }
        return (must + blind).take(PROBE_LIMIT)
    }

    private suspend fun probeChannel(channel: TvChannel, played: String?): ProbeOutcome {
        var good = -1
        for (position in channel.streams.indices) {
            if (StreamProbe.probe(channel.streams[position]).playable) {
                good = position
                break
            }
        }
        if (good < 0) return ProbeOutcome(channel, channel.streams.size, false, false)
        // 探到的那条排到最前，但**这台电视真播过的那条更靠前**（用户 2026-10-21 指令：
        // 已播过的线路绝不自动换线/换台）。以前这里直接把探到的第一条顶上去，
        // 于是按一次「立即更新」就可能把父母天天看的那条线从第 1 位挪走——
        // 播放中确实不换线（LineRecovery），但下次进这个台要先试的就是别的地址了。
        val ordered = CatalogMerge.probeOrder(channel.streams, good, played)
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
        val probeSkipped: Int? = null,
        // 这一轮有没有把设备上的目录文件换掉（内置清单换底、补线、删线、重排都算）。
        // 界面以前只在 addedLines>0 时重新读盘，本地换底和只删不增的情况会被漏掉。
        val catalogChanged: Boolean = false,
        val localOnly: Boolean = false,
    ) {
        val message: String
            get() {
                val head = when {
                    skipped -> "刚刚已检查过，稍后再试"
                    localOnly -> "已应用新版内置目录（未联网补充线路）"
                    successSources == 0 -> "公开源暂时都不可达，已保留现有线路"
                    addedLines == 0 -> "检查了 $successSources/$totalSources 个公开源，没有发现新线路"
                    else -> "已从 $successSources/$totalSources 个公开源补充 $addedLines 条新线路"
                }
                // 探测只在「立即更新」那一次跑，把探到多少条、多少个频道确认能出画面报出来，
                // 不然按现在的判据，界面上「可用」和「清单通」这两种绿是同一个颜色。
                val tail = if (probedChannels == null || probedLines == null) {
                    null
                } else {
                    val base = "已检测 $probedChannels 个频道（必看台 + 这台没出过画面的台）、" +
                        "$probedLines 条线路，$playableChannels 个频道确认能取到画面并排到最前"
                    if (probeSkipped != null && probeSkipped > 0) {
                        "$base；还有 $probeSkipped 个台没排上，下次再按一次"
                    } else base
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

        /** 一次「立即更新」最多探多少个台（必看台 + 没出过画面的台，按这个顺序截断）。 */
        const val PROBE_LIMIT = 200

        /** 探测预算：到点就把剩下的台留到下一次按按钮，别让一个按钮挂十几分钟。 */
        const val PROBE_BUDGET_MS = 90_000L

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
    fun streams(channelName: String, packaged: List<String>, fresh: List<String>,
                playable: String? = null, blocked: Set<String> = emptySet()): List<String> =
        putPlayableFirst(
            (RelabeledStreams.linesFor(channelName) + packaged +
                fresh.sortedByDescending { StreamUrl.isIpv6(it) })
                .filterNot { DeadStreams.isDead(it) || isBlocked(it, blocked, playable) }
                .filterNot { StreamRules.neverPlayable(it) != null }
                .filterNot { RelabeledStreams.belongsElsewhere(channelName, it) }
                .distinctBy { StreamUrl.canonical(it) },
            playable,
        )

    /**
     * 目录证据判坏的地址：公开源下一次还会把它带回来，合并时按归一键挡掉。
     *
     * 「这台电视真出过画面」的那条**例外**（用户 2026-10-22 指令：已播过的线路绝不自动换线/换台）——
     * 这台机器（与电视同一个宽带、有 IPv6 出口）抽帧看到的坏，抵不过那台电视真播出来过一副画面，那种线留着。
     */
    fun isBlocked(stream: String, blocked: Set<String>, played: String? = null): Boolean {
        if (StreamUrl.canonical(stream) !in blocked) return false
        return played.isNullOrBlank() || !StreamUrl.sameLine(stream, played)
    }

    /**
     * 「立即更新」探测之后怎么排这一台的线路：探到的那条上到第 1 位，
     * 但这台电视真播过（或用户手工选过）的那条永远压在它前面。
     *
     * 次序反过来就是 bug：探测是在这台机器的网络上看到的，而播过那条线是在父母眼前出过画面的。
     */
    fun probeOrder(streams: List<String>, goodIndex: Int, played: String?): List<String> =
        putPlayableFirst(
            listOf(streams[goodIndex]) + streams.filterIndexed { position, _ -> position != goodIndex },
            played,
        )

    /**
     * 把这台电视**真出过画面**（或用户手工选过）的那条线顶到第一条。
     *
     * 「线路可用性优于频道数量」（用户 2026-10-22 指令）：设备上验过的线比目录里排头的
     * 没验过的线值钱，所以它一旦存在就排最前，连改挂那条也让在它后面
     * ——我这台机器上抽帧认过台标，不等于那台电视当时真播得出来。
     * 写法不同但归一后是同一条也算命中；没命中或没给验过的线就原样返回。
     */
    fun putPlayableFirst(streams: List<String>, playable: String?): List<String> {
        if (playable.isNullOrBlank()) return streams
        val index = streams.indexOfFirst { StreamUrl.sameLine(it, playable) }
        if (index <= 0) return streams
        return listOf(streams[index]) + streams.filterIndexed { position, _ -> position != index }
    }
}
