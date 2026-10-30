package com.sun.tv.parents.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 「画面停住不动了」这条判据的正负对照（用户 2026-10-30 CCTV 4K 假在播真冻住）。
 *
 * 三格必须同时成立才算这把尺有牙：
 *  ① 真冻住（playing=true 且位置不动）到点必须判红；
 *  ② 缓冲/暂停（playing=false，位置本来就不动）**绝不许**判红——10-30 限速实验就是这个形状；
 *  ③ 位置在前进时，哪怕一直在 playing 也不许判红。
 */
class PosFreezeTrackerTest {

    private fun feed(t: PosFreezeTracker, times: Int, pos: Long, playing: Boolean = true) {
        repeat(times) { t.sample(pos, playing) }
    }

    @Test
    fun `真冻住到点判红`() {
        val t = PosFreezeTracker(limitMs = 60_000)
        // 第一拍只记起点、不累加（那时还没有「上一拍」可比）。所以「位置连续 60 秒没动」
        // 在第 61 拍读出 —— 比配置值晚 1 秒，这是有意的：宁可晚一秒，也不拿没基准的第一拍判红。
        feed(t, 60, pos = 12_000)
        assertFalse("第 60 拍（已计 59 秒）还不该判红", t.frozen)
        assertEquals(59_000, t.frozenForMs)
        t.sample(12_000, true)
        assertTrue("第 61 拍（计满 60 秒）必须判红", t.frozen)
        assertEquals(60_000, t.frozenForMs)
    }

    @Test
    fun `缓冲时位置不动不许判红`() {
        val t = PosFreezeTracker(limitMs = 60_000)
        // 10-30 13:10 限速实验现读：state=2 playing=false，pos 停在 0/21400
        feed(t, 200, pos = 0, playing = false)
        assertFalse(t.frozen)
        assertEquals(0, t.frozenForMs)
    }

    @Test
    fun `位置在前进不许判红`() {
        val t = PosFreezeTracker(limitMs = 60_000)
        repeat(300) { i -> t.sample(1_000L * i, true) }
        assertFalse(t.frozen)
        assertEquals(0, t.frozenForMs)
    }

    @Test
    fun `冻了一阵又恢复要归零`() {
        val t = PosFreezeTracker(limitMs = 60_000)
        feed(t, 31, pos = 5_000)
        assertEquals("第一拍只记起点，31 拍累计 30 秒", 30_000, t.frozenForMs)
        t.sample(6_000, true)
        assertEquals("位置一动就归零", 0, t.frozenForMs)
        assertFalse(t.frozen)
    }

    @Test
    fun `位置从 0 开始也不误判`() {
        val t = PosFreezeTracker(limitMs = 60_000)
        // 直播起播那一下 lastPos 还没值：第一拍只记起点、不累加
        assertEquals(0, t.sample(0, true))
        assertEquals(1_000, t.sample(0, true))
        assertEquals(2_000, t.sample(0, true))
    }
}
