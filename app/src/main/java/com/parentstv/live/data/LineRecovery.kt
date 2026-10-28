package com.parentstv.live.data

import androidx.media3.common.C
import androidx.media3.common.PlaybackException

/**
 * 播放器出故障时「这条线路接下来怎么办」的决策，抽成纯函数是为了能被单测逐格覆盖真值表。
 *
 * 规则（2026-10-21 用户指令）：
 * - **已经正常播放过的线路只在原地重连**，绝不自动换线路、绝不自动换频道；
 *   响应慢、卡住、重连次数再多都不构成换线理由。
 * - 从没出过画面的线路才允许换：先原地重连 sameLineReconnects 次，仍不通换下一条；
 *   最后一条也不行才换频道。
 * - ⚠️10-26 点档 #123 乙：调用方传 `lineHasPlayed` 时给的必须是**这条线真画出过第一帧**
 *   （`PlayerScreen` 里的 `framedLines`，只在 `onRenderedFirstFrame` 记账），不再拿
 *   「播放器自报在播」（`playedLines`，STATE_READY／onIsPlayingChanged 两处记账）当依据——
 *   后者的病是「说在播却没出画」那一族：线被记成播过 ⇒ 既不换线也不换台 ⇒ 挂死。
 */
object LineRecovery {

    enum class Action { ReconnectSameLine, NextLine, NextChannel }

    /**
     * 看门狗「这一刻该不该接管」的判据（10-26 用户点档 #123 乙）。两把尺按
     * 「这条线画出过画面没有」分开，不许折成一把：
     *
     * - 画出过画面 ⇒ 只看**连续卡顿时长** `stalledMs`。这就是原来那一档，规则不变：
     *   正常播放时 `waiting` 为假、`stalledMs` 每秒被清零，永远不会到点，所以这一臂不可能
     *   把好线判成坏线（用户 2026-10-21「已正常播放的线路绝不自动换线/换台」）。
     * - 没画出过画面 ⇒ 只看**这一次尝试等了多久** `attemptMs`，到点就接管，
     *   **不再额外要求播放器自报卡住**。新增的就是这一支：READY／`isPlaying=true`
     *   而屏幕一帧都没有的那些线路，旧判据里 `waiting = playWhenReady && !isPlaying` 恒为假，
     *   窗口根本不开，于是「正在连接」可以挂到永远。
     *
     * 门槛一个字节没动（还是设置里那一档 `lineSwitchSeconds`，设备现读 10 秒）；
     * 变的只是「没出画时不再要求 `!isPlaying`」。
     */
    fun windowOver(
        lineHasPicture: Boolean,
        stalledMs: Long,
        attemptMs: Long,
        limitMs: Long,
        playingSmooth: Boolean = false,
    ): Boolean = when {
        lineHasPicture -> stalledMs >= limitMs
        // 正在顺畅播、只是还没攒够 [STEADY_MS]：这一刻不许判失败。
        // 少了这一臂，一条起播慢一点的好线会在“整档到点”那一刻被换掉。
        playingSmooth -> false
        else -> attemptMs >= limitMs
    }

    /**
     * 这条线「这一刻算不算顺畅」——判据只有一条：真在播，且缓冲领先播放头够多。
     *
     * 为什么要单独一把：10-28 现读 `36.110.54.118:866/hls/28`，主机只给 5–6 Mbps 而这条流要 8 Mbps，
     * 于是播放器每一小段都能吐出一帧（`onRenderedFirstFrame` 会响），`aheadMs` 却长期只有 400–900 ms。
     * 按旧的「见过一帧＝播过」口径，这种线会被判成 `ReconnectSameLine`，原地重连到永远——
     * 父母看到的就是「一直缓冲、一直卡」，而且永不换走。顺畅＝缓冲领先播放头 ≥ [SMOOTH_AHEAD_MS]。
     */
    fun isSmooth(playing: Boolean, aheadMs: Long): Boolean = playing && aheadMs >= SMOOTH_AHEAD_MS

    /**
     * 「缓冲领先播放头」够这个数才算顺畅。定 1 秒是照真机读数分的档：
     * 36.110 那条线实测 406/484/488/534/561/892 ms（**最高 892**，全在阈下）；
     * 而同机的线1（27.129）同一天读数 1330/1939/3352/3544/5502 ms（**最低 1330**，全在阈上）。
     * 取 1000 是这两族之间唯一干净的分界；取 2000 会把「缓冲刚好 1.3–1.9 秒的好线」判成不顺畅。
     */
    const val SMOOTH_AHEAD_MS = 1_000L

    /**
     * 一条线要「顺畅」累计这么久，才承认它真的播过、从此按「已播过的线路绝不自动换线」保护起来。
     * 10 秒＝设置里默认那一档的等待时长：比它更短的顺畅说明不了什么，只能说明它断断续续出了几帧。
     */
    const val STEADY_MS = 10_000L

    /**
     * 这一刻播放器拿到的是**直播流**还是**一枚定长的片子**（10-27 新加，只用来读数）。
     *
     * 病征：`8.138.7.223/tv/cctvnews.php?id=…` 那一族会 302 到同一支广告片，电视上 2.4–2.6 秒
     * 就"出了画"，于是 `onRenderedFirstFrame`／`framedLines` 全绿——可"出画"这一格答的是
     * 「有没有画面」，从来答不了「这是不是这个台的节目」。
     *
     * 分法只问一件事：**这东西有没有尽头**。
     * - 直播流：Media3 给 `isCurrentMediaItemLive=true`，且时长是未知（`C.TIME_UNSET`）；
     *   永远播不完，画面停了才叫冻。
     * - 定长片：`isCurrentMediaItemLive=false` 且给出具体毫秒数，放完停在最后一帧——
     *   这时候播放器侧样样"正常"（`isPlaying` 掉回 false 是因为**放完了**，不是卡住）。
     *
     * 取或：两个信号任一说"直播"就按直播念（宁可放过，也别把 4K/8K 那种真直播读成片子——
     * 现读它们偶尔也报一个具体时长）。时长未知（`C.TIME_UNSET`）不单独定音，理由同上。
     */
    fun liveStream(isLive: Boolean, durationMs: Long): Boolean =
        isLive || durationMs == C.TIME_UNSET

    /**
     * 「画面在动、没有声音」要先分清是哪一层没声，所以把音频轨读数压成一个词给日志用（2026-10-27，
     * 用户电视上现听 `36.110.54.118:866/hls/28/index.m3u8` 无声，指令「解决没声音问题，不删线、不进黑名单」）。
     *
     * - `无音轨`：这条流压根没带音频轨（源侧问题，播放器再动也没用）。
     * - `aac`：Android 硬件一律解得了 ⇒ 若还是没声，问题不在解码器（往音量／音频焦点那侧查）。
     * - `ac3`：AC-3／E-AC-3（杜比）。盒子没拿到授权就没有解码器，**画面照跑、声音没有**，
     *   这正是 IPTV 转播流上最常见的一种"没声音"。
     * - `mp2`：MPEG-1/2 Layer-2／Layer-3，部分盒子缺解码器，同上一族。
     * - `其他(xxx)`：带 mime 但不在上面几族里，原样念出 subtype，别压成"未知"糊掉。
     */
    fun audioClass(sampleMimeType: String?, audioTrackCount: Int, playing: Boolean): String {
        // 「一条音频轨都没有」有两种来路：源真没带轨，和播放器还没把 currentTracks 填起来
        //（起播头几秒它就是空的）。10-27 真机抓到 3 行 `mime=null tracks=0 playing=false`
        // 被旧口径读成「无音轨」＝说谎。能把两者分开的唯一凭据是播放器有没有真在播。
        if (audioTrackCount <= 0 || sampleMimeType.isNullOrEmpty()) return if (playing) "无音轨" else "未证"
        val m = sampleMimeType.lowercase()
        return when {
            m.contains("ac3") -> "ac3"
            m.contains("mp4a-latm") || m.contains("aac") -> "aac"
            m.contains("mpeg") -> "mp2"
            else -> "其他(${sampleMimeType.substringAfterLast('/', sampleMimeType)})"
        }
    }

    fun action(
        lineHasPlayed: Boolean,
        reconnectsOnLine: Int,
        sameLineReconnects: Int,
        lineIndex: Int,
        lastLineIndex: Int,
    ): Action = when {
        lineHasPlayed -> Action.ReconnectSameLine
        reconnectsOnLine < sameLineReconnects -> Action.ReconnectSameLine
        lineIndex < lastLineIndex -> Action.NextLine
        else -> Action.NextChannel
    }

    /**
     * 走到本台最后一条线还不通时，回到本台**还没试过的那条线**，而不是立刻换台。
     *
     * 病征（10-28 实测）：东方卫视 4 条线，爸妈手动点了线4（`36.110.54.118:866/hls/28`，
     * 主机只给 5–6 Mbps、这条流要 8 Mbps）——按 `action()` 走到末位就直接 `NextChannel`，
     * 于是「这条线不行」被翻译成「离开这个台」，而线1 一直是好的。
     *
     * 只兜一圈：`tried` 里是这一趟已经试过的线，全部试过就返回 null（调用方照旧换台），
     * 免得两条坏线来回弹。
     */
    fun nextUntriedLine(current: Int, size: Int, tried: Set<Int>): Int? {
        if (size <= 1) return null
        for (k in 1 until size) {
            val i = (current + k) % size
            if (i !in tried) return i
        }
        return null
    }

    /**
     * 看门狗每秒喂一拍，攒出「这条线该不该被接管」。抽成纯类是为了能拿**真机读数回放**：
     * `LineRecoveryTest` 里直接喂 2026-10-28 17:37–17:43 那两串 `LoadWatch` 采样，
     * 一组是 36.110 那条（东方卫视 线4，缓冲 406–892 ms），一组是同机线1（1330–7576 ms）。
     *
     * `sample` 的参数都是这一刻的原始读数，返回值＝「整档到点、该换走」。
     */
    class SteadyTracker(private val limitMs: Long) {
        private var stalledMs = 0L
        private var smoothRunMs = 0L
        private var smoothTotalMs = 0L
        var steady = false
            private set

        /**
         * @param rendered 这一条线在本台**真画出过第一帧**没有（`PlayerScreen` 的 `framedLines`）。
         *   没有画面的「自称在播」不算顺畅——那正是 #123 乙 那一族：`playing=true`、缓冲也在涨，
         *   屏幕上却一帧都没有。
         */
        fun sample(
            playing: Boolean,
            waiting: Boolean,
            aheadMs: Long,
            rendered: Boolean,
            attemptMs: Long,
        ): Boolean {
            if (waiting) stalledMs += TICK_MS else stalledMs = 0
            val smooth = rendered && isSmooth(playing, aheadMs)
            if (smooth) {
                smoothRunMs += TICK_MS
                smoothTotalMs += TICK_MS
            } else {
                smoothRunMs = 0
            }
            if (!steady && smoothRunMs >= STEADY_MS) steady = true
            // 判定仍走 `windowOver` 那一把尺（单测盯的就是它）：
            // 没顺畅过时把「这一趟有没有顺畅过一秒」当 `playingSmooth` 传进去——
            // 有过一秒顺畅 ⇒ 视为「大体正常」，只认连续卡满一整档；
            // 一秒都没有 ⇒ 整档到点就接管（#123 乙 那一族与 36.110 那一族都落在这里）。
            return windowOver(
                lineHasPicture = steady,
                stalledMs = stalledMs,
                attemptMs = attemptMs,
                limitMs = limitMs,
                playingSmooth = smoothTotalMs > 0L,
            )
        }
    }

    /** 看门狗一拍就是 1 秒（`PlayerScreen` 里 `delay(1_000)`），回放也按这个粒度累加。 */
    const val TICK_MS = 1_000L
}

/**
 * 报错之后这一档设置的时长还要不要等满（用户 10-25 点档：「可重试那一类等满、不可重试那类不等」）。
 *
 * 分法只问一件事：**再等下去会不会变成另一回事**。
 * - 会（网络没连上／连接超时／报不出码的那些）⇒ 等满，屏幕上那句倒计时继续走，
 *   到点由看门狗按 [LineRecovery] 决定原地重连还是换线；
 * - 不会（对端已经答复过内容不对／这容器的格式解不了／解码器报失败）⇒ 立刻走，
 *   这类地址再等 10 秒还是同一句答复，白白让父母对着黑屏。
 * 码表用 [PlaybackException] 里本项目 `friendlyError` 已经认得的那几个，
 * 没有新引常量；认不出的码一律按"可重试"处理（宁可多等，也别把慢线误判成死线）。
 */
object FaultPolicy {

    private val noWaitCodes = setOf(
        PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS,
        PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED,
        PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED,
        PlaybackException.ERROR_CODE_DECODING_FAILED,
    )

    /** true＝这条报错值得把设置的时长等满；false＝立刻换线。 */
    fun waitOutWindow(code: Int?): Boolean = code !in noWaitCodes
}
