package com.parentstv.live.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 验一件事：画面静止这条尺要能在「像素一直不变」时报一次、在「像素还在变」时永不报，
 * 而且一次停留里只许报一次。
 *
 * 三种会说谎的情形：
 *  1) 恒真——observe 写成「只要哈希相同就报」，那第二个采样点就把电影里的一个正常定格判成冻帧，
 *     电视上每隔几秒黑一次。F1/F2 盯这一格。
 *  2) 恒假——阈值或起点算错（比如拿 nowMs 当起点减自己），永远凑不满 30 秒，
 *     这一档就变成一段谁都不读的装饰代码。F3 盯这一格。
 *  3) 量化吃不掉噪声——真冻帧时编码器还在出新的比特流，逐位比会一直"在动"，
 *     于是这把尺在真故障上失明。F5 用「同一幅画加一层 LSB 噪声」盯这一格。
 */
class FreezeWatchTest {

    private fun frame(seed: Int, lsb: Int = 0): IntArray {
        val px = IntArray(32 * 18)
        for (i in px.indices) {
            val v = ((seed + i * 7) and 0xF0) or ((seed + i) and 0x0F)
            px[i] = (v shl 16) or (v shl 8) or v or (lsb and 1)
        }
        return px
    }

    @Test
    fun `F0 同一帧哈希稳定，不同帧哈希不同`() {
        assertEquals(FreezeWatch.hash(frame(3)), FreezeWatch.hash(frame(3)))
        assertNotEquals(FreezeWatch.hash(frame(3)), FreezeWatch.hash(frame(9)))
    }

    @Test
    fun `F1 静止未满阈值不报`() {
        val st = FreezeWatch.State()
        val h = FreezeWatch.hash(frame(5))
        assertFalse(FreezeWatch.observe(st, h, 0L))
        assertFalse(FreezeWatch.observe(st, h, FreezeWatch.SILENCE_MS - 1))
    }

    @Test
    fun `F2 静止满阈值报一次，之后不再报`() {
        val st = FreezeWatch.State()
        val h = FreezeWatch.hash(frame(5))
        assertFalse(FreezeWatch.observe(st, h, 0L))
        assertTrue(FreezeWatch.observe(st, h, FreezeWatch.SILENCE_MS))
        // 牙齿：再喂 10 个采样点也不许第二次报——否则每 3 秒重连一次
        for (k in 1..10) {
            assertFalse("第 $k 个采样点又报了", FreezeWatch.observe(st, h, FreezeWatch.SILENCE_MS + k * FreezeWatch.SAMPLE_MS))
        }
    }

    @Test
    fun `F3 画面一变就把起点挪走`() {
        val st = FreezeWatch.State()
        assertFalse(FreezeWatch.observe(st, FreezeWatch.hash(frame(1)), 0L))
        assertFalse(FreezeWatch.observe(st, FreezeWatch.hash(frame(1)), 20_000L))
        // 第 25 秒画面动了：冻帧计数作废，从此刻重起
        assertFalse(FreezeWatch.observe(st, FreezeWatch.hash(frame(2)), 25_000L))
        assertFalse(FreezeWatch.observe(st, FreezeWatch.hash(frame(2)), 40_000L))
        assertTrue(FreezeWatch.observe(st, FreezeWatch.hash(frame(2)), 55_000L))
    }

    @Test
    fun `F4 第一帧只记起点不作判`() {
        val st = FreezeWatch.State()
        val h = FreezeWatch.hash(frame(7))
        // 只有一帧读数时，「与上一帧相同」这件事根本不成立，不许判冻
        assertFalse(FreezeWatch.observe(st, h, 0L))
        assertEquals(0L, st.sinceMs)
        assertFalse(st.fired)
    }

    @Test
    fun `F5 逐位噪声被量化吃掉`() {
        val clean = frame(11, lsb = 0)
        val noisy = frame(11, lsb = 1)
        assertNotEquals(java.util.Arrays.hashCode(clean), java.util.Arrays.hashCode(noisy))
        val st = FreezeWatch.State()
        val h = FreezeWatch.hash(clean)
        assertFalse(FreezeWatch.observe(st, h, 0L))
        assertTrue(FreezeWatch.observe(st, FreezeWatch.hash(noisy), FreezeWatch.SILENCE_MS))
    }

    @Test
    fun `F6 reset 之后允许再报一次`() {
        val st = FreezeWatch.State()
        val h = FreezeWatch.hash(frame(4))
        assertFalse(FreezeWatch.observe(st, h, 0L))
        assertTrue(FreezeWatch.observe(st, h, FreezeWatch.SILENCE_MS))
        FreezeWatch.reset(st)
        assertFalse(FreezeWatch.observe(st, h, FreezeWatch.SILENCE_MS + 3_000))
        assertTrue(FreezeWatch.observe(st, h, FreezeWatch.SILENCE_MS * 2 + 3_000))
    }
}
