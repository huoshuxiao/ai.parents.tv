package com.parentstv.live.ui

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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.parentstv.live.data.BeijingTime
import com.parentstv.live.data.CatalogRepository
import com.parentstv.live.data.CatalogUpdater
import com.parentstv.live.data.ChannelCatalog
import com.parentstv.live.data.TvChannel
import com.parentstv.live.data.TvPrefs
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
        if (updater.isStale()) {
            updating = true
            runCatching { updater.refresh(force = false) }
                .onSuccess { result ->
                    if (result.addedLines > 0) {
                        runCatching { repository.load() }.getOrNull()?.let { catalog = it }
                    }
                    updateMessage = result.message
                    lastUpdateAt = updater.lastUpdateAt
                }
                .onFailure { updateMessage = "自动更新失败：${it.message ?: "网络错误"}" }
            updating = false
        }
    }

    fun refreshLinesNow() {
        if (updating) return
        updating = true
        updateMessage = null
        scope.launch {
            runCatching { updater.refresh(force = true) }
                .onSuccess { result ->
                    if (result.addedLines > 0) {
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

    fun markWatched(channel: TvChannel) {
        prefs.lastChannelId = channel.id
        prefs.lastCategoryId = channel.category
        prefs.setSubgroup(channel.category, channel.subgroup)
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
        )

        is Screen.Player -> PlayerScreen(
            initialChannel = current.channel,
            initialQueue = current.queue,
            categoryName = currentCatalog.categoryName(current.channel.category),
            favorites = favorites,
            prefs = prefs,
            onToggleFavorite = { channel ->
                val next = if (channel.id in favorites) {
                    favorites - channel.id
                } else {
                    favorites + channel.id
                }
                favorites = next
                prefs.favorites = next
            },
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
            .background(
                Brush.verticalGradient(
                    listOf(TvColors.Background, TvColors.BackgroundAlt),
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
