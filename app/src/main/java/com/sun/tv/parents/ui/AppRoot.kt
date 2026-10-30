package com.sun.tv.parents.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.draw.paint
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import com.sun.tv.parents.R
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sun.tv.parents.data.BeijingTime
import com.sun.tv.parents.data.CatalogRepository
import com.sun.tv.parents.data.CatalogUpdater
import com.sun.tv.parents.data.ChannelCatalog
import com.sun.tv.parents.data.ChannelNumbers
import com.sun.tv.parents.data.EpgRepository
import com.sun.tv.parents.data.TvChannel
import com.sun.tv.parents.data.TvPrefs
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun AppRoot() {
    val context = LocalContext.current
    val repository = remember { CatalogRepository(context) }
    val updater = remember { CatalogUpdater(context) }
    val prefs = remember { TvPrefs(context) }
    val scope = rememberCoroutineScope()

    var catalog by remember { mutableStateOf<ChannelCatalog?>(null) }
    var loadError by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var screen by remember { mutableStateOf<Screen>(Screen.Home) }
    var favorites by remember { mutableStateOf(prefs.favorites) }
    var updating by remember { mutableStateOf(false) }
    var updateMessage by remember { mutableStateOf<String?>(null) }
    var lastUpdateAt by remember { mutableLongStateOf(updater.lastUpdateAt) }

    LaunchedEffect(reloadKey) {
        loadError = null
        val loaded = runCatching { repository.load() }
            .onFailure { loadError = it.message ?: it.javaClass.simpleName }
            .getOrNull()
        if (loaded == null) return@LaunchedEffect

        catalog = loaded
        // 每次启动都让更新器看一眼：内置清单比缓存新时它只做本地换底、不发网络请求。
        // 以前这里被 isStale() 挡着，装上新 APK 后 12 小时内新目录根本不会上屏
        // （10-23 13:47 装机实测：包内已是 305 台，首页仍显示旧目录的 44/15）。
        updating = updater.isStale()
        runCatching { updater.refresh(force = false) }
            .onSuccess { result ->
                if (result.catalogChanged) {
                    runCatching { repository.load() }.getOrNull()?.let { catalog = it }
                }
                updateMessage = result.message
                lastUpdateAt = updater.lastUpdateAt
            }
            .onFailure { updateMessage = "自动更新失败：${it.message ?: "网络错误"}" }
        updating = false
    }

    fun refreshLinesNow() {
        if (updating) return
        updating = true
        updateMessage = null
        scope.launch {
            runCatching { updater.refresh(force = true) }
                .onSuccess { result ->
                    if (result.catalogChanged) {
                        runCatching { repository.load() }.getOrNull()?.let { catalog = it }
                    }
                    updateMessage = result.message
                    lastUpdateAt = updater.lastUpdateAt
                }
                .onFailure { updateMessage = "更新失败：${it.message ?: "网络错误"}" }
            updating = false
        }
    }

    val currentCatalog = catalog
    if (currentCatalog == null) {
        LoadingScreen(error = loadError, onRetry = { reloadKey++ })
        return
    }

    // 数字键台号按整份目录数，跨组也认这个号，所以这份表要在播放器外面算好递进去。
    val numberedChannels = remember(currentCatalog) {
        ChannelNumbers.numbered(currentCatalog.playableChannels())
    }

    fun markWatched(channel: TvChannel) {
        prefs.lastChannelId = channel.id
        prefs.lastCategoryId = channel.category
        prefs.setSubgroup(channel.category, channel.subgroup)
    }

    /** 数字键跳到别的组时，队列换成那一组——与主页点进去时拿到的队列同一个口径。 */
    fun queueOf(channel: TvChannel): List<TvChannel> {
        // 用户 10-24 18:3x「取消子分组」：主页不再按省份/子类切一刀，播放器这一侧的队列
        // 也跟着变成「整个分类」。再按 channel.subgroup 过滤会出现「主页看得到、上下键翻不到」
        // 的两套口径（央视·卫视那 62 台尤其明显）。
        return currentCatalog.playableChannels().filter { it.category == channel.category }
    }

    // 用户 10-29 00:5x「默认跳转的台，第一次默认 CCTV1 频道，之后是上次退出的频道」：
    // 目录就绪后**只自动进一次**（rememberSaveable 挡住重组、也挡住从设置页返回首页时的二次触发）。
    var autoStarted by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(currentCatalog) {
        if (autoStarted) return@LaunchedEffect
        autoStarted = true
        val all = currentCatalog.playableChannels()
        if (all.isEmpty()) return@LaunchedEffect
        val remembered = if (prefs.rememberLastChannel) {
            prefs.lastChannelId?.let { id -> all.firstOrNull { it.id == id } }
        } else {
            null
        }
        val target = remembered
            ?: all.firstOrNull { it.name.startsWith("CCTV-1") }
            ?: all.first()
        markWatched(target)
        screen = Screen.Player(target, queueOf(target))
    }

    // 用户 10-29 00:5x「节目单，APP启动时和午夜12点节目单自动更新一次」：启动即强制拉一次，
    // 之后每天北京时间 00:00 再强制拉一次（强刷会重写磁盘快照，播放器那边照旧读快照）。
    // 与首帧抢带宽的问题沿用 10-25 的处置：这里不阻塞 UI，播放器仍等出画面后才读。
    LaunchedEffect(prefs.epgEnabled) {
        if (!prefs.epgEnabled) return@LaunchedEffect
        val epg = EpgRepository(context)
        // 用户 10-30「使用延时加载不抢视频流带宽」：启动就把 1 MB 节目单扔上去会和首片抢链路
        // （历史上那 12 MB 就这么把首屏拖成一句「正在连接」）。先让播放自己的取流跑 20 秒再取。
        kotlinx.coroutines.delay(20_000)
        runCatching { epg.snapshot(force = true) }
        while (true) {
            kotlinx.coroutines.delay(BeijingTime.millisToNextMidnight())
            runCatching { epg.snapshot(force = true) }
        }
    }

    when (val current = screen) {
        Screen.Home -> HomeScreen(
            catalog = currentCatalog,
            favorites = favorites,
            prefs = prefs,
            onPlay = { channel, queue ->
                markWatched(channel)
                screen = Screen.Player(channel, queue.ifEmpty { listOf(channel) })
            },
            onSettings = { screen = Screen.Settings },
        )

        Screen.Settings -> SettingsScreen(
            catalog = currentCatalog,
            favorites = favorites,
            prefs = prefs,
            lastUpdateText = if (lastUpdateAt == 0L) {
                "尚未更新"
            } else {
                BeijingTime.format(lastUpdateAt, "MM-dd HH:mm")
            },
            updateMessage = updateMessage,
            updating = updating,
            onRefreshLines = { refreshLinesNow() },
            onBack = { screen = Screen.Home },
            onClearFavorites = {
                prefs.clearFavorites()
                favorites = emptySet()
            },
            // 手动挂了一条腾讯体育临时线之后立刻重读目录，回首页就能在体育组最后一个看到它。
            onManualLinesChanged = {
                scope.launch {
                    runCatching { repository.load() }.getOrNull()?.let { catalog = it }
                }
            },
        )

        is Screen.Player -> PlayerScreen(
            initialChannel = current.channel,
            initialQueue = current.queue,
            numberedChannels = numberedChannels,
            onNumberedJump = { target ->
                markWatched(target)
                screen = Screen.Player(target, queueOf(target))
            },
            // 分组名按**正在播这一台**的 category 现查：数字键是在播放器内部换台的，
            // 传进来的 `current.channel` 还停在进播放器那一台，写成值就会滞后
            //（10-25 装机实测：从 Animal Planet 按 14 跳到 CCTV-14，画面那行显示「国际频道」）。
            categoryNameOf = currentCatalog::categoryName,
            // 选线面板要按地址查构建时实测的尺寸，查询能力就是目录上的 qualityOf。
            lineQualityOf = currentCatalog::qualityOf,
            favorites = favorites,
            prefs = prefs,
            onChannelChanged = { channel -> markWatched(channel) },
            onBack = { screen = Screen.Home },
        )
    }
}

sealed interface Screen {
    data object Home : Screen
    data object Settings : Screen
    data class Player(val channel: TvChannel, val queue: List<TvChannel>) : Screen
}

@Composable
private fun LoadingScreen(
    error: String?,
    onRetry: () -> Unit,
) {
    val retryFocus = remember { FocusRequester() }
    LaunchedEffect(error) {
        if (error != null) {
            delay(120)
            runCatching { retryFocus.requestFocus() }
        }
    }
    Box(
        modifier = Modifier
            .fillMaxSize()
            // 用户 10-29 00:5x「启动时加载画面的图片换高清的全屏大图片」：原来是纯渐变 +
            // 那个 320×180 的 tv_banner（放大到电视上糊成一片）。换成 1792×1024 的全屏插画，
            // 上面压一层半透明暗罩，保证中间的转圈与错误文字仍然看得清。
            .paint(painterResource(R.drawable.splash_hd), contentScale = ContentScale.Crop)
            .background(
                Brush.verticalGradient(
                    listOf(Color(0x66070B14), Color(0x990C1424)),
                ),
            ),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
            modifier = Modifier.padding(48.dp),
        ) {
            ParentsTvLogo(showSubtitle = false)
            Spacer(Modifier.height(32.dp))
            if (error == null) {
                CircularProgressIndicator(color = TvColors.Accent, strokeWidth = 4.dp)
                Spacer(Modifier.height(20.dp))
                Text(
                    text = "正在加载频道…",
                    color = TvColors.TextSecondary,
                    fontSize = 18.sp,
                )
            } else {
                Text(
                    text = "频道数据加载失败",
                    color = TvColors.TextPrimary,
                    fontSize = 28.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(10.dp))
                Text(
                    text = error,
                    color = TvColors.TextSecondary,
                    fontSize = 15.sp,
                    textAlign = TextAlign.Center,
                    maxLines = 4,
                )
                Spacer(Modifier.height(24.dp))
                Button(
                    onClick = onRetry,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = TvColors.Accent,
                        contentColor = TvColors.Background,
                    ),
                    modifier = Modifier.focusRequester(retryFocus),
                ) {
                    Text("重新加载", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }
}

