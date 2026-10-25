package com.parentstv.live.data

import androidx.media3.common.PlaybackException

/**
 * 播放器出故障时「这条线路接下来怎么办」的决策，抽成纯函数是为了能被单测逐格覆盖真值表。
 *
 * 规则（2026-10-21 用户指令）：
 * - **已经正常播放过的线路只在原地重连**，绝不自动换线路、绝不自动换频道；
 *   响应慢、卡住、重连次数再多都不构成换线理由。
 * - 从没出过画面的线路才允许换：先原地重连 sameLineReconnects 次，仍不通换下一条；
 *   最后一条也不行才换频道。
 */
object LineRecovery {

    enum class Action { ReconnectSameLine, NextLine, NextChannel }

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
