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
import androidx.compose.material.icons.rounded.AspectRatio
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.Favorite
import androidx.compose.material.icons.rounded.FavoriteBorder
import androidx.compose.material.icons.rounded.LiveTv
import androidx.compose.material.icons.rounded.List
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Schedule
import androidx.compose.material.icons.rounded.SkipNext
import androidx.compose.material.icons.rounded.SkipPrevious
import androidx.compose.material.icons.rounded.WifiOff
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.dash.DashMediaSource
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import com.parentstv.live.data.BackKeyAction
import com.parentstv.live.data.BeijingTime
import com.parentstv.live.data.DirectionKeyAction
import com.parentstv.live.data.EpgProgram
import com.parentstv.live.data.EpgRepository
import com.parentstv.live.data.EpgSnapshot
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
import kotlinx.coroutines.withContext

@Composable
fun PlayerScreen(
    initialChannel: TvChannel,
    initialQueue: List<TvChannel>,
    categoryName: String,
    favorites: Set<String>,
    prefs: TvPrefs,
    onToggleFavorite: (TvChannel) -> Unit,
    onChannelChanged: (TvChannel) -> Unit,
    onBack: () -> Unit,
) {
    val context = LocalContext.current
    val queue = remember(initialQueue, initialChannel) {
        initialQueue.filter { it.streams.isNotEmpty() }.ifEmpty { listOf(initialChannel) }
    }
    var index by remember {
        mutableIntStateOf(queue.indexOfFirst { it.id == initialChannel.id }.coerceAtLeast(0))
    }
    val channel = queue.getOrElse(index) { initialChannel }
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
    // 本台里已经真出过画面的线路号。换线只允许发生在没出过画面的线路上（见 LineRecovery）。
    val playedLines = remember(channel.id) { mutableSetOf<Int>() }
    var errorMessage by remember(channel.id) { mutableStateOf<String?>(null) }
    var statusText by remember(channel.id) { mutableStateOf(connectingText(channel.name, streamIndex)) }
    var videoSize by remember(channel.id, streamIndex) { mutableStateOf<VideoSize?>(null) }
    // 本次进入播放器后已经试过、且所有线路都起不了画面的频道。自动换台要靠它决定什么时候停手。
    val unplayableChannels = remember { mutableSetOf<String>() }
    var isPlaying by remember { mutableStateOf(false) }
    var isBuffering by remember { mutableStateOf(true) }
    var controlsVisible by remember { mutableStateOf(true) }
    var zapNotice by remember { mutableStateOf(false) }
    var aspectIndex by remember { mutableIntStateOf(0) }
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
    val onAirProgram = remember(epgSnapshot, channel.name, minuteTick) {
        epgSnapshot.onAir(channel.name)
    }
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
    val aspectLabels = listOf("适应", "铺满", "缩放")

    val httpFactory = remember {
        DefaultHttpDataSource.Factory()
            .setUserAgent(
                "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 " +
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            )
            .setConnectTimeoutMs(12_000)
            .setReadTimeoutMs(18_000)
            .setAllowCrossProtocolRedirects(true)
    }
    val exoPlayer = remember {
        ExoPlayer.Builder(context)
            .setMediaSourceFactory(DefaultMediaSourceFactory(httpFactory))
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

    // 节目单整份 12 MB 左右，取一次存 6 小时，切频道不再重复下载；
    // 关掉开关就不占这次请求。
    LaunchedEffect(prefs.epgEnabled) {
        if (!prefs.epgEnabled) return@LaunchedEffect
        epgSnapshot = EpgRepository(context).snapshot()
    }

    LaunchedEffect(Unit) {
        while (true) {
            delay(30_000)
            minuteTick += 1
        }
    }

    LaunchedEffect(linePickerVisible, channel.id, probeNonce) {
        if (!linePickerVisible) return@LaunchedEffect
        lineProbes.clear()
        probingLines = true
        coroutineScope {
            channel.streams.forEachIndexed { lineIndex, url ->
                launch(Dispatchers.IO) {
                    val result = StreamProbe.probe(url)
                    withContext(Dispatchers.Main) {
                        lineProbes[lineIndex] = result
                    }
                }
            }
        }
        probingLines = false
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
            // 起播阶段按设置里配的等待时间算失败（默认 10 秒）；已经出过画面后卡住的时间沿用同一档。
            val limit = prefs.lineSwitchSeconds * 1_000L
            if (stalledMs >= limit) {
                tryNextLine("线路长时间无响应，请检查网络后重试")
                break
            }
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
                        statusText = "直播已结束"
                    }

                    else -> Unit
                }
            }

            override fun onVideoSizeChanged(size: VideoSize) {
                videoSize = size
            }

            override fun onIsPlayingChanged(playing: Boolean) {
                isPlaying = playing
                if (playing) {
                    isBuffering = false
                    statusText = ""
                    playedLines.add(lineBeingPlayed)
                    prefs.setLine(channel.id, url)
                }
            }

            override fun onPlayerError(error: PlaybackException) {
                tryNextLine(friendlyError(error))
            }
        }
        exoPlayer.addListener(listener)

        try {
            val mediaItem = MediaItem.fromUri(url)
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

    // 数字键停手 2.5 秒后把这串数字当台号用：本组队列里第 N 个台。
    LaunchedEffect(digitEntry) {
        val typed = digitEntry
        if (typed.isEmpty()) return@LaunchedEffect
        delay(DIGIT_ENTRY_TIMEOUT_MS)
        digitEntry = ""
        val number = typed.toIntOrNull() ?: return@LaunchedEffect
        if (number in 1..queue.size) {
            if (number - 1 != index) jumpTo(number - 1)
        } else {
            statusText = "本组只有 ${queue.size} 个台，没有第 $number 台"
            zapNotice = true
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
            .onKeyEvent { event ->
                if (event.type != KeyEventType.KeyDown) return@onKeyEvent false
                when (event.key) {
                    Key.DirectionUp -> {
                        if (!controlsVisible) {
                            zapUpOrDown(prefs.upKeyAction)
                        } else {
                            false
                        }
                    }

                    Key.DirectionDown -> {
                        if (!controlsVisible) {
                            zapUpOrDown(prefs.downKeyAction)
                        } else {
                            false
                        }
                    }

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

                    Key.ChannelUp -> {
                        zap(-1)
                        true
                    }

                    Key.ChannelDown -> {
                        zap(1)
                        true
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
                        text = channel.name,
                        color = Color.White,
                        fontSize = 32.sp,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = "$categoryName · ${channel.subgroup}",
                        color = Color(0xFFCBD5E1),
                        fontSize = 15.sp,
                    )
                    if (prefs.epgEnabled && onAirProgram != null) {
                        Spacer(Modifier.height(4.dp))
                        Text(
                            text = onAirLine(onAirProgram),
                            color = TvColors.Success,
                            fontSize = 15.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text(
                        text = "线路 ${streamIndex + 1} / ${channel.streams.size}",
                        color = Color.White,
                        fontSize = 20.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(3.dp))
                    Text(
                        text = when {
                            statusText.isNotBlank() -> statusText
                            isPlaying -> "直播中"
                            else -> "已暂停"
                        },
                        color = if (statusText.isNotBlank()) TvColors.Accent else Color(0xFFCBD5E1),
                        fontSize = 13.sp,
                    )
                    val resolution = if (prefs.showVideoResolution) resolutionLine(videoSize) else null
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

        if (errorMessage == null && isBuffering && !isPlaying) {
            Column(
                modifier = Modifier.align(Alignment.Center),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Box(
                    modifier = Modifier
                        .size(64.dp)
                        .clip(RoundedCornerShape(32.dp))
                        .background(Color.Black.copy(alpha = 0.45f)),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator(
                        color = TvColors.Accent,
                        strokeWidth = 4.dp,
                        modifier = Modifier.size(38.dp),
                    )
                }
                Spacer(Modifier.height(12.dp))
                Text(
                    text = statusText.ifBlank { "缓冲中…" },
                    color = Color.White.copy(alpha = 0.82f),
                    fontSize = 15.sp,
                )
            }
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
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    PlayerActionButton(
                        icon = if (channel.id in favorites) {
                            Icons.Rounded.Favorite
                        } else {
                            Icons.Rounded.FavoriteBorder
                        },
                        label = if (channel.id in favorites) "已收藏" else "收藏",
                        accent = channel.id in favorites,
                        focusRequester = barFocus,
                        onClick = { onToggleFavorite(channel) },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.LiveTv,
                        label = "选台",
                        enabled = queue.size > 1,
                        onClick = { channelPickerVisible = true },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.SkipPrevious,
                        label = "上一个",
                        enabled = queue.size > 1,
                        onClick = { zap(-1) },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.SkipNext,
                        label = "下一个",
                        enabled = queue.size > 1,
                        onClick = { zap(1) },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.AspectRatio,
                        label = aspectLabels[aspectIndex],
                        onClick = { aspectIndex = (aspectIndex + 1) % aspectModes.size },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.List,
                        label = "选线",
                        onClick = { linePickerVisible = true },
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
                onRefresh = { probeNonce += 1 },
                onSelect = ::chooseLine,
                onDismiss = { linePickerVisible = false },
            )
        }

        if (channelPickerVisible) {
            ChannelPickerDialog(
                queue = queue,
                selectedIndex = index,
                favorites = favorites,
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
                onDismiss = { guideVisible = false },
            )
        }
    }
}

private const val SAME_LINE_RECONNECTS = 2
private const val RECONNECT_AFTER_STABLE_MS = 60_000L
private const val RECOVERY_DEBOUNCE_MS = 1_500L

/** 控制栏显示多久后自己收起来（用户 2026-10-22 要求 10 秒，原来写死 5 秒）。 */
private const val CONTROLS_AUTO_HIDE_MS = 10_000L

/** 数字键选台：停手这么久就把输入的数字当台号。 */
private const val DIGIT_ENTRY_TIMEOUT_MS = 2_500L

private val DIGIT_KEYS: Map<Key, Int> = mapOf(
    Key.Zero to 0, Key.One to 1, Key.Two to 2, Key.Three to 3, Key.Four to 4,
    Key.Five to 5, Key.Six to 6, Key.Seven to 7, Key.Eight to 8, Key.Nine to 9,
    Key.NumPad0 to 0, Key.NumPad1 to 1, Key.NumPad2 to 2, Key.NumPad3 to 3,
    Key.NumPad4 to 4, Key.NumPad5 to 5, Key.NumPad6 to 6, Key.NumPad7 to 7,
    Key.NumPad8 to 8, Key.NumPad9 to 9,
)

private fun connectingText(channelName: String, lineIndex: Int): String =
    if (lineIndex == 0) "正在连接 $channelName…" else "正在尝试线路 ${lineIndex + 1}…"

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

private fun onAirLine(program: EpgProgram, now: Long = System.currentTimeMillis()): String {
    val minutes = ((program.stop - now) / 60_000L).coerceAtLeast(0L)
    val range = "${BeijingTime.format(program.start, "HH:mm")}-${BeijingTime.format(program.stop, "HH:mm")}"
    return "${program.title} · $range · 还剩 $minutes 分钟"
}

@Composable
private fun ProgramGuideDialog(
    channelName: String,
    programs: List<EpgProgram>,
    epgEnabled: Boolean,
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
    favorites: Set<String>,
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
                text = "本组共 ${queue.size} 个台 · 也可以直接用数字键输台号（如 1 2 表示第 12 台）",
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
                        position = position,
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
    position: Int,
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
            text = "${position + 1}",
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
    onRefresh: () -> Unit,
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
                text = "${channel.name} · 共 ${channel.streams.size} 条 · 绿=清单和画面都取到 · 黄=清单通但画面取不到",
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
                text = streamLabel(url),
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
