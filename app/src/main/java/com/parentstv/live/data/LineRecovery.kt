package com.parentstv.live.data

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
