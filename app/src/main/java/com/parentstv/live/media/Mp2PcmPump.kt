package com.parentstv.live.media

import javazoom.jl.decoder.Bitstream
import javazoom.jl.decoder.Decoder
import javazoom.jl.decoder.SampleBuffer
import java.io.ByteArrayOutputStream
import java.io.InputStream

/**
 * MP2（MPEG-1 Layer II）字节流 → 16 位小端交错 PCM。
 *
 * Bitstream 与 Decoder 都跨 poll 复用：实测每轮新建会在每个边界丢掉约 9 ms（那一段输出是 0），
 * 所以只在 jLayer 真读到数据尽头时重建，重建前把游标倒回最后一个整帧边界，一个字节都不丢。
 */
class Mp2PcmPump {

    var sampleRate = 0
        private set
    var channelCount = 0
        private set

    private var buffer = ByteArray(0)
    private var cursor = 0
    private var streamStart = 0
    private var advanced = 0
    private var bitstream: Bitstream? = null
    private var decoder: Decoder? = null
    private var ended = false
    private var phantomBytesRead = 0

    val hasBufferedInput: Boolean
        get() = buffer.size > cursor

    val bufferedBytes: Int
        get() = buffer.size - cursor

    fun feed(bytes: ByteArray, offset: Int = 0, length: Int = bytes.size) {
        if (length <= 0) return
        val grown = buffer.copyOf(buffer.size + length)
        System.arraycopy(bytes, offset, grown, buffer.size, length)
        buffer = grown
        compact()
    }

    /** 上游说没有更多数据了：末尾剩下的帧也要解干净。 */
    fun finish() {
        ended = true
    }

    fun reset() {
        closeStream()
        buffer = ByteArray(0)
        cursor = 0
        ended = false
    }

    /** 解出当前能解的整帧。没攒够一窗数据时返回空数组，等下一次 feed。 */
    fun poll(): ByteArray {
        val out = ByteArrayOutputStream(minOf(bufferedBytes * 6, 1 shl 20))
        while (true) {
            val cursorBefore = cursor
            if (!ensureStream()) break
            val stream = bitstream ?: break
            if (!ended && bufferedBytes < READ_AHEAD_KEEP_BYTES) break
            val header = try {
                stream.readFrame()
            } catch (e: Exception) {
                null
            }
            if (header == null) {
                closeStream()
                if (cursor == cursorBefore) break else continue
            }
            if (!decodeInto(stream, header, out)) {
                closeStream()
                if (cursor == cursorBefore) break else continue
            }
        }
        if (ended) closeStream()
        compact()
        return out.toByteArray()
    }

    private fun decodeInto(stream: Bitstream, header: javazoom.jl.decoder.Header, out: ByteArrayOutputStream): Boolean {
        val buffer = try {
            decoder?.decodeFrame(header, stream) as? SampleBuffer
        } catch (e: Exception) {
            null
        } ?: return false
        if (sampleRate == 0) {
            sampleRate = buffer.sampleFrequency
            channelCount = buffer.channelCount
        }
        val samples = buffer.buffer
        val length = buffer.bufferLength
        for (i in 0 until length) {
            val v = samples[i].toInt()
            out.write(v and 0xFF)
            out.write((v shr 8) and 0xFF)
        }
        try {
            stream.closeFrame()
        } catch (e: Exception) {
            // closeFrame 只在读到尽头时抱怨，帧本身已经解出来了。
        }
        // 实测：jLayer 的 framesize 不含 4 字节帧头（真样本 249 帧，Σ(framesize+4)==191232==文件长度）。
        advanced += header.framesize + FRAME_HEADER_BYTES
        return true
    }

    /** 起一轨新的 Bitstream：先把游标对到真正的帧头上，再让 jLayer 从那儿开始读。 */
    private fun ensureStream(): Boolean {
        if (bitstream != null) return true
        if (bufferedBytes < READ_AHEAD_KEEP_BYTES && !ended) return false
        val headerAt = findHeaderIndex()
        if (headerAt < 0) {
            if (bufferedBytes > DROP_UNSYNCED_AFTER_BYTES) {
                // 一直找不到帧头（源断了/全是坏字节）：扔最老的，不许无限攒。
                cursor = buffer.size - KEEP_UNSYNCED_BYTES
                streamStart = cursor
            }
            return false
        }
        cursor += headerAt
        if (!ended && buffer.size - cursor < READ_AHEAD_KEEP_BYTES) return false
        streamStart = cursor
        advanced = 0
        phantomBytesRead = 0
        decoder = Decoder()
        bitstream = Bitstream(PullStream())
        return true
    }

    private fun closeStream() {
        val stream = bitstream ?: return
        try {
            stream.close()
        } catch (e: Exception) {
            // 读到半帧时 close 会报错，这里没有要保的读数。
        }
        bitstream = null
        decoder = null
        // jLayer 的预读已经越过了最后一个整帧；把游标倒回去，剩下的字节重读，不丢。
        cursor = streamStart + advanced
        advanced = 0
    }

    /** 从头找一个站得住的帧头；返回它相对游标的偏移，找不到返回 -1。 */
    private fun findHeaderIndex(): Int {
        var i = cursor
        val limit = minOf(buffer.size - FRAME_HEADER_BYTES, cursor + SEARCH_WINDOW_BYTES)
        while (i < limit) {
            if (frameLengthAt(i) > 0) return i - cursor
            i++
        }
        return -1
    }

    /** 这一处是不是合法且完整的帧头：返回帧长度（不含 4 字节帧头），不是则 0。 */
    private fun frameLengthAt(offset: Int): Int {
        if (offset + FRAME_HEADER_BYTES > buffer.size) return 0
        if (buffer[offset].toInt() and 0xFF != 0xFF) return 0
        if (buffer[offset + 1].toInt() and 0xE0 != 0xE0) return 0
        val version = (buffer[offset + 1].toInt() shr 3) and 0x03
        val layer = (buffer[offset + 1].toInt() shr 1) and 0x03
        val bitrateIndex = (buffer[offset + 2].toInt() shr 4) and 0x0F
        val sampleRateIndex = (buffer[offset + 2].toInt() shr 2) and 0x03
        val padding = (buffer[offset + 2].toInt() shr 1) and 0x01
        if (version == 1 || layer != 2 || bitrateIndex == 0 || bitrateIndex == 15 || sampleRateIndex == 3) {
            return 0
        }
        val bitrateTable = if (version == 3) LAYER_2_BITRATES_MPEG1 else LAYER_2_BITRATES_MPEG2
        val sampleRateTable = when (version) {
            3 -> SAMPLE_RATES_MPEG1
            2 -> SAMPLE_RATES_MPEG2
            else -> SAMPLE_RATES_MPEG25
        }
        val bitrate = bitrateTable[bitrateIndex]
        val rate = sampleRateTable[sampleRateIndex]
        if (bitrate <= 0 || rate <= 0) return 0
        val samplesPerFrame = if (version == 3) 1152 else 576
        // 帧长（含 4 字节帧头）＝系数×码率/采样率＋填充位；这里返回 jLayer 那侧的「不含帧头」。
        val frameLength = samplesPerFrame / 8 * bitrate * 1000 / rate + padding
        return if (offset + frameLength > buffer.size) 0 else frameLength - FRAME_HEADER_BYTES
    }

    private fun compact() {
        if (cursor >= COMPACT_FROM_BYTES) {
            buffer = buffer.copyOfRange(cursor, buffer.size)
            streamStart -= cursor
            cursor = 0
        }
    }

    /** 交给 jLayer 的读口：读走的就是 buffer 里从游标往后的字节，读完算 EOF。 */
    private inner class PullStream : InputStream() {
        override fun read(): Int {
            if (cursor < buffer.size) {
                val v = buffer[cursor].toInt() and 0xFF
                cursor++
                return v
            }
            if (ended && phantomBytesRead < PHANTOM_AFTER_EOS_BYTES) {
                phantomBytesRead++
                return 0
            }
            return -1
        }

        override fun read(b: ByteArray, off: Int, len: Int): Int {
            if (len <= 0) return 0
            val available = buffer.size - cursor
            if (available <= 0) {
                // EOS 之后给 jLayer 的预读留一窗「幻影零」：不然它看不到邻帧头，末尾那几帧会拒绝同步。
                if (!ended || phantomBytesRead >= PHANTOM_AFTER_EOS_BYTES) return -1
                val n = minOf(len, PHANTOM_AFTER_EOS_BYTES - phantomBytesRead)
                java.util.Arrays.fill(b, off, off + n, 0.toByte())
                phantomBytesRead += n
                return n
            }
            val n = minOf(len, available)
            System.arraycopy(buffer, cursor, b, off, n)
            cursor += n
            return n
        }
    }

    companion object {
        /** Media3 的 `ts/MpegAudioReader` 对 Layer II 报的就是这一串 mime（真机读数）。 */
        const val MPEG_AUDIO_LAYER_2_MIME = "audio/mpeg-L2"

        /** 只有这一种音轨归软解；其余（AAC/AC3/E-AC3…）必须留给 MediaCodec 那一组，一行都不许抢。 */
        fun takesLayer2(sampleMimeType: String?): Boolean = sampleMimeType == MPEG_AUDIO_LAYER_2_MIME

        /**
         * 渲染器对某个采样格式该答的那一位，档位号只能取自媒体库自己（`androidx.media3.common.C`），不许自造数字。
         *
         * 为什么单独把这一格抽成纯函数：这一版 media3（1.5.1）`C.FORMAT_HANDLED == 4`，
         * 而 `RendererCapabilities.isFormatSupported()` 的字节码只认「== 4，或允许越界时 == 3」。
         * 之前这里写成自造的 `1`＝`FORMAT_UNSUPPORTED_SUBTYPE`＝不支持 ⇒ 轨道永不选中、`render()` 一次都不进，
         * 症状与真机读数一致（`Mp2Diag` 0 行、应用进程没有新 AudioTrack）。
         */
        fun formatSupport(sampleMimeType: String?): Int =
            if (takesLayer2(sampleMimeType)) {
                androidx.media3.common.C.FORMAT_HANDLED
            } else {
                androidx.media3.common.C.FORMAT_UNSUPPORTED_TYPE
            }

        /**
         * 渲染器"有没有东西可以放"的判据，照库自己的口径：
         * `MediaCodecRenderer.isReady() = isSourceReady() || hasOutputBuffer()`
         * ——上游有货算 ready，手上攒着没放完的也算 ready。
         *
         * 反过来的写法（"队列没满才算 ready"）在真机上是自锁：10-28 11:09 队列 294912 已超高水位、
         * 音轨 96000 装满后再不排空 ⇒ 永远报 false ⇒ 播放器停在 BUFFERING（视频还有 10.9 秒），
         * 而 `doSomeWork` 进 BUFFERING 时会 `stopRenderers()`，`onStopped()` 又把音轨 pause 掉 ⇒ 队列更排不空。
         *
         * 抽成纯函数是为了纯 JVM 夹具能钉住它：渲染器本体要 `AudioTrack`／`DecoderInputBuffer`，单测里建不起来。
         */
        fun readyToRender(queuedPcmBytes: Int, sourceReady: Boolean): Boolean =
            queuedPcmBytes > 0 || sourceReady

        /**
         * 媒体时钟：锚点 + 音轨播放头走过的帧数。
         *
         * 用户 10-28 13:0x 报「东方卫视 线4 字幕与声音不同步」——原实现没给播放器提供时钟，
         * 视频按墙钟走、声音还压在 AudioTrack 的 0.5 秒缓冲里，画面就比声音快约半秒。
         * 库里的口径是音频渲染器拿音轨的**实际播放头**当时钟（`MediaCodecAudioRenderer` 即如此），
         * 于是把这条算式抽成纯函数钉住：`frames/sampleRate` 才是声音真正走到的位置。
         */
        fun clockPositionUs(anchorMediaUs: Long, playedFrames: Long, sampleRate: Int): Long =
            anchorMediaUs + playedFrames * 1_000_000L / sampleRate.coerceAtLeast(1)

        /**
         * 抽头那边已经把 4 字节帧头吃掉了（`ts/MpegAudioReader` 自己数帧、只把帧体交给下游），
         * 而泵是靠同步字找帧界的，所以按「帧体长度 + 采样率 + 声道数」把头补回来。
         *
         * 只接受唯一解：三张版本码表 × 码率索引 × pad 位全枚举，凑出 `帧总长 == 体长 + 4` 的候选
         * **有且只有一个**才返回，否则 null——宁可如实报"对不上"，也不猜一个头把静音装成在播。
         *
         * CRC 那一位一律补成"无 CRC"（jLayer 不跳 CRC 字节）。真遇上带 CRC 的源，症状会从"没声音"
         * 变成"整段噪声"——那是另一格修法，别在这一格上顺手猜。
         */
        fun synthesizeHeader(sampleRate: Int, channelCount: Int, bodyBytes: Int): ByteArray? {
            if (sampleRate <= 0 || channelCount <= 0 || bodyBytes <= 0) return null
            val total = bodyBytes + FRAME_HEADER_BYTES
            var found: Int? = null
            for (version in VERSION_CANDIDATES) {
                val rates = sampleRateTable(version) ?: continue
                val srIndex = rates.indexOf(sampleRate)
                if (srIndex < 0 || srIndex == 3) continue
                val bitrates = if (version == VERSION_MPEG1) LAYER_2_BITRATES_MPEG1 else LAYER_2_BITRATES_MPEG2
                val divisor = if (version == VERSION_MPEG1) 144 else 72
                for (brIndex in 1 until bitrates.size) {
                    val kbps = bitrates[brIndex]
                    if (kbps <= 0) continue
                    for (pad in 0..1) {
                        if (divisor * kbps * 1000 / sampleRate + pad * PAD_SLOT_BYTES != total) continue
                        if (found != null) return null
                        val byte1 = SYNC_TAIL or (versionBits(version) shl 3) or (LAYER_II_BITS shl 1) or 1
                        val byte2 = (brIndex shl 4) or (srIndex shl 2) or (pad shl 1)
                        val byte3 = (if (channelCount == 1) MODE_MONO else MODE_STEREO) shl 6 or ORIGINAL_FLAG
                        found = (0xFF shl 24) or (byte1 shl 16) or (byte2 shl 8) or byte3
                    }
                }
            }
            val head = found ?: return null
            return byteArrayOf((head shr 24).toByte(), (head shr 16).toByte(), (head shr 8).toByte(), head.toByte())
        }

        private const val VERSION_MPEG1 = 1
        private const val VERSION_MPEG2 = 2
        private const val VERSION_MPEG25 = 3
        private val VERSION_CANDIDATES = intArrayOf(VERSION_MPEG1, VERSION_MPEG2, VERSION_MPEG25)

        // 帧头第 2 字节 = 同步字余下 3 位（111）+ 版本 2 位 + 层 2 位 + CRC 标志 1 位；Layer II 的层位是 10。
        private const val SYNC_TAIL = 0b111 shl 5
        private const val LAYER_II_BITS = 0b10
        private const val PAD_SLOT_BYTES = 4
        private const val MODE_STEREO = 0b00
        private const val MODE_MONO = 0b11
        private const val ORIGINAL_FLAG = 1 shl 2

        private fun sampleRateTable(version: Int): IntArray? = when (version) {
            VERSION_MPEG1 -> SAMPLE_RATES_MPEG1
            VERSION_MPEG2 -> SAMPLE_RATES_MPEG2
            else -> SAMPLE_RATES_MPEG25
        }

        private fun versionBits(version: Int): Int = when (version) {
            VERSION_MPEG1 -> 0b11
            VERSION_MPEG2 -> 0b10
            else -> 0b00
        }

        private const val FRAME_HEADER_BYTES = 4

        // jLayer 内部一次预读 4096 字节；留 8 KB 余量就不会在直播中途撞上 EOF。
        private const val READ_AHEAD_KEEP_BYTES = 8192
        private const val SEARCH_WINDOW_BYTES = 64 * 1024
        private const val COMPACT_FROM_BYTES = 128 * 1024
        private const val DROP_UNSYNCED_AFTER_BYTES = 192 * 1024
        private const val KEEP_UNSYNCED_BYTES = 32 * 1024
        private const val PHANTOM_AFTER_EOS_BYTES = 8192

        // Layer II 码表（kbps）：MPEG-1 与 MPEG-2/2.5 两张，索引取自帧头。
        private val LAYER_2_BITRATES_MPEG1 =
            intArrayOf(0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, -1)
        private val LAYER_2_BITRATES_MPEG2 =
            intArrayOf(0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, -1)
        private val SAMPLE_RATES_MPEG1 = intArrayOf(44100, 48000, 32000, -1)
        private val SAMPLE_RATES_MPEG2 = intArrayOf(22050, 24000, 16000, -1)
        private val SAMPLE_RATES_MPEG25 = intArrayOf(11025, 12000, 8000, -1)
    }
}
