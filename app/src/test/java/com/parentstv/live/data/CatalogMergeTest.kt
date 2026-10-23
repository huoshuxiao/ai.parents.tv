package com.parentstv.live.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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

    @Test
    fun thisTvAlreadyPlayedThatLineSoTheBlockListDoesNotApply() {
        // 用户 10-21 指令「已播过的线路绝不自动换线/换台」压过黑名单：
        // 境外抽帧看到的坏，抵不过这台电视真出过画面。
        val played = "http://74.91.26.218:82/live/cctv3hd.m3u8"
        val other = "http://39.135.138.58:18890/PLTV/88888888/224/3221225634/index.m3u8"
        val blocked = setOf(StreamUrl.canonical(played))
        val merged = CatalogMerge.streams("CCTV-3 综艺", listOf(other), listOf(played), played, blocked)
        assertEquals("播过的那条留下并排第一", listOf(played, other), merged)
        // 负对照：同一个黑名单、没播过记录时，那条线必须被挡掉（不然这一格就成了恒真）。
        assertEquals(emptyList<String>(),
            CatalogMerge.streams("CCTV-3 综艺", emptyList(), listOf(played), null, blocked))
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
}
