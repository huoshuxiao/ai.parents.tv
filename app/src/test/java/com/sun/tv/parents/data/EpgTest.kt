package com.sun.tv.parents.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar
import java.util.TimeZone

class EpgNameTest {

    @Test
    fun `央视名归一后能对上节目单的简写`() {
        assertEquals("cctv1综合", ChannelName.normalize("CCTV-1 综合"))
        assertTrue(ChannelName.lookupKeys("CCTV-1 综合").contains("cctv1"))
        assertTrue(ChannelName.lookupKeys("CCTV1").contains("cctv1"))
    }

    @Test
    fun `清晰度后缀不参与匹配`() {
        assertEquals("北京卫视", ChannelName.normalize("北京卫视高清"))
        assertTrue(ChannelName.lookupKeys("湖南卫视 4K").contains("湖南卫视"))
    }

    @Test
    fun `4K与8K里的K是清晰度不是频道号`() {
        // 10-30 电视实测：「CCTV 8K 节目单使用的是 CCTV 8」——旧逻辑把 CCTV-8K 的 8 当频道号，
        // 于是 8K 台拿着 CCTV-8 的节目单。4K 同病（会拿 CCTV-4 中文国际的）。
        assertTrue(ChannelName.lookupKeys("CCTV-8K 超高清").contains("cctv8k"))
        assertFalse(ChannelName.lookupKeys("CCTV-8K 超高清").contains("cctv8"))
        assertTrue(ChannelName.lookupKeys("CCTV-4K 超高清").contains("cctv4k"))
        assertFalse(ChannelName.lookupKeys("CCTV-4K 超高清").contains("cctv4"))
        // 负对照：真·CCTV-8 仍然要能命中 cctv8，且不许跑到 8k 去。
        assertTrue(ChannelName.lookupKeys("CCTV-8 电视剧").contains("cctv8"))
        assertFalse(ChannelName.lookupKeys("CCTV-8 电视剧").contains("cctv8k"))
    }

    @Test
    fun `8K频道不会拿到CCTV8的节目单4K能拿到自己的`() {
        val snapshot = EpgSnapshot(
            windowStart = 0L,
            windowEnd = Long.MAX_VALUE,
            entries = mapOf(
                "cctv8" to listOf(EpgProgram("电视剧名称", 0L, 1L)),
                "cctv4k" to listOf(EpgProgram("4K节目", 0L, 1L)),
            ),
        )
        // now=0：夹具里的节目是 0–1 ms 这一格，用真实时间取会被「已经结束」过滤掉（假红）。
        assertTrue(snapshot.programsFor("CCTV-8K 超高清", 0L).isEmpty())
        assertEquals("4K节目", snapshot.programsFor("CCTV-4K 超高清", 0L).firstOrNull()?.title)
    }

    @Test
    fun `未知频道不会命中`() {
        val snapshot = EpgSnapshot(
            windowStart = 0L,
            windowEnd = Long.MAX_VALUE,
            entries = mapOf("cctv1" to listOf(EpgProgram("新闻联播", 0L, 1L))),
        )
        assertTrue(snapshot.programsFor("辽宁体育").isEmpty())
        assertNull(snapshot.onAir("辽宁体育"))
    }
}

class XmltvTimeTest {

    @Test
    fun `带偏移的时间按偏移换算成 utc`() {
        val expected = Calendar.getInstance(TimeZone.getTimeZone("UTC")).apply {
            isLenient = false
            clear()
            set(2026, Calendar.OCTOBER, 21, 11, 30, 0)
        }.timeInMillis
        assertEquals(expected, XmltvTime.parse("20261021193000 +0800"))
    }

    @Test
    fun `省略偏移时按北京时间读`() {
        val expected = Calendar.getInstance(TimeZone.getTimeZone("UTC")).apply {
            isLenient = false
            clear()
            set(2026, Calendar.OCTOBER, 21, 11, 30, 0)
        }.timeInMillis
        assertEquals(expected, XmltvTime.parse("20261021193000"))
    }

    @Test
    fun `负偏移换算方向正确`() {
        val plus = XmltvTime.parse("20261021193000 +0800") ?: 0L
        val minus = XmltvTime.parse("20261021193000 -0500") ?: 0L
        assertEquals(13L * 60 * 60 * 1000, minus - plus)
    }

    @Test
    fun `残缺时间返回空而不是猜`() {
        assertNull(XmltvTime.parse(null))
        assertNull(XmltvTime.parse(""))
        assertNull(XmltvTime.parse("20261021"))
    }
}

class EpgSnapshotTest {

    private val morning = EpgProgram("早间新闻", 1_000L, 5_000L)
    private val now = EpgProgram("体育新闻", 5_000L, 9_000L)
    private val later = EpgProgram("晚间直播", 9_000L, 20_000L)
    private val snapshot = EpgSnapshot(
        windowStart = 0L,
        windowEnd = 30_000L,
        entries = mapOf("辽宁体育" to listOf(morning, now, later)),
    )

    @Test
    fun `正在播出只认已经开始的节目`() {
        assertEquals(now, snapshot.onAir("辽宁体育", 6_000L))
        assertNull(snapshot.onAir("辽宁体育", 500L))
    }

    @Test
    fun `节目单会丢掉已经结束的部分`() {
        val list = snapshot.programsFor("辽宁体育", 6_000L)
        assertEquals(listOf(now, later), list)
        assertFalse(list.contains(morning))
    }

    @Test
    fun `窗口右端是开区间`() {
        assertTrue(snapshot.covers(0L))
        assertTrue(snapshot.covers(29_999L))
        assertFalse(snapshot.covers(30_000L))
    }

    @Test
    fun `空快照不报错`() {
        assertTrue(EpgSnapshot().isEmpty)
        assertTrue(EpgSnapshot().programsFor("CCTV-1 综合").isEmpty())
    }
}
