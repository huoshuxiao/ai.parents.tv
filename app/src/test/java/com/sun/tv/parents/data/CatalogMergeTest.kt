package com.sun.tv.parents.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 自动更新那一步的合并，喂的是设备上 `files/channels_live.json` 在 10-22 00:2x 现读的真列表。
 *
 * 钉的是这件事：公开源会把挂错台的那条线一次次带回「西藏卫视」名下，
 * 只改打进 APK 的目录挡不住下一次更新，所以合并时必须按台标改挂。
 */
class CatalogMergeTest {

    private val mislabeled =
        "http://112.27.5.218:9901/tsfile/live/0111_1.m3u8?key=txiptv&playlive=1&authid=0"

    /** 设备缓存里「西藏卫视」的五条原样。 */
    private val tibet = listOf(
        mislabeled,
        "http://39.135.138.58:18890/PLTV/88888888/224/3221225723/index.m3u8",
        "http://[2409:8087:1e03:21::2]:6060/cms001/ch00000090990000001072/index.m3u8",
        "http://t.061899.xyz/tl/tl.php?id=xzws",
        "http://101.35.240.114:88/live.php?id=西藏卫视",
    )

    /** 设备缓存里「四川卫视」的七条原样（不含那条被认错台标的线）。 */
    private val sichuan = listOf(
        "http://ep.b1791.com/php/sctv.php?id=scws",
        "http://[2409:8087:1e03:21::2]:6060/cms001/ch00000090990000001130/index.m3u8",
        "http://iptv.huuc.edu.cn/hls/schd.m3u8",
        "http://148.135.93.213/live.php?id=四川卫视",
        "http://112.27.235.94:8000/hls/35/index.m3u8",
        "https://zby.130519.xyz/PLTV/11/224/3221226152/index.m3u8",
        "http://219.159.147.195:9901/tsfile/live/1033_1.m3u8?key=txiptv&playlive=1&authid=0",
    )

    @Test
    fun publicListCannotBringTheMislabeledLineBackToTibet() {
        val merged = CatalogMerge.streams("西藏卫视", tibet, listOf(mislabeled))
        assertFalse("公开源再带一次也要被摘掉", merged.any { StreamUrl.sameLine(it, mislabeled) })
        assertEquals("其余四条不许被牵连", 4, merged.size)
        assertTrue(merged.containsAll(tibet.drop(1)))
    }

    @Test
    fun reassignedLineGoesToTheFrontOfItsTrueChannel() {
        val merged = CatalogMerge.streams("四川卫视", sichuan, listOf(mislabeled))
        assertEquals("台标实测那条必须排第一（已验证线路在前）", mislabeled, merged.first())
        assertEquals("一条都不能少", sichuan.size + 1, merged.size)
        assertTrue(merged.containsAll(sichuan))
    }

    @Test
    fun sameHostDifferentProgramSurvivesOnItsOwnChannel() {
        val jiangxi = listOf(
            "http://112.27.5.218:9901/tsfile/live/faacts/0138_1.m3u8?key=txiptv&playlive=1&authid=0",
            "http://39.135.138.58:18890/PLTV/88888888/224/3221225705/index.m3u8",
        )
        assertEquals("江西卫视两条原样保留", jiangxi, CatalogMerge.streams("江西卫视", jiangxi, emptyList()))
    }

    /**
     * 用户 2026-10-22 要求 IPv6 优先：本机没有到那些 v6 源的出口，
     * 按 IPv4 在前排的话，家里能走的 IPv6 线路在设备上永远排在试完才轮到的位置。
     */
    @Test
    fun newlyFoundIpv6LinesGoAheadOfNewlyFoundIpv4Ones() {
        val v6 = "http://[2409:8087:1e03:21::2]:6060/cms001/ch00000090990000001130/index.m3u8"
        val v6b = "http://[2409:8087:7000:20:1000::22]:6060/yinhe/2/ch00000090990000001334/index.m3u8"
        val v4 = "http://148.135.93.213/live.php?id=四川卫视"
        val v4b = "http://iptv.huuc.edu.cn/hls/schd.m3u8"
        val merged = CatalogMerge.streams("某某卫视", listOf(v4b), listOf(v4, v6b, v6))
        // sortedByDescending 是稳定排序：两条 v6 之间保持公开源给的原次序（v6b 在 v6 前）。
        assertEquals("已有的线路仍在前，新线路里 IPv6 排在 IPv4 前面",
            listOf(v4b, v6b, v6, v4), merged)
    }

    @Test
    fun ipv6PreferenceOnlyReordersTheNewlyFoundBatch() {
        // 正对照的另一半：IPv6 优先不许把「新发现」的线插到内置线路前面。
        val packaged = listOf("http://ep.b1791.com/php/sctv.php?id=scws")
        val v6 = "http://[2409:8087:1e03:21::2]:6060/cms001/ch00000090990000001130/index.m3u8"
        val merged = CatalogMerge.streams("某某卫视", packaged, listOf(v6))
        assertEquals(listOf(packaged[0], v6), merged)
    }

    @Test
    fun lineThisTvAlreadyPlayedGoesFirst() {
        // 「线路可用性优于频道数量」（用户 10-22）：这台电视真出过画面的那条线，
        // 比目录里排头的没验过的线值钱。
        val first = "http://74.91.26.218:82/live/cctv3hd.m3u8"
        val second = "http://39.135.138.58:18890/PLTV/88888888/224/3221225634/index.m3u8"
        val played = "http://222.169.85.8:9901/tsfile/live/0003_1.m3u8?key=txiptv&playlive=1&authid=0"
        val merged = CatalogMerge.streams("CCTV-3 综艺", listOf(first, second, played), emptyList(), played)
        assertEquals(listOf(played, first, second), merged)
    }

    /**
     * 用户 2026-10-25「央视·卫视、地方频道，线路按清晰度由高到低排序，"清晰度排序"压过播过置顶」：
     * 打进 APK 的那一份次序就是清晰度序，所以设备端合并／探测这两处都不许再把它重排。
     * 六格：两组豁免（cctv／local）各一格、两组不豁免（体育／国际）各一格、
     * 分组认不出（category=null）一格（必须仍按 10-22 老规矩置顶）、
     * 探测那一棒一格。同一份输入只改 category 就必须出现两种结果，否则这六格是恒真的。
     */
    private val resFirstFirst = "http://74.91.26.218:82/live/cctv3hd.m3u8"
    private val resFirstSecond = "http://39.135.138.58:18890/PLTV/88888888/224/3221225634/index.m3u8"
    private val resFirstPlayed = "http://222.169.85.8:9901/tsfile/live/0003_1.m3u8?key=txiptv&playlive=1&authid=0"

    @Test
    fun cctvGroupKeepsTheResolutionOrderEvenWithAPlayedLine() {
        val merged = CatalogMerge.streams("CCTV-3 综艺", listOf(resFirstFirst, resFirstSecond, resFirstPlayed),
            emptyList(), resFirstPlayed, category = "cctv")
        assertEquals("央视·卫视：播过那条不再置顶，目录那一份次序原样留着",
            listOf(resFirstFirst, resFirstSecond, resFirstPlayed), merged)
    }

    @Test
    fun localGroupKeepsTheResolutionOrderEvenWithAPlayedLine() {
        val merged = CatalogMerge.streams("辽源新闻综合", listOf(resFirstFirst, resFirstSecond, resFirstPlayed),
            emptyList(), resFirstPlayed, category = "local")
        assertEquals(listOf(resFirstFirst, resFirstSecond, resFirstPlayed), merged)
    }

    @Test
    fun sportsGroupAlsoFollowsResolutionFirst() {
        // 10-30 起六组都按清晰度序（AGENTS 线路排序规则），体育／国际不再把播过那条顶回第 1 位。
        // 牙齿：同一份输入把 category 换成认不出的，行为必须翻面（否则这一格是恒真）。
        val merged = CatalogMerge.streams("辽宁体育", listOf(resFirstFirst, resFirstSecond, resFirstPlayed),
            emptyList(), resFirstPlayed, category = "sports")
        assertEquals(listOf(resFirstFirst, resFirstSecond, resFirstPlayed), merged)
        val intl = CatalogMerge.streams("CGTN", listOf(resFirstFirst, resFirstSecond, resFirstPlayed),
            emptyList(), resFirstPlayed, category = "international")
        assertEquals(listOf(resFirstFirst, resFirstSecond, resFirstPlayed), intl)
        val unknown = CatalogMerge.streams("某某台", listOf(resFirstFirst, resFirstSecond, resFirstPlayed),
            emptyList(), resFirstPlayed, category = null)
        assertEquals(listOf(resFirstPlayed, resFirstFirst, resFirstSecond), unknown)
        assertNotEquals("认不出分组那一侧必须仍走置顶", merged, unknown)
    }

    @Test
    fun unknownCategoryFallsBackToPinningThePlayedLine() {
        // 认不出分组就别动老规矩：宁可置顶，也不把父母天天看那条挪到后面。
        val merged = CatalogMerge.streams("CCTV-3 综艺", listOf(resFirstFirst, resFirstSecond, resFirstPlayed),
            emptyList(), resFirstPlayed, category = null)
        assertEquals(listOf(resFirstPlayed, resFirstFirst, resFirstSecond), merged)
        // 牙齿：同一份输入只有 category 不同，两次的结果必须不同（不然上面几格是恒真）
        val exempt = CatalogMerge.streams("CCTV-3 综艺", listOf(resFirstFirst, resFirstSecond, resFirstPlayed),
            emptyList(), resFirstPlayed, category = "cctv")
        assertNotEquals("cctv 那一档必须真的换了行为", merged, exempt)
    }

    @Test
    fun probeOrderFollowsTheSameCategoryRule() {        // 「立即更新」探测完那一棒：六组都让探到的那条（清晰度序里第一条能用的）占第 1 位，
        // 只有分组认不出来（category 为空）时仍把播过那条压回第 1 位。
        val streams = listOf(resFirstFirst, resFirstSecond, resFirstPlayed)
        assertEquals(listOf(resFirstSecond, resFirstFirst, resFirstPlayed),
            CatalogMerge.probeOrder(streams, 1, resFirstPlayed, "cctv"))
        assertEquals(listOf(resFirstSecond, resFirstFirst, resFirstPlayed),
            CatalogMerge.probeOrder(streams, 1, resFirstPlayed, "sports"))
        assertEquals(listOf(resFirstPlayed, resFirstSecond, resFirstFirst),
            CatalogMerge.probeOrder(streams, 1, resFirstPlayed, null))
    }

    /**
     * 设备底稿是上一版落下的那份缓存，里面「播过那条」已经被顶到第 1 位了——
     * 只关掉置顶不够，那条会一直留在第 1 位。这两格钉的就是「按打进 APK 的那一份归位」。
     */
    @Test
    fun aLinePinnedByTheOldBuildGetsPutBackWhereTheCatalogSays() {
        val asset = listOf(resFirstFirst, resFirstSecond, resFirstPlayed)
        val cached = listOf(resFirstPlayed, resFirstFirst, resFirstSecond)   // 旧版本置顶后的样子
        val merged = CatalogMerge.streams("CCTV-3 综艺", cached, emptyList(), resFirstPlayed,
            category = "cctv", assetOrder = asset)
        assertEquals("央视·卫视：按内置目录归位", asset, merged)
        // 牙齿：同一份输入只把 assetOrder 摘掉，那条就还赖在第 1 位——证明归位的是这一行
        val noAnchor = CatalogMerge.streams("CCTV-3 综艺", cached, emptyList(), resFirstPlayed,
            category = "cctv")
        assertEquals(cached, noAnchor)
        // 别的组（10-30 起也是六组里的一员）给了 assetOrder 同样要归位，不再按旧规矩把播过的顶到第 1 位
        val sports = CatalogMerge.streams("辽宁体育", cached, emptyList(), resFirstPlayed,
            category = "sports", assetOrder = asset)
        assertEquals(asset, sports)
    }

    @Test
    fun linesTheCatalogDoesNotKnowStayBehindTheKnownOnes() {
        // 公开源这次新带回来的线（目录里没有）不许插到已知线前面，它们之间保持原相对次序
        val asset = listOf(resFirstFirst, resFirstPlayed)
        val newcomer = "http://103.185.24.134:3001/CCTV3-HD/index.m3u8"
        val merged = CatalogMerge.streams("CCTV-3 综艺", listOf(resFirstPlayed, resFirstFirst),
            listOf(newcomer), resFirstPlayed, category = "cctv", assetOrder = asset)
        assertEquals(listOf(resFirstFirst, resFirstPlayed, newcomer), merged)
    }

    @Test
    fun playableLineMatchesEvenWhenWrittenDifferently() {
        // 记下来的地址写法可能和目录里不一样（百分号转义/默认端口），归一后是同一条就算命中。
        val kept = "http://dassby.qqff.top:99/live/%E8%BE%BD%E5%AE%81%E4%BD%93%E8%82%B2/index.m3u8"
        val other = "http://148.135.93.213/live.php?id=ln"
        val merged = CatalogMerge.streams("辽宁体育", listOf(other, kept), emptyList(),
            "http://dassby.qqff.top:99/live/辽宁体育/index.m3u8")
        assertEquals("归一后同一条要顶到第一", listOf(kept, other), merged)
    }

    @Test
    fun withoutAPlayedLineTheMergeOrderIsUntouched() {
        // 负对照：没给「验过的线」（或给的地址不在这台里）时，一条都不许多动——
        // 不然这个函数就成了随手打乱目录的源头。
        val streams = listOf("http://a.example/1.m3u8", "http://b.example/2.m3u8")
        assertEquals(streams, CatalogMerge.putPlayableFirst(streams, null))
        assertEquals(streams, CatalogMerge.putPlayableFirst(streams, "   "))
        assertEquals(streams, CatalogMerge.putPlayableFirst(streams, "http://c.example/3.m3u8"))
        assertEquals(streams, CatalogMerge.putPlayableFirst(streams, streams[0]))
    }

    /**
     * 用户 2026-10-22 指令「线路可用性优于频道数量」在设备端的那一环：
     * 目录生成时实测判坏的地址，公开源下一次还会带回来，合并时必须按黑名单挡掉。
     * 钉的是 10-22 模拟器现读到的那次泄漏——一次开机后台更新就把设备缓存写成
     * 比内置目录多 561 条线、其中 151 条是判坏的。
     */
    @Test
    fun evidenceBlockedListRefusesWhatPublicSourcesBringBack() {
        val dead = "http://101.35.240.114:88/live.php?id=西藏卫视"
        val good = "http://39.135.138.58:18890/PLTV/88888888/224/3221225723/index.m3u8"
        val blocked = setOf(StreamUrl.canonical(dead))
        val merged = CatalogMerge.streams("西藏卫视", listOf(good), listOf(dead), null, blocked)
        assertEquals("判坏的那条不许被公开源带回来", listOf(good), merged)
        // 负对照：同一个调用，只把黑名单换成空集，那条线就会活着进来——
        // 证明摘掉它的是黑名单这一行，不是别处（容器/改挂规则都碰不到这条地址）。
        assertEquals("黑名单为空时这条线本该留下",
            listOf(good, dead), CatalogMerge.streams("西藏卫视", listOf(good), listOf(dead), null, emptySet()))
    }

    @Test
    fun blockedLineWrittenDifferentlyIsStillBlocked() {
        // 黑名单存的是归一键，公开源给的是转义写法：两种写法必须算同一条。
        val blocked = setOf(StreamUrl.canonical("http://dassby.qqff.top:99/live/辽宁体育/index.m3u8"))
        val escaped = "http://dassby.qqff.top:99/live/%E8%BE%BD%E5%AE%81%E4%BD%93%E8%82%B2/index.m3u8"
        val merged = CatalogMerge.streams("某某卫视", emptyList(), listOf(escaped), null, blocked)
        assertEquals("转义写法也要被挡掉", emptyList<String>(), merged)
        assertFalse(CatalogMerge.isBlocked(escaped, emptySet()))
    }

    /**
     * 用户 10-28 16:3x 改口：「播过那条豁免规则调整，已正常播的线路保留，有台标，不正常的删除」。
     * 判死的线即使这台电视播过、甚至正指着它，也要从目录里删掉。
     *
     * 旧行为（10-21 起）是「播过就豁免」，真机上的病正是它：重庆/山西/江苏/浙江/广西/青海 六台的
     * `line_*` 都指着被拉黑的死线，每次切台先撞死线、缓冲十秒。
     */
    @Test
    fun playedLineIsNoLongerExemptFromTheBlockList() {
        val played = "http://74.91.26.218:82/live/cctv3hd.m3u8"
        val other = "http://39.135.138.58:18890/PLTV/88888888/224/3221225634/index.m3u8"
        val blocked = setOf(StreamUrl.canonical(played))
        val merged = CatalogMerge.streams("CCTV-3 综艺", listOf(other), listOf(played), played, blocked)
        assertEquals("判死的线即使播过也不许留下，本台只剩那一条好线", listOf(other), merged)
        // 负对照：没进黑名单时，播过那条仍按老规矩排第一（证明这一格咬的是黑名单，不是排序）。
        assertEquals(listOf(played, other),
            CatalogMerge.streams("CCTV-3 综艺", listOf(other), listOf(played), played, emptySet()))
    }

    /**
     * 用户 2026-10-25 指令「卫视频道，台标与频道不一致的，根据台标移入对应卫视频道」在设备端的那一半：
     * 构建已经把这条地址搬进台标对应的那一台，可公开列表下一次还会按**旧台名**再送一遍过来。
     * 挡它的是目录里的 lineMoves（随 channels.json 装进 APK），不是只改一次内置目录。
     */
    @Test
    fun catalogLineMoveRefusesTheOldChannelName() {
        val moved = "http://148.135.93.213/live.php?id=hljws"
        val moves = mapOf(StreamUrl.canonical(moved) to LineMove(from = "黑龙江卫视", to = "辽宁卫视"))
        val good = "http://39.135.138.58:18890/PLTV/88888888/224/3221225723/index.m3u8"
        val back = CatalogMerge.streams("黑龙江卫视", listOf(good), listOf(moved), null, emptySet(), moves)
        assertEquals("旧台名再送一次也要被摘掉", listOf(good), back)
        val forward = CatalogMerge.streams("辽宁卫视", listOf(moved), emptyList(), null, emptySet(), moves)
        assertEquals("台标对应的那一台要留下它", listOf(moved), forward)
        // 负对照：同一份地址、把 lineMoves 换成空表 ⇒ 那条线必须活着进来。
        // 没有这一格，这一格就成了恒真（摘掉它的可能是容器规则或别的 filter）。
        assertEquals("没有改挂记录时这条线本该留下",
            listOf(good, moved),
            CatalogMerge.streams("黑龙江卫视", listOf(good), listOf(moved), null, emptySet(), emptyMap()))
    }

    @Test
    fun catalogLineMoveWinsOverTheHandwrittenRoster() {
        // 两条名册都可能写同一个地址（手抄那份是 10-21/10-24 的历史）。口径只留一个：
        // 目录那份优先——它是每次重建按现读证据算出来的，手抄那份不会自己更新。
        val url = "http://112.27.5.218:9901/tsfile/live/0111_1.m3u8?key=txiptv&playlive=1&authid=0"
        assertEquals("没有目录记录时用手抄那份", "四川卫视", RelabeledStreams.trueChannel(url))
        val moves = mapOf(StreamUrl.canonical(url) to LineMove(from = "西藏卫视", to = "重庆卫视"))
        assertEquals("目录记录要盖过手抄", "重庆卫视", RelabeledStreams.trueChannel(url, moves))
        assertTrue("改挂后挂在四川卫视名下也该摘掉",
            RelabeledStreams.belongsElsewhere("四川卫视", url, moves))
    }

    @Test
    fun oldCacheWithoutLineMovesDecodesToEmptyMap() {
        // 设备上那份 channels_live.json 是旧版写的，没有 lineMoves 这一格；解码不许整片失败。
        val text = """{"schemaVersion":1,"generatedAt":"2026-10-25T01:00:00Z","blockedLines":{},
            "channels":[{"id":"x1","name":"某某卫视","category":"satellite","streams":["http://a.example/1.m3u8"]}]}"""
        val catalog = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
            .decodeFromString(ChannelCatalog.serializer(), text)
        assertTrue("旧缓存读出来是空表，不是崩溃", catalog.lineMoves.isEmpty())
        assertEquals(1, catalog.channels.size)
    }

    @Test
    fun oldCacheWithoutBlockedListDecodesToEmptySet() {
        // 设备上缓存的 channels_live.json 是旧版写的、没有 blockedLines 这一格；
        // 解码不许因此整片失败（那样目录会退化成读不到，父母那台就一个台都没了）。
        val text = """{"schemaVersion":1,"generatedAt":"2026-10-22T05:48:11Z","channels":[
            {"id":"x1","name":"某某卫视","category":"sports","streams":["http://a.example/1.m3u8"]}]}"""
        val catalog = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
            .decodeFromString(ChannelCatalog.serializer(), text)
        assertTrue(catalog.blockedLines.isEmpty())
        assertEquals(1, catalog.channels.size)
    }

    /**
     * 「立即更新」探测之后的置顶次序。
     * 钉的是这次真读到的形状：探测把"这台机器上第一条探通的线"顶到第 1 位，
     * 于是按一次按钮就可能把父母天天看的那条从第 1 位挪走（播放中不换线，下次进台先试的却不是它）。
     */
    @Test
    fun probeResultPutsThePlayableLineFirstWhenNothingHasBeenPlayed() {
        val first = "http://a.example/1.m3u8"
        val probed = "http://b.example/2.m3u8"
        val third = "http://c.example/3.m3u8"
        assertEquals(listOf(probed, first, third),
            CatalogMerge.probeOrder(listOf(first, probed, third), 1, null))
    }

    @Test
    fun probeResultNeverDisplacesTheLineThisTvAlreadyPlayed() {
        val first = "http://a.example/1.m3u8"
        val probed = "http://b.example/2.m3u8"
        val played = "http://c.example/3.m3u8"
        val ordered = CatalogMerge.probeOrder(listOf(first, probed, played), 1, played)
        assertEquals("播过那条仍然第 1，探到的排第二，一条不许少", listOf(played, probed, first), ordered)
        // 负对照：给的播过记录不在这台的线路里时，行为退回"探到的排第一"——
        // 证明这一格咬的是"播过那条在场"，不是无条件把最后一条提到最前。
        assertEquals(listOf(probed, first, played),
            CatalogMerge.probeOrder(listOf(first, probed, played), 1, "http://other.example/x.m3u8"))
    }

    @Test
    fun probeResultKeepsOneWrittenDifferentlyInFront() {
        // 记下来的写法与目录里的写法不同（中文路径转义）也要认成同一条，否则这一格形同虚设。
        val playedEscaped = "http://dassby.qqff.top:99/live/%E8%BE%BD%E5%AE%81%E4%BD%93%E8%82%B2/index.m3u8"
        val playedPlain = "http://dassby.qqff.top:99/live/辽宁体育/index.m3u8"
        val other = "http://148.135.240.114:88/live.php?id=ln"
        assertEquals(listOf(playedPlain, other),
            CatalogMerge.probeOrder(listOf(other, playedPlain), 1, playedEscaped))
    }

    // ---- 包内目录新增的线要落到设备上（10-28：两份目录 generatedAt 相等时也必须落） ----

    /** 设备缓存里东方卫视的六条原样（10-28 12:43 `files/channels_live.json` 现读）。 */
    private val dfCached = listOf(
        "http://107.150.60.122/live/dfwshd.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=dfwshd",
        "http://36.110.54.118:866/hls/28/index.m3u8",
        "http://iptv.huuc.edu.cn/hls/dfhd.m3u8",
        "http://t.061899.xyz/tl/tl.php?id=dfws",
        "https://zby.130519.xyz/PLTV/11/224/3221225725/index.m3u8",
    )

    /** 打进 APK 的东方卫视四条（10-28 目录现读）。 */
    private val dfPackaged = listOf(
        "http://107.150.60.122/live/dfwshd.m3u8",
        "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=dfwshd",
        "http://36.110.54.118:866/hls/28/index.m3u8",
        "http://27.129.145.110:4433/hls/40/index.m3u8",
    )

    @Test
    fun packagedLineTheDeviceLacksIsReportedAsExtra() {
        assertEquals(
            "只报 27.129 这条；缓存独有的三条不许混进来，已有的三条也不许当新增",
            listOf("http://27.129.145.110:4433/hls/40/index.m3u8"),
            CatalogMerge.extrasInAssetOnly(dfPackaged, dfCached),
        )
    }

    @Test
    fun alreadyLandedLineIsNotExtraAgain() {
        // 落过一次之后再跑，必须是空的——否则每次更新都会当成"新增"去写盘。
        val landed = dfCached + "http://27.129.145.110:4433/hls/40/index.m3u8"
        assertTrue(CatalogMerge.extrasInAssetOnly(dfPackaged, landed).isEmpty())
    }

    @Test
    fun emptyOrDifferentSpellingCacheBehaves() {
        assertEquals(dfPackaged, CatalogMerge.extrasInAssetOnly(dfPackaged, emptyList()))
        // 同一条只是写法不同（协议/主机大小写、默认端口、尾斜杠）不算新增。
        val spelledDifferently = listOf(
            "HTTP://107.150.60.122:80/live/dfwshd.m3u8/",
            "http://63.141.230.178:82/gslb/zbdq5.m3u8?id=dfwshd",
            "http://36.110.54.118:866/hls/28/index.m3u8",
        )
        assertEquals(
            listOf("http://27.129.145.110:4433/hls/40/index.m3u8"),
            CatalogMerge.extrasInAssetOnly(dfPackaged, spelledDifferently),
        )
    }

    /** 新增那条若背着黑名单（目录判过坏），合并时仍要被挡掉——只有"新"不是放行理由。 */
    @Test
    fun extraStillObeysBlocklist() {
        val extra = "http://27.129.145.110:4433/hls/40/index.m3u8"
        val merged = CatalogMerge.streams(
            "东方卫视", dfCached, listOf(extra),
            blocked = setOf(StreamUrl.canonical(extra)),
            category = "cctv", assetOrder = dfPackaged,
        )
        assertTrue("黑名单里的新增线不许进来", merged.none { StreamUrl.sameLine(it, extra) })
        val ok = CatalogMerge.streams(
            "东方卫视", dfCached, listOf(extra),
            category = "cctv", assetOrder = dfPackaged,
        )
        assertTrue("没有黑名单时必须落进来", ok.any { StreamUrl.sameLine(it, extra) })
        assertEquals("一条都不许删", dfCached.size + 1, ok.size)
        assertEquals("央视·卫视按包内次序：前三条仍是包内前三", dfPackaged.take(3), ok.take(3))
    }

    // ---- 包内判死的线也要能落到设备上（资产与设备条数统一，10-28） ----

    /** 设备东方卫视的 7 条（10-28 14:0x 现读）：前四条是资产里的，后三条是公开源补进来且三轮回测全灭的。 */
    private val dfDevice7 = dfPackaged + listOf(
        "http://iptv.huuc.edu.cn/hls/dfhd.m3u8",
        "http://t.061899.xyz/tl/tl.php?id=dfws",
        "https://zby.130519.xyz/PLTV/11/224/3221225725/index.m3u8",
    )

    private val dfBlocked = setOf(
        "http://iptv.huuc.edu.cn/hls/dfhd.m3u8",
        "http://t.061899.xyz/tl/tl.php?id=dfws",
        "https://zby.130519.xyz/PLTV/11/224/3221225725/index.m3u8",
    )

    @Test
    fun deadLinesStillInDeviceCacheAreCounted() {
        assertEquals(
            "设备 7 条里有 3 条已被包内黑名单判死 ⇒ 更新器不许因为时间戳相同就整条跳过",
            3,
            CatalogMerge.blockedInUseCount(dfDevice7, dfBlocked),
        )
    }

    /**
     * 用户 10-28 16:3x 改口：「播过那条豁免规则调整，已正常播的线路保留，有台标，不正常的删除」。
     * 所以「这台电视正看着的那条线」也不再从黑名单里豁免——它被判死就该删。
     * 这一格挡的是把旧例外又悄悄加回来：真机现场正是六台卫视的 `line_*` 指着死线、
     * 每次切台先撞死线缓冲十秒。
     */
    @Test
    fun theLineBeingPlayedIsNoLongerExempt() {
        val played = "http://iptv.huuc.edu.cn/hls/dfhd.m3u8"
        assertTrue("判死的线即使正在播也要算进待删", CatalogMerge.isBlocked(played, dfBlocked))
        assertEquals(
            "三条判死全算，不再有「播过那条」的减项",
            3,
            CatalogMerge.blockedInUseCount(dfDevice7, dfBlocked),
        )
    }

    /** 黑名单为空或设备本来就没有这些地址 ⇒ 0，更新器照旧走 12 小时节流。 */
    @Test
    fun nothingToDropCountsAsZero() {
        assertEquals(0, CatalogMerge.blockedInUseCount(dfDevice7, emptySet()))
        assertEquals(0, CatalogMerge.blockedInUseCount(dfPackaged, dfBlocked))
    }

    /** 合并之后设备这一台就只剩资产那四条：三轮回测全灭的三条被黑名单挡掉。 */
    @Test
    fun mergingDropsTheDeadOnesAndKeepsFour() {
        val merged = CatalogMerge.streams(
            "东方卫视", dfDevice7, emptyList(),
            blocked = dfBlocked, category = "cctv", assetOrder = dfPackaged,
        )
        assertEquals("7 条摘掉 3 条死线＝4 条", 4, merged.size)
        assertTrue("留下的必须是资产那四条", dfPackaged.all { want -> merged.any { StreamUrl.sameLine(it, want) } })
    }
}
