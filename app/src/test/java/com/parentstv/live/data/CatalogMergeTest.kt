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
}
