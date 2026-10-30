package com.sun.tv.parents.media

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 音画同步那条算式的尺：媒体位置 ＝ 锚点 + 音轨播放头帧数/采样率。
 *
 * 用户 10-28 13:0x 报「东方卫视 线4 字幕与声音不同步」。原因不是解码，而是**没有时钟**：
 * 原实现里播放器只能用墙钟推时间，声音却压在 AudioTrack 的 0.5 秒缓冲里
 * （同一份 `Mp2Diag` 读数：`轨缓冲=96000` 字节 @48000×2×2 ＝ 0.5 秒）。
 * 这一格把「声音真正走到哪」钉成纯函数，渲染器只负责把音轨播放头喂进来。
 */
class Mp2ClockTest {

    @Test
    fun oneSecondOfFramesIsOneSecondOfPosition() {
        assertEquals(1_000_000L, Mp2PcmPump.clockPositionUs(0L, 48_000L, 48_000))
        assertEquals(500_000L, Mp2PcmPump.clockPositionUs(0L, 24_000L, 48_000))
    }

    @Test
    fun anchorMovesTheWholeClock() {
        // 从 5 分钟的媒体位置起播：头走 1 秒 ⇒ 位置就是 301 秒。
        assertEquals(301_000_000L, Mp2PcmPump.clockPositionUs(300_000_000L, 48_000L, 48_000))
    }

    @Test
    fun otherSampleRatesDivideCorrectly() {
        assertEquals(1_000_000L, Mp2PcmPump.clockPositionUs(0L, 44_100L, 44_100))
        assertEquals(1_000_000L, Mp2PcmPump.clockPositionUs(0L, 32_000L, 32_000))
    }

    /** 采样率还没解出来时（0）不许除零：退回 1 Hz 的兜底，位置仍然是锚点。 */
    @Test
    fun zeroSampleRateDoesNotThrow() {
        val position = Mp2PcmPump.clockPositionUs(7_000L, 0L, 0)
        assertEquals(7_000L, position)
    }

    /** 32 位播放头回绕后按无符号取值（48 kHz 约 24.8 小时）：这一格钉住取数那一侧不会变负。 */
    @Test
    fun wrappedHeadStaysPositive() {
        val wrapped = 0xFFFF_FFFFL // 调用侧 `and 0xFFFFFFFF` 之后就是 4294967295
        assertEquals(89_478_485_312L, Mp2PcmPump.clockPositionUs(0L, wrapped, 48_000))
    }
}
