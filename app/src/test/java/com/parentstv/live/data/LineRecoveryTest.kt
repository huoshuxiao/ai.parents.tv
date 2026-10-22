package com.parentstv.live.data

import com.parentstv.live.data.LineRecovery.Action
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

/**
 * 「已经正常播放过的线路不许自动换」这条规则的完整真值表。
 *
 * 2026-10-21 用户指令：已正常播放的线路不可以换线路，响应时间长也不可以自动换线路。
 * 触发场景是咪咕直播 4K-2：第 1 条已经出过画面，卡住后被自动切到了第 2 条。
 */
class LineRecoveryTest {

    /** 改动前的决策：完全不看 lineHasPlayed，重连次数用完就往下换。用来做对照臂。 */
    private fun legacyAction(
        reconnectsOnLine: Int,
        sameLineReconnects: Int,
        lineIndex: Int,
        lastLineIndex: Int,
    ): Action = when {
        reconnectsOnLine < sameLineReconnects -> Action.ReconnectSameLine
        lineIndex < lastLineIndex -> Action.NextLine
        else -> Action.NextChannel
    }

    @Test
    fun playedLineNeverSwitchesAcrossEntireInputGrid() {
        var cells = 0
        for (sameLineReconnects in 0..3) {
            for (reconnectsOnLine in 0..5) {
                for (lineIndex in 0..3) {
                    for (lastLineIndex in -1..3) {
                        cells += 1
                        val action = LineRecovery.action(
                            lineHasPlayed = true,
                            reconnectsOnLine = reconnectsOnLine,
                            sameLineReconnects = sameLineReconnects,
                            lineIndex = lineIndex,
                            lastLineIndex = lastLineIndex,
                        )
                        assertEquals(
                            "已播放线路在 reconnects=$reconnectsOnLine/" +
                                "same=$sameLineReconnects, 位置=$lineIndex/$lastLineIndex 处被换掉了",
                            Action.ReconnectSameLine,
                            action,
                        )
                    }
                }
            }
        }
        // 网格本身不能是空转：4*6*4*5=480 格，其中含「重连次数早已用尽」的那些格。
        assertEquals(480, cells)
    }

    @Test
    fun unplayedLineStillEscalatesReconnectThenNextLineThenNextChannel() {
        // 第 1 条、共 3 条：重连没用完 → 原地重连；用完了 → 换第 2 条。
        assertEquals(
            Action.ReconnectSameLine,
            LineRecovery.action(lineHasPlayed = false, reconnectsOnLine = 1, sameLineReconnects = 2, lineIndex = 0, lastLineIndex = 2),
        )
        assertEquals(
            Action.NextLine,
            LineRecovery.action(lineHasPlayed = false, reconnectsOnLine = 2, sameLineReconnects = 2, lineIndex = 0, lastLineIndex = 2),
        )
        // 边界：恰好差一次仍算「没用完」。
        assertEquals(
            Action.ReconnectSameLine,
            LineRecovery.action(lineHasPlayed = false, reconnectsOnLine = 1, sameLineReconnects = 2, lineIndex = 1, lastLineIndex = 2),
        )
        // 最后一条也没出过画面、重连用尽 → 才允许换频道。
        assertEquals(
            Action.NextChannel,
            LineRecovery.action(lineHasPlayed = false, reconnectsOnLine = 2, sameLineReconnects = 2, lineIndex = 2, lastLineIndex = 2),
        )
        // 只有一条线路的频道：永远没有「下一条」可换，用尽即换台。
        assertEquals(
            Action.ReconnectSameLine,
            LineRecovery.action(lineHasPlayed = false, reconnectsOnLine = 0, sameLineReconnects = 2, lineIndex = 0, lastLineIndex = 0),
        )
        assertEquals(
            Action.NextChannel,
            LineRecovery.action(lineHasPlayed = false, reconnectsOnLine = 2, sameLineReconnects = 2, lineIndex = 0, lastLineIndex = 0),
        )
    }

    @Test
    fun ruleOnlyBitesOnTheReportedScenario() {
        // 咪咕直播 4K-2：第 1 条播过之后卡住，重连次数已用尽、后面还有第 2 条。
        val playedFirstOfTwo = LineRecovery.action(
            lineHasPlayed = true,
            reconnectsOnLine = 2,
            sameLineReconnects = 2,
            lineIndex = 0,
            lastLineIndex = 1,
        )
        // 对照臂必须给出「换线」，否则这条测试就是恒真的空壳。
        assertEquals(Action.NextLine, legacyAction(reconnectsOnLine = 2, sameLineReconnects = 2, lineIndex = 0, lastLineIndex = 1))
        assertNotEquals(legacyAction(reconnectsOnLine = 2, sameLineReconnects = 2, lineIndex = 0, lastLineIndex = 1), playedFirstOfTwo)
        assertEquals(Action.ReconnectSameLine, playedFirstOfTwo)

        // 播过的线路即使已经是最后一条，也不许自动换台（旧行为是换台）。
        val playedLastOfTwo = LineRecovery.action(
            lineHasPlayed = true,
            reconnectsOnLine = 2,
            sameLineReconnects = 2,
            lineIndex = 1,
            lastLineIndex = 1,
        )
        assertEquals(Action.NextChannel, legacyAction(reconnectsOnLine = 2, sameLineReconnects = 2, lineIndex = 1, lastLineIndex = 1))
        assertEquals(Action.ReconnectSameLine, playedLastOfTwo)
    }

    @Test
    fun unplayedLinesAreByteForIdenticalToOldBehaviour() {
        // 证明这次改动的作用域：没播过的线路，新旧决策在每一格输入上都一致。
        for (sameLineReconnects in 0..3) {
            for (reconnectsOnLine in 0..5) {
                for (lineIndex in 0..3) {
                    for (lastLineIndex in -1..3) {
                        assertEquals(
                            legacyAction(reconnectsOnLine, sameLineReconnects, lineIndex, lastLineIndex),
                            LineRecovery.action(
                                lineHasPlayed = false,
                                reconnectsOnLine = reconnectsOnLine,
                                sameLineReconnects = sameLineReconnects,
                                lineIndex = lineIndex,
                                lastLineIndex = lastLineIndex,
                            ),
                        )
                    }
                }
            }
        }
    }

    @Test
    fun unplayedLineSwitchesOnTheFirstStallUnderTheNewDefault() {
        // 播放器现在给「没播过的线路」传 sameLineReconnects = 0：
        // 设置里那个等待时间（默认 10 秒）一到就换下一条，不再原地重连两次。
        assertEquals(
            Action.NextLine,
            LineRecovery.action(
                lineHasPlayed = false, reconnectsOnLine = 0, sameLineReconnects = 0,
                lineIndex = 0, lastLineIndex = 1,
            ),
        )
        assertEquals(
            Action.NextChannel,
            LineRecovery.action(
                lineHasPlayed = false, reconnectsOnLine = 0, sameLineReconnects = 0,
                lineIndex = 1, lastLineIndex = 1,
            ),
        )
        // 负对照：把生产参数换回旧的那一档，同一格必须给出不同结论，
        // 否则上面两格是恒真的空壳。
        assertEquals(
            Action.ReconnectSameLine,
            LineRecovery.action(
                lineHasPlayed = false, reconnectsOnLine = 0, sameLineReconnects = 2,
                lineIndex = 0, lastLineIndex = 1,
            ),
        )
        // 播过的线路不受这一档影响：传进来的重连次数是多少都不许换线。
        assertEquals(
            Action.ReconnectSameLine,
            LineRecovery.action(
                lineHasPlayed = true, reconnectsOnLine = 0, sameLineReconnects = 0,
                lineIndex = 0, lastLineIndex = 1,
            ),
        )
    }
}
