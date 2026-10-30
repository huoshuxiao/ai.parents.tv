package com.sun.tv.parents.ui

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
import androidx.compose.foundation.layout.widthIn
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
import androidx.media3.common.C
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
import com.sun.tv.parents.data.FreezeWatch
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine
import com.sun.tv.parents.data.BackKeyAction
import com.sun.tv.parents.data.BeijingTime
import com.sun.tv.parents.data.ChannelNumbers
import com.sun.tv.parents.data.DirectionKeyAction
import com.sun.tv.parents.data.EpgProgram
import com.sun.tv.parents.data.EpgRepository
import com.sun.tv.parents.data.EpgSnapshot
import com.sun.tv.parents.data.FaultPolicy
import com.sun.tv.parents.data.LineRecovery
import com.sun.tv.parents.data.PosFreezeTracker
import com.sun.tv.parents.data.MenuKeyAction
import com.sun.tv.parents.data.StreamProbe
import com.sun.tv.parents.data.StreamUrl
import com.sun.tv.parents.data.TvChannel
import com.sun.tv.parents.data.TvPrefs
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
    // 本台里播放器**自报已经在播**的线路号（STATE_READY 与 onIsPlayingChanged(true) 两处记账）。
    // ⚠️它不等于「画出过画面」：10-26 真机现读有 6+7 格 `playing=true` 却整段没有一行 `firstFrame`
    // ——那种线路在这一份里是「播过」的，于是既换不了线也换不了台，屏幕上就挂死（点档 #123 乙）。
    // 真正「画出过画面」的那一份是下面 `framedLines`，换线保护改用它。
    val playedLines = remember(channel.id) { mutableSetOf<Int>() }
    // 本台里**真画出过第一帧**的线路号（只在 `onRenderedFirstFrame` 记账）。
    // 「已正常播放的线路绝不自动换线/换台」（用户 2026-10-21 指令）这条保护现在以它为准：
    // 见过画面的线继续只原地重连，没见过画面的线到点就接管——包括播放器自报在播、画面却始终空着那一档。
    val framedLines = remember(channel.id) { mutableSetOf<Int>() }
    // 本台里**顺畅播满 `LineRecovery.STEADY_MS`** 的线路号（10-28 新增）。
    // 「已播过的线路绝不自动换线」这条保护从此以它为准：见过一帧不算播过——
    // 现读 36.110.54.118 那条线每段都能吐一帧、缓冲却只有 400–900 ms，
    // 按旧的「见过一帧」口径会被判成「卡住了就原地重连」，于是这条线永远换不走。
    val steadyLines = remember(channel.id) { mutableSetOf<Int>() }
    // 这一趟已经失败过的线路号：末位线倒下时用它决定「回本台其它线」还是「换台」（10-28）。
    val triedLines = remember(channel.id) { mutableSetOf<Int>() }
    var errorMessage by remember(channel.id) { mutableStateOf<String?>(null) }
    var statusText by remember(channel.id) { mutableStateOf(connectingText(channel.name, streamIndex)) }
    var videoSize by remember(channel.id, streamIndex) { mutableStateOf<VideoSize?>(null) }
    // 本次进入播放器后已经试过、且所有线路都起不了画面的频道。自动换台要靠它决定什么时候停手。
    val unplayableChannels = remember { mutableSetOf<String>() }
    var isPlaying by remember { mutableStateOf(false) }
    var isBuffering by remember { mutableStateOf(true) }
    // 卡顿这条尺（用户 10-26「优化稳定性减少卡顿」）：这一条线真出过画面之后，播放被中断
    // （isPlaying 由 true 掉回 false、且这一刻没有报错）算一次卡顿，恢复时记下它压了多久。
    // 只记账不动作——要不要因此换线/重连仍由 FreezeWatch 与 LineRecovery 那两套规则决定。
    var renderedHere by remember(channel.id, streamIndex) { mutableStateOf(false) }
    var stallStartedAt by remember(channel.id, streamIndex) { mutableLongStateOf(0L) }
    val stallCount = remember(channel.id, streamIndex) { intArrayOf(0) }
    val stallMillis = remember(channel.id, streamIndex) { longArrayOf(0L) }
    // 画面冻住这条尺：连续 FreezeWatch.SILENCE_MS 没有任何新东西，就当作这台没在给新画面。
    // 动作只有「原地重连」一种，绝不换线、绝不换台（用户 09-29 立的规则：已经出过画面的线路只原地重连）。
    val freezeState = remember(channel.id, streamIndex) { FreezeWatch.State() }
    // #152 甲（用户 10-27 点档）：判据主要换成**解码帧计数**。PixelCopy 在这台电视上 429 次全是
    // sample blank，像素那条尺量不到任何东西；`VideoFrameMetadataListener` 每渲染一帧回调一次，
    // 帧号满 30 秒不动＝解码/渲染侧真的没在出帧。只在这条线**真出过第一帧**之后才启用（renderedHere）：
    // 纯音频线（MP2 那几条）一个视频帧都不会有，否则每 30 秒就被误判成冻住、白白重连。
    val frameFreezeState = remember(channel.id, streamIndex) { FreezeWatch.State() }
    val frameSeq = remember { java.util.concurrent.atomic.AtomicLong(0) }
    val framePtsUs = remember { java.util.concurrent.atomic.AtomicLong(-1L) }
    val playerViewRef = remember { mutableStateOf<PlayerView?>(null) }
    LaunchedEffect(channel.id, streamIndex, retryNonce, isPlaying) {
        if (!isPlaying) return@LaunchedEffect
        val buf = IntArray(256 * 144)
        var logged = false
        while (true) {
            delay(FreezeWatch.SAMPLE_MS)
            val now = SystemClock.elapsedRealtime()
            val seq = frameSeq.get()
            var frozen = renderedHere && FreezeWatch.observe(frameFreezeState, seq, now)
            // 像素那条尺留着：这台电视上它量不到东西，但换一台设备它就是唯一能抓住「解码在出帧、
            // 画面却一帧不变」的读数——帧计数抓不到内容级冻帧（同一帧被反复编码送出去时帧号照样涨）。
            val surface = playerViewRef.value?.videoSurfaceView as? SurfaceView
            val hash = if (surface != null) sampleSurfaceHash(surface, buf) else null
            if (hash != null) frozen = FreezeWatch.observe(freezeState, hash, now) || frozen
            if (!logged && (renderedHere || hash != null)) {
                logged = true
                android.util.Log.i(
                    "FreezeWatch",
                    "sampling channel=${channel.id} line=${streamIndex + 1} 帧累计=$seq hash=$hash",
                )
            }
            if (!frozen) continue
            if (reconnectsOnLine >= SAME_LINE_RECONNECTS) continue
            reconnectsOnLine += 1
            android.util.Log.i(
                "FreezeWatch",
                "frozen ${FreezeWatch.SILENCE_MS}ms → 原地重连 channel=${channel.id}" +
                    " line=${streamIndex + 1} 帧累计=$seq hash=$hash",
            )
            statusText = "线路 ${streamIndex + 1} 画面停了，正在原地重连（第 $reconnectsOnLine 次）…"
            errorMessage = null
            isBuffering = true
            retryNonce += 1
        }
    }
    // 每 10 秒打一行累计读数：真机上按台停一分钟，日志就能算出「这台在这条线上卡了几次、共多久」。
    LaunchedEffect(channel.id, streamIndex) {
        while (true) {
            delay(10_000L)
            android.util.Log.i(
                "StallWatch",
                "watch channel=${channel.id} line=${streamIndex + 1} playing=$isPlaying" +
                    " 卡顿${stallCount[0]}次/${stallMillis[0]}ms t=${SystemClock.elapsedRealtime()}",
            )
        }
    }
    var controlsVisible by remember { mutableStateOf(true) }
    // 长按上下键的节流时间戳（见 onPreviewKeyEvent 里那段说明）
    var lastZapAt by remember { mutableStateOf(0L) }
    var zapNotice by remember { mutableStateOf(false) }
    var aspectIndex by remember { mutableIntStateOf(0) }
    var lineSeconds by remember { mutableIntStateOf(prefs.lineSwitchSeconds) }
    var linePickerVisible by remember { mutableStateOf(false) }
    var channelPickerVisible by remember { mutableStateOf(false) }
    // 遥控器数字键选台：数字先进缓冲区，停手 2.5 秒后当成台号跳过去。
    var digitEntry by remember { mutableStateOf("") }
    var probeNonce by remember { mutableIntStateOf(0) }
    // 面板上那颗「重新检测」按下去才重算；单纯打开面板不算（用户 10-26「算一次就好」）。
    var reprobeRequested by remember { mutableStateOf(false) }
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
    // 用户 10-30「现状看不到节目单」：10-25 那次「左上角整块去掉」把「正在播什么」一起删了，
    // 之后一直没补回来。这里按**正在播的这台 + 当前分钟**现算，换台就换名、不沿用旧节目。
    val onAirProgram = remember(epgSnapshot, channel.name, minuteTick) {
        epgSnapshot.onAir(channel.name)
    }

    val rootFocus = remember { FocusRequester() }
    val barFocus = remember { FocusRequester() }
    val retryFocus = remember { FocusRequester() }
    // 底栏 5 颗按钮各自的焦点入口。遥控器左右键以前交给 Compose 自己找下一个焦点：
    // 从最左边那颗往右按会落到铺满全屏的根布局（实测 uiautomator 里 focused 节点变成
    // [0,0][1920,1080]），之后左右键既不动焦点也不换线，用户看到的就是「按了没反应」。
    // 改成自己按下标走、两头回环，不再让焦点跑到根布局上。
    val barButtons = remember { listOf(barFocus, FocusRequester(), FocusRequester(), FocusRequester(), FocusRequester()) }
    var barIndex by remember { mutableStateOf(0) }
    var barHasFocus by remember { mutableStateOf(false) }
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
                // 10-28 试过 min30/max90/起播3/恢复4：东方卫视线 3 上 120 秒仍 5 次停顿、86% 时间在 BUFFERING，
                // ahead 从没攒到 30 秒 ⇒ **缓冲深度不是钥匙**（那台服务端吞吐 < 码率），已改回。
                /* minBufferMs = */ 20_000,
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
            .setRenderersFactory(com.sun.tv.parents.media.ParentsRenderersFactory(context))
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
    // 10-28 用户现读「只有声音、没有视频、没有倒计时」：带宽不够时音频轨（~0.8 Mbps）先到、
    // 视频轨（~7 Mbps）到不了，屏幕上就是「有声黑屏」，老人以为坏了。第一帧上屏之前一律不出声，
    // 配合下面那条「没画面就显示倒计时」，父母看到的是「正在连接 · 还有 N 秒」而不是录音机。
    LaunchedEffect(renderedHere, isPlaying) {
        // 纯音频流（没有视频轨）不能永远静音：轨道表里只要「有音频、没视频」就放开，
        // 而轨道表还没填起来时（起播头一两秒）保持静音——那一小段正是「有声黑屏」的来源。
        val groups = exoPlayer.currentTracks.groups
        val hasAudio = groups.any { it.type == androidx.media3.common.C.TRACK_TYPE_AUDIO }
        val hasVideo = groups.any { it.type == androidx.media3.common.C.TRACK_TYPE_VIDEO }
        exoPlayer.volume = PlayerUi.volumeFor(renderedHere, hasAudio, hasVideo)
    }
    // MP2「有声但断」定位（用户 10-28「解决 MP2 有声但断」）：把两件事分开——
    // **加载器没数据**（ahead 接近 0、isLoading=true）vs **数据在缓冲里但渲染器不吃**
    // （ahead 有十几秒、isLoading=false，却 playing=false）。只打日志、不改行为。
    LaunchedEffect(channel.id, streamIndex) {
        var lastKey = ""
        while (true) {
            delay(2_000L)
            val state = exoPlayer.playbackState
            val ahead = exoPlayer.bufferedPosition - exoPlayer.currentPosition
            val key = "$state/$isPlaying/${ahead / 5_000}"
            if (key != lastKey || !isPlaying) {
                lastKey = key
                android.util.Log.i(
                    "LoadWatch",
                    "channel=${channel.id} line=${streamIndex + 1} state=$state playing=$isPlaying" +
                        " loading=${exoPlayer.isLoading} aheadMs=$ahead" +
                        " pos=${exoPlayer.currentPosition} buffered=${exoPlayer.bufferedPosition}" +
                        " t=${SystemClock.elapsedRealtime()}",
                )
            }
        }
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

    // 帧计数这条尺的接线（#152 甲）：`VideoFrameMetadataListener` 每渲染一帧回调一次，
    // 上面那段冻帧判据拿这个计数当读数。这里另外每 10 秒打一行累计：
    // 「帧累计在涨、画面照旧」＝内容级冻帧（帧计数抓不到，得靠像素那条尺或人工看）；
    // 「帧累计不动」＝解码/渲染侧真停了，30 秒后 FreezeWatch 会原地重连一次。
    LaunchedEffect(exoPlayer) {
        exoPlayer.setVideoFrameMetadataListener { presentationTimeUs, _, _, _ ->
            framePtsUs.set(presentationTimeUs)
            frameSeq.incrementAndGet()
        }
    }
    LaunchedEffect(channel.id, streamIndex, retryNonce) {
        var lastSeq = -1L
        while (true) {
            delay(10_000L)
            val seq = frameSeq.get()
            android.util.Log.i(
                "FrameWatch",
                "channel=${channel.id} line=${streamIndex + 1} 帧累计=$seq" +
                    " 本段新增=${if (lastSeq < 0) "-" else (seq - lastSeq).toString()}" +
                    " ptsUs=${framePtsUs.get()} playing=$isPlaying t=${SystemClock.elapsedRealtime()}",
            )
            lastSeq = seq
        }
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
        // 真机回测要把"遥控器排队"与"这条线起播"分成两个数：应用接受这次选线的时刻
        // 只有这里知道（`QualityWatch` 与 `firstFrame` 都在出帧那一瞬才打，两者只差几毫秒，
        // 拿它们当起点会把 150 秒的按键排队算进"起播耗时"）。
        android.util.Log.i(
            "LineProbe",
            "select channel=${channel.id} line=${lineIndex + 1} from=${streamIndex + 1}" +
                " t=${SystemClock.elapsedRealtime()}",
        )
        prefs.setLine(channel.id, url)
        linePickerVisible = false
        reconnectsOnLine = 0
        // 用户手动点的这条线：把「这一趟试过哪些线」清掉，否则 10-28 新加的末位兜圈
        // 会把它当成已试过而跳过，等于用户点了却回不去。
        triedLines.clear()
        if (streamIndex == lineIndex) {
            retryNonce += 1
        } else {
            streamIndex = lineIndex
        }
        errorMessage = null
        statusText = ""
        isBuffering = true
        // 用户 10-29 00:5x：选完线路把控制栏收起来（原来这里会把它重新亮出来）。
        controlsVisible = false
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
            // 用户 10-30「节目单看不到 / 启动与午夜各自动更新一次」：跨午夜（或缓存过期）时
            // 这里补一次取数——AppRoot 那次 force 刷新只写盘，播放器内存里那份不会自己变；
            // snapshot() 内部有 6 小时节流，缓存新鲜就直接返回，不会重复下那 12 MB。
            if (prefs.epgEnabled &&
                (epgSnapshot.isEmpty || !epgSnapshot.covers(System.currentTimeMillis()))
            ) {
                epgSnapshot = runCatching { EpgRepository(context).snapshot() }.getOrDefault(epgSnapshot)
            }
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
        // 用户 10-26 指令「线路清晰度计算一次就好，不需要每次打开线路时都重算」：
        // 算过的线直接把开机缓存里的读数亮出来，只有没算过的（或按了「重新检测」）才去探。
        // 少探一路就少一路和正在播的那条线抢首片带宽——那正是屏幕上「缓冲中」的一个来源。
        val force = reprobeRequested
        reprobeRequested = false
        lineProbes.clear()
        channel.streams.forEachIndexed { lineIndex, url ->
            StreamProbe.cached(url)?.let { lineProbes[lineIndex] = it }
        }
        val todo = StreamProbe.linesToProbe(channel.streams, force)
        // 这一行是给「算一次就好」留证据用的：同一个台第二次打开面板应当 `要探0条`。
        // 不写日志就只能靠肉眼看面板上那句「正在检测 x/y」，验收时说不清是哪一次打开。
        android.util.Log.i(
            "LineProbe",
            "panel channel=${channel.id} 共${channel.streams.size}条 要探${todo.size}条" +
                " 缓存命中${channel.streams.size - todo.size}条 force=$force" +
                " t=${SystemClock.elapsedRealtime()}",
        )
        if (todo.isEmpty()) {
            probingLines = false
            return@LaunchedEffect
        }
        probingLines = true
        try {
            coroutineScope {
                // 并发上限 2：一个台几条线就并发几路，每路最长十几秒，全撒出去就是
                // 自己把正在播的那条首片的带宽抢干——屏幕上就是「缓冲中」。
                val probeGate = Semaphore(PROBE_CONCURRENCY)
                todo.forEach { (lineIndex, url) ->
                    launch(Dispatchers.IO) {
                        probeGate.withPermit {
                            val result = StreamProbe.probe(url)
                            withContext(Dispatchers.Main) {
                                StreamProbe.rememberResult(url, result)
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
            // 「回来几条」少于「排了几条」＝这一批是被关面板掐掉的，那几条没进缓存，
            // 下一次打开还会再探一遍。验收「算一次就好」时这一行决定第二次的「要探N条」该怎么读。
            val cachedBack = todo.count { StreamProbe.cached(it.second) != null }
            android.util.Log.i(
                "LineProbe",
                "panelDone channel=${channel.id} 排${todo.size}条 回来${cachedBack}条" +
                    " t=${SystemClock.elapsedRealtime()}",
            )
        }
    }

    fun tryNextLine(message: String?) {
        // 同一次故障可能同时从 onPlayerError 和看门狗两条路进来，1.5 秒内只认第一次，
        // 否则会一口气跳过两条本来能用的线路。
        val now = System.currentTimeMillis()
        if (now - lastRecoveryAt < RECOVERY_DEBOUNCE_MS) return
        lastRecoveryAt = now
        // 已经画出过画面的线路只在原地重连，绝不自动换线、绝不自动换台（用户 2026-10-21 指令）；
        // 没画出过画面的线路按设置里的等待时间到点就换下一条（用户 2026-10-22 要求默认 10 秒），
        // 所以这一臂传的重连次数是 0——不然「10 秒换线」实际要等 30 秒。
        // 10-26 点档 #123 乙：这里的依据从 `playedLines`（自报在播就算）换成 `framedLines`
        // （真画出第一帧才算）。差的正是「READY／isPlaying=true 却一帧都没有」那一族——
        // 旧口径下它们被记成「播过」，于是这张真值表第一臂永远短路成原地重连，换不了也走不了。
        // 10-28：这一臂的「播过」从「见过一帧」（framedLines）收紧成「顺畅播满 STEADY_MS」
        // （steadyLines）。现读 36.110.54.118 那条线：每段都能吐一帧、缓冲长期只有 400–900 ms，
        // 按旧口径会被判成 ReconnectSameLine 原地重连到永远，屏幕上就是「一直缓冲、一直卡」。
        val hasPicture = streamIndex in steadyLines
        val action = LineRecovery.action(
            lineHasPlayed = hasPicture,
            reconnectsOnLine = reconnectsOnLine,
            sameLineReconnects = if (hasPicture) SAME_LINE_RECONNECTS else 0,
            lineIndex = streamIndex,
            lastLineIndex = channel.streams.lastIndex,
        )
        // 10-26 真机现读：有 3 格在"这条线已经出过第一帧"之后 2–10 秒被应用自己换到了下一条
        // （台账里没有 `LineProbe: select` 行＝不是选线面板按下去的），而下面这条判据的
        // 480 格夹具证明 `lineHasPlayed=true` 永远不会走 NextLine ⇒ 只可能是这一刻 `played=false`。
        // 这一行是把"这一刻到底看到了什么"打出来：改了判据没用的话，先从这里回答。
        // 两个集合都打出来，就是为了分清「没记账」与「记了但没画」：
        // playedLines 里有、framedLines 里没有＝播放器自报在播却没出画（#123 那一族）。
        android.util.Log.i(
            "LineRecovery",
            "channel=${channel.id} line=${streamIndex + 1} hasPicture=$hasPicture" +
                " 顺畅播满=$steadyLines 已出画=$framedLines 自报在播=$playedLines" +
                " 重连=$reconnectsOnLine 选择=$action 消息=${message ?: "-"}" +
                " t=${SystemClock.elapsedRealtime()}",
        )
        when (action) {
            LineRecovery.Action.ReconnectSameLine -> {
                reconnectsOnLine += 1
                statusText = if (hasPicture) {
                    "线路 ${streamIndex + 1} 卡住了，正在原地重连（第 $reconnectsOnLine 次）…"
                } else {
                    "线路 ${streamIndex + 1} 卡住了，正在重连（第 $reconnectsOnLine 次）…"
                }
                errorMessage = null
                isBuffering = true
                retryNonce += 1
            }

            LineRecovery.Action.NextLine -> {
                triedLines.add(streamIndex)
                reconnectsOnLine = 0
                statusText = "线路 ${streamIndex + 1} 不可用，正在切换备用线路…"
                streamIndex += 1
            }

            LineRecovery.Action.NextChannel -> {
                triedLines.add(streamIndex)
                // 末位线不通时先回本台还没试过的线（10-28：东方卫视 线4 卡死会一路换台，
                // 而它的线1 一直是好的）。全部试过才认「这台没戏」。
                val wrap = LineRecovery.nextUntriedLine(streamIndex, channel.streams.size, triedLines)
                if (wrap != null) {
                    reconnectsOnLine = 0
                    statusText = "线路 ${streamIndex + 1} 不可用，正在切回线路 ${wrap + 1}…"
                    streamIndex = wrap
                } else if (!zapToNextChannel(message ?: "线路全部不可用")) {
                    statusText = ""
                    isBuffering = false
                    controlsVisible = true
                }
            }
        }
    }

    // 「这条线拿到的是直播还是一枚定长片子」——只打读数、不改行为（判据在 `LineRecovery.liveStream`）。
    // 起播后 4 秒读一次：那时首帧多半已经落定、清单也解析完了，`duration` 才不是"还没拿到"。
    // 为什么不顺手把它接进换线判据：那一改就会让"放出画面"的线被跳过，与 2026-10-21
    // 「已正常播放过的线路只在原地重连」那条指令挨着，要单独点档。
    LaunchedEffect(channel.id, streamIndex, retryNonce) {
        delay(4_000L)
        val durationMs = exoPlayer.duration
        android.util.Log.i(
            "LiveWatch",
            "channel=${channel.id} line=${streamIndex + 1}" +
                " live=${LineRecovery.liveStream(exoPlayer.isCurrentMediaItemLive, durationMs)}" +
                " duration=$durationMs" +
                " t=${SystemClock.elapsedRealtime()}",
        )
        // 「有画没声」那一族的读数（2026-10-27 用户现听指认）。只打、不改行为：
        // 音频轨有没有、是什么编码、播放器给的音量多少、在不在播——四样分开念，
        // 才能分清是「源没带音轨」「盒子解不了这个编码」还是「音量/焦点被谁压住了」。
        fun audioRead(): Triple<String, String?, Int> {
            val groups = exoPlayer.currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }
            val group = groups.firstOrNull { it.isSelected } ?: groups.firstOrNull()
            // `TrackGroup` 这一版没有 `trackFormat` 属性，只能按索引取（15:04 JVM 就是这么报的
            // Unresolved reference 'trackFormat'）；组里至少一条，取第 0 条不必判越界。
            return Triple(LineRecovery.audioClass(group?.getTrackFormat(0)?.sampleMimeType, groups.size,
                exoPlayer.isPlaying), group?.getTrackFormat(0)?.sampleMimeType, groups.size)
        }
        fun logAudio(atMs: Long, read: Triple<String, String?, Int>) = android.util.Log.i(
            "AudioWatch",
            "channel=${channel.id} line=${streamIndex + 1}" +
                " audio=${read.first}" +
                " mime=${read.second} tracks=${read.third}" +
                " volume=${exoPlayer.volume} playing=${exoPlayer.isPlaying}" +
                " t=$atMs",
        )
        // 4 秒这一拍常常还没填 tracks ⇒ 旧口径会把它打成「无音轨」（真机 10-27 那 3 行就是说谎的样本）。
        // 现在读到「未证」就补两拍（10 秒、20 秒）；填上了就只打一行，不刷日志。
        val firstAudio = audioRead()
        logAudio(4_000L, firstAudio)
        if (firstAudio.first == "未证") {
            delay(6_000L)
            val secondAudio = audioRead()
            logAudio(10_000L, secondAudio)
            if (secondAudio.first == "未证") {
                delay(10_000L)
                logAudio(20_000L, audioRead())
            }
        }
    }

    LaunchedEffect(channel.id, streamIndex, retryNonce) {
        var playingMs = 0L
        // 判定全在 `LineRecovery.SteadyTracker` 里（纯类，单测直接拿真机读数回放）。
        val tracker = LineRecovery.SteadyTracker(prefs.lineSwitchSeconds * 1_000L)
        // 用户 10-30「CCTV 4K 线路1 倒计时 1 秒后画面不动了没声音，没自动换线」：
        // 冻住那一下 playWhenReady=true 且 isPlaying 仍为 true（音频时钟还在动），旧判据整条不响；
        // 像素尺 FreezeWatch 在盒子/电视上拿不到帧（10-26 现读 429 次 sample blank），也兜不住。
        // 这里补一条与画面无关的活口：**播放位置不再前进**就算停。
        val posFreeze = PosFreezeTracker(prefs.lineSwitchSeconds * 1_000L)
        while (true) {
            delay(1_000)
            val pos = exoPlayer.currentPosition
            val frozenMs = posFreeze.sample(pos, exoPlayer.isPlaying)
            if (posFreeze.frozen) {
                // 让这件事在日志里留痕：真机上再偶发一次时，`StallWatch: frozenPos` 这一行
                // 能直接证明「位置不前进」这条判据看见了它（而不是又靠 FreezeWatch 的空白帧）。
                android.util.Log.i(
                    "StallWatch",
                    "frozenPos channel=${channel.id} line=${streamIndex + 1}" +
                        " frozen=${frozenMs}ms pos=$pos playing=${exoPlayer.isPlaying}" +
                        " t=${SystemClock.elapsedRealtime()}",
                )
                tryNextLine("画面停住不动了")
                break
            }
            val waiting = exoPlayer.playWhenReady && !exoPlayer.isPlaying
            val ahead = exoPlayer.bufferedPosition - exoPlayer.currentPosition
            if (waiting) playingMs = 0 else if (exoPlayer.isPlaying) playingMs += 1_000
            if (playingMs >= RECONNECT_AFTER_STABLE_MS) reconnectsOnLine = 0
            // 等多久算这条线失败：一律按设置里配的「线路时长」这一档算（默认 10 秒，
            // 档位见 LINE_SECONDS_OPTIONS），起播阶段和已经出过画面之后卡住都用同一档。
            // 这里以前对「本台一条线都还没出过画面」的首条线另压了一档 4 秒（上一轮为了躲
            // 「正在连接十几秒」加的），于是屏幕上等的时长跟设置里配的数对不上——
            // 用户 10-25 指令第 4 条要的就是「按设置里配的时长来等画面」，短档连同那常量一起删掉。
            // 顺畅累计到 STEADY_MS 才承认这条线「真播过」，之后才受「绝不自动换线」保护；
            // 没顺畅过的线整档到点就接管（10-28：36.110 那条每段吐一帧、缓冲只有 400–892 ms）。
            val overWindow = tracker.sample(
                playing = exoPlayer.isPlaying,
                waiting = waiting,
                aheadMs = ahead,
                rendered = renderedHere,
                attemptMs = SystemClock.elapsedRealtime() - windowStartedAt,
            )
            if (tracker.steady) steadyLines.add(streamIndex)
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
    // 10-28 用户现读「只有声音、没有视频、也没有倒计时」：音频轨只有 ~0.2 Mbps，带宽不够时音频照走、
    // 视频一帧都没有，而旧判据 `isBuffering && !isPlaying` 这一刻是假的（音频在播）⇒ 倒计时压根不显示，
    // 屏幕上只剩下声音，父母不知道还在等画面。现在多一臂：**这条线还没画出过第一帧**也算等待中。
    val waitingForPicture =
        errorMessage == null && PlayerUi.waitingForPicture(renderedHere, isBuffering, isPlaying)
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
        // 用户 10-29 00:5x「『正在连接xxx频道/正在尝试线路』去掉，保留线路数量和倒计时」：
        // 自动换到备用线时这里原来写的是「正在尝试线路 k…」，等于把点名要去掉的那句又放回来了。
        statusText = ""
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
                renderedHere = true
                // 这才是「这条线在本台真画出过画面」的唯一记账点。上面 `playedLines` 那两处
                // （READY／自报在播）都不算——见 :166 那段。
                framedLines.add(lineBeingPlayed)
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
                    val started = stallStartedAt
                    if (started != 0L) {
                        val dur = SystemClock.elapsedRealtime() - started
                        stallStartedAt = 0L
                        stallCount[0] += 1
                        stallMillis[0] += dur
                        android.util.Log.i(
                            "StallWatch",
                            "recover channel=${channel.id} line=${lineBeingPlayed + 1} stall=${dur}ms" +
                                " 卡顿${stallCount[0]}次/${stallMillis[0]}ms t=${SystemClock.elapsedRealtime()}",
                        )
                    }
                    isBuffering = false
                    statusText = ""
                    shownName = channel.name   // 万一某些解码器不回调首帧，这一臂兜底
                    shownMeta = captionMeta(channelNumber, categoryNameOf(channel.category), channel.subgroup)
                    playedLines.add(lineBeingPlayed)
                    // 只有顺畅播满那一段之后才「记住这条线」。10-28：一条只会吐半秒缓冲的线
                    // 也会把 isPlaying 翻成 true，照旧记账等于下次开机直接落到卡的那条上。
                    if (lineBeingPlayed in steadyLines) prefs.setLine(channel.id, url)
                } else if (renderedHere && errorMessage == null && stallStartedAt == 0L) {
                    stallStartedAt = SystemClock.elapsedRealtime()
                    android.util.Log.i(
                        "StallWatch",
                        "stall channel=${channel.id} line=${lineBeingPlayed + 1} t=$stallStartedAt",
                    )
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
                    // 同理：等满窗口期间也只留倒计时，不再写「正在尝试线路 k」。
                    statusText = ""
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
        // 在底栏上左右移动也算「用户还在操作」，3 秒倒计时要重新开始；
        // 否则焦点刚挪到一半控制栏就缩回去了，看起来又像「按了没反应」。
        barIndex,
    ) {
        val blocked = linePickerVisible || channelPickerVisible || guideVisible ||
            errorMessage != null || !isPlaying
        if (controlsVisible && !blocked) {
            delay(CONTROLS_AUTO_HIDE_MS)
            controlsVisible = false
        }
    }

    // 数字键停手 2.5 秒后把这串数字当**全频道台号**用：第 N 台，不管它落在哪一组。
    fun jumpToNumber(number: Int) {
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

    LaunchedEffect(digitEntry) {
        val typed = digitEntry
        if (typed.isEmpty()) return@LaunchedEffect
        delay(DIGIT_ENTRY_TIMEOUT_MS)
        digitEntry = ""
        typed.toIntOrNull()?.let { jumpToNumber(it) }
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
            controlsVisible -> {
                // 选台/选线面板关闭时，焦点会被恢复到面板打开前的位置（常常是根布局）。
                // 一次 requestFocus 可能正好撞在这次恢复上，所以重试到「底栏真的拿到焦点」为止。
                repeat(10) {
                    runCatching { barButtons[0].requestFocus() }
                    delay(120)
                    if (barHasFocus) return@LaunchedEffect
                }
            }
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
            // 焦点落到这层铺满全屏的根布局时，说明底栏此刻「没有焦点」——把焦点请回底栏第一颗。
            // 不回请的话，左右键在 onKeyEvent 里因为 controlsVisible=true 直接放行给焦点系统，
            // 而根布局四周没有别的可聚焦节点，结果就是按什么都没反应。
            .onFocusChanged { state ->
                if (state.isFocused && controlsVisible && !barHasFocus &&
                    !linePickerVisible && !channelPickerVisible && !guideVisible &&
                    errorMessage == null
                ) {
                    runCatching { barButtons[0].requestFocus() }
                }
            }
            // 遥控器操作优先级最高（用户 10-23 19:1x「不受弹出影响」）：
            // 换台键走在预览阶段——控制栏弹出、错误提示弹着、焦点停在按钮上，都不挡上下换台；
            // 先把这些弹层收起来再换台。选台/选线/节目单是独立窗口，不受这一层影响。
            .onPreviewKeyEvent { event ->
                if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                val barShown = controlsVisible && errorMessage == null &&
                    !linePickerVisible && !channelPickerVisible && !guideVisible
                if (barShown) {
                    when (event.key) {
                        Key.DirectionLeft, Key.DirectionRight -> {
                            if (!barHasFocus) {
                                runCatching { barButtons[0].requestFocus() }
                            } else {
                                val step = if (event.key == Key.DirectionRight) 1 else -1
                                val next = ((barIndex + step) % barButtons.size + barButtons.size) % barButtons.size
                                runCatching { barButtons[next].requestFocus() }
                            }
                            return@onPreviewKeyEvent true
                        }

                        Key.DirectionUp, Key.DirectionDown -> {
                            // 底栏没焦点时第一下先把焦点请回底栏，而不是直接换台——
                            // 否则用户按了键只见频道跳走、控制栏依旧没有焦点框。
                            if (!barHasFocus) {
                                runCatching { barButtons[0].requestFocus() }
                                return@onPreviewKeyEvent true
                            }
                        }

                        else -> {}
                    }
                }
                // 用户 10-29 00:5x「按住上下键选台不流畅」：长按遥控器会以每秒十几次的节奏
                // 发重复 KeyDown，每一下都 jumpTo 会把播放器 stop/clear/prepare 一遍、还写 3 个 pref，
                // 换台就一顿一顿。这里做 250ms 节流：按住时最多每秒 4 台，中间的重复键直接吞掉。
                val isZapKey = event.key == Key.DirectionUp || event.key == Key.DirectionDown ||
                    event.key == Key.ChannelUp || event.key == Key.ChannelDown
                if (isZapKey) {
                    val now = SystemClock.elapsedRealtime()
                    if (now - lastZapAt < 250L) return@onPreviewKeyEvent true
                    lastZapAt = now
                }
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
                        } else if (!barHasFocus) {
                            // 底栏在、但没有焦点时，OK 先负责把焦点送回底栏；否则这一下
                            // 交给一个不可点击的根布局吃掉，用户看到的就是「OK 也没反应」。
                            runCatching { barButtons[0].requestFocus() }
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
                    setShutterBackgroundColor(0xFF070B14.toInt())
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
            // 用户 10-25「缓冲中/正在连接 xxx 改在画面右上方」：那句提示跟着右上角这一列走，
            // 等待画面期间即使控制栏已经自动收起也要露着。
            visible = controlsVisible || zapNotice || waitingForPicture,
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
                Spacer(Modifier.weight(1f))
                Column(horizontalAlignment = Alignment.End) {
                    // 用户 10-29 00:5x「画面左上角的内容全部去掉，台号 频道名称改放画面右侧线路旁，
                    // 切换/自动换台时名称与线路同步更新」：这一行直接从正在播的 channel 取，
                    // 不再用首帧时记下的 shownName，所以换台途中不会出现「新线路 + 旧台名」。
                    Text(
                        text = listOfNotNull(channelNumber?.toString(), channel.name).joinToString("  "),
                        color = Color.White,
                        fontSize = 26.sp,
                        fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.End,
                    )
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = "线路 ${streamIndex + 1} / ${channel.streams.size}",
                        color = Color.White,
                        fontSize = 20.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    if (onAirProgram != null) {
                        Spacer(Modifier.height(2.dp))
                        Text(
                            text = "正在播  ${onAirProgram.title}",
                            color = Color(0xFFCBD5E1),
                            fontSize = 17.sp,
                            fontWeight = FontWeight.Normal,
                            textAlign = TextAlign.End,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.widthIn(max = 560.dp),
                        )
                    }
                    // 播通时这一行不再写字样（用户 10-23：去掉画面上的「直播」文字），暂停提示照旧。
                    // 还没出画面的等待期：那句「正在连接 / 还有 N 秒」按用户 10-25 指令挪到
                    // 右上角这一列（原来那块在画面正中），同屏只留这一句。
                    val playStatus = when {
                        waitingForPicture ->
                            countdownLine(statusText, countdownSeconds.coerceAtLeast(1))
                        statusText.isNotBlank() -> statusText
                        isPlaying -> ""
                        else -> "已暂停"
                    }
                    if (playStatus.isNotBlank()) {
                        Spacer(Modifier.height(3.dp))
                        Text(
                            text = playStatus,
                            color = if (statusText.isNotBlank()) TvColors.Accent else Color(0xFFCBD5E1),
                            fontSize = if (waitingForPicture) 17.sp else 13.sp,
                            fontWeight = if (waitingForPicture) FontWeight.SemiBold else FontWeight.Normal,
                            textAlign = TextAlign.End,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.widthIn(max = 520.dp),
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

        // 用户 10-25「缓冲中/正在连接 xxx 改在画面右上方」：画面正中不再叠提示，
        // 那句话已经在右上角「线路 k / N」下面那一行显示（见上面的 playStatus）。

        // 数字键选台：把正在输入的那几位数显示在右下角，停手 2.5 秒后按台号跳过去。
        if (digitEntry.isNotEmpty()) {
            // 用户 10-29 00:5x「按数字键时，数字居中，大字」：原来在右下角 46sp，
            // 现在整屏居中、140sp，父母隔两米也看得清。
            Text(
                text = digitEntry,
                color = Color.White,
                fontSize = 140.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.align(Alignment.Center),
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
                    modifier = Modifier.onFocusChanged { barHasFocus = it.hasFocus },
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    PlayerActionButton(
                        icon = Icons.Rounded.List,
                        label = "选线",
                        focusRequester = barButtons[0],
                        onFocusChanged = { if (it) barIndex = 0 },
                        onClick = { linePickerVisible = true },
                    )
                    PlayerActionButton(
                        icon = Icons.Rounded.LiveTv,
                        label = "选台",
                        enabled = queue.size > 1,
                        focusRequester = barButtons[1],
                        onFocusChanged = { if (it) barIndex = 1 },
                        onClick = { channelPickerVisible = true },
                    )
                    // 用户 10-25 指令 1：这一排去掉「上一个／下一个／铺满／收藏」四颗
                    //（换台靠上下键与数字键，画面比例留给设置里的菜单键，收藏在首页做），
                    // 并把「节目单」与「时长」两颗的位置对调。
                    PlayerActionButton(
                        icon = Icons.Rounded.Timer,
                        label = "时长 ${lineSeconds}秒",
                        focusRequester = barButtons[2],
                        onFocusChanged = { if (it) barIndex = 2 },
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
                        focusRequester = barButtons[3],
                        onFocusChanged = { if (it) barIndex = 3 },
                        onClick = { guideVisible = true },
                    )
                    Spacer(Modifier.weight(1f))
                    PlayerActionButton(
                        icon = Icons.Rounded.ArrowBack,
                        label = "返回",
                        focusRequester = barButtons[4],
                        onFocusChanged = { if (it) barIndex = 4 },
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
                onRefresh = {
                    reprobeRequested = true
                    probeNonce += 1
                },
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
                onNumber = { number ->
                    channelPickerVisible = false
                    jumpToNumber(number)
                    // 用户 10-29 00:5x「选择完频道或线路后，弹出的各种对话框和控制栏自动隐藏」
                    controlsVisible = false
                },
                onSelect = { position ->
                    channelPickerVisible = false
                    jumpTo(position)
                    controlsVisible = false
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
                val n = rw * rh
                // 全 0 缓冲是这台电视没把视频层交给 PixelCopy（10-26 小米电视实测），
                // 那是「看不见」不是「画面冻住」——不喂给这条尺，免得每 30 秒白重连一次。
                if (FreezeWatch.isBlank(buf, n)) {
                    android.util.Log.i("FreezeWatch", "sample blank → 这台电视没把视频层交给 PixelCopy，这一尺不判")
                    null
                } else {
                    FreezeWatch.hash(buf.copyOf(n))
                }
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

private fun connectingText(channelName: String, lineIndex: Int): String = ""
// 用户 10-29 00:5x「『正在连接xxx频道/正在尝试线路』去掉，保留线路数量和倒计时」：
// 这两句从画面上撤掉了（函数留着是为了不动那五处调用点的结构），画面上只剩
// 右上角「线路 k / N」＋「还有 N 秒」。channelName/lineIndex 参数已无用，保留签名兼容调用点。

/**
 * 还没出画面时画面中间那一句（用户 10-25 指令 3：「将 loading 改为设置的时长的倒计时」）。
 *
 * 前半截直接沿用状态栏那句话的口径（[connectingText] 给的就是「正在连接 某台…」/
 * 「正在尝试线路 k…」，原地重连、切备用线那些字样也一并留着），末尾挂上还剩几秒：
 * 同屏不再出现两句重复提示，右上角这一段等待期只留「线路 k / N」。
 * 全中文，不留 loading 这种英文字样。
 */
private fun countdownLine(status: String, secondsLeft: Int): String {
    val head = status.removeSuffix("…").trim()
    // 用户 10-29 00:5x：连接类的字眼全部撤掉，等待期只留倒计时。
    return if (head.isBlank()) "还有 $secondsLeft 秒" else "$head · 还有 $secondsLeft 秒"
}

/**
 * 遥控器操作优先级最高（用户 10-24 18:3x「不受状态栏等遮罩影响」）：
 * 选台/选线/节目单是**独立窗口**，根布局那层 preview 收不到它们的按键，
 * 所以每个弹层自己接一遍 CH± 与媒体上一台/下一台——弹层开着也能直接换台，
 * 换台那一下由 [PlayerScreen] 的 jumpTo 统一把弹层收掉。
 * 上下键不在这里抢：弹层里它们要用来走列表（那是用户明确要的焦点导航）。
 */
private fun Modifier.zapWhileOpen(
    onZap: (Int) -> Unit,
    onDigit: (Int) -> Unit = {},
): Modifier = onPreviewKeyEvent { event ->
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

        else -> {
            // 面板上那句「遥控器按数字键直接输它」以前是空话——这里压根没接数字键。
            val digit = DIGIT_KEYS[event.key]
            if (digit == null) false else {
                onDigit(digit)
                true
            }
        }
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
    onNumber: (Int) -> Unit,
    onSelect: (Int) -> Unit,
    onDismiss: () -> Unit,
) {
    var digitBuffer by remember { mutableStateOf("") }
    LaunchedEffect(digitBuffer) {
        if (digitBuffer.isEmpty()) return@LaunchedEffect
        delay(DIGIT_ENTRY_TIMEOUT_MS)
        val typed = digitBuffer
        digitBuffer = ""
        typed.toIntOrNull()?.let(onNumber)
    }
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
                .zapWhileOpen(onZap) { digit -> digitBuffer = (digitBuffer + digit).takeLast(3) }
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

/**
 * 线路面板那一行的耗时文案（用户 10-29「毫秒换秒」＋「换」＝快时不显示数字）。
 *
 * 规则：清单和画面两项**都不超过 300 ms 就只写「可用」**，一个数字都不露；
 * 哪项超过 300 ms 才把那一项写出来（秒、一位小数）并追「较慢」。
 * 一项测不到（null）不算慢，也不写「—」——面板上不出现拿不准的读数。
 */
private const val SLOW_MS = 300L

private fun probeTimingText(responseMs: Long?, segmentMs: Long?): String {
    fun label(name: String, ms: Long?): String? {
        if (ms == null || ms <= SLOW_MS) return null
        return String.format(java.util.Locale.US, "%s %.1f 秒 较慢", name, ms / 1000.0)
    }
    val parts = listOfNotNull(label("清单", responseMs), label("画面", segmentMs))
    return if (parts.isEmpty()) "可用" else "可用 · " + parts.joinToString(" · ")
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
        result?.playable == true -> probeTimingText(result.responseMs, result.segmentMs)
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
