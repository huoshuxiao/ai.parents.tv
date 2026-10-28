package com.parentstv.live.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
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
        // 包内目录新增的线：即便两份目录的 `generatedAt` 相等（10-28 现读那份就是这样，
        // 内容改过、时间戳没动），装新包这一下也要把它们落到设备上，否则新线永远上不了电视。
        val assetExtra: Map<String, List<String>> = cached?.let { cache ->
            assets.channels.mapNotNull { asset ->
                val cachedStreams = cache.channels.firstOrNull { it.id == asset.id }?.streams
                    ?: return@mapNotNull null
                val extras = CatalogMerge.extrasInAssetOnly(asset.streams, cachedStreams)
                if (extras.isEmpty()) null else asset.id to extras
            }.toMap()
        } ?: emptyMap()
        val settings = TvPrefs(context)
        // 包内黑名单已经判死、设备缓存却还在用的地址：判死的线也要能落到设备上（见 blockedInUseCount）。
        val blockedInUse: Int = cached?.let { cache ->
            val assetBlocked = assets.blockedLines.keys
            cache.channels.sumOf { ch ->
                CatalogMerge.blockedInUseCount(ch.streams, assetBlocked)
            }
        } ?: 0
        if (!force && !stale && !assetsNewer && assetExtra.isEmpty() && blockedInUse == 0) {
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
        // 目录生成时实测判坏的那些地址（随 channels.json 一起装进 APK）。
        // 公开源下一次还会把它们带回来，合并时按黑名单挡掉——这是「可用性优于数量」在设备端的那一环。
        // 10-28：**只认包内那一份**。①只取 `base.blockedLines` 时，两份目录 generatedAt 相同 ⇒ base 就是缓存
        // ⇒ APK 新判死的地址永远不生效（现读：装机后一条没摘）；②取 base ∪ assets 又会把**缓存里的旧拉黑**
        // 复活（黑龙江卫视 idclive、浙江卫视 ali-m-l 这两条「复活两源」当年被拉黑过，缓存里还留着，
        // 一并集就把刚从资产里救回来的线又挡掉了）。资产是随包发布的当前裁决，缓存只是它的旧快照。
        val blocked = assets.blockedLines.keys
        // 用户 10-28 点档「乙」：公开源新发现的地址不再直接进目录，必须先出现在打进 APK 的
        // `line_allowlist.json`（由 tools/vet_line_candidates.py 探过清单/分片/画面才写）；
        // 不在白名单的一律写进 files/line_candidates.json 等下一轮过闸。
        val allowlist = runCatching {
            context.assets.open(ALLOWLIST_ASSET).bufferedReader().use { it.readText() }
        }.map { LineAllowlist.parse(it) }.getOrDefault(emptyList())
        val candidatesFound = mutableListOf<CandidateLine>()
        // 构建按台标改挂过的那些地址（台标证据在 tools/wshi_logo_moves.json，理由写在每条记录里）。
        val moves = base.lineMoves
        val merged = base.channels.mapNotNull { channel ->
            val (fresh, notAllowlisted) = LineAllowlist.admit(
                discovered[normalize(channel.name)].orEmpty(), allowlist,
            )
            // 只记「目录里还没有、又没过闸」的那些；公开源照例会把已有地址再送一遍，别当成候选。
            notAllowlisted.forEach { url ->
                if (channel.streams.none { StreamUrl.sameLine(it, url) }) {
                    candidatesFound += CandidateLine(channel.name, url)
                }
            }
            // 台标实测属于本台、却常被公开源挂到别名下的线，改挂进来并排在最前；
            // 新线路之间按 IPv6 优先排；这台电视真出过画面的那条顶到第一条——
            // 但央视·卫视与地方频道两组除外（用户 2026-10-25：清晰度排序压过播过置顶），
            // 那两组按打进 APK 的那一份目录复位，连旧版本已经置顶在缓存里的那条也一并归位。
            val lines = CatalogMerge.streams(channel.name, channel.streams,
                fresh + assetExtra[channel.id].orEmpty(),
                settings.lineFor(channel.id), blocked, moves, channel.category,
                (assets.channels.firstOrNull { it.id == channel.id }?.streams ?: channel.streams))
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
        // 包内新增的「整台」：`base`（设备缓存）里没有的，按 id 补进来。
        // 10-28 现读：包内 278 台、设备在用 277 台，缺的正是新建的河北卫视——
        // 上面那段只遍历 `base.channels`，于是「线能加、台加不进来」。
        val knownIds = base.channels.mapTo(HashSet()) { it.id }
        val assetOnly = assets.channels.filter { it.id !in knownIds }
        val mergedAll = if (assetOnly.isEmpty()) merged else {
            changed = true
            added += assetOnly.sumOf { it.streams.size }
            merged + assetOnly
        }

        // 必看台先探，接着探**这台电视上从没出过画面**的台，看过的台不占预算；仍然限时，
        // 超预算的台留到下一次按按钮，不然这一下要挂十几分钟。
        val targets = probeTargets(mergedAll, settings)
        val outcomes = if (force) probeAvailability(targets) { channel -> settings.lineFor(channel.id) } else null
        val probeSkipped = outcomes?.let { targets.size - it.size }
        val channels = if (outcomes == null) {
            mergedAll
        } else {
            val probed = outcomes.associateBy { it.channel.id }
            mergedAll.map { probed[it.id]?.channel ?: it }
        }

        // 只删不增也要落盘，否则清理掉的失效线路会一直留在旧缓存里。
        // 「以内置清单为底重拼过」（`base === assets`：装了新版本、内置清单比缓存新）也必须落盘——
        // 界面读的是缓存文件，光在内存里换底不换文件，旧缓存里那些新版本已经判坏的线路一条都没被清掉。
        // （10-22 现读到的形状：升级后若公开源这一轮没补新线、次序也没变，以前这一格直接跳过写盘，
        //   脏缓存原样留在设备上，界面上的坏线一根没少。）
        if (candidatesFound.isNotEmpty()) recordCandidates(candidatesFound)

        if (added > 0 || changed || outcomes != null || base === assets) {
            // 黑名单也一起同步成包内那一份：设备上那份是旧快照，留着它会让"资产已复活/已判死"两边打架。
            val updated = base.copy(channels = channels, blockedLines = assets.blockedLines)
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
        // 用户 10-25「未来新增的线路也不添加标清线路」：公开列表不写尺寸，所以设备端在探测
        // 读到 RESOLUTION 的那一刻就把只有标清档的那一条摘掉（这台电视真播过的那条例外，
        // 长期指令：已播过的线路绝不自动换线/换台）。摘掉后要把整条链走完，
        // 否则下次探测第一条还是它。
        val sd = LinkedHashSet<String>()
        var winner: String? = null
        for (url in channel.streams) {
            val outcome = StreamProbe.probe(url)
            if (!outcome.playable) continue
            if (url != played && Definition.isSdResolution(outcome.resolution)) {
                sd += url
                continue
            }
            winner = url
            break
        }
        val kept = if (sd.isEmpty()) channel.streams else channel.streams.filterNot { it in sd }
        // 探到能用那条为止一共探了几条：标清那些都是在它之前探到的，没探完的（winner 为空）就是全探了。
        val tried = if (winner == null) channel.streams.size else sd.size + kept.indexOf(winner) + 1
        if (winner == null) {
            return ProbeOutcome(if (sd.isEmpty()) channel else channel.copy(streams = kept),
                                tried, false, sd.isNotEmpty())
        }
        // 探到的那条排到最前，但**这台电视真播过的那条更靠前**（用户 2026-10-21 指令：
        // 已播过的线路绝不自动换线/换台）。以前这里直接把探到的第一条顶上去，
        // 于是按一次「立即更新」就可能把父母天天看的那条线从第 1 位挪走——
        // 播放中确实不换线（LineRecovery），但下次进这个台要先试的就是别的地址了。
        val ordered = CatalogMerge.probeOrder(kept, kept.indexOf(winner), played, channel.category)
        return ProbeOutcome(channel.copy(streams = ordered), tried, true, ordered != channel.streams)
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
                            !Definition.isSdTitle(name) &&
                            urls.none { StreamUrl.sameLine(it, line) }
                        ) {
                            // 「未来新增的线路也不添加标清线路」（用户 10-25）：设备端量不到尺寸，
                            // 只挡标题自己写着标清的那一类；按尺寸摘那一半在构建侧（build_catalog 的
                            // is_sd_line），地址随 blockedLines 装进 APK 一起挡。
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

    @Serializable
    private data class CandidateLine(val channel: String, val url: String)

    @Serializable
    private data class CandidateFile(
        val generatedAt: String = "",
        val items: List<CandidateLine> = emptyList(),
    )

    /**
     * 把「公开源送来、但不在白名单里」的地址累加进 `files/line_candidates.json`（按地址去重）。
     * 只记账、不改目录：闸在 `tools/vet_line_candidates.py`，探过清单/分片/画面之后才写进
     * `assets/line_allowlist.json`，下一版 APK 才准它们自动进目录（用户 10-28 点档「乙」）。
     */
    private fun recordCandidates(found: List<CandidateLine>) {
        val file = File(context.filesDir, CANDIDATES_NAME)
        val old = runCatching {
            if (file.exists()) json.decodeFromString(CandidateFile.serializer(), file.readText()) else null
        }.getOrNull()
        val seen = old?.items.orEmpty().mapTo(HashSet()) { StreamUrl.canonical(it.url) }
        val items = old?.items.orEmpty() + found.filter { seen.add(StreamUrl.canonical(it.url)) }
        val stamp = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US)
            .apply { timeZone = java.util.TimeZone.getTimeZone("UTC") }
            .format(java.util.Date())
        runCatching {
            file.writeText(json.encodeToString(CandidateFile.serializer(), CandidateFile(stamp, items)))
        }
    }

    private companion object {
        const val CACHE_NAME = "channels_live.json"
        /** 打进 APK 的白名单：只有列在这里的地址才准由公开源自动加进目录（用户 10-28 点档「乙」）。 */
        const val ALLOWLIST_ASSET = "line_allowlist.json"
        /** 没过闸的新地址落这里，等 `tools/vet_line_candidates.py` 探完再进白名单。 */
        const val CANDIDATES_NAME = "line_candidates.json"
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
     *
     * [moves] 是目录里的「按台标改挂」记录（用户 10-25 指令：台标与频道不一致的移入对应卫视频道）：
     * 构建已经把这条地址搬进台标对应的那一台，这里只挡另一半——公开列表下一次按**旧台名**再送过来时，
     * 不许它回到错误频道名下（与 blockedLines 同一个漏口，见 RelabeledStreams）。
     */
    fun streams(channelName: String, packaged: List<String>, fresh: List<String>,
                playable: String? = null, blocked: Set<String> = emptySet(),
                moves: Map<String, LineMove> = emptyMap(),
                category: String? = null,
                assetOrder: List<String> = emptyList()): List<String> {
        val merged = (RelabeledStreams.linesFor(channelName) + packaged +
            fresh.sortedByDescending { StreamUrl.isIpv6(it) })
            .filterNot { DeadStreams.isDead(it) || isBlocked(it, blocked) }
            .filterNot { StreamRules.neverPlayable(it) != null }
            .filterNot { RelabeledStreams.belongsElsewhere(channelName, it, moves) }
            .distinctBy { StreamUrl.canonical(it) }
        return if (category in RESOLUTION_FIRST_CATEGORIES) byAssetOrder(merged, assetOrder)
        else putPlayableFirst(merged, playable)
    }

    /**
     * 打进 APK 的那一份里有、设备缓存里没有的线路。
     *
     * 为什么要单独一格：内置换底只认 `generatedAt`（见 `refresh` 里 `assetsNewer`），
     * 而 10-28 现读的那份目录内容改过、时间戳还是 `2026-10-27T09:25:08Z`，
     * 与设备缓存**逐字相同** ⇒ 装新包后新加的线一条都落不到电视上
     * （现读：设备 `files/channels_live.json` 277 台/604 条，东方卫视 6 条里没有当天新加的
     * `27.129.145.110:4433`）。这一格按地址归一比较，与时间戳无关。
     *
     * 只回答「新增哪些」，一条都不删：缓存里那些公开源带回的线要不要清，仍归 `streams()` 的规则管。
     */
    fun extrasInAssetOnly(assetStreams: List<String>, cachedStreams: List<String>): List<String> =
        assetStreams.filter { asset -> cachedStreams.none { StreamUrl.sameLine(it, asset) } }

    /**
     * 设备缓存里还在用、而包内黑名单已经判死的地址数（10-28 起播过也不例外，见 [isBlocked]）。
     *
     * 与 [extrasInAssetOnly] 是同一件事的两半：「目录里新加的线要能落到设备上」和「目录判死的线
     * 也要能从设备上摘掉」，都不能因为两份目录的 `generatedAt` 相等就整条跳过。
     * 10-28 现读：设备 `files/channels_live.json` 607 条、包内 455 条，多出来的那批里有三条
     * 东方卫视的旧线三轮回测全灭（超时／连接重置／TLS 握手失败），黑名单写死了却落不到设备。
     */
    fun blockedInUseCount(cachedStreams: List<String>, blocked: Set<String>): Int =
        cachedStreams.count { isBlocked(it, blocked) }

    /**
     * 按打进 APK 那一份目录的次序重排。
     *
     * 为什么不能只靠「不置顶」：设备这一棒的底稿是 `files/channels_live.json`（上一次更新落的那份），
     * 那条播过的线在**旧版本**里已经被顶到第 1 位了，光关掉置顶它还是留在第 1 位。
     * 内置目录就是按清晰度排好的那一份，所以这两组直接照它复位；
     * 目录里没有的（公开源这次新带回来的）保持原有相对次序排在后面。
     */
    fun byAssetOrder(streams: List<String>, assetOrder: List<String>): List<String> {
        if (assetOrder.isEmpty()) return streams
        val rank = HashMap<String, Int>()
        assetOrder.forEachIndexed { index, url ->
            val key = StreamUrl.canonical(url)
            if (!rank.containsKey(key)) rank[key] = index
        }
        val last = assetOrder.size
        return streams.sortedBy { rank[StreamUrl.canonical(it)] ?: last }
    }

    /**
     * 目录证据判坏的地址：公开源下一次还会把它带回来，合并时按归一键挡掉。
     *
     * 用户 10-28 16:3x 改口：「**播过那条豁免规则调整，已正常播的线路保留，有台标，不正常的删除**」
     * （原 10-22 是「播过的那条一律留着」）。⇒ 黑名单就是「不正常」的名单：一条线被判死，
     * 即使这台电视播过它，也从目录里删掉。现读现场正是这条规则要治的病：重庆/山西/江苏/浙江/广西/青海
     * 六台的 `line_*` 都指着被拉黑的死线，每次切台都先撞死线、缓冲十秒才轮到好线。
     * 「保留」那一半不受影响——正常播、台标相符的线本来就不会进黑名单。
     */
    fun isBlocked(stream: String, blocked: Set<String>): Boolean =
        StreamUrl.canonical(stream) in blocked

    /**
     * 「立即更新」探测之后怎么排这一台的线路：探到的那条上到第 1 位，
     * 而这台电视真播过（或用户手工选过）的那条要不要再压它一手，看 [category]。
     */
    fun probeOrder(streams: List<String>, goodIndex: Int, played: String?,
                   category: String? = null): List<String> {
        val probed = listOf(streams[goodIndex]) +
            streams.filterIndexed { position, _ -> position != goodIndex }
        return if (category in RESOLUTION_FIRST_CATEGORIES) probed
        else putPlayableFirst(probed, played)
    }

    /**
     * 这两组「线路按清晰度由高到低」（用户 2026-10-25 指令，与构建侧 RES_FIRST_CATS 同一份口径）：
     * 打进 APK 的那一份次序已经是清晰度序，所以这里要做的是**别再动它**——
     * 别的组仍按 10-21/10-22 那条走（播过那条最前）；分组认不出来（category 为空）也按老规矩置顶，
     * 宁可不改次序，也不要把天天看的那条挪走。
     */
    val RESOLUTION_FIRST_CATEGORIES = setOf("cctv", "local")

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
