package com.parentstv.live.media

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 「只有 Layer II 归软解」这一句的尺：`MpegAudioRenderer.supportsFormat()` 只剩一次 `takesLayer2()` 调用，
 * 所以这一格把选择口径钉住就够了（渲染器本身在纯 JVM 里建不起来——`DecoderInputBuffer` 要 `new MediaCodec.BufferInfo()`，
 * 而本项目的单元测试没开 `returnDefaultValues`，也没装 Robolectric）。
 *
 * mime 那一串出自真机读数（10-27 那台 MiTV 上 `AudioWatch` 打的 `mime=audio/mpeg-L2`），
 * 写成字面量而不是常量对照，是因为常量写错时"两臂同源于同一处笔误"会一起绿。
 */
class Mp2RendererSelectionTest {

    @Test
    fun theMimeMedia3EmitsForLayer2() {
        assertEquals("audio/mpeg-L2", Mp2PcmPump.MPEG_AUDIO_LAYER_2_MIME)
    }

    @Test
    fun layer2IsTheOnlyTrackThisRendererTakes() {
        assertTrue(Mp2PcmPump.takesLayer2("audio/mpeg-L2"))
        assertFalse("AAC 必须留给 MediaCodec", Mp2PcmPump.takesLayer2("audio/mp4a-latm"))
        assertFalse("AC-3 走硬解", Mp2PcmPump.takesLayer2("audio/ac3"))
        assertFalse("E-AC-3 走硬解", Mp2PcmPump.takesLayer2("audio/eac3"))
        assertFalse("泛 MPEG 串不是 Layer II", Mp2PcmPump.takesLayer2("audio/mpeg"))
        assertFalse("Layer I 不接", Mp2PcmPump.takesLayer2("audio/mpeg-L1"))
        assertFalse("Layer III 不接", Mp2PcmPump.takesLayer2("audio/mpeg-L3"))
    }

    @Test
    fun missingMimeIsNotTaken() {
        assertFalse(Mp2PcmPump.takesLayer2(null))
        assertFalse(Mp2PcmPump.takesLayer2(""))
    }

    /** 这一格挡的是"哪天有人把它放宽成 contains"：放宽之后别的容器会被这个渲染器抢走，画面还在、声音换了一条路。 */
    @Test
    fun matchingIsExactNotSubstringOrCaseInsensitive() {
        assertFalse(Mp2PcmPump.takesLayer2("audio/mpeg-L22"))
        assertFalse(Mp2PcmPump.takesLayer2("AUDIO/MPEG-L2"))
        assertFalse(Mp2PcmPump.takesLayer2(" audio/mpeg-L2"))
        assertFalse(Mp2PcmPump.takesLayer2("audio/mpeg-L2; codecs=mp2"))
    }

    /**
     * 档位号这一格钉**字面量 4**，生产代码用的是 `C.FORMAT_HANDLED`——两臂不同源，
     * 库里这一版要是哪天不再是 4，红的是这一格，逼人来现读，而不是"跟着常量一起绿"。
     *
     * 它挡的真实事故是 10-27 那一版：`supportsFormat()` 自造了 `1`，而 1.5.1 里
     * `1 == FORMAT_UNSUPPORTED_SUBTYPE`（不支持），`RendererCapabilities.isFormatSupported()` 只认
     * 「== 4」，于是这条轨道永远选不上、`render()` 一次都没进，`Mp2Diag` 真机 0 行。
     */
    @Test
    fun layer2AnswersTheHandledFlag() {
        assertEquals("答 4 才会被选中（FORMAT_HANDLED）", 4, Mp2PcmPump.formatSupport("audio/mpeg-L2"))
        assertTrue("自造的 1 在这一版是「不支持」，正是那次没声音的形状", Mp2PcmPump.formatSupport("audio/mpeg-L2") != 1)
    }

    /** 其余采样格式必须答「类型不支持」＝0，让 MediaCodecAudioRenderer 先去接；抢了就把 AAC 拖进软解这条死路。 */
    @Test
    fun everythingElseAnswersUnsupportedType() {
        for (mime in listOf("audio/mp4a-latm", "audio/ac3", "audio/eac3", "audio/mpeg", "audio/mpeg-L1", null, "")) {
            assertEquals("不该抢的格式：$mime", 0, Mp2PcmPump.formatSupport(mime))
        }
    }
}
