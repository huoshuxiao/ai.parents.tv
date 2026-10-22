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
}
