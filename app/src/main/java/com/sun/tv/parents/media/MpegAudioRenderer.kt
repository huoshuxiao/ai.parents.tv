package com.sun.tv.parents.media

import android.media.AudioAttributes
import android.media.AudioFormat as PlatformAudioFormat
import android.media.AudioTrack
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.PlaybackParameters
import androidx.media3.decoder.DecoderInputBuffer
import androidx.media3.exoplayer.BaseRenderer
import androidx.media3.exoplayer.FormatHolder
import androidx.media3.exoplayer.MediaClock
import androidx.media3.exoplayer.Renderer
import androidx.media3.exoplayer.RendererCapabilities

/**
 * 盒子没有 MP2 硬解（MediaCodec 报不出解码器）时，用 jLayer 软解补上这一条音轨。
 * 只认 audio/mpeg-L2，其余采样格式仍由 MediaCodecAudioRenderer 先接。
 */
class MpegAudioRenderer : BaseRenderer(C.TRACK_TYPE_AUDIO) {

    private val pump = Mp2PcmPump()
    // 这块读样本的缓冲必须让库自己分配（DIRECT）。原来写的是 DISABLED（"我自己管内存"），
    // 而该模式下 `data == null` 时 `ensureSpaceForWrite` 不许分配、直接抛 InsufficientCapacityException
    // ⇒ `render()` 第一拍就炸、ExoPlayer 报 Playback error（10-27 20:52 真机栈：readOneSample:102）。
    private val input = DecoderInputBuffer(DecoderInputBuffer.BUFFER_REPLACEMENT_MODE_DIRECT)
    private val readFormatHolder: FormatHolder = getFormatHolder()
    private val pcmQueue = ArrayDeque<ByteArray>()
    private var pcmQueueHeadOffset = 0
    private var queuedPcmBytes = 0
    private var track: AudioTrack? = null
    private var volume = 1f
    private var streamEnded = false
    /** 上游此刻手里有没有货（最近一次 `readSource` 的结果）；与"队列有多满"是两件事。 */
    private var sourceReady = false
    /** 当前这一轨 AudioTrack 的播放头 0 对应的媒体位置（每次 `onPositionReset` 重新锚定）。 */
    private var anchorMediaUs = 0L
    /** 自锚点以来真正写进音轨的字节数：音轨还没建时拿它兜底。 */
    private var bytesWrittenSinceAnchor = 0L
    private var lastSyncLogMs = 0L
    private var audioFormat: Format? = null
    private var headerOk = 0
    private var headerFailed = 0
    // 「有声但断」那一格的读数（10-27 甲档：只量不改行为）。
    private var minBufferBytes = 0
    private var trackBufferBytes = 0
    private var writeBlocked = 0
    private var pcmWrittenBytes = 0

    override fun getName(): String = "MpegAudioRenderer"

    override fun supportsFormat(format: Format): Int =
        RendererCapabilities.create(Mp2PcmPump.formatSupport(format.sampleMimeType))

    // 这一格曾经写成 `queuedPcmBytes < QUEUE_HIGH_WATER_BYTES`（"队列没满才算就绪"），方向是反的：
    // 真机 10-28 11:09 的现场是队列 294912（已超高水位）、音轨 96000 装满后再不排空，
    // 于是 isReady 永远 false ⇒ 播放器停在 BUFFERING，视频明明还有 10.9 秒。
    // 而库那边 `doSomeWork` 是**先进 BUFFERING 再 stopRenderers()**，`onStopped()` 会把音轨 pause 掉，
    // 暂停的音轨不排空、队列不下降 ⇒ 自己锁死自己，唯一的出路成了看门狗到点换线。
    // 判据按库自己的口径来（`MediaCodecRenderer.isReady() = isSourceReady() || hasOutputBuffer()`）。
    override fun isReady(): Boolean = Mp2PcmPump.readyToRender(queuedPcmBytes, sourceReady)

    override fun isEnded(): Boolean = streamEnded && queuedPcmBytes == 0

    // 用户 10-28 13:0x 报「东方卫视 线4 字幕与声音不同步」：视频按墙钟走，而声音还压在
    // AudioTrack 那 0.5 秒缓冲里，于是画面比声音快约半秒。库里的做法是音频渲染器提供一个
    // MediaClock（`MediaCodecAudioRenderer` 就是拿 `AudioSink` 的播放位置当时钟），
    // 这里照同一口径：位置＝锚点 + AudioTrack 播放头走过的帧数。这样视频跟着**真正播出来的声音**走。
    override fun getMediaClock(): MediaClock = audioClock

    /** 自身不支持变速（做不到改采样率又不改音高），如实答默认参数，让库里那一层自己回落。 */
    private val audioClock = object : MediaClock {
        override fun getPlaybackParameters(): PlaybackParameters = PlaybackParameters.DEFAULT

        override fun setPlaybackParameters(playbackParameters: PlaybackParameters) {
            // 不改速度：报 DEFAULT，库那边发现参数对不上会回落墙钟（现状），不会假装跟上了。
        }

        override fun getPositionUs(): Long =
            Mp2PcmPump.clockPositionUs(anchorMediaUs, playedFrames(), pump.sampleRate)
    }

    private fun sampleRateHz(): Int = pump.sampleRate.coerceAtLeast(1)

    /** 音轨播放头（帧）。32 位会回绕，48 kHz 下约 24.8 小时，按无符号取回。 */
    private fun playedFrames(): Long {
        val audioTrack = track ?: return bytesWrittenSinceAnchor / (pump.channelCount.coerceAtLeast(1) * 2)
        return audioTrack.playbackHeadPosition.toLong() and 0xFFFF_FFFFL
    }

    /** 还压在音轨里没播出去的字节数——这就是改之前画面领先声音的那个量。 */
    private fun pendingInTrackBytes(): Long {
        val head = playedFrames() * (pump.channelCount.coerceAtLeast(1) * 2)
        return (bytesWrittenSinceAnchor - head).coerceAtLeast(0L)
    }

    override fun handleMessage(msg: Int, payload: Any?) {
        if (msg == Renderer.MSG_SET_VOLUME) {
            volume = (payload as? Float) ?: 1f
            track?.setVolume(volume)
        }
    }

    override fun render(positionUs: Long, elapsedRealtimeUs: Long) {
        drainToTrack()
        while (queuedPcmBytes < QUEUE_HIGH_WATER_BYTES) {
            if (streamEnded) {
                val tail = pump.poll()
                if (tail.isEmpty()) break
                queuePcm(tail)
                continue
            }
            if (!readOneSample()) break
            val pcm = pump.poll()
            if (pcm.isEmpty()) continue
            queuePcm(pcm)
        }
    }

    private fun queuePcm(pcm: ByteArray) {
        pcmQueue.addLast(pcm)
        queuedPcmBytes += pcm.size
        ensureTrack()
        drainToTrack()
    }

    override fun onStarted() {
        track?.play()
    }

    override fun onStopped() {
        track?.pause()
    }

    override fun onPositionReset(positionUs: Long, streaming: Boolean) {
        clearPcmQueue()
        track?.let {
            it.pause()
            it.flush()
        }
        // 位置复位之后写进音轨的第一个字节就是这个位置，播放头也是从 0 重新数。
        anchorMediaUs = positionUs
        bytesWrittenSinceAnchor = 0
    }

    override fun onDisabled() {
        // 短访也要留下总量：这一台常常只播十几秒就被换线/换台，中途那一行不一定打得出来。
        android.util.Log.i(
            "Mp2Diag",
            "收场 补头成功=$headerOk 补不出=$headerFailed 写不进=$writeBlocked" +
                " 已交PCM=$pcmWrittenBytes 轨缓冲=$trackBufferBytes minBuffer=$minBufferBytes",
        )
        writeBlocked = 0
        pcmWrittenBytes = 0
        sourceReady = false
        anchorMediaUs = 0
        bytesWrittenSinceAnchor = 0
        lastSyncLogMs = 0
        clearPcmQueue()
        pump.reset()
        releaseTrack()
        headerOk = 0
        headerFailed = 0
    }

    override fun onReset() {
        onDisabled()
    }

    private fun readOneSample(): Boolean {
        input.clear()
        input.ensureSpaceForWrite(INPUT_SAMPLE_BYTES)
        return when (readSource(readFormatHolder, input, /* readFlags= */ 0)) {
            C.RESULT_BUFFER_READ -> {
                sourceReady = true
                input.flip()
                val data = input.data ?: return !streamEnded
                val body = ByteArray(data.remaining())
                data.get(body)
                val bytes = withSynthesizedHeader(body) ?: return true
                pump.feed(bytes)
                if (hasReadStreamToEnd()) {
                    streamEnded = true
                    pump.finish()
                }
                true
            }

            C.RESULT_FORMAT_READ -> {
                audioFormat = readFormatHolder.format
                readFormatHolder.clear()
                sourceReady = true
                true
            }

            else -> {
                sourceReady = false
                false
            }
        }
    }

    /**
     * 抽头只给帧体（`ts/MpegAudioReader` 自己吃掉 4 字节头），泵靠同步字找帧界，所以这里把头补回来。
     * 补不出来就丢这一帧并记数——不拿"没货"冒充"在播"，也不为这一帧把整条播放判死（画面还要留给父母看）。
     */
    private fun withSynthesizedHeader(body: ByteArray): ByteArray? {
        val format = audioFormat
        val header = format?.let {
            Mp2PcmPump.synthesizeHeader(it.sampleRate, it.channelCount, body.size)
        }
        if (header == null) {
            headerFailed++
            if (headerFailed == 1) {
                android.util.Log.i(
                    "Mp2Diag",
                    "帧头补不出 rate=${format?.sampleRate} ch=${format?.channelCount} body=${body.size}" +
                        " mime=${format?.sampleMimeType} ⇒ 这一族没有声音，丢帧不报错",
                )
            }
            return null
        }
        if (headerOk == 0) {
            android.util.Log.i(
                "Mp2Diag",
                "帧头已补 rate=${format!!.sampleRate} ch=${format.channelCount} body=${body.size}" +
                    " head=${header.joinToString("") { "%02x".format(it) }}",
            )
        }
        headerOk++
        return header + body
    }

    private fun ensureTrack() {
        if (track != null || pump.sampleRate <= 0 || pump.channelCount <= 0) return
        val sampleRate = pump.sampleRate
        val channelMask = if (pump.channelCount == 1) {
            PlatformAudioFormat.CHANNEL_OUT_MONO
        } else {
            PlatformAudioFormat.CHANNEL_OUT_STEREO
        }
        val minBuffer = AudioTrack.getMinBufferSize(sampleRate, channelMask, PlatformAudioFormat.ENCODING_PCM_16BIT)
        minBufferBytes = minBuffer
        if (minBuffer <= 0) {
            throw createRendererException(
                IllegalStateException("AudioTrack getMinBufferSize=$minBuffer rate=$sampleRate"),
                /* format= */ null,
                SOURCE_RENDERER,
            )
        }
        // #153 乙（用户 10-27 点档）：轨缓冲从 4 个 MP2 帧（实测到手 18,432 B ≈ 0.096 秒）抬到 0.5 秒。
        // 这一档只动"轨有多深"，不动任何判据：拿同一条 MP2 线前后各量一遍，看停顿次数变不变，
        // 好把"轨太浅"和"源/解码器停顿"两件事分开。
        val wantedBuffer = maxOf(
            minBuffer,
            (sampleRate * pump.channelCount * 2 * TRACK_SECONDS).toInt(),
            PCM_BYTES_PER_FRAME * 4,
        )
        android.util.Log.i(
            "Mp2Diag",
            "轨建前 rate=$sampleRate ch=${pump.channelCount} minBuffer=$minBuffer" +
                " 实取=$wantedBuffer ≈%.3fs".format(wantedBuffer.toDouble() / (sampleRate * pump.channelCount * 2.0)) +
                " 队列高水位=$QUEUE_HIGH_WATER_BYTES",
        )
        try {
            val created = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build(),
                )
                .setAudioFormat(
                    PlatformAudioFormat.Builder()
                        .setEncoding(PlatformAudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(sampleRate)
                        .setChannelMask(channelMask)
                        .build(),
                )
                .setBufferSizeInBytes(wantedBuffer)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
            created.setVolume(volume)
            created.play()
            // 这一版 AudioTrack 只公开 `getBufferSizeInFrames`（`javap android.jar` 现读），按帧读再用库自己的 rate/声道折算。
            val bufferFrames = created.bufferSizeInFrames
            val actualRate = created.sampleRate
            val actualChannels = created.channelCount
            trackBufferBytes = if (actualRate > 0 && actualChannels > 0) bufferFrames * actualChannels * 2 else bufferFrames
            android.util.Log.i(
                "Mp2Diag",
                "轨已建 state=${created.state} 实得帧=$bufferFrames rate=$actualRate ch=$actualChannels" +
                    " 折算=$trackBufferBytes B ≈%.3fs".format(
                        if (trackBufferBytes > 0 && actualRate > 0 && actualChannels > 0)
                            trackBufferBytes.toDouble() / (actualRate * actualChannels * 2) else -1.0,
                    ) +
                    " 播放态=${if (created.playState == AudioTrack.PLAYSTATE_PLAYING) "playing" else "非playing"}",
            )
            track = created
        } catch (e: Exception) {
            throw createRendererException(e, /* format= */ null, SOURCE_RENDERER)
        }
    }

    private fun drainToTrack() {
        val audioTrack = track ?: return
        logSyncIfDue()
        while (pcmQueue.isNotEmpty()) {
            val head = pcmQueue.first()
            val written = audioTrack.write(
                head,
                pcmQueueHeadOffset,
                head.size - pcmQueueHeadOffset,
                AudioTrack.WRITE_NON_BLOCKING,
            )
            if (written <= 0) {
                writeBlocked++
                // 队里还有 PCM 却没被收走＝轨的缓冲不够；队空＝泵供不上，两件事要分开念。
                if (writeBlocked == 1 || writeBlocked % 200 == 0) {
                    android.util.Log.i(
                        "Mp2Diag",
                        "写不进 written=$written 第${writeBlocked}次 队列剩=$queuedPcmBytes" +
                            " 已交=$pcmWrittenBytes 轨缓冲=$trackBufferBytes minBuffer=$minBufferBytes",
                    )
                }
                return
            }
            pcmWrittenBytes += written
            bytesWrittenSinceAnchor += written
            pcmQueueHeadOffset += written
            if (pcmQueueHeadOffset >= head.size) {
                pcmQueueHeadOffset = 0
                pcmQueue.removeFirst()
                queuedPcmBytes -= head.size
            }
        }
    }

    private fun clearPcmQueue() {
        pcmQueue.clear()
        pcmQueueHeadOffset = 0
        queuedPcmBytes = 0
    }

    /**
     * 每 5 秒打一行「音画同步」读数：`轨内积压` 就是声音比画面落后多少（改之前一直是满轨 96,000 B ≈ 500 ms）。
     * 只读不改行为，用户报过一次不同步，得有个能复核的数。
     */
    private fun logSyncIfDue() {
        val nowMs = android.os.SystemClock.elapsedRealtime()
        if (nowMs - lastSyncLogMs < 5_000L) return
        lastSyncLogMs = nowMs
        val bytesPerSec = sampleRateHz() * pump.channelCount.coerceAtLeast(1) * 2
        val pendingUs = pendingInTrackBytes() * 1_000_000L / bytesPerSec
        android.util.Log.i(
            "Mp2Diag",
            "同步 锚点=${anchorMediaUs / 1000}ms 头=${playedFrames()}帧 轨内积压=${pendingInTrackBytes()}B" +
                " ≈${pendingUs / 1000}ms 队列=${queuedPcmBytes}B",
        )
    }

    private fun releaseTrack() {
        track?.let {
            try {
                it.stop()
            } catch (e: IllegalStateException) {
                // 还没播过就 stop 会抛，这里没有要保的读数。
            }
            it.release()
        }
        track = null
    }

    companion object {
        /** createRendererException 的 source 位：0 就是 ExoPlaybackException 里那个「渲染器侧出错」。 */
        private const val SOURCE_RENDERER = 0
        private const val INPUT_SAMPLE_BYTES = 64 * 1024
        private const val PCM_BYTES_PER_FRAME = 1152 * 2 * 2
        private const val QUEUE_HIGH_WATER_BYTES = 256 * 1024

        /** 想要多深的 AudioTrack 缓冲（秒）。0.096 秒实测下每 10 秒一次的停顿都会把轨抽干。 */
        private const val TRACK_SECONDS = 0.5
    }
}
