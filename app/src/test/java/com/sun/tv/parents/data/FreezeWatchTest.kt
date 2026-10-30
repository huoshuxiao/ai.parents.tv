package com.sun.tv.parents.data

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

    @Test
    fun `F7 全 0 缓冲算看不见，不算画面冻住`() {
        // 10-26 小米电视 MiTV-ASTP0 现读：PixelCopy 回 SUCCESS 但整块是黑，
        // 哈希恒等于 -3823061780159657145（＝256×144 空缓冲逐位算出来的那个数）。
        val blank = IntArray(256 * 144)
        assertTrue(FreezeWatch.isBlank(blank, blank.size))
        assertEquals(-3823061780159657145L, FreezeWatch.hash(blank))
    }

    @Test
    fun `F8 不透明纯黑也算看不见（哈希不看 alpha，与空缓冲撞成同一个数）`() {
        val opaqueBlack = IntArray(256 * 144) { 0xFF000000.toInt() }
        assertTrue(FreezeWatch.isBlank(opaqueBlack, opaqueBlack.size))
        assertEquals(FreezeWatch.hash(IntArray(256 * 144)), FreezeWatch.hash(opaqueBlack))
    }

    @Test
    fun `F9 只要有一个像素 RGB 非 0 就不算空缓冲`() {
        val real = IntArray(256 * 144)
        real[12345] = 0xFF203040.toInt()   // 一个带颜色的像素：这台电视真把画面交出来了
        assertFalse(FreezeWatch.isBlank(real, real.size))
        assertNotEquals(FreezeWatch.hash(IntArray(256 * 144)), FreezeWatch.hash(real))
    }

    @Test
    fun `F10 count 之外残留的旧像素不许替这一帧说话`() {
        // 采样窗口比缓冲区小时只看前 count 个；后面残留的那一点颜色不算数。
        val stale = IntArray(10)
        stale[9] = 0xFF0000FF.toInt()
        assertTrue(FreezeWatch.isBlank(stale, 5))
        assertFalse(FreezeWatch.isBlank(stale, 10))
    }

    @Test
    fun `F11 帧计数那条读数（#152 甲）：帧号在涨永不报，停住满阈值才报一次`() {
        // 甲档把解码帧号当读数喂给同一个 observe。两种真读数：
        //  ① 25fps 正常播放：每 3 秒多 75 帧，连续 5 分钟一次都不许报；
        //  ② 解码停了：帧号停在 9000，第 29.9 秒不报、第 30 秒报一次、之后不再报。
        val live = FreezeWatch.State()
        for (k in 0..100) {                 // 0 → 300 秒
            assertFalse("正常播放第 $k 个采样点被判冻", FreezeWatch.observe(live, 9000L + k * 75, k * FreezeWatch.SAMPLE_MS))
        }
        val dead = FreezeWatch.State()
        assertFalse(FreezeWatch.observe(dead, 9000L, 70_000L))
        assertFalse(FreezeWatch.observe(dead, 9000L, 99_999L))
        assertTrue(FreezeWatch.observe(dead, 9000L, 100_000L))
        assertFalse(FreezeWatch.observe(dead, 9000L, 200_000L))
    }
}
