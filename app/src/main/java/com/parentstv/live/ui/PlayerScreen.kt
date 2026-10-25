package com.parentstv.live.ui

import android.net.Uri
import android.view.ViewGroup
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ArrowBack
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.Favorite
import androidx.compose.material.icons.rounded.LiveTv
import androidx.compose.material.icons.rounded.List
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Schedule
import androidx.compose.material.icons.rounded.Timer
import androidx.compose.material.icons.rounded.WifiOff
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.VideoSize
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.dash.DashMediaSource
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.exoplayer.trackselection.DefaultTrackSelector
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import android.os.SystemClock
import android.view.SurfaceView
import com.parentstv.live.data.FreezeWatch
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine
import com.parentstv.live.data.BackKeyAction
import com.parentstv.live.data.BeijingTime
import com.parentstv.live.data.ChannelNumbers
import com.parentstv.live.data.DirectionKeyAction
import com.parentstv.live.data.EpgProgram
import com.parentstv.live.data.EpgRepository
import com.parentstv.live.data.EpgSnapshot
import com.parentstv.live.data.FaultPolicy
import com.parentstv.live.data.LineRecovery
import com.parentstv.live.data.MenuKeyAction
import com.parentstv.live.data.StreamProbe
import com.parentstv.live.data.StreamUrl
import com.parentstv.live.data.TvChannel
import com.parentstv.live.data.TvPrefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext

@Composable
fun PlayerScreen(
    initialChannel: TvChannel,
    initialQueue: List<TvChannel>,
    numberedChannels: List<TvChannel>,
    onNumberedJump: (TvChannel) -> Unit,
    categoryNameOf: (String) -> String,
    // 目录里构建时实测到的画面尺寸（规范地址 -> "宽x高"），选线面板按地址查它给每条线标清晰度
    //（用户 10-25 指令 5「线路选择画面没看到每条线路的清晰度」；实现在 ChannelCatalog.qualityOf）。
    lineQualityOf: (String) -> String?,
    favorites: Set<String>,
    prefs: TvPrefs,
    onChannelChanged: (TvChannel) -> Unit,
    onBack: () -> Unit,
) {
    val context = LocalContext.current
    val queue = remember(numberedChannels, initialQueue, initialChannel) {
        // 用户 2026-10-23 指令：上下键要**全频道**换台、OK 选台也要能选到全央视/卫视，
        // 「不可以只组内」。所以播放器自己走的这条队列就是整份目录（按数字键那套台号排），
        // 不再用主页递进来的分组队列；initialQueue 只在目录为空时兜底。
        numberedChannels.filter { it.streams.isNotEmpty() }
            .ifEmpty { initialQueue.filter { it.streams.isNotEmpty() } }
            .ifEmpty { listOf(initialChannel) }
    }
    var index by remember(initialChannel.id) {
        // 数字键跨组跳台是把 initialQueue/initialChannel 整体换掉，队列换了而位次没跟着重算，
        // 落到新队列里的就会是「老队列的那个下标」——所以这一格必须按 initialChannel 重算。
        mutableIntStateOf(queue.indexOfFirst { it.id == initialChannel.id }.coerceAtLeast(0))
    }
    val channel = queue.getOrElse(index) { initialChannel }
    // 数字键念的是**全频道**台号（用户 2026-10-23：全频道从 1 开始，央视 1-17 对键 1-17，
    // CCTV-5+ 对键 18，其余依次追加），跟「本组第几台」不是一回事，所以这份表从外面递进来的整份目录算。
    val channelNumbers = remember(numberedChannels) { ChannelNumbers.table(numberedChannels) }
    val channelNumber = channelNumbers[channel.id]
    var streamIndex by remember(channel.id) {
        val remembered = prefs.lineFor(channel.id)
        val preferred = if (remembered == null) {
            -1
        } else {
            channel.streams.indexOfFirst { StreamUrl.sameLine(it, remembered) }
        }
        mutableIntStateOf(preferred.coerceAtLeast(0))
    }
    var retryNonce by remember(channel.id) { mutableIntStateOf(0) }
    var reconnectsOnLine by remember(channel.id) { mutableIntStateOf(0) }
    var lastRecoveryAt by remember(channel.id) { mutableLongStateOf(0L) }
    // 这一条线路这一次「尝试」是从哪一刻起算的：首帧之前要等满设置的那一档时长
    //（用户 10-25 指令 4/5 ＋ 点档「可重试那一类等满」）。换台、换线、原地重连都会重新起表。
    val windowStartedAt = remember(channel.id, streamIndex, retryNonce) { SystemClock.elapsedRealtime() }
    // 本台里已经真出过画面的线路号。换线只允许发生在没出过画面的线路上（见 LineRecovery）。
    val playedLines = remember(channel.id) { mutableSetOf<Int>() }
    var errorMessage by remember(channel.id) { mutableStateOf<String?>(null) }
    var statusText by remember(channel.id) { mutableStateOf(connectingText(channel.name, streamIndex)) }
    var videoSize by remember(channel.id, streamIndex) { mutableStateOf<VideoSize?>(null) }
    // 本次进入播放器后已经试过、且所有线路都起不了画面的频道。自动换台要靠它决定什么时候停手。
    val unplayableChannels = remember { mutableSetOf<String>() }
    var isPlaying by remember { mutableStateOf(false) }
    var isBuffering by remember { mutableStateOf(true) }
    // 画面冻住这条尺：像素连续 FreezeWatch.SILENCE_MS 不变，就当作这台在喂同一帧。
    // 动作只有「原地重连」一种，绝不换线、绝不换台（用户 09-29 立的规则：已经出过画面的线路只原地重连）。
    val freezeState = remember(channel.id, streamIndex) { FreezeWatch.State() }
    val playerViewRef = remember { mutableStateOf<PlayerView?>(null) }
    LaunchedEffect(channel.id, streamIndex, retryNonce, isPlaying) {
        if (!isPlaying) return@LaunchedEffect
        val buf = IntArray(256 * 144)
        var logged = false
        while (true) {
            delay(FreezeWatch.SAMPLE_MS)
            val surface = (playerViewRef.value?.videoSurfaceView as? SurfaceView) ?: continue
            val hash = sampleSurfaceHash(surface, buf) ?: continue
            if (!logged) {
                logged = true
                android.util.Log.i("FreezeWatch", "sampling channel=${channel.id} line=${streamIndex + 1} hash=$hash")
            }
            if (!FreezeWatch.observe(freezeState, hash, SystemClock.elapsedRealtime())) continue
            if (reconnectsOnLine >= SAME_LINE_RECONNECTS) continue
            reconnectsOnLine += 1
            android.util.Log.i("FreezeWatch", "frozen ${FreezeWatch.SILENCE_MS}ms → 原地重连 channel=${channel.id} line=${streamIndex + 1}")
            statusText = "线路 ${streamIndex + 1} 画面停了，正在原地重连（第 $reconnectsOnLine 次）…"
            errorMessage = null
            isBuffering = true
            retryNonce += 1
        }
    }
    var controlsVisible by remember { mutableStateOf(true) }
    var zapNotice by remember { mutableStateOf(false) }
    var aspectIndex by remember { mutableIntStateOf(0) }
    var lineSeconds by remember { mutableIntStateOf(prefs.lineSwitchSeconds) }
    var linePickerVisible by remember { mutableStateOf(false) }
    var channelPickerVisible by remember { mutableStateOf(false) }
    // 遥控器数字键选台：数字先进缓冲区，停手 2.5 秒后当成台号跳过去。
    var digitEntry by remember { mutableStateOf("") }
    var probeNonce by remember { mutableIntStateOf(0) }
    var probingLines by remember { mutableStateOf(false) }
    val lineProbes = remember { mutableStateMapOf<Int, StreamProbe.Result>() }

    var guideVisible by remember { mutableStateOf(false) }
    var epgSnapshot by remember { mutableStateOf(EpgSnapshot()) }
    var minuteTick by remember { mutableIntStateOf(0) }
    // 画面左上角那句「正在播什么」按用户 10-25 指令 2 去掉了，节目单本身留着：
    // 这一份 programsFor 喂的是控制栏「节目单」按钮打开的那张表。
    val upcomingPrograms = remember(epgSnapshot, channel.name, minuteTick) {
        epgSnapshot.programsFor(channel.name)
    }

    val rootFocus = remember { FocusRequester() }
    val barFocus = remember { FocusRequester() }
    val retryFocus = remember { FocusRequester() }
    val aspectModes = remember {
        listOf(
            AspectRatioFrameLayout.RESIZE_MODE_FIT,
            AspectRatioFrameLayout.RESIZE_MODE_FILL,
            AspectRatioFrameLayout.RESIZE_MODE_ZOOM,
        )
    }

    // 数据源超时必须落在「换线等待」以内。原来是 connect 12 秒 / read 18 秒，而看门狗
    // 默认 10 秒就判失败：一条慢线的 connect 还没走完就被 stop() 掐断在途连接，
    // 下一次重连又从 DNS 从零开始，永远连不完——屏幕上就是那句不散的「正在连接」。
    // 压到预算以内，让它要么连上、要么以错误尽快报出来，换线走 onPlayerError 那条快路。
    // （用户 10-24 指令：这条不靠调大线路时长解决，所以动的是网络超时，不是 lineSwitchSeconds。）
    val httpFactory = remember {
        DefaultHttpDataSource.Factory()
            .setUserAgent(
                "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 " +
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            )
            .setConnectTimeoutMs(6_000)
            .setReadTimeoutMs(12_000)
            .setAllowCrossProtocolRedirects(true)
    }
    // 不指定 LoadControl 就是 Media3 的默认档：缓冲要攒满 50 秒才罢休，断流之后
    // 还要重新攒满 5 秒才肯恢复播放。直播的字节是边播边到的，「攒满 50 秒」等于
    // 起播后加载器一直满速抢带宽，弱线被自己抢死；那 5 秒门槛就是屏幕上
    // 迟迟不散的「缓冲中」。压到 20/40 秒 + 起播 1.2 秒 + 断流后 2.5 秒。
    val loadControl = remember {
        DefaultLoadControl.Builder()
            .setBufferDurationsMs(
                /* minBufferMs = */ 20_000,
                // 上限放到 60 秒（用户 10-24 18:3x「缓冲中/正在连接」）：断流那一下要靠已缓冲的
                // 内容撑过去，40 秒在 1080p 上只剩 40 秒余量、抖两下就见底；60 秒约 45 MB，电视盒扛得住。
                /* maxBufferMs = */ 60_000,
                /* bufferForPlaybackMs = */ 1_200,
                /* bufferForPlaybackAfterRebufferMs = */ 2_500,
            )
            .setPrioritizeTimeOverSizeThresholds(true)
            .build()
    }
    // 清晰度不许自己往下掉（用户 10-24 18:3x「全高清线路看着看着就变成高清线路了」）。
    // 默认的 DefaultTrackSelector 会按带宽在多码率清单里自适应选档：网一抖，同一台同一线路
    // 就从 1080p 落到 720p，右上角那行字跟着从「全高清」变「高清」——看起来像识别错了，
    // 其实是**同一条线上的码率降档**（目录侧实测：央视·卫视 215 条线里只有 4 条是多档清单
    // ＝CCTV-9、CGTN、CGTN 纪录，其余 211 条是直接给分片的单档流）。
    // 钉死「解码器支持的最高档」：宁可多缓冲，也不偷偷降清晰度。
    val trackSelector = remember(context) {
        DefaultTrackSelector(
            context,
            DefaultTrackSelector.Parameters.Builder()
                .setForceHighestSupportedBitrate(true)
                .build(),
        )
    }
    val exoPlayer = remember {
        ExoPlayer.Builder(context)
            .setMediaSourceFactory(DefaultMediaSourceFactory(httpFactory))
            .setTrackSelector(trackSelector)
            .setLoadControl(loadControl)
            .build()
            .apply {
                playWhenReady = true
                repeatMode = Player.REPEAT_MODE_OFF
            }
    }
    DisposableEffect(exoPlayer) {
        onDispose { exoPlayer.release() }
    }

    fun jumpTo(next: Int) {
        if (queue.size <= 1) return
        val target = ((next % queue.size) + queue.size) % queue.size
        if (target == index) return
        index = target
        errorMessage = null
        statusText = connectingText(queue[target].name, 0)
        isBuffering = true
        zapNotice = true
        linePickerVisible = false
        channelPickerVisible = false
        onChannelChanged(queue[target])
    }

    fun zap(delta: Int) = jumpTo(index + delta)

    // 上、下键各自可配成「上一个台／下一个台／只移动焦点」，返回 false 表示按键交还给焦点系统。
    fun zapUpOrDown(action: DirectionKeyAction): Boolean = when (action) {
        DirectionKeyAction.ZapPrevious -> {
            zap(-1)
            true
        }

        DirectionKeyAction.ZapNext -> {
            zap(1)
            true
        }

        DirectionKeyAction.MoveFocus -> false
    }

    // 本频道所有线路都试过一轮仍起不了画面：自动挪到下一个还没试过的频道。
    // 一圈回到原处说明队列里每个频道都失败过，返回 false 让界面给出总失败提示，
    // 否则会在几个坏频道之间无限转圈。
    fun zapToNextChannel(reason: String): Boolean {
        unplayableChannels.add(channel.id)
        val candidate = (1..queue.size)
            .map { (index + it) % queue.size }
            .firstOrNull { queue[it].id !in unplayableChannels }
        if (candidate == null) {
            errorMessage = "已经试过队列里的 ${queue.size} 个频道，全部线路都起不了画面（$reason）"
            statusText = ""
            isBuffering = false
            controlsVisible = true
            linePickerVisible = false
            return false
        }
        jumpTo(candidate)
        return true
    }

    fun chooseLine(lineIndex: Int, keepControlsHidden: Boolean = false) {
        val url = channel.streams.getOrNull(lineIndex) ?: return
        prefs.setLine(channel.id, url)
        linePickerVisible = false
        reconnectsOnLine = 0
        if (streamIndex == lineIndex) {
            retryNonce += 1
        } else {
            streamIndex = lineIndex
        }
        errorMessage = null
        statusText = "正在尝试线路 ${lineIndex + 1}…"
        isBuffering = true
        controlsVisible = !keepControlsHidden
    }

    // 左右键换线走的是与「选线」面板同一条路（chooseLine），
    // 免得记住线路、清零重连计数这些动作在两处各写一遍、日后走岔。
    fun stepLine(delta: Int) {
        val total = channel.streams.size
        if (total <= 1) {
            statusText = "这个台只有 1 条线路"
            zapNotice = true
            return
        }
        val target = ((streamIndex + delta) % total + total) % total
        chooseLine(target, keepControlsHidden = true)
        zapNotice = true
    }

    LaunchedEffect(channel.id) {
        onChannelChanged(channel)
    }

    // 节目单整份 12 MB 左右，取一次存 6 小时，切频道不再重复下载；关掉开关就不占这次请求。
    // 原来是「一进播放器就下」——那 12 MB 正好和首片抢同一条链路，弱网下屏幕上就是
    // 一句迟迟不散的「正在连接」。改成等第一条线路真的出过画面之后再取，且一场只取一次
    // （isPlaying 每次断流都会翻，不加这一道就会反复重下 12 MB）。
    var epgFetched by remember { mutableStateOf(false) }
    LaunchedEffect(prefs.epgEnabled, isPlaying) {
        if (!prefs.epgEnabled || !isPlaying || epgFetched) return@LaunchedEffect
        epgFetched = true
        epgSnapshot = EpgRepository(context).snapshot()
    }

    LaunchedEffect(Unit) {
        while (true) {
            delay(30_000)
            minuteTick += 1
        }
    }

    // 预加载（用户 10-24 18:3x「缓冲中/正在连接」）：播稳 5 秒后把**下一个台**的第一条线
    // 先走一遍（清单＋变体＋首片 64 KB），DNS/TCP/TLS 与分片路径热着 ⇒ 上下键换过去少一次冷启动。
    // 每个台只做一次、失败不重试、不改任何判据；不碰当前正在播那条的带宽之外的东西。
    val prefetchedChannels = remember { mutableSetOf<String>() }
    LaunchedEffect(channel.id, isPlaying) {
        if (!isPlaying) return@LaunchedEffect
        delay(5_000)
        if (!isPlaying) return@LaunchedEffect
        val next = queue.getOrNull((index + 1) % queue.size) ?: return@LaunchedEffect
        if (next.id == channel.id || !prefetchedChannels.add(next.id)) return@LaunchedEffect
        val url = next.streams.firstOrNull() ?: return@LaunchedEffect
        android.util.Log.i("Prefetch", "warm next channel=${next.id} line=1")
        StreamProbe.prefetch(url)
    }

    LaunchedEffect(linePickerVisible, channel.id, probeNonce) {
        if (!linePickerVisible) return@LaunchedEffect
        lineProbes.clear()
        probingLines = true
        try {
            coroutineScope {
                // 并发上限 2：一个台几条线就并发几路，每路最长十几秒，全撒出去就是
                // 自己把正在播的那条首片的带宽抢干——屏幕上就是「缓冲中」。
                val probeGate = Semaphore(PROBE_CONCURRENCY)
                channel.streams.forEachIndexed { lineIndex, url ->
                    launch(Dispatchers.IO) {
                        probeGate.withPermit {
                            val result = StreamProbe.probe(url)
                            withContext(Dispatchers.Main) {
                                lineProbes[lineIndex] = result
                            }
                        }
                    }
                }
            }
        } finally {
            // 关掉面板会取消这个协程，原来这一句在协程体末尾、走不到，
            // 「检测中…」就永远挂着、「重新检测」按钮一直是灰的。
            probingLines = false
        }
    }

    fun tryNextLine(message: String?) {
        // 同一次故障可能同时从 onPlayerError 和看门狗两条路进来，1.5 秒内只认第一次，
        // 否则会一口气跳过两条本来能用的线路。
        val now = System.currentTimeMillis()
        if (now - lastRecoveryAt < RECOVERY_DEBOUNCE_MS) return
        lastRecoveryAt = now
        // 已经出过画面的线路只在原地重连，绝不自动换线、绝不自动换台（用户 2026-10-21 指令）；
        // 没出过画面的线路按设置里的等待时间到点就换下一条（用户 2026-10-22 要求默认 10 秒），
        // 所以这一臂传的重连次数是 0——不然「10 秒换线」实际要等 30 秒。
        val played = streamIndex in playedLines
        when (
            LineRecovery.action(
                lineHasPlayed = played,
                reconnectsOnLine = reconnectsOnLine,
                sameLineReconnects = if (played) SAME_LINE_RECONNECTS else 0,
                lineIndex = streamIndex,
                lastLineIndex = channel.streams.lastIndex,
            )
        ) {
            LineRecovery.Action.ReconnectSameLine -> {
                reconnectsOnLine += 1
                statusText = if (played) {
                    "线路 ${streamIndex + 1} 卡住了，正在原地重连（第 $reconnectsOnLine 次）…"
                } else {
                    "线路 ${streamIndex + 1} 卡住了，正在重连（第 $reconnectsOnLine 次）…"
                }
                errorMessage = null
                isBuffering = true
                retryNonce += 1
            }

            LineRecovery.Action.NextLine -> {
                reconnectsOnLine = 0
                statusText = "线路 ${streamIndex + 1} 不可用，正在切换备用线路…"
                streamIndex += 1
            }

            LineRecovery.Action.NextChannel -> {
                reconnectsOnLine = 0
                if (!zapToNextChannel(message ?: "线路全部不可用")) {
                    statusText = ""
                    isBuffering = false
                    controlsVisible = true
                }
            }
        }
    }

    LaunchedEffect(channel.id, streamIndex, retryNonce) {
        var stalledMs = 0L
        var playingMs = 0L
        while (true) {
            delay(1_000)
            val waiting = exoPlayer.playWhenReady && !exoPlayer.isPlaying
            if (waiting) {
                stalledMs += 1_000
                playingMs = 0
            } else {
                stalledMs = 0
                if (exoPlayer.isPlaying) playingMs += 1_000
            }
            if (playingMs >= RECONNECT_AFTER_STABLE_MS) reconnectsOnLine = 0
            // 等多久算这条线失败：一律按设置里配的「线路时长」这一档算（默认 10 秒，
            // 档位见 LINE_SECONDS_OPTIONS），起播阶段和已经出过画面之后卡住都用同一档。
            // 这里以前对「本台一条线都还没出过画面」的首条线另压了一档 4 秒（上一轮为了躲
            // 「正在连接十几秒」加的），于是屏幕上等的时长跟设置里配的数对不上——
            // 用户 10-25 指令第 4 条要的就是「按设置里配的时长来等画面」，短档连同那常量一起删掉。
            val limit = prefs.lineSwitchSeconds * 1_000L
            // 首帧之前按「这一次尝试起算的整档时长」到点（用户 10-25 指令 5：要按设置里配的数等满）；
            // 已经出过画面之后才卡住的，仍按连续卡顿时长算，否则播到第 10 秒就会误判成失败。
            val overWindow = if (streamIndex in playedLines) {
                stalledMs >= limit
            } else {
                waiting && SystemClock.elapsedRealtime() - windowStartedAt >= limit
            }
            if (overWindow) {
                tryNextLine("线路长时间无响应，请检查网络后重试")
                break
            }
        }
    }

    // 台号行与画面同刻翻页（用户 10-25 点档「甲」）：新台的第一帧真上屏之前，左上角这两行
    // 仍写上一台——否则会出现「台号 13 · 央视·卫视」压着 CCTV-1 那一帧（10-25 实拍）。
    var shownName by remember { mutableStateOf(channel.name) }
    var shownMeta by remember { mutableStateOf(captionMeta(channelNumber, categoryNameOf(channel.category), channel.subgroup)) }

    // 还没出画面这段等待期：把原来那个「转圈 + 缓冲中」换成按设置时长逐秒递减的倒计时
    //（用户 10-25 指令 3「线路或频道切换时，将 loading 改为设置的时长的倒计时」）。
    // 触发条件与原来那个居中圈完全一样，起点就是上面 watchdog 用的同一把尺（lineSeconds），
    // 数到 1 大概就是该换下一条线的时候——圈不再显示，屏幕上只剩这一句大白话。
    val waitingForPicture = errorMessage == null && isBuffering && !isPlaying
    var countdownSeconds by remember(channel.id, streamIndex) { mutableIntStateOf(lineSeconds) }
    LaunchedEffect(waitingForPicture, channel.id, streamIndex, retryNonce, lineSeconds) {
        if (!waitingForPicture) {
            // 画面出来了（或报错卡住了）：倒计时归位到设置时长，下一次切换从头数。
            countdownSeconds = lineSeconds
            return@LaunchedEffect
        }
        var left = lineSeconds.coerceAtLeast(1)
        countdownSeconds = left
        while (left > 1) {
            delay(1_000)
            left -= 1
            countdownSeconds = left
        }
    }

    DisposableEffect(exoPlayer, channel.id, streamIndex, retryNonce) {
        val url = channel.streams.getOrNull(streamIndex)
        if (url == null) {
            errorMessage = "该频道暂无可用线路"
            statusText = ""
            isBuffering = false
            controlsVisible = true
            return@DisposableEffect onDispose { }
        }
        errorMessage = null
        isBuffering = true
        statusText = if (streamIndex == 0) {
            connectingText(channel.name, 0)
        } else {
            "正在尝试线路 ${streamIndex + 1}…"
        }
        // 回调里要记的是「本次连接试图播的这条线」，闭包捕获住编号，别等状态变了再读。
        val lineBeingPlayed = streamIndex

        val listener = object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                when (playbackState) {
                    Player.STATE_BUFFERING -> {
                        isBuffering = true
                        if (statusText.isBlank()) statusText = "缓冲中…"
                    }

                    Player.STATE_READY -> {
                        isBuffering = false
                        statusText = ""
                        errorMessage = null
                        playedLines.add(lineBeingPlayed)
                        // 自动换线换到的这条才是「这个频道实际能看的线路」，
                        // 记住它，下次进台直接用，不必从第 1 条重头试。
                        prefs.setLine(channel.id, url)
                    }

                    Player.STATE_ENDED -> {
                        isBuffering = false
                        statusText = "节目已结束"
                    }

                    else -> Unit
                }
            }

            override fun onRenderedFirstFrame() {
                // 首帧已经画到屏幕上：立刻撤掉「正在连接 / 还有 N 秒」，并把台号行翻到这一台。
                // 10-25 实拍：首帧比 STATE_READY 早，湖南卫视那一跳画面已出、倒计时还压着 2 秒。
                // 这一行是给「倒计时到底压了多久」留证据用的（adb logcat -s ZapFix）。
                android.util.Log.i(
                    "ZapFix",
                    "firstFrame channel=${channel.id} line=${lineBeingPlayed + 1}" +
                        " t=${SystemClock.elapsedRealtime()}",
                )
                isBuffering = false
                statusText = ""
                shownName = channel.name
                shownMeta = captionMeta(channelNumber, categoryNameOf(channel.category), channel.subgroup)
            }

            override fun onVideoSizeChanged(size: VideoSize) {
                // 掉清晰度这条诊断线：屏幕上「全高清 → 高清」到底是同一条线在降档，还是换了线路，
                // 看这行的 line= 有没有变就知道（adb logcat -s QualityWatch）。
                android.util.Log.i(
                    "QualityWatch",
                    "channel=${channel.id} line=${lineBeingPlayed + 1} ${size.width}x${size.height}" +
                        " t=${SystemClock.elapsedRealtime()}",
                )
                videoSize = size
            }

            override fun onIsPlayingChanged(playing: Boolean) {
                isPlaying = playing
                if (playing) {
                    isBuffering = false
                    statusText = ""
                    shownName = channel.name   // 万一某些解码器不回调首帧，这一臂兜底
                    shownMeta = captionMeta(channelNumber, categoryNameOf(channel.category), channel.subgroup)
                    playedLines.add(lineBeingPlayed)
                    prefs.setLine(channel.id, url)
                }
            }

            override fun onPlayerError(error: PlaybackException) {
                val message = friendlyError(error)
                // 用户 10-25 点档「可重试那一类等满、不可重试那类不等」：
                // 网络没连上／连接超时这类再等有可能变好，就不在这里抢着换线——把这一档设置的时长
                // 等满（倒计时照走、errorMessage 先不落，屏幕上那句「还有 N 秒」不中断），
                // 到点由上面那个看门狗按 LineRecovery 决定原地重连还是换下一条；
                // 而对端已经答复过内容不对／容器解不了／解码失败这类，等满也不会变好，立刻走。
                if (FaultPolicy.waitOutWindow(error.errorCode) &&
                    lineSeconds * 1_000L > SystemClock.elapsedRealtime() - windowStartedAt
                ) {
                    statusText = if (streamIndex == 0) {
                        connectingText(channel.name, 0)
                    } else {
                        "正在尝试线路 ${streamIndex + 1}…"
                    }
                    errorMessage = null
                    isBuffering = true
                    return
                }
                tryNextLine(message)
            }
        }
        exoPlayer.addListener(listener)

        try {
            // 直播延时（用户 10-24 18:3x「优化线路延时高」）：Media3 默认落后直播点 3 秒，
            // 这里压到 2 秒；追帧倍速放宽到 0.95–1.10，落后多了靠轻微加速追回来，不靠跳帧。
            // 只对直播生效（点播/回看忽略 LiveConfiguration）。
            val mediaItem = MediaItem.Builder()
                .setUri(url)
                .setLiveConfiguration(
                    MediaItem.LiveConfiguration.Builder()
                        .setTargetOffsetMs(2_000)
                        .setMinPlaybackSpeed(0.95f)
                        .setMaxPlaybackSpeed(1.10f)
                        .build(),
                )
                .build()
            val mediaSource = when {
                url.contains(".m3u8", ignoreCase = true) ||
                    url.contains("m3u", ignoreCase = true) ->
                    HlsMediaSource.Factory(httpFactory).createMediaSource(mediaItem)

                url.contains(".mpd", ignoreCase = true) ->
                    DashMediaSource.Factory(httpFactory).createMediaSource(mediaItem)

                else -> DefaultMediaSourceFactory(httpFactory).createMediaSource(mediaItem)
            }
            exoPlayer.stop()
            exoPlayer.clearMediaItems()
            exoPlayer.setMediaSource(mediaSource)
            exoPlayer.prepare()
            exoPlayer.play()
        } catch (error: Throwable) {
            tryNextLine(friendlyError(error))
        }

        onDispose {
            exoPlayer.removeListener(listener)
        }
    }

    LaunchedEffect(
        controlsVisible,
        isPlaying,
        errorMessage,
        streamIndex,
        linePickerVisible,
        channelPickerVisible,
        guideVisible,
    ) {
        val blocked = linePickerVisible || channelPickerVisible || guideVisible ||
            errorMessage != null || !isPlaying
        if (controlsVisible && !blocked) {
            delay(CONTROLS_AUTO_HIDE_MS)
            controlsVisible = false
        }
    }

    // 数字键停手 2.5 秒后把这串数字当**全频道台号**用：第 N 台，不管它落在哪一组。
    LaunchedEffect(digitEntry) {
        val typed = digitEntry
        if (typed.isEmpty()) return@LaunchedEffect
        delay(DIGIT_ENTRY_TIMEOUT_MS)
        digitEntry = ""
        val number = typed.toIntOrNull() ?: return@LaunchedEffect
        val target = numberedChannels.getOrNull(number - 1)
        if (target == null) {
            statusText = "全频道共 ${numberedChannels.size} 个台，没有第 $number 台"
            zapNotice = true
        } else {
            val position = queue.indexOfFirst { it.id == target.id }
            if (position >= 0) {
                if (position != index) jumpTo(position)
            } else {
                // 别的组的台：本组队列里没有它，交给外面换成那一组的队列再进台，
                // 这样跳过去之后上下键仍然是「在本组里换台」，不会因为一次跳台换了语义。
                onNumberedJump(target)
            }
        }
    }

    LaunchedEffect(zapNotice) {
        if (zapNotice) {
            delay(2_500)
            zapNotice = false
        }
    }

    LaunchedEffect(controlsVisible, errorMessage, linePickerVisible, channelPickerVisible, guideVisible) {
        if (linePickerVisible || channelPickerVisible || guideVisible) return@LaunchedEffect
        delay(80)
        when {
            errorMessage != null -> runCatching { retryFocus.requestFocus() }
            controlsVisible -> runCatching { barFocus.requestFocus() }
            else -> runCatching { rootFocus.requestFocus() }
        }
    }

    BackHandler {
        if (guideVisible) {
            guideVisible = false
            return@BackHandler
        }
        if (linePickerVisible) {
            linePickerVisible = false
            return@BackHandler
        }
        if (channelPickerVisible) {
            channelPickerVisible = false
            return@BackHandler
        }
        if (errorMessage != null ||
            !controlsVisible ||
            prefs.backKeyAction == BackKeyAction.BackToList
        ) {
            onBack()
        } else {
            controlsVisible = false
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            .focusRequester(rootFocus)
            .focusable()
            // 遥控器操作优先级最高（用户 10-23 19:1x「不受弹出影响」）：
            // 换台键走在预览阶段——控制栏弹出、错误提示弹着、焦点停在按钮上，都不挡上下换台；
            // 先把这些弹层收起来再换台。选台/选线/节目单是独立窗口，不受这一层影响。
            .onPreviewKeyEvent { event ->
                if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                when (event.key) {
                    Key.DirectionUp -> {
                        controlsVisible = false
                        errorMessage = null
                        zapUpOrDown(prefs.upKeyAction)
                    }

                    Key.DirectionDown -> {
                        controlsVisible = false
                        errorMessage = null
                        zapUpOrDown(prefs.downKeyAction)
                    }

                    Key.ChannelUp -> {
                        controlsVisible = false
                        errorMessage = null
                        zap(-1)
                        true
                    }

                    Key.ChannelDown -> {
                        controlsVisible = false
                        errorMessage = null
                        zap(1)
                        true
                    }

                    else -> false
                }
            }
            .onKeyEvent { event ->
                if (event.type != KeyEventType.KeyDown) return@onKeyEvent false
                when (event.key) {
                    Key.DirectionLeft -> {
                        if (!controlsVisible) {
                            stepLine(-1)
                            true
                        } else {
                            false
                        }
                    }

                    Key.DirectionRight -> {
                        if (!controlsVisible) {
                            stepLine(1)
                            true
                        } else {
                            false
                        }
                    }

                    // OK 键不再担播放/暂停：控制栏隐藏时它打开「选择频道」，
                    // 控制栏已经显示时交回焦点系统，让 OK 能按下底栏里聚焦的那个按钮。
                    Key.DirectionCenter, Key.Enter, Key.NumPadEnter -> {
                        if (!controlsVisible) {
                            controlsVisible = true
                            channelPickerVisible = true
                            true
                        } else {
                            false
                        }
                    }

                    Key.MediaPlayPause -> {
                        if (exoPlayer.isPlaying) exoPlayer.pause() else exoPlayer.play()
                        true
                    }

                    Key.MediaNext -> {
                        zap(1)
                        true
                    }

                    Key.MediaPrevious -> {
                        zap(-1)
                        true
                    }

                    Key.Menu -> {
                        when (prefs.menuKeyAction) {
                            MenuKeyAction.Lines -> {
                                controlsVisible = true
                                linePickerVisible = true
                                true
                            }

                            MenuKeyAction.Aspect -> {
                                aspectIndex = (aspectIndex + 1) % aspectModes.size
                                controlsVisible = true
                                true
                            }

                            MenuKeyAction.Off -> false
                        }
                    }

                    else -> {
                        val digit = DIGIT_KEYS[event.key]
                        if (digit == null) {
                            false
                        } else {
                            // 最多留 3 位（台号到 999 就够了），再多出来的位数挤掉最前面那位。
                            digitEntry = (digitEntry + digit).takeLast(3)
                            zapNotice = true
                            true
                        }
                    }
                }
            },
    ) {
        AndroidView(
            factory = { ctx ->
                PlayerView(ctx).apply {
                    useController = false
                    setShowBuffering(PlayerView.SHOW_BUFFERING_NEVER)
                    setKeepContentOnPlayerReset(true)
                    setShutterBackgroundColor(android.graphics.Color.BLACK)
                    resizeMode = aspectModes[aspectIndex]
                    layoutParams = ViewGroup.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT,
                    )
                    player = exoPlayer
                }
            },
            update = { view ->
                view.player = exoPlayer
                view.resizeMode = aspectModes[aspectIndex]
                playerViewRef.value = view
            },
            modifier = Modifier.fillMaxSize(),
        )

        AnimatedVisibility(
            visible = controlsVisible || zapNotice,
            modifier = Modifier.align(Alignment.TopCenter),
            enter = fadeIn(),
            exit = fadeOut(),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(
                        Brush.verticalGradient(
                            listOf(Color.Black.copy(alpha = 0.88f), Color.Transparent),
                        ),
                    )
                    .padding(start = 48.dp, end = 48.dp, top = 34.dp, bottom = 44.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = shownName,
                        color = Color.White,
                        fontSize = 32.sp,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = shownMeta,
                        color = Color(0xFFCBD5E1),
                        fontSize = 15.sp,
                    )
                    // 左上角那句「正在播什么」按用户 10-25 指令 2 摘掉了：节目单只看控制栏
                    // 「节目单」按钮打开的那张表，画面上不再叠一层 EPG 字样。
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text(
                        text = "线路 ${streamIndex + 1} / ${channel.streams.size}",
                        color = Color.White,
                        fontSize = 20.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    // 播通时这一行不再写字样（用户 10-23：去掉画面上的「直播」文字），暂停提示照旧。
                    // 还没出画面的等待期也只留「线路 k / N」这一条（用户 10-25 指令 3）：
                    // 那句状态文字已经写在画面中间的倒计时上，同屏不能出现两句一样的提示。
                    val playStatus = when {
                        waitingForPicture -> ""
                        statusText.isNotBlank() -> statusText
                        isPlaying -> ""
                        else -> "已暂停"
                    }
                    if (playStatus.isNotBlank()) {
                        Spacer(Modifier.height(3.dp))
                        Text(
                            text = playStatus,
                            color = if (statusText.isNotBlank()) TvColors.Accent else Color(0xFFCBD5E1),
                            fontSize = 13.sp,
                        )
                    }
                    // 分辨率行照旧归设置里的「显示清晰度」管，只是等待期间不出现（同上，10-25 指令 3），
                    // 画面播稳之后这一段自己恢复。
                    val resolution = if (prefs.showVideoResolution && !waitingForPicture) {
                        resolutionLine(videoSize)
                    } else {
                        null
                    }
                    if (resolution != null) {
                        Spacer(Modifier.height(3.dp))
                        Text(
                            text = resolution,
                            color = Color(0xFF94A3B8),
                            fontSize = 12.sp,
                        )
                    }
                }
            }
        }

        // 换线/换台还没出画面这一段：画面中间只留这一句按设置时长递减的倒计时，
        // 原来那个「转圈 + 缓冲中」整块不再出现（用户 10-25 指令 3）。
        if (waitingForPicture) {
            Text(
                text = countdownLine(statusText, countdownSeconds.coerceAtLeast(1)),
                color = Color.White,
                fontSize = 30.sp,
                fontWeight = FontWeight.SemiBold,
                textAlign = TextAlign.Center,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier
                    .align(Alignment.Center)
                    .width(760.dp)
                    .clip(RoundedCornerShape(16.dp))
                    .background(Color.Black.copy(alpha = 0.55f))
                    .padding(horizontal = 28.dp, vertical = 16.dp),
            )
        }

        // 数字键选台：把正在输入的那几位数显示在右下角，停手 2.5 秒后按台号跳过去。
        if (digitEntry.isNotEmpty()) {
            Text(
                text = digitEntry,
                color = Color.White,
                fontSize = 46.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier
                    .align(Alignment.BottomEnd)
                    .padding(end = 56.dp, bottom = 120.dp)
                    .clip(RoundedCornerShape(14.dp))
                    .background(Color.Black.copy(alpha = 0.7f))
                    .padding(horizontal = 22.dp, vertical = 8.dp),
            )
        }

        AnimatedVisibility(
            visible = controlsVisible && errorMessage == null,
            modifier = Modifier.align(Alignment.BottomCenter),
            enter = slideInVertically { it / 2 } + fadeIn(),
            exit = slideOutVertically { it / 2 } + fadeOut(),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(
                        Brush.verticalGradient(
                            listOf(Color.Transparent, Color.Black.copy(alpha = 0.92f)),
                        ),
                    )
                    .padding(horizontal = 40.dp, vertical = 26.dp),
            ) {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    PlayerActionButton(
                        icon = Icons.Rounded.List,
                        label = "选线",
                        focusRequester = barFocus,
                        onClick = { linePickerVisible = true },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.LiveTv,
                        label = "选台",
                        enabled = queue.size > 1,
                        onClick = { channelPickerVisible = true },
                    )
                    // 用户 10-25 指令 1：这一排去掉「上一个／下一个／铺满／收藏」四颗
                    //（换台靠上下键与数字键，画面比例留给设置里的菜单键，收藏在首页做），
                    // 并把「节目单」与「时长」两颗的位置对调。
                    PlayerActionButton(
                        icon = Icons.Rounded.Timer,
                        label = "时长 ${lineSeconds}秒",
                        onClick = {
                            lineSeconds = if (lineSeconds in LINE_SECONDS_OPTIONS) {
                                LINE_SECONDS_OPTIONS[
                                    (LINE_SECONDS_OPTIONS.indexOf(lineSeconds) + 1) %
                                        LINE_SECONDS_OPTIONS.size]
                            } else {
                                LINE_SECONDS_OPTIONS[0]
                            }
                            prefs.lineSwitchSeconds = lineSeconds
                        },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.Schedule,
                        label = "节目单",
                        onClick = { guideVisible = true },
                    )
                    Spacer(Modifier.weight(1f))
                    PlayerActionButton(
                        icon = Icons.Rounded.ArrowBack,
                        label = "返回",
                        onClick = onBack,
                    )
                }
                Spacer(Modifier.height(10.dp))
                Text(
                    text = playerKeyHint(prefs),
                    color = Color(0xFF94A3B8),
                    fontSize = 12.sp,
                )
            }
        }

        errorMessage?.let { message ->
            Column(
                modifier = Modifier
                    .align(Alignment.Center)
                    .width(620.dp)
                    .clip(RoundedCornerShape(22.dp))
                    .background(Color(0xF20C1424))
                    .border(1.dp, TvColors.Divider, RoundedCornerShape(22.dp))
                    .padding(32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(
                    imageVector = Icons.Rounded.WifiOff,
                    contentDescription = null,
                    tint = TvColors.Accent,
                    modifier = Modifier.size(44.dp),
                )
                Spacer(Modifier.height(12.dp))
                Text(
                    text = "暂时无法播放",
                    color = Color.White,
                    fontSize = 28.sp,
                    fontWeight = FontWeight.Bold,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    text = channel.name,
                    color = TvColors.Accent,
                    fontSize = 18.sp,
                )
                Spacer(Modifier.height(10.dp))
                Text(
                    text = message,
                    color = TvColors.TextSecondary,
                    fontSize = 15.sp,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    text = "已尝试 ${streamIndex + 1} / ${channel.streams.size} 条线路",
                    color = TvColors.TextSecondary.copy(alpha = 0.7f),
                    fontSize = 13.sp,
                )
                Spacer(Modifier.height(20.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    PlayerActionButton(
                        icon = Icons.Rounded.List,
                        label = "选择线路",
                        onClick = { linePickerVisible = true },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.Refresh,
                        label = "重试全部线路",
                        focusRequester = retryFocus,
                        onClick = {
                            unplayableChannels.clear()
                            reconnectsOnLine = 0
                            streamIndex = 0
                            errorMessage = null
                            statusText = connectingText(channel.name, 0)
                            isBuffering = true
                            retryNonce += 1
                        },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.ArrowBack,
                        label = "返回列表",
                        onClick = onBack,
                    )
                }
            }
        }

        if (linePickerVisible) {
            LinePickerDialog(
                channel = channel,
                selectedIndex = streamIndex,
                probes = lineProbes,
                probing = probingLines,
                playingSize = videoSize,
                catalogQualityOf = lineQualityOf,
                onRefresh = { probeNonce += 1 },
                onZap = ::zap,
                onSelect = ::chooseLine,
                onDismiss = { linePickerVisible = false },
            )
        }

        if (channelPickerVisible) {
            ChannelPickerDialog(
                queue = queue,
                selectedIndex = index,
                numbers = channelNumbers,
                totalChannels = numberedChannels.size,
                favorites = favorites,
                onZap = ::zap,
                onSelect = { position ->
                    channelPickerVisible = false
                    jumpTo(position)
                },
                onDismiss = { channelPickerVisible = false },
            )
        }

        if (guideVisible) {
            ProgramGuideDialog(
                channelName = channel.name,
                programs = upcomingPrograms,
                epgEnabled = prefs.epgEnabled,
                onZap = ::zap,
                onDismiss = { guideVisible = false },
            )
        }
    }
}

private const val SAME_LINE_RECONNECTS = 2
private const val RECONNECT_AFTER_STABLE_MS = 60_000L
private const val RECOVERY_DEBOUNCE_MS = 1_500L
// 选线面板一次允许几路并发探测。全撒出去会抢走正在播的那条线的带宽（见上面那段）。
private const val PROBE_CONCURRENCY = 2
// 控制栏「时长」按钮的循环档（用户 10-23 19:4x「控制栏增加线路时长选项，10/20/30/50秒」；
// 20:5x 追加「再加一个120秒，让长时间线路能播」）。
private val LINE_SECONDS_OPTIONS = listOf(10, 20, 30, 50, 120)

/** 控制栏显示多久后自己收起来（用户 2026-10-22 要求 10 秒，原来写死 5 秒）。 */
private const val CONTROLS_AUTO_HIDE_MS = 3_000L

/** 数字键选台：停手这么久就把输入的数字当台号。 */
private const val DIGIT_ENTRY_TIMEOUT_MS = 2_500L

private val DIGIT_KEYS: Map<Key, Int> = mapOf(
    Key.Zero to 0, Key.One to 1, Key.Two to 2, Key.Three to 3, Key.Four to 4,
    Key.Five to 5, Key.Six to 6, Key.Seven to 7, Key.Eight to 8, Key.Nine to 9,
    Key.NumPad0 to 0, Key.NumPad1 to 1, Key.NumPad2 to 2, Key.NumPad3 to 3,
    Key.NumPad4 to 4, Key.NumPad5 to 5, Key.NumPad6 to 6, Key.NumPad7 to 7,
    Key.NumPad8 to 8, Key.NumPad9 to 9,
)

/**
 * 从视频 SurfaceView 上取一小块中心像素算哈希（画面冻住这条尺的采样端）。
 *
 * 只取中心 256×144：整幅 1280×720 每 3 秒拷一次在电视盒子上是白给的开销，
 * 而「画面没在动」这件事在中心一块上一样能看出来（台标与字幕条本来就在边上，
 * 它们会闪，取中心也顺手把它们排除掉）。PixelCopy 要 API 26，低版本这一格直接不作判。
 */
private suspend fun sampleSurfaceHash(view: SurfaceView, buf: IntArray): Long? {
    if (android.os.Build.VERSION.SDK_INT < 26) return null
    val w = view.width
    val h = view.height
    if (w < 64 || h < 64) return null
    val rw = minOf(256, w)
    val rh = minOf(144, h)
    val left = (w - rw) / 2
    val top = (h - rh) / 2
    val rect = android.graphics.Rect(left, top, left + rw, top + rh)
    val bmp = android.graphics.Bitmap.createBitmap(rw, rh, android.graphics.Bitmap.Config.ARGB_8888)
    return suspendCancellableCoroutine { cont ->
        val onDone: (Int) -> Unit = { result ->
            val hash = if (result == android.view.PixelCopy.SUCCESS) {
                bmp.getPixels(buf, 0, rw, 0, 0, rw, rh)
                FreezeWatch.hash(buf.copyOf(rw * rh))
            } else {
                null
            }
            if (cont.isActive) cont.resume(hash)
            bmp.recycle()
        }
        try {
            android.view.PixelCopy.request(view, rect, bmp, { result -> onDone(result) }, view.handler)
        } catch (t: Throwable) {
            bmp.recycle()
            if (cont.isActive) cont.resume(null)
        }
    }
}

private fun connectingText(channelName: String, lineIndex: Int): String =
    if (lineIndex == 0) "正在连接 $channelName…" else "正在尝试线路 ${lineIndex + 1}…"

/**
 * 还没出画面时画面中间那一句（用户 10-25 指令 3：「将 loading 改为设置的时长的倒计时」）。
 *
 * 前半截直接沿用状态栏那句话的口径（[connectingText] 给的就是「正在连接 某台…」/
 * 「正在尝试线路 k…」，原地重连、切备用线那些字样也一并留着），末尾挂上还剩几秒：
 * 同屏不再出现两句重复提示，右上角这一段等待期只留「线路 k / N」。
 * 全中文，不留 loading 这种英文字样。
 */
private fun countdownLine(status: String, secondsLeft: Int): String {
    val head = status.removeSuffix("…").trim().ifBlank { "正在连接" }
    return "$head · 还有 $secondsLeft 秒"
}

/**
 * 遥控器操作优先级最高（用户 10-24 18:3x「不受状态栏等遮罩影响」）：
 * 选台/选线/节目单是**独立窗口**，根布局那层 preview 收不到它们的按键，
 * 所以每个弹层自己接一遍 CH± 与媒体上一台/下一台——弹层开着也能直接换台，
 * 换台那一下由 [PlayerScreen] 的 jumpTo 统一把弹层收掉。
 * 上下键不在这里抢：弹层里它们要用来走列表（那是用户明确要的焦点导航）。
 */
private fun Modifier.zapWhileOpen(onZap: (Int) -> Unit): Modifier = onPreviewKeyEvent { event ->
    if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
    when (event.key) {
        Key.ChannelUp, Key.MediaPrevious -> {
            onZap(-1)
            true
        }

        Key.ChannelDown, Key.MediaNext -> {
            onZap(1)
            true
        }

        else -> false
    }
}

/** 清晰度标签（用户 10-24 18:3x「线路选择加清晰度」）：标签 + 原始尺寸，尺寸未知返回 null。 */
private fun qualityText(width: Int, height: Int): String? {
    if (width <= 0 || height <= 0) return null
    val tier = when {
        height >= 2000 || width >= 3800 -> "4K"
        height >= 1000 || width >= 1900 -> "全高清"
        height >= 700 || width >= 1200 -> "高清"
        else -> "标清"
    }
    return "$tier $width×$height"
}

/** 清单/目录里写的那一档（"1920×1080"、"1920x1080"）转成同一个标签；读数为空返回 null。 */
private fun qualityText(resolution: String?): String? {
    val m = Regex("(\\d+)[×x](\\d+)").find(resolution ?: return null) ?: return null
    return qualityText(m.groupValues[1].toIntOrNull() ?: 0, m.groupValues[2].toIntOrNull() ?: 0)
}

/**
 * 选线面板里一条线的清晰度标签，按「谁最准」依次取第一个有读数的来源
 *（用户 10-25 指令 5「线路选择画面没看到每条线路的清晰度」）：
 * 正在播的这条用播放器报的真实解码尺寸 → 目录里构建时实测的尺寸（每条线都有）
 * → 本次打开面板的现场探针读数 → 三样都没有返回 null，由调用方写「清晰度未知」。
 */
private fun lineQualityLabel(
    playingLine: Boolean,
    playingSize: VideoSize?,
    catalogResolution: String?,
    probeResolution: String?,
): String? {
    if (playingLine && playingSize != null) {
        qualityText(playingSize.width, playingSize.height)?.let { return it }
    }
    return qualityText(catalogResolution) ?: qualityText(probeResolution)
}

private fun resolutionLine(size: VideoSize?): String? {
    val width = size?.width ?: 0
    val height = size?.height ?: 0
    if (width <= 0 || height <= 0) return null
    val tier = when {
        height >= 2160 || width >= 3840 -> "4K"
        height >= 1080 || width >= 1920 -> "全高清"
        height >= 720 || width >= 1280 -> "高清"
        else -> "标清"
    }
    return "$width×$height · $tier"
}

@Composable
private fun ProgramGuideDialog(
    channelName: String,
    programs: List<EpgProgram>,
    epgEnabled: Boolean,
    onZap: (Int) -> Unit,
    onDismiss: () -> Unit,
) {
    val now = System.currentTimeMillis()
    val onAirIndex = programs.indexOfLast { it.start <= now }.coerceAtLeast(0)
    val listState = rememberLazyListState(initialFirstVisibleItemIndex = onAirIndex)

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Column(
            modifier = Modifier
                .width(760.dp)
                .heightIn(max = 560.dp)
                .zapWhileOpen(onZap)
                .clip(RoundedCornerShape(22.dp))
                .background(TvColors.BackgroundAlt)
                .border(1.dp, TvColors.Divider, RoundedCornerShape(22.dp))
                .padding(horizontal = 26.dp, vertical = 22.dp),
        ) {
            Text(
                text = "节目单",
                color = TvColors.TextPrimary,
                fontSize = 27.sp,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                text = when {
                    !epgEnabled -> "$channelName · 节目单已在设置里关闭"
                    programs.isEmpty() -> "$channelName · 公开节目单暂时没覆盖这个频道"
                    else -> "$channelName · 共 ${programs.size} 条 · 时间为北京时间"
                },
                color = TvColors.TextSecondary,
                fontSize = 13.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(12.dp))
            if (programs.isEmpty()) {
                Text(
                    text = "可以先在设置里检查网络，或稍后再看；线路和频道不受影响。",
                    color = TvColors.TextSecondary.copy(alpha = 0.85f),
                    fontSize = 15.sp,
                    modifier = Modifier.padding(vertical = 24.dp),
                )
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(max = 372.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    itemsIndexed(programs, key = { position, _ -> position }) { position, program ->
                        ProgramRow(
                            program = program,
                            now = now,
                            onAir = position == onAirIndex,
                        )
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "按返回键关闭",
                    color = TvColors.TextSecondary.copy(alpha = 0.8f),
                    fontSize = 12.sp,
                )
                Spacer(Modifier.weight(1f))
                PlayerActionButton(
                    icon = Icons.Rounded.Check,
                    label = "完成",
                    onClick = onDismiss,
                )
            }
        }
    }
}

@Composable
private fun ProgramRow(
    program: EpgProgram,
    now: Long,
    onAir: Boolean,
) {
    val shape = RoundedCornerShape(11.dp)
    val live = program.start <= now && program.stop > now
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(
                when {
                    onAir -> TvColors.AccentSoft
                    live -> TvColors.SurfaceFocused
                    else -> Color.Transparent
                },
            )
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = BeijingTime.format(program.start, "HH:mm"),
            color = if (onAir) TvColors.Accent else TvColors.TextSecondary,
            fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.width(14.dp))
        Text(
            text = program.title,
            color = if (program.start > now) TvColors.TextSecondary else TvColors.TextPrimary,
            fontSize = 17.sp,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        if (onAir) {
            Spacer(Modifier.width(10.dp))
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(6.dp))
                    .background(TvColors.Accent)
                    .padding(horizontal = 7.dp, vertical = 2.dp),
            ) {
                Text(
                    text = "正在播出",
                    color = Color(0xFF1A1200),
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
        }
    }
}

private fun playerKeyHint(prefs: TvPrefs): String {
    val up = when (prefs.upKeyAction) {
        DirectionKeyAction.ZapPrevious -> "上键上一个台"
        DirectionKeyAction.ZapNext -> "上键下一个台"
        DirectionKeyAction.MoveFocus -> "上键移焦点"
    }
    val down = when (prefs.downKeyAction) {
        DirectionKeyAction.ZapPrevious -> "下键上一个台"
        DirectionKeyAction.ZapNext -> "下键下一个台"
        DirectionKeyAction.MoveFocus -> "下键移焦点"
    }
    val back = when (prefs.backKeyAction) {
        BackKeyAction.HideControls -> "返回键收起控制栏"
        BackKeyAction.BackToList -> "返回键返回列表"
    }
    val menu = when (prefs.menuKeyAction) {
        MenuKeyAction.Lines -> "菜单键选线"
        MenuKeyAction.Aspect -> "菜单键切换画面"
        MenuKeyAction.Off -> "菜单键无动作"
    }
    return "OK 选台 · 左右键换线 · 数字键输台号 · $up · $down · $menu · $back"
}

@Composable
private fun ChannelPickerDialog(
    queue: List<TvChannel>,
    selectedIndex: Int,
    numbers: Map<String, Int>,
    totalChannels: Int,
    favorites: Set<String>,
    onZap: (Int) -> Unit,
    onSelect: (Int) -> Unit,
    onDismiss: () -> Unit,
) {
    val listState = rememberLazyListState(
        initialFirstVisibleItemIndex = (selectedIndex - 2).coerceAtLeast(0),
    )
    val selectedFocus = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        delay(160)
        runCatching { selectedFocus.requestFocus() }
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Column(
            modifier = Modifier
                .width(880.dp)
                .heightIn(max = 620.dp)
                .zapWhileOpen(onZap)
                .clip(RoundedCornerShape(22.dp))
                .background(TvColors.BackgroundAlt)
                .border(1.dp, TvColors.Divider, RoundedCornerShape(22.dp))
                .padding(horizontal = 26.dp, vertical = 22.dp),
        ) {
            Text(
                text = "选择频道",
                color = TvColors.TextPrimary,
                fontSize = 27.sp,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                text = "全频道共 $totalChannels 个台 · 每行最前面那个数就是台号，遥控器按数字键直接输它",
                color = TvColors.TextSecondary,
                fontSize = 13.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(12.dp))
            LazyColumn(
                state = listState,
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f, fill = false)
                    .heightIn(max = 420.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                itemsIndexed(queue, key = { position, channel -> position }) { position, item ->
                    ChannelChoiceRow(
                        number = numbers[item.id],
                        channel = item,
                        selected = position == selectedIndex,
                        favorite = item.id in favorites,
                        focusRequester = if (position == selectedIndex) selectedFocus else null,
                        onClick = { onSelect(position) },
                    )
                }
            }
            Spacer(Modifier.height(12.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "上下键移动 · OK 键选台 · 返回键关闭",
                    color = TvColors.TextSecondary.copy(alpha = 0.8f),
                    fontSize = 12.sp,
                )
                Spacer(Modifier.weight(1f))
                PlayerActionButton(
                    icon = Icons.Rounded.Check,
                    label = "关闭",
                    onClick = onDismiss,
                )
            }
        }
    }
}

@Composable
private fun ChannelChoiceRow(
    number: Int?,
    channel: TvChannel,
    selected: Boolean,
    favorite: Boolean,
    focusRequester: FocusRequester?,
    onClick: () -> Unit,
) {
    val fallbackFocus = remember { FocusRequester() }
    var focused by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(11.dp)

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(50.dp)
            .clip(shape)
            .background(
                when {
                    focused -> TvColors.SurfaceFocused
                    selected -> TvColors.AccentSoft
                    else -> TvColors.Surface
                },
            )
            .border(
                width = 2.dp,
                color = when {
                    focused -> TvColors.Accent
                    selected -> TvColors.Accent.copy(alpha = 0.55f)
                    else -> TvColors.Divider.copy(alpha = 0.65f)
                },
                shape = shape,
            )
            .focusRequester(focusRequester ?: fallbackFocus)
            .onFocusChanged { focused = it.isFocused }
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = number?.toString() ?: "",
            color = if (selected || focused) TvColors.Accent else TvColors.TextSecondary,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.width(58.dp),
        )
        Text(
            text = channel.name,
            color = if (selected) TvColors.Accent else TvColors.TextPrimary,
            fontSize = 17.sp,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        if (favorite) {
            Icon(
                imageVector = Icons.Rounded.Favorite,
                contentDescription = null,
                tint = TvColors.Accent,
                modifier = Modifier.size(16.dp),
            )
            Spacer(Modifier.width(10.dp))
        }
        Text(
            text = "${channel.streams.size} 条线路",
            color = TvColors.TextSecondary.copy(alpha = 0.75f),
            fontSize = 12.sp,
        )
        if (selected) {
            Spacer(Modifier.width(12.dp))
            Text(
                text = "正在播放",
                color = TvColors.Accent,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}

@Composable
private fun LinePickerDialog(
    channel: TvChannel,
    selectedIndex: Int,
    probes: Map<Int, StreamProbe.Result>,
    probing: Boolean,
    playingSize: VideoSize?,
    catalogQualityOf: (String) -> String?,
    onRefresh: () -> Unit,
    onZap: (Int) -> Unit,
    onSelect: (Int) -> Unit,
    onDismiss: () -> Unit,
) {
    val listState = rememberLazyListState(
        initialFirstVisibleItemIndex = (selectedIndex - 1).coerceAtLeast(0),
    )
    val selectedFocus = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        delay(160)
        runCatching { selectedFocus.requestFocus() }
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Column(
            modifier = Modifier
                .width(820.dp)
                .heightIn(max=492.dp)
                .zapWhileOpen(onZap)
                .clip(RoundedCornerShape(22.dp))
                .background(TvColors.BackgroundAlt)
                .border(1.dp, TvColors.Divider, RoundedCornerShape(22.dp))
                .padding(horizontal = 26.dp, vertical = 22.dp),
        ) {
            Text(
                text = "选择线路",
                color = TvColors.TextPrimary,
                fontSize = 27.sp,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                text = "${channel.name} · 共 ${channel.streams.size} 条 · 绿=可用 · " +
                    "黄=清单通但画面取不到 · 清晰度取自当前解码/目录实测/现场探测，都没有写未知",
                color = TvColors.TextSecondary,
                fontSize = 13.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(12.dp))

            LazyColumn(
                state = listState,
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 264.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                itemsIndexed(channel.streams, key = { lineIndex, _ -> lineIndex }) { lineIndex, url ->
                    LineChoiceRow(
                        lineIndex = lineIndex,
                        url = url,
                        selected = lineIndex == selectedIndex,
                        result = probes[lineIndex],
                        probing = probing,
                        // 每条线的清晰度按「谁最准」依次取：正在播的这条用播放器报的真实解码尺寸
                        // → 目录里构建时实测的尺寸（每条线都有，不再只有正在播的那条看得见）
                        // → 本次打开面板的现场探针读数 → 都没有才写「清晰度未知」
                        //（用户 10-25 指令 5「线路选择画面没看到每条线路的清晰度」）。
                        quality = lineQualityLabel(
                            playingLine = lineIndex == selectedIndex,
                            playingSize = playingSize,
                            catalogResolution = catalogQualityOf(url),
                            probeResolution = probes[lineIndex]?.resolution,
                        ) ?: "清晰度未知",
                        focusRequester = if (lineIndex == selectedIndex) selectedFocus else null,
                        onClick = { onSelect(lineIndex) },
                    )
                }
            }

            Spacer(Modifier.height(12.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = if (probing) {
                        "正在检测 ${probes.size}/${channel.streams.size}"
                    } else {
                        "只有绿色那条真取到过画面 · 时间均为北京时间"
                    },
                    color = TvColors.TextSecondary.copy(alpha = 0.8f),
                    fontSize = 12.sp,
                )
                Spacer(Modifier.weight(1f))
                PlayerActionButton(
                    icon = Icons.Rounded.Refresh,
                    label = "重新检测",
                    enabled = !probing,
                    onClick = onRefresh,
                )
                Spacer(Modifier.width(10.dp))
                PlayerActionButton(
                    icon = Icons.Rounded.Check,
                    label = "完成",
                    onClick = onDismiss,
                )
            }
        }
    }
}

@Composable
private fun LineChoiceRow(
    lineIndex: Int,
    url: String,
    selected: Boolean,
    result: StreamProbe.Result?,
    probing: Boolean,
    quality: String,
    focusRequester: FocusRequester?,
    onClick: () -> Unit,
) {
    val fallbackFocus = remember { FocusRequester() }
    var focused by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(13.dp)
    val responseText = when {
        result?.playable == true -> "可用 · 清单 ${result.responseMs} ms · 画面 ${result.segmentMs} ms"
        result?.responseMs != null -> "清单通，画面取不到（${result.error}）"
        result?.error != null -> result.error
        probing -> "检测中…"
        else -> "未检测"
    }
    val responseColor = when {
        result?.playable == true -> TvColors.Success
        // 清单通了不代表能看，绝不能给绿色（真机上「线路 1 显示可用却播不出来」就是这么来的）。
        result?.responseMs != null -> TvColors.Accent
        else -> Color(0xFFFF8A80)
    }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(58.dp)
            .clip(shape)
            .background(
                when {
                    focused -> TvColors.SurfaceFocused
                    selected -> TvColors.AccentSoft
                    else -> TvColors.Surface
                },
            )
            .border(
                width = 2.dp,
                color = when {
                    focused -> TvColors.Accent
                    selected -> TvColors.Accent.copy(alpha = 0.55f)
                    else -> TvColors.Divider.copy(alpha = 0.65f)
                },
                shape = shape,
            )
            .focusRequester(focusRequester ?: fallbackFocus)
            .onFocusChanged { focused = it.isFocused }
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = "线路 ${lineIndex + 1}",
                    color = if (selected || focused) TvColors.Accent else TvColors.TextPrimary,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                if (selected) {
                    Spacer(Modifier.width(8.dp))
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(6.dp))
                            .background(TvColors.Accent)
                            .padding(horizontal = 7.dp, vertical = 2.dp),
                    ) {
                        Text(
                            text = "使用中",
                            color = Color(0xFF1A1200),
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                }
            }
            Spacer(Modifier.height(2.dp))
            Text(
                text = "$quality · ${streamLabel(url)}",
                color = TvColors.TextSecondary,
                fontSize = 12.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.width(16.dp))
        Column(horizontalAlignment = Alignment.End) {
            Text(
                text = responseText,
                color = responseColor,
                fontSize = 13.sp,
                fontWeight = FontWeight.Medium,
            )
            Text(
                text = result?.let {
                    BeijingTime.format(it.checkedAt, "HH:mm:ss") + " 北京"
                } ?: "—",
                color = TvColors.TextSecondary.copy(alpha = 0.72f),
                fontSize = 11.sp,
            )
        }
    }
}

private fun streamLabel(url: String): String {
    val host = runCatching { Uri.parse(url).host }.getOrNull()
    val kind = when {
        url.contains(".m3u8", ignoreCase = true) -> "HLS"
        url.contains(".mpd", ignoreCase = true) -> "DASH"
        url.contains(".flv", ignoreCase = true) -> "FLV"
        else -> "直播流"
    }
    return if (host.isNullOrBlank()) kind else "$kind · $host"
}

private fun friendlyError(error: Throwable): String {
    val playbackError = error as? PlaybackException
    val code = playbackError?.errorCode
    val reason = when (code) {
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED -> "网络连接失败"
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT -> "连接超时"
        PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS -> "直播源返回错误，可能已经失效"
        PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED,
        PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED -> "直播流格式无法解析"
        PlaybackException.ERROR_CODE_DECODING_FAILED -> "画面解码失败"
        else -> "播放失败"
    }
    return if (code == null) {
        "$reason：${error.message ?: error.javaClass.simpleName}"
    } else {
        "$reason（代码 $code）"
    }
}

/** 左上角第二行：「台号 N · 分组 · 子组」。台号行与画面同刻翻页时两处都用这一句。 */
private fun captionMeta(number: Int?, category: String, subgroup: String): String =
    (if (number != null) "台号 $number · " else "") + "$category · $subgroup"
