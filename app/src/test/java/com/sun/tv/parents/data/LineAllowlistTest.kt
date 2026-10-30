package com.sun.tv.parents.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 验「乙」那条闸：公开源新发现的地址，只有出现在 `line_allowlist.json` 里才准进目录，
 * 其余一律进候选台账等过闸（10-28 00:04 那 155 条就是没这道闸时直接进设备目录的）。
 */
class LineAllowlistTest {

    @Test
    fun `白名单里的地址放行，其余进候选`() {
        val (keep, drop) = LineAllowlist.admit(
            listOf("http://a.example/live/1.m3u8", "http://b.example/live/2.m3u8"),
            listOf("http://a.example/live/1.m3u8"),
        )
        assertEquals(listOf("http://a.example/live/1.m3u8"), keep)
        assertEquals(listOf("http://b.example/live/2.m3u8"), drop)
    }

    @Test
    fun `比较走 canonical：大小写不同不算新地址`() {
        val (keep, drop) = LineAllowlist.admit(
            listOf("HTTP://A.Example/live/1.m3u8"),
            listOf("http://a.example/live/1.m3u8"),
        )
        assertEquals(1, keep.size)
        assertTrue(drop.isEmpty())
    }

    @Test
    fun `空白名单＝一条都不许自动加`() {
        val (keep, drop) = LineAllowlist.admit(listOf("http://c.example/x.m3u8"), emptyList())
        assertTrue(keep.isEmpty())
        assertEquals(1, drop.size)
    }

    @Test
    fun `没有候选时两边都是空，不产生空台账`() {
        val (keep, drop) = LineAllowlist.admit(emptyList(), listOf("http://d.example/x.m3u8"))
        assertTrue(keep.isEmpty())
        assertTrue(drop.isEmpty())
    }

    @Test
    fun `APK 里那份白名单能解析出来；坏文件当空表不炸`() {
        val raw = """{"generatedAt":"x","note":"n","lines":["http://e.example/x.m3u8"]}"""
        assertEquals(listOf("http://e.example/x.m3u8"), LineAllowlist.parse(raw))
        assertTrue(LineAllowlist.parse("{not json").isEmpty())
        assertTrue(LineAllowlist.parse("").isEmpty())
    }
}
