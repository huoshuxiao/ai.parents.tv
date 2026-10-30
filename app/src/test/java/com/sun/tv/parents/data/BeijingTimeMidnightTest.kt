package com.sun.tv.parents.data

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

/** 节目单「午夜 12 点自动更新一次」那一臂的延时算法（用户 2026-10-30 要求可复核）。 */
class BeijingTimeMidnightTest {

    private val zone = ZoneId.of(BeijingTime.ZONE_ID)

    private fun at(y: Int, m: Int, d: Int, h: Int, min: Int, s: Int = 0): Long =
        ZonedDateTime.of(y, m, d, h, min, s, 0, zone).toInstant().toEpochMilli()

    @Test
    fun `正午到零点正好十二小时`() {
        assertEquals(12L * 60 * 60 * 1000, BeijingTime.millisToNextMidnight(at(2026, 10, 30, 12, 0)))
    }

    @Test
    fun `零点整算到第二天二十四小时`() {
        assertEquals(24L * 60 * 60 * 1000, BeijingTime.millisToNextMidnight(at(2026, 10, 30, 0, 0)))
    }

    @Test
    fun `零点前三十秒被夹到一分钟`() {
        assertEquals(60_000L, BeijingTime.millisToNextMidnight(at(2026, 10, 30, 23, 59, 30)))
    }

    @Test
    fun `零点前一分钟不夹取`() {
        assertEquals(60_000L, BeijingTime.millisToNextMidnight(at(2026, 10, 30, 23, 59, 0)))
    }

    @Test
    fun `跨月边界`() {
        // 10 月 31 日 23:00 → 还剩 1 小时到 11 月 1 日 00:00
        assertEquals(60L * 60 * 1000, BeijingTime.millisToNextMidnight(at(2026, 10, 31, 23, 0)))
    }
}
