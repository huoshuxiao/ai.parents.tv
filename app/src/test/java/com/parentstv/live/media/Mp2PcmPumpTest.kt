package com.parentstv.live.media

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * 夹具用的样本是从 36.110.54.118:866/hls/28 的真分片抽出来的音轨（ffprobe：mp2/48000/2ch）。
 * 期望读数出自同一次实测（tools/.cache/mp2/Probe.java）：191232 字节 = 249 帧、573696 个样本、max|s|=4767。
 *
 * EOS 那一格按实测记账：jLayer 不给「后面没有帧头」的最后一帧做同步，所以 finish() 之后
 * 只吐得出前 248 帧（571392 个样本、rms 567.022、5.952 秒），最后 768 字节留在缓存里。
 */
class Mp2PcmPumpTest {

    private val sample: ByteArray by lazy { readSample() }

    private fun readSample(): ByteArray {
        val fromClasspath = javaClass.getResourceAsStream("/mp2/$SAMPLE_NAME")
        val bytes = fromClasspath?.use { it.readBytes() }
            ?: File("src/test/resources/mp2/$SAMPLE_NAME").readBytes()
        assertEquals(SAMPLE_BYTES, bytes.size)
        return bytes
    }

    private fun shortsOf(pcm: ByteArray): ShortArray {
        val out = ShortArray(pcm.size / 2)
        for (i in out.indices) {
            out[i] = ((pcm[i * 2].toInt() and 0xFF) or (pcm[i * 2 + 1].toInt() shl 8)).toShort()
        }
        return out
    }

    private fun drain(pump: Mp2PcmPump, into: ByteArrayOutputStream) {
        while (true) {
            val pcm = pump.poll()
            if (pcm.isEmpty()) break
            into.write(pcm)
        }
    }

    /** 生产里的用法：喂一段解一段，最后告诉它没有更多数据了。 */
    private fun run(chunks: List<ByteArray>): Pair<Mp2PcmPump, ByteArray> {
        val pump = Mp2PcmPump()
        val all = ByteArrayOutputStream()
        for (chunk in chunks) {
            pump.feed(chunk)
            all.write(pump.poll())
        }
        pump.finish()
        drain(pump, all)
        return pump to all.toByteArray()
    }

    @Test
    fun realSampleDecodesToAudiblePcm() {
        val (pump, pcm) = run(listOf(sample))
        assertEquals(48000, pump.sampleRate)
        assertEquals(2, pump.channelCount)
        assertEquals(EXPECTED_PCM_BYTES, pcm.size)
        assertEquals("最后一帧按 EOS 记账", FRAME_BYTES, pump.bufferedBytes)
        val shorts = shortsOf(pcm)
        assertEquals(571392, shorts.size)
        assertEquals(4767, shorts.maxOf { Math.abs(it.toInt()) })
        val rms = Math.sqrt(shorts.sumOf { it.toDouble() * it.toDouble() } / shorts.size)
        assertTrue("rms=$rms 太小就是静音或脏数据", rms > 200.0)
        assertEquals(5.952, shorts.size / 2.0 / 48000.0, 0.001)
    }

    @Test
    fun feedingInTsSizedChunksGivesByteIdenticalPcm() {
        val oneShot = run(listOf(sample)).second
        val chunks = sample.toList().chunked(752).map { it.toByteArray() }
        val (pump, chunked) = run(chunks)
        assertArrayEquals("跨帧切着喂必须和一口喂逐字节相同（不许在边界丢样本）", oneShot, chunked)
        assertEquals(EXPECTED_PCM_BYTES, chunked.size)
        assertEquals(FRAME_BYTES, pump.bufferedBytes)
    }

    @Test
    fun everyPollEmitsWholeFramesOnly() {
        val pump = Mp2PcmPump()
        var off = 0
        while (off < sample.size) {
            val n = minOf(752, sample.size - off)
            pump.feed(sample, off, n)
            assertEquals(0, pump.poll().size % PCM_BYTES_PER_FRAME)
            off += n
        }
        pump.finish()
        while (true) {
            val pcm = pump.poll()
            if (pcm.isEmpty()) break
            assertEquals(0, pcm.size % PCM_BYTES_PER_FRAME)
        }
    }

    @Test
    fun incompleteTailFrameIsKeptNotGarbled() {
        val oneShot = run(listOf(sample)).second
        val pump = Mp2PcmPump()
        val out = ByteArrayOutputStream()
        pump.feed(sample.copyOf(sample.size - 10))
        pump.finish()
        drain(pump, out)
        val partial = out.toByteArray()
        assertEquals("末尾半帧不许当杂音吐出来", 0, partial.size % PCM_BYTES_PER_FRAME)
        assertArrayEquals(oneShot, partial)
        assertEquals("半帧的字节必须还留着不丢", SAMPLE_BYTES - 10 - DECODED_INPUT_BYTES, pump.bufferedBytes)
        // 补齐那 10 字节：整帧回来了，仍然按 EOS 那一格压着，不多吐一帧脏数据
        pump.feed(sample, sample.size - 10, 10)
        drain(pump, out)
        assertEquals(EXPECTED_PCM_BYTES, out.size())
        assertEquals(FRAME_BYTES, pump.bufferedBytes)
    }

    @Test
    fun garbageBeforeFirstFrameIsDroppedAndFramesSurvive() {
        val oneShot = run(listOf(sample)).second
        val (pump, pcm) = run(listOf(ByteArray(4096), sample))
        assertArrayEquals(oneShot, pcm)
        assertEquals(EXPECTED_PCM_BYTES, pcm.size)
        assertEquals(FRAME_BYTES, pump.bufferedBytes)
    }

    @Test
    fun endlessGarbageStopsGrowing() {
        val pump = Mp2PcmPump()
        pump.feed(ByteArray(200 * 1024) { (it % 251).toByte() })
        assertEquals(0, pump.poll().size)
        assertTrue("解不出帧时按上限截断，实测剩 ${pump.bufferedBytes}", pump.bufferedBytes <= 64 * 1024)
    }

    private companion object {
        const val SAMPLE_NAME = "mp2_from_36_110_54_118_hls28.mp2"
        const val SAMPLE_BYTES = 191232
        const val FRAME_BYTES = 768
        const val PCM_BYTES_PER_FRAME = 1152 * 2 * 2
        const val DECODED_INPUT_BYTES = 248 * FRAME_BYTES
        const val EXPECTED_PCM_BYTES = 571392 * 2
    }
}
