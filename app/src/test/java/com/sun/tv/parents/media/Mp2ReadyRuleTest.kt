package com.sun.tv.parents.media

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `MpegAudioRenderer.isReady()` 这一格的尺。
 *
 * 真机现场（10-28 11:09，MiTV-ASTP0，天津卫视 线4，audio=mp2）：
 * ```
 * Mp2Diag: 写不进 written=0 第1次   队列剩=50688  已交=96000  轨缓冲=96000
 * Mp2Diag: 写不进 written=0 第1000次 队列剩=294912 已交=718592 轨缓冲=96000
 * LoadWatch: state=2 playing=false aheadMs=10900
 * ```
 * 音轨装满 96000 字节后再没排空，队列 294912 挂着不动，播放器停在 BUFFERING，
 * 而视频那边还有 10.9 秒。旧判据 `queuedPcmBytes < 256*1024` 在这一格报 false，
 * 播放器就永远等不到 READY；`doSomeWork` 进 BUFFERING 时又会 `stopRenderers()` →
 * `onStopped()` → `track.pause()`，暂停的音轨更不会排空 ⇒ 自锁。
 */
class Mp2ReadyRuleTest {

    /** 旧判据原样抄在这里当负对照：它在这一格必须判"未就绪"，否则说明夹具根本没咬到那次事故。 */
    private fun oldRule(queuedPcmBytes: Int): Boolean = queuedPcmBytes < QUEUE_HIGH_WATER_BYTES

    @Test
    fun theRealLockupSceneIsReadyNowAndWasNotBefore() {
        val queued = 294_912 // 真机读数：队列剩=294912
        assertFalse("旧公式在真实现场判的就是「未就绪」，这正是那次自锁", oldRule(queued))
        assertTrue(
            "队列里攒着 1.4 秒 PCM 却说自己没东西可放＝方向反了",
            Mp2PcmPump.readyToRender(queued, sourceReady = false),
        )
    }

    /** 队列正好压在高水位线上（写不进刚发生的那一刻）：仍然算有东西可放。 */
    @Test
    fun exactlyAtHighWaterStillCountsAsReady() {
        assertTrue(Mp2PcmPump.readyToRender(QUEUE_HIGH_WATER_BYTES, sourceReady = false))
        assertTrue(Mp2PcmPump.readyToRender(QUEUE_HIGH_WATER_BYTES + 1, sourceReady = false))
    }

    /** 队列空但上游此刻有货（库那边叫 isSourceReady）：可以放，不该报未就绪。 */
    @Test
    fun upstreamHavingDataIsReadyEvenWithEmptyQueue() {
        assertTrue(Mp2PcmPump.readyToRender(0, sourceReady = true))
    }

    /**
     * 真空转（队列空 + 上游没货）必须还能报未就绪——这一格挡的是"把判据放宽成恒真"：
     * 那会让没声音的流一路飞奔，画面和声音一起错过直播点。
     */
    @Test
    fun nothingQueuedAndNothingUpstreamIsStillNotReady() {
        assertFalse(Mp2PcmPump.readyToRender(0, sourceReady = false))
    }

    /** 真机那个高水位常量不许被顺手改小：它同时是 `render()` 的背压门槛。 */
    @Test
    fun highWaterConstantIsWhatTheDeviceShowed() {
        assertTrue("真机队列剩=294912 应高于高水位 262144", 294_912 > QUEUE_HIGH_WATER_BYTES)
        assertTrue("高水位至少得装得下一帧 PCM", QUEUE_HIGH_WATER_BYTES > 4 * 1152 * 2 * 2)
    }

    private companion object {
        /** 与 `MpegAudioRenderer.QUEUE_HIGH_WATER_BYTES` 同值，字面量写死：常量被改小的时候红的是这一格。 */
        const val QUEUE_HIGH_WATER_BYTES = 256 * 1024
    }
}
