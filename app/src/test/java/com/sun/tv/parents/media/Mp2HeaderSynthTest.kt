package com.sun.tv.parents.media

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * 「抽头只给帧体，头要自己补回来」这一格的尺。锚点全部出自真样本
 * `mp2_from_36_110_54_118_hls28.mp2`（249 帧、每帧 768 字节 = 4 字节头 + 764 字节体；ffprobe：mp2/48000/2ch；
 * 第一帧头现读 `ff fd c4 04`）——这一族在真机上的病就是"没声音"，所以这里逐帧对表原始字节，不写手抄行形。
 */
class Mp2HeaderSynthTest {

    private companion object {
        const val SAMPLE_NAME = "mp2_from_36_110_54_118_hls28.mp2"
        const val FRAME_TOTAL = 768
        const val BODY = FRAME_TOTAL - 4
        const val RATE = 48000
        const val CHANNELS = 2
    }

    private val sample: ByteArray by lazy {
        val fromClasspath = javaClass.getResourceAsStream("/mp2/$SAMPLE_NAME")
        fromClasspath?.use { it.readBytes() } ?: File("src/test/resources/mp2/$SAMPLE_NAME").readBytes()
    }

    private fun frames(): List<ByteArray> =
        (0 until sample.size / FRAME_TOTAL).map { sample.copyOfRange(it * FRAME_TOTAL, (it + 1) * FRAME_TOTAL) }

    private fun pumpRun(chunks: List<ByteArray>): ByteArray {
        val pump = Mp2PcmPump()
        val all = ByteArrayOutputStream()
        for (chunk in chunks) {
            pump.feed(chunk)
            all.write(pump.poll())
        }
        pump.finish()
        while (true) {
            val pcm = pump.poll()
            if (pcm.isEmpty()) break
            all.write(pcm)
        }
        return all.toByteArray()
    }

    @Test
    fun synthesizedHeaderMatchesTheRealSampleFrameForFrame() {
        val fs = frames()
        assertEquals(249, fs.size)
        for ((k, frame) in fs.withIndex()) {
            val head = Mp2PcmPump.synthesizeHeader(RATE, CHANNELS, BODY)
            assertNotNull("第 $k 帧补不出头", head)
            assertArrayEquals("第 $k 帧的头与原始字节不符", frame.copyOfRange(0, 4), head)
        }
    }

    /** 这一格是真机那条路：喂进去的只有帧体（抽头吃掉头），补回头之后 PCM 必须与含头路径逐字节相同。 */
    @Test
    fun headerlessBodiesWithSynthesizedHeaderDecodeIdentically() {
        val withHeaders = pumpRun(listOf(sample))
        val bodies = frames().map { it.copyOfRange(4, FRAME_TOTAL) }
        val reassembled = ByteArrayOutputStream()
        for (body in bodies) {
            val head = Mp2PcmPump.synthesizeHeader(RATE, CHANNELS, body.size)!!
            reassembled.write(head)
            reassembled.write(body)
        }
        val viaSynth = pumpRun(listOf(reassembled.toByteArray()))
        assertEquals(withHeaders.size, viaSynth.size)
        assertArrayEquals(withHeaders, viaSynth)
        // 不修的话这条路一个字都不出（泵找不到同步字 ⇒ 全当杂音丢掉），所以这一格本身就是牙齿。
        assertEquals(0, pumpRun(bodies).size)
    }

    @Test
    fun bodyThatNoBitrateTableCanExplainIsRefused() {
        assertNull(Mp2PcmPump.synthesizeHeader(RATE, CHANNELS, BODY - 1))
        assertNull(Mp2PcmPump.synthesizeHeader(RATE, CHANNELS, BODY + 3))
    }

    @Test
    fun missingRateChannelsOrBodyAreRefused() {
        assertNull(Mp2PcmPump.synthesizeHeader(0, CHANNELS, BODY))
        assertNull(Mp2PcmPump.synthesizeHeader(-1, CHANNELS, BODY))
        assertNull(Mp2PcmPump.synthesizeHeader(RATE, 0, BODY))
        assertNull(Mp2PcmPump.synthesizeHeader(RATE, CHANNELS, 0))
        assertNull(Mp2PcmPump.synthesizeHeader(45000, CHANNELS, BODY))
    }

    @Test
    fun monoFrameDiffersOnlyInChannelModeBits() {
        assertArrayEquals(
            byteArrayOf(0xFF.toByte(), 0xFD.toByte(), 0xC4.toByte(), 0xC4.toByte()),
            Mp2PcmPump.synthesizeHeader(RATE, 1, BODY),
        )
    }

    /**
     * 这一族在真源里只出现三种帧几何（10-27 21:3x 用 `tools/mp2_crc_probe.py` 扫了 20 条 MP2 线，
     * 18 条可达的**全部**是 MPEG-1 / 48000Hz / 立体声 / 无 CRC，码率只有 96k、128k、256k 三档）：
     *   96k  ⇒ 帧长 288（体 284，pad=0）或 292（体 288，pad=1）
     *   128k ⇒ 帧长 384（体 380，pad=0）或 388（体 384，pad=1）
     *   256k ⇒ 帧长 768（体 764，pad=0）或 772（体 768，pad=1）
     * 每一档的两种 pad 都必须**唯一解**、且头字节与真源逐字相同（`fffd6404`/`fffd6604`/`fffd8404`/
     * `fffd8604`/`fffdc404`/`fffdc604` 都是真流里读到的，不是我推的）。256k pad=1 那一枚正是真机上
     * 四川卫视 线3 打出来的 `head=fffdc604`。
     */
    @Test
    fun theThreeRealGeometriesAndBothPadVariantsAreAllUnique() {
        fun h(b: Int) = Mp2PcmPump.synthesizeHeader(RATE, CHANNELS, b)?.joinToString("") { "%02x".format(it) }
        assertEquals("fffd6404", h(284))
        assertEquals("fffd6604", h(288))
        assertEquals("fffd8404", h(380))
        assertEquals("fffd8604", h(384))
        assertEquals("fffdc404", h(764))
        assertEquals("fffdc604", h(768))
        // 邻近体长一个都不许"凑"出头来：这一条挡的是"多解时随手挑一个"。
        for (b in listOf(283, 285, 379, 381, 763, 765)) {
            assertNull("体长 $b 不该有唯一解", Mp2PcmPump.synthesizeHeader(RATE, CHANNELS, b))
        }
    }
}
