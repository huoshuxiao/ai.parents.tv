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
import androidx.compose.material.icons.rounded.List
import androidx.compose.material.icons.rounded.Pause
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Refresh
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
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.dash.DashMediaSource
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import com.parentstv.live.data.BackKeyAction
import com.parentstv.live.data.BeijingTime
import com.parentstv.live.data.MenuKeyAction
import com.parentstv.live.data.StreamProbe
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
        val preferred = channel.streams.indexOf(prefs.lineFor(channel.id))
        mutableIntStateOf(preferred.coerceAtLeast(0))
    }
    var retryNonce by remember(channel.id) { mutableIntStateOf(0) }
    var errorMessage by remember(channel.id) { mutableStateOf<String?>(null) }
    var statusText by remember(channel.id) { mutableStateOf("正在连接…") }
    var isPlaying by remember { mutableStateOf(false) }
    var isBuffering by remember { mutableStateOf(true) }
    var controlsVisible by remember { mutableStateOf(true) }
    var zapNotice by remember { mutableStateOf(false) }
    var aspectIndex by remember { mutableIntStateOf(0) }
    var linePickerVisible by remember { mutableStateOf(false) }
    var probeNonce by remember { mutableIntStateOf(0) }
    var probingLines by remember { mutableStateOf(false) }
    val lineProbes = remember { mutableStateMapOf<Int, StreamProbe.Result>() }

    val rootFocus = remember { FocusRequester() }
    val playFocus = remember { FocusRequester() }
    val retryFocus = remember { FocusRequester() }
    val aspectModes = remember {
        listOf(
            AspectRatioFrameLayout.RESIZE_MODE_FIT,
            AspectRatioFrameLayout.RESIZE_MODE_FILL,
            AspectRatioFrameLayout.RESIZE_MODE_ZOOM,
        )
    }
    val aspectLabels = listOf("画面 适应", "画面 铺满", "画面 缩放")

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

    fun zap(delta: Int) {
        if (queue.size <= 1) return
        val next = (index + delta + queue.size) % queue.size
        index = next
        errorMessage = null
        statusText = "正在连接…"
        isBuffering = true
        zapNotice = true
        linePickerVisible = false
        onChannelChanged(queue[next])
    }

    fun chooseLine(lineIndex: Int) {
        val url = channel.streams.getOrNull(lineIndex) ?: return
        prefs.setLine(channel.id, url)
        linePickerVisible = false
        if (streamIndex == lineIndex) {
            retryNonce += 1
        } else {
            streamIndex = lineIndex
        }
        errorMessage = null
        statusText = "正在尝试线路 ${lineIndex + 1}…"
        isBuffering = true
        controlsVisible = true
    }

    LaunchedEffect(channel.id) {
        onChannelChanged(channel)
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

    fun tryNextLine(message: String) {
        if (streamIndex < channel.streams.lastIndex) {
            statusText = "线路 ${streamIndex + 1} 不可用，正在切换备用线路…"
            streamIndex += 1
        } else {
            errorMessage = message
            statusText = ""
            isBuffering = false
            controlsVisible = true
        }
    }

    LaunchedEffect(channel.id, streamIndex, retryNonce) {
        var stalledMs = 0L
        while (true) {
            delay(1_000)
            val waiting = exoPlayer.playWhenReady && !exoPlayer.isPlaying
            stalledMs = if (waiting) stalledMs + 1_000 else 0L
            if (stalledMs >= 25_000) {
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
        statusText = if (streamIndex == 0) "正在连接…" else "正在尝试线路 ${streamIndex + 1}…"

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
                    }

                    Player.STATE_ENDED -> {
                        isBuffering = false
                        statusText = "直播已结束"
                    }

                    else -> Unit
                }
            }

            override fun onIsPlayingChanged(playing: Boolean) {
                isPlaying = playing
                if (playing) {
                    isBuffering = false
                    statusText = ""
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

    LaunchedEffect(controlsVisible, isPlaying, errorMessage, streamIndex, linePickerVisible) {
        if (controlsVisible && isPlaying && errorMessage == null && !linePickerVisible) {
            delay(5_000)
            controlsVisible = false
        }
    }

    LaunchedEffect(zapNotice) {
        if (zapNotice) {
            delay(2_500)
            zapNotice = false
        }
    }

    LaunchedEffect(controlsVisible, errorMessage, linePickerVisible) {
        if (linePickerVisible) return@LaunchedEffect
        delay(80)
        when {
            errorMessage != null -> runCatching { retryFocus.requestFocus() }
            controlsVisible -> runCatching { playFocus.requestFocus() }
            else -> runCatching { rootFocus.requestFocus() }
        }
    }

    BackHandler {
        if (linePickerVisible) {
            linePickerVisible = false
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
                        if (!controlsVisible && prefs.zapWithUpDown) {
                            zap(-1)
                            true
                        } else {
                            false
                        }
                    }

                    Key.DirectionDown -> {
                        if (!controlsVisible && prefs.zapWithUpDown) {
                            zap(1)
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

                    Key.DirectionCenter, Key.Enter, Key.NumPadEnter -> {
                        if (!controlsVisible) {
                            controlsVisible = true
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
                            MenuKeyAction.Favorite -> {
                                onToggleFavorite(channel)
                                controlsVisible = true
                                true
                            }

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

                    else -> false
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
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(6.dp))
                        .background(TvColors.Live)
                        .padding(horizontal = 8.dp, vertical = 3.dp),
                ) {
                    Text(
                        text = "直播",
                        color = Color.White,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                    )
                }
                Spacer(Modifier.width(14.dp))
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
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text(
                        text = "${index + 1} / ${queue.size}",
                        color = Color.White,
                        fontSize = 20.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(3.dp))
                    Text(
                        text = when {
                            statusText.isNotBlank() -> statusText
                            isPlaying -> "线路 ${streamIndex + 1} / ${channel.streams.size} · 直播中"
                            else -> "已暂停"
                        },
                        color = if (statusText.isNotBlank()) TvColors.Accent else Color(0xFFCBD5E1),
                        fontSize = 13.sp,
                    )
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
                    .padding(horizontal = 48.dp, vertical = 26.dp),
            ) {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
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
                        onClick = { onToggleFavorite(channel) },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.SkipPrevious,
                        label = "上一个",
                        enabled = queue.size > 1,
                        onClick = { zap(-1) },
                    )
                    PlayerActionButton(
                        icon = if (isPlaying) Icons.Rounded.Pause else Icons.Rounded.PlayArrow,
                        label = if (isPlaying) "暂停" else "播放",
                        focusRequester = playFocus,
                        onClick = {
                            if (exoPlayer.isPlaying) exoPlayer.pause() else exoPlayer.play()
                        },
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
                    Spacer(Modifier.weight(1f))
                    PlayerActionButton(
                        icon = Icons.Rounded.ArrowBack,
                        label = "返回列表",
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
                            streamIndex = 0
                            errorMessage = null
                            statusText = "正在连接…"
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
    }
}

private fun playerKeyHint(prefs: TvPrefs): String {
    val zap = if (prefs.zapWithUpDown) "上下键换台" else "上下键换台已关闭"
    val back = when (prefs.backKeyAction) {
        BackKeyAction.HideControls -> "返回键收起控制栏"
        BackKeyAction.BackToList -> "返回键返回列表"
    }
    val menu = when (prefs.menuKeyAction) {
        MenuKeyAction.Favorite -> "菜单键收藏"
        MenuKeyAction.Lines -> "菜单键选线"
        MenuKeyAction.Aspect -> "菜单键切换画面"
        MenuKeyAction.Off -> "菜单键无动作"
    }
    return "$zap · $back · $menu"
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
                text = "${channel.name} · 共 ${channel.streams.size} 条 · 响应为本次取流耗时",
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
                        "响应越快通常越容易起播 · 时间均为北京时间"
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
        result?.responseMs != null -> "响应 ${result.responseMs} ms"
        result?.error != null -> result.error
        probing -> "检测中…"
        else -> "未检测"
    }
    val responseColor = when {
        result?.responseMs != null -> TvColors.Success
        result?.error != null -> Color(0xFFFF8A80)
        else -> TvColors.TextSecondary
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
