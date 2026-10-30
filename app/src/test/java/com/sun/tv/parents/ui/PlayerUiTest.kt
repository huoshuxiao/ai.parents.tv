package com.sun.tv.parents.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 2026-10-28 用户现读「只有声音、没有视频、没有倒计时」两半各一条判据。
 * 这里锁的是**真机那一串读数**：36.110 那条线 `aheadMs` 只有 406–892 ms、音频在播、画面一帧没有。
 */
class PlayerUiTest {

    @Test
    fun waitingIsShownWheneverNoFrameYet() {
        // 正对照（本次要修的那一格）：音频在播（isPlaying=true）、没在缓冲、但**一帧都没出过**
        // ⇒ 旧判据 `缓冲中 && 没在播` 为假 ⇒ 屏幕什么都不显示；新判据必须显示。
        assertTrue(PlayerUi.waitingForPicture(renderedFrame = false, buffering = false, playing = true))
        // 老场景不许丢：出过画面之后又卡住（缓冲中且没在播）仍要显示。
        assertTrue(PlayerUi.waitingForPicture(renderedFrame = true, buffering = true, playing = false))
        // 正常播放：不显示。
        assertFalse(PlayerUi.waitingForPicture(renderedFrame = true, buffering = false, playing = true))
        // 出过画面、在缓冲但仍在播（追帧阶段）也不显示——别在好线上闪提示。
        assertFalse(PlayerUi.waitingForPicture(renderedFrame = true, buffering = true, playing = true))
    }

    @Test
    fun volumeStaysOffUntilFirstFrameButNeverMutesAudioOnlyStreams() {
        // 正对照：有音轨、有视频轨、还没出画面 ⇒ 静音（这就是「有声黑屏」那一格）。
        assertEquals(0f, PlayerUi.volumeFor(renderedFrame = false, hasAudioTrack = true, hasVideoTrack = true), 0.0f)
        // 出画面 ⇒ 正常音量。
        assertEquals(1f, PlayerUi.volumeFor(renderedFrame = true, hasAudioTrack = true, hasVideoTrack = true), 0.0f)
        // 纯音频流（广播类）：有音轨、无视频轨 ⇒ 不许静音，否则永远没声。
        assertEquals(1f, PlayerUi.volumeFor(renderedFrame = false, hasAudioTrack = true, hasVideoTrack = false), 0.0f)
        // 轨道表还没填起来（起播头一两秒）：两者都假 ⇒ 保持静音，正是要遮住的那一小段。
        assertEquals(0f, PlayerUi.volumeFor(renderedFrame = false, hasAudioTrack = false, hasVideoTrack = false), 0.0f)
    }
}
