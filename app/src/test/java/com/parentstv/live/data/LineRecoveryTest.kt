package com.parentstv.live.data

import androidx.media3.common.C
import com.parentstv.live.data.LineRecovery.Action
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 「已经正常播放过的线路不许自动换」这条规则的完整真值表。
 *
 * 2026-10-21 用户指令：已正常播放的线路不可以换线路，响应时间长也不可以自动换线路。
 * 触发场景是咪咕直播 4K-2：第 1 条已经出过画面，卡住后被自动切到了第 2 条。
 *
 * 2026-10-26 点档 #123 乙：`lineHasPlayed` 这一位在播放器里改由 `framedLines`（真画出过第一帧）喂，
 * 不再由 `playedLines`（STATE_READY／onIsPlayingChanged 自报在播）喂；
 * 看门狗那一档新增 `windowOver`（见下面最后两个测试）。规则本身一字没动。
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

    /** 点档乙**之前**看门狗那一档的形状，留在这里当对照臂（新代码不读它）。 */
    private fun legacyWindowOver(
        stalledMs: Long,
        attemptMs: Long,
        limitMs: Long,
        waiting: Boolean,
        lineHasPicture: Boolean,
    ): Boolean = if (lineHasPicture) stalledMs >= limitMs else waiting && attemptMs >= limitMs

    @Test
    fun playerClaimingPlayingWithoutPictureNowTimesOut() {
        val limit = 10_000L
        // 这就是「说在播却没出画」那一格：`waiting=false`（播放器自报在播）、这条线一帧都没画过、
        // 这一次尝试已经等满 10 秒。旧判据在这里根本不开窗（于是既不换线也不换台、挂死），
        // 新判据必须接管。两臂在同一格上必须给出不同答案，否则这格是恒真的空壳。
        assertFalse(
            legacyWindowOver(stalledMs = 0L, attemptMs = 10_000L, limitMs = limit, waiting = false, lineHasPicture = false),
        )
        assertTrue(
            LineRecovery.windowOver(lineHasPicture = false, stalledMs = 0L, attemptMs = 10_000L, limitMs = limit),
        )
        // 门槛一个字节没动：等不满那一档谁都不许接管（不许把「到点」偷换成「立刻」）。
        assertFalse(
            LineRecovery.windowOver(lineHasPicture = false, stalledMs = 0L, attemptMs = 9_999L, limitMs = limit),
        )
        // 旧判据本来就管得着的那一格（自报卡住＋到点）必须继续管着，新判据不许把它放掉。
        assertTrue(
            legacyWindowOver(stalledMs = 0L, attemptMs = 10_000L, limitMs = limit, waiting = true, lineHasPicture = false),
        )
        assertTrue(
            LineRecovery.windowOver(lineHasPicture = false, stalledMs = 0L, attemptMs = 10_000L, limitMs = limit),
        )
    }

    @Test
    fun windowChangeOnlyAddsTakeoversAndNeverTouchesFramedArm() {
        val limit = 10_000L
        var added = 0
        var cells = 0
        for (waiting in listOf(true, false)) {
            for (stalled in listOf(0L, 1_000L, 9_999L, 10_000L, 60_000L)) {
                for (attempt in listOf(0L, 5_000L, 9_999L, 10_000L, 60_000L)) {
                    val legacy = legacyWindowOver(stalled, attempt, limit, waiting, lineHasPicture = false)
                    val now = LineRecovery.windowOver(false, stalled, attempt, limit)
                    // 方向性：新判据只可能比旧判据**多**接管，一处都不许更少（更少＝把原来救得回来的
                    // 那一档放掉，那才是真会伤到父母的改动）。
                    if (legacy) {
                        assertTrue("旧判据接管而新判据不接管：waiting=$waiting attempt=$attempt", now)
                    }
                    if (!legacy && now) {
                        added += 1
                        // 新增的那些格必须**恰好**是「没自报卡住、而这一次尝试等满了」那一族，
                        // 别的地方多接管一格＝作用域越界。
                        assertFalse("新增接管发生在 waiting=true 那一侧", waiting)
                        assertTrue("新增接管发生在没等满的那一格", attempt >= limit)
                    }
                    // 画出过画面那一臂：新旧逐格相同——用户 2026-10-21 那条保护没被这次改动碰过。
                    assertEquals(
                        legacyWindowOver(stalled, attempt, limit, waiting, lineHasPicture = true),
                        LineRecovery.windowOver(true, stalled, attempt, limit),
                    )
                    cells += 1
                }
            }
        }
        // 网格本身不能空转：2×5×5＝50 格；新增接管的那一族＝waiting=false × attempt∈{10_000,60_000}
        // × stalled 那 5 档不参与＝10 格。数字对不上就是尺变了，不是测试写法变了。
        assertEquals(50, cells)
        assertEquals(10, added)
    }

    /**
     * 10-27 新档：`8.138.7.223` 那一族 302 到同一支广告片，电视上 2.4–2.6 秒就"出了画"，
     * 出画那一格全绿却根本没播本台节目。这一把只问「拿到的是直播还是一枚定长片子」，
     * 现读真机台账（`tools/.cache/tv_retest_g2fix/lines.jsonl`）里那 7 格是
     * `first_frame=true` 而 `playing=false`——放完了，不是卡住。
     */
    @Test
    fun finiteClipReadsAsNotLiveWhileUnknownDurationStaysLive() {
        // 正对照：广告片——播放器明说不是直播，还给出具体毫秒数。
        assertFalse(LineRecovery.liveStream(false, 15_000L))
        // 负对照一：直播流——isCurrentMediaItemLive 为真，时长未知。
        assertTrue(LineRecovery.liveStream(true, C.TIME_UNSET))
        // 负对照二：时长未知单独出现（isLive 没报到）不许定音成"片子"，读不到就当直播念。
        assertTrue(LineRecovery.liveStream(false, C.TIME_UNSET))
        // 负对照三：isLive 说直播、duration 却给了个数（4K/8K 现读会这样）——取或，按直播念。
        assertTrue(LineRecovery.liveStream(true, 8_000L))
        // 负对照四：时长 0 不是"未知"那一档（未知是 Long.MIN_VALUE），别把两者混成一个读数。
        assertNotEquals(C.TIME_UNSET, 0L)
        assertFalse(LineRecovery.liveStream(false, 0L))
    }

    @Test
    fun audioClassSeparatesNoTrackFromUndecodableCodec() {
        // 正对照：**在播了**却一条音频轨都没有 ⇒ 这才许说"源没带音轨"。
        assertEquals("无音轨", LineRecovery.audioClass(null, 0, true))
        assertEquals("无音轨", LineRecovery.audioClass("audio/mp4a-latm", 0, true))
        // 10-27 真机抓到的三行说谎读数（`tracks=0 playing=false`＝播放器还没把 currentTracks 填起来）：
        // 这三格在旧口径下全部读成"无音轨"，新口径必须读成"未证"。
        assertEquals("未证", LineRecovery.audioClass(null, 0, false))
        assertEquals("未证", LineRecovery.audioClass("audio/mp4a-latm", 0, false))
        assertEquals("未证", LineRecovery.audioClass("", 2, false))
        // AAC：硬件一律解得了，所以它出现时问题一定不在解码器（往音量／焦点那侧查）。
        assertEquals("aac", LineRecovery.audioClass("audio/mp4a-latm", 1, true))
        // 杜比一族：盒子没授权就没解码器，画面照跑、声音没有——这正是用户报的那一形状。
        assertEquals("ac3", LineRecovery.audioClass("audio/ac3", 1, true))
        assertEquals("ac3", LineRecovery.audioClass("audio/eac3", 1, true))
        // MPEG 音频一族同病；大小写不许影响结论（Media3 给的 mime 不保证小写）。
        assertEquals("mp2", LineRecovery.audioClass("audio/mpeg", 1, true))
        assertEquals("mp2", LineRecovery.audioClass("AUDIO/MPEG", 1, false))
        // 认不出来的不许压成"未知"糊掉：subtype 要原样念出来。
        assertEquals("其他(opus)", LineRecovery.audioClass("audio/opus", 1, true))
        // 牙齿：轨道填上以后，"在不在播"不许改编码那一格的结论
        //（否则将来有人拿 playing 当"有没有声"的判据，把编码这一族读成"有声"）。
        assertEquals("aac", LineRecovery.audioClass("audio/mp4a-latm", 1, false))
        assertEquals("ac3", LineRecovery.audioClass("audio/ac3", 1, false))
    }

    /**
     * 10-28 现读的真故障：`36.110.54.118:866/hls/28`（东方卫视 线4）主机只给 5–6 Mbps、
     * 这条流要 8 Mbps，于是它每一小段都能吐出一帧（`onRenderedFirstFrame` 会响），
     * 缓冲却长期只有 400–900 ms。旧口径「见过一帧＝播过」把它判成 ReconnectSameLine，
     * 原地重连到永远——屏幕上就是「一直缓冲、一直卡」，而且永不换走。
     *
     * 新口径：**顺畅**（在播且缓冲领先 ≥ 1 秒）累计满 10 秒才承认「播过」。
     */
    @Test
    fun starvingLineThatRendersAFrameIsNotTreatedAsPlayed() {
        val limit = 10_000L
        // 正对照（本次要修的格）：见过帧但从未顺畅 ⇒ 整档到点必须接管。
        assertFalse(LineRecovery.windowOver(lineHasPicture = false, stalledMs = 0L, attemptMs = 9_999L, limitMs = limit))
        assertTrue(LineRecovery.windowOver(lineHasPicture = false, stalledMs = 0L, attemptMs = 10_000L, limitMs = limit))
        // 反面对照：这一刻正在顺畅播、只是还没攒够 10 秒 ⇒ 一秒都不许判失败（别把慢启动的好线换掉）。
        assertFalse(
            LineRecovery.windowOver(
                lineHasPicture = false, stalledMs = 0L, attemptMs = 60_000L, limitMs = limit, playingSmooth = true,
            ),
        )
        // 已经顺畅播满（steady）⇒ 回到老规则：只有连续卡顿到点才接管，attempt 再久也不算。
        assertFalse(
            LineRecovery.windowOver(
                lineHasPicture = true, stalledMs = 9_999L, attemptMs = 600_000L, limitMs = limit, playingSmooth = false,
            ),
        )
        assertTrue(
            LineRecovery.windowOver(
                lineHasPicture = true, stalledMs = 10_000L, attemptMs = 600_000L, limitMs = limit, playingSmooth = false,
            ),
        )
        // 「顺畅」这把尺本身：在播且缓冲 ≥ 1000 ms 才算；差 1 ms 不算，没在播也不算。
        assertTrue(LineRecovery.isSmooth(true, 1_000L))
        assertFalse(LineRecovery.isSmooth(true, 999L))
        assertFalse(LineRecovery.isSmooth(false, 30_000L))
        // 真机两族读数就是这条分界的来历：36.110 那族最高 892（全在阈下），
        // 同机线1 那族最低 1330（全在阈上）——一个都不许跨过界。
        listOf(406L, 484L, 561L, 892L).forEach { assertFalse("ahead=$it 不该算顺畅", LineRecovery.isSmooth(true, it)) }
        listOf(1_330L, 1_939L, 3_352L, 5_502L).forEach { assertTrue("ahead=$it 该算顺畅", LineRecovery.isSmooth(true, it)) }
    }

    /**
     * 10-28：末位线不通要先回本台还没试过的线，不许一路换台。
     * 真实场景＝东方卫视 4 条线（线1 27.129 好、线2/线3 忽好忽坏、线4 `36.110` 稳定短供），
     * 爸妈在线上点 线4 ⇒ 旧逻辑 `action()` 走到 `lineIndex == lastLineIndex` 直接 NextChannel，
     * 屏幕上「离开这个台」，而线1 明明能看。
     */
    @Test
    fun lastLineFailureWrapsToUntriedLineBeforeLeavingTheChannel() {
        // 正对照：4 条线、停在线4（index 3）、这一趟只试过线4 ⇒ 回到线1（index 0）。
        assertEquals(0, LineRecovery.nextUntriedLine(current = 3, size = 4, tried = setOf(3)))
        // 线1 也试过了 ⇒ 顺推到线2（index 1）；不许直接跳到线3 把中间的漏掉。
        assertEquals(1, LineRecovery.nextUntriedLine(current = 3, size = 4, tried = setOf(0, 3)))
        // 全试过 ⇒ 返回 null，调用方照旧换台（不做第二圈，免得两条坏线来回弹）。
        assertNull(LineRecovery.nextUntriedLine(current = 3, size = 4, tried = setOf(0, 1, 2, 3)))
        // 单线台没有「别的线」可回（用户 10-26 点档 #134：单线台也走换台）。
        assertNull(LineRecovery.nextUntriedLine(current = 0, size = 1, tried = setOf(0)))
    }

    /**
     * **真机读数回放**（2026-10-28 17:37–17:43 的 `LoadWatch`，`line=` 1 基）：
     * 同一台电视、同一条 8 Mbps 的东方卫视，一串是「36.110 那条线4」（缓冲 406–892 ms），
     * 一串是同机「线1」（1330–7576 ms，中间有过一次 4 秒回填）。看门狗按 1 秒一拍喂进去。
     */
    @Test
    fun replayOfRealDeviceReadings() {
        val limit = 10_000L
        // —— 线4（36.110）：state=2、playing=false、缓冲 406–892 ms ——
        val starving = listOf(534L, 406L, 484L, 892L, 488L, 561L)
        val starveTracker = LineRecovery.SteadyTracker(limit)
        var failedAt = -1L
        var attempt = 0L
        starving.forEach { ahead ->
            repeat(2) { // 每 2 秒采样一次 ⇒ 拍两次
                attempt += LineRecovery.TICK_MS
                val over = starveTracker.sample(playing = false, waiting = true, aheadMs = ahead, rendered = true, attemptMs = attempt)
                if (over && failedAt < 0) failedAt = attempt
            }
        }
        assertEquals("线4 必须整档到点（10 秒）被接管", 10_000L, failedAt)
        assertFalse("线4 一次都不该被记成顺畅/播过", starveTracker.steady)

        // —— 线1（27.129）：正常在播，缓冲 1330–7576 ms，17:39 有过一次 2 拍缓冲 ——
        val healthy = listOf(
            3_352L, 3_544L, 5_502L, 3_496L, 5_507L, 3_502L, 5_491L, 3_486L, 5_475L, 3_491L,
            867L, 1_427L, 2_558L, 7_576L, 4_630L, 5_944L, 4_923L, 5_918L, 4_547L, 5_918L,
            4_736L, 7_112L, 3_922L, 5_216L,
        )
        val healthyTracker = LineRecovery.SteadyTracker(limit)
        var attempt2 = 0L
        var steadyAt = -1L
        var anyFail = false
        healthy.forEachIndexed { i, ahead ->
            val buffering = i == 10 || i == 11   // 17:39:31 / 17:39:33 那两拍确实在缓冲
            repeat(2) {
                attempt2 += LineRecovery.TICK_MS
                val over = healthyTracker.sample(
                    playing = !buffering, waiting = buffering, aheadMs = ahead,
                    rendered = true, attemptMs = attempt2,
                )
                if (over) anyFail = true
                if (healthyTracker.steady && steadyAt < 0) steadyAt = attempt2
            }
        }
        assertFalse("好线在整段 48 秒里一次都不许被接管（含那次 4 秒回填）", anyFail)
        assertTrue("好线必须在 12 秒内被记成「播过」", steadyAt in 1..12_000)

        // —— 对照三：#123 乙 那一族「自称在播、缓冲也在涨、却一帧都没有」——
        // `playing=true`、`ahead=8000` 看着很像顺畅，但没出过画面 ⇒ 必须整档到点接管。
        val noPic = LineRecovery.SteadyTracker(limit)
        var failNoPic = -1L
        for (i in 1..15) {
            val over = noPic.sample(
                playing = true, waiting = false, aheadMs = 8_000L,
                rendered = false, attemptMs = i * LineRecovery.TICK_MS,
            )
            if (over && failNoPic < 0) failNoPic = i * LineRecovery.TICK_MS
        }
        assertEquals("没画面的线必须整档到点被接管", 10_000L, failNoPic)
        assertFalse("没画面一次都不许记成顺畅", noPic.steady)

        // —— 对照四：「大体正常、只是偶尔掉到 1 秒以下」的好线不许被误换 ——
        // 每 6 拍掉一拍（ahead=800），其余 1500；它攒不满连续 10 秒（所以不会变 steady），
        // 但「这一趟顺畅过」为真 ⇒ 只看连续卡顿，不能因为那次掉档就整条换走。
        val dip = LineRecovery.SteadyTracker(limit)
        var failDip = false
        for (i in 1..60) {
            val low = i % 6 == 0
            val over = dip.sample(
                playing = true, waiting = false, aheadMs = if (low) 800L else 1_500L,
                rendered = true, attemptMs = i * LineRecovery.TICK_MS,
            )
            if (over) failDip = true
        }
        assertFalse("偶尔掉档的好线不许被整条换走", failDip)
    }
}
