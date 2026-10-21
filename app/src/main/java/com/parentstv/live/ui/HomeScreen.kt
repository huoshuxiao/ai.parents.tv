package com.parentstv.live.ui

import android.app.Activity
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items as rowItems
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items as gridItems
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.LiveTv
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.PowerSettingsNew
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material.icons.rounded.Star
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.parentstv.live.data.BeijingTime
import com.parentstv.live.data.ChannelCatalog
import com.parentstv.live.data.TvChannel
import com.parentstv.live.data.TvPrefs
import kotlinx.coroutines.delay

private const val FAVORITES_ID = "favorites"

@Composable
fun HomeScreen(
    catalog: ChannelCatalog,
    favorites: Set<String>,
    prefs: TvPrefs,
    onPlay: (TvChannel, List<TvChannel>) -> Unit,
    onSettings: () -> Unit,
) {
    val categoryDefs = catalog.categories
    val allChannels = remember(catalog) { catalog.playableChannels() }
    val byCategory = remember(allChannels) { allChannels.groupBy { it.category } }
    val byId = remember(allChannels) { allChannels.associateBy { it.id } }
    val favoriteChannels = remember(allChannels, favorites) {
        allChannels.filter { it.id in favorites }
    }
    val validCategoryIds = remember(categoryDefs) { categoryDefs.map { it.id }.toSet() }
    val lastChannel =
        if (prefs.rememberLastChannel) prefs.lastChannelId?.let { byId[it] } else null

    var selectedCategoryId by remember {
        mutableStateOf(
            lastChannel?.category?.takeIf { it in validCategoryIds }
                ?: (if (prefs.rememberLastChannel) prefs.lastCategoryId else null)?.takeIf {
                    it in validCategoryIds
                }
                ?: categoryDefs.firstOrNull()?.id
                ?: FAVORITES_ID,
        )
    }
    val initialDef = categoryDefs.firstOrNull { it.id == selectedCategoryId }
    var selectedSubgroup by remember {
        mutableStateOf(
            lastChannel?.subgroup?.takeIf { it in initialDef?.subgroups.orEmpty() }
                ?: prefs.subgroupFor(selectedCategoryId)
                    ?.takeIf { it in initialDef?.subgroups.orEmpty() }
                ?: initialDef?.subgroups?.firstOrNull()
                ?: "",
        )
    }

    fun selectCategory(id: String) {
        selectedCategoryId = id
        val def = categoryDefs.firstOrNull { it.id == id }
        val subgroups = def?.subgroups.orEmpty()
        val subgroup = prefs.subgroupFor(id)?.takeIf { it in subgroups }
            ?: subgroups.firstOrNull()
            ?: ""
        selectedSubgroup = subgroup
        prefs.lastCategoryId = id
        if (subgroup.isNotBlank()) prefs.setSubgroup(id, subgroup)
    }

    fun selectSubgroup(subgroup: String) {
        selectedSubgroup = subgroup
        if (selectedCategoryId != FAVORITES_ID) {
            prefs.setSubgroup(selectedCategoryId, subgroup)
        }
    }

    val isFavorites = selectedCategoryId == FAVORITES_ID
    val selectedDef = categoryDefs.firstOrNull { it.id == selectedCategoryId }
    val title = if (isFavorites) "我的收藏" else selectedDef?.name ?: "频道"
    val categoryChannels = if (isFavorites) {
        favoriteChannels
    } else {
        byCategory[selectedCategoryId].orEmpty()
    }
    val subgroups = if (isFavorites) emptyList() else selectedDef?.subgroups.orEmpty()
    val visibleChannels = if (isFavorites || subgroups.isEmpty() || selectedSubgroup.isBlank()) {
        categoryChannels
    } else {
        categoryChannels.filter { it.subgroup == selectedSubgroup }
    }
    val subtitle = when {
        isFavorites -> "播放时点按爱心，就能把喜欢的频道存在这里"
        selectedCategoryId == "cctv" -> "央视全套高清频道 · 全国省级卫视"
        selectedCategoryId == "local" ->
            if (selectedSubgroup.isNotBlank()) "$selectedSubgroup · 地方频道" else "按省份整理的地方频道"
        selectedCategoryId == "hktw" -> "香港 · 台湾 · 澳门 · 海外华人"
        selectedCategoryId == "featured" -> "影视轮播 · NewTV · CHC · 少儿"
        selectedCategoryId == "sports" -> "五星体育 · 咪咕直播 · 港台体育"
        selectedCategoryId == "international" -> "新闻 · 纪实 · 综合"
        else -> "电视直播"
    }

    val initialCandidates = remember {
        when {
            isFavorites -> favoriteChannels
            subgroups.isEmpty() || selectedSubgroup.isBlank() -> categoryChannels
            else -> categoryChannels.filter { it.subgroup == selectedSubgroup }
        }
    }
    val initialFocusId = remember {
        (if (prefs.rememberLastChannel) prefs.lastChannelId else null)
            ?.takeIf { id -> initialCandidates.any { it.id == id } }
            ?: initialCandidates.firstOrNull()?.id
    }
    val initialIndex = remember {
        initialCandidates.indexOfFirst { it.id == initialFocusId }.coerceAtLeast(0)
    }
    val gridState = rememberLazyGridState(initialFirstVisibleItemIndex = initialIndex)
    val gridFocus = remember { FocusRequester() }
    val selectedNavFocus = remember { FocusRequester() }
    var firstLayout by remember { mutableStateOf(true) }
    var confirmExit by remember { mutableStateOf(false) }
    val activity = LocalContext.current as? Activity

    // 父母按返回键多半只是想收起画面，直接退出会把正在播的频道丢掉，所以先问一句。
    BackHandler(enabled = !confirmExit) { confirmExit = true }

    LaunchedEffect(Unit) {
        delay(180)
        runCatching { gridFocus.requestFocus() }
    }
    LaunchedEffect(selectedCategoryId, selectedSubgroup) {
        if (firstLayout) {
            firstLayout = false
        } else {
            runCatching { gridState.scrollToItem(0) }
        }
    }

    Row(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(TvColors.Background, TvColors.BackgroundAlt),
                ),
            ),
    ) {
        HomeSidebar(
            catalog = catalog,
            selectedCategoryId = selectedCategoryId,
            favoriteCount = favoriteChannels.size,
            byCategory = byCategory,
            onSelectCategory = { id -> selectCategory(id) },
            onSettings = onSettings,
            selectedFocusRequester = selectedNavFocus,
        )
        Box(
            modifier = Modifier
                .width(1.dp)
                .fillMaxHeight()
                .background(TvColors.Divider.copy(alpha = 0.5f)),
        )
        Column(modifier = Modifier.weight(1f).fillMaxHeight()) {
            HomeHeader(
                title = title,
                subtitle = subtitle,
                count = visibleChannels.size,
            )
            if (subgroups.isNotEmpty()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(start = 36.dp, end = 40.dp, bottom = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (selectedCategoryId == "local") {
                        Text(
                            text = "省份",
                            fontSize = 14.sp,
                            color = TvColors.TextSecondary,
                            modifier = Modifier.padding(end = 14.dp),
                        )
                    }
                    LazyRow(
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                        modifier = Modifier.weight(1f),
                    ) {
                        rowItems(subgroups, key = { it }) { subgroup ->
                            SubgroupChip(
                                label = subgroup,
                                selected = subgroup == selectedSubgroup,
                                onClick = { selectSubgroup(subgroup) },
                            )
                        }
                    }
                }
            }
            LazyVerticalGrid(
                state = gridState,
                columns = GridCells.Adaptive(minSize = 210.dp),
                contentPadding = PaddingValues(
                    start = 36.dp,
                    end = 40.dp,
                    top = 2.dp,
                    bottom = 28.dp,
                ),
                horizontalArrangement = Arrangement.spacedBy(14.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                gridItems(visibleChannels, key = { it.id }) { channel ->
                    ChannelCard(
                        channel = channel,
                        isFavorite = channel.id in favorites,
                        focusRequester = if (channel.id == initialFocusId) gridFocus else null,
                        leftFocus = selectedNavFocus,
                        onClick = { onPlay(channel, visibleChannels) },
                    )
                }
                if (visibleChannels.isEmpty()) {
                    item(span = { GridItemSpan(maxLineSpan) }) {
                        EmptyState(isFavorites = isFavorites)
                    }
                }
            }
        }
    }

    if (confirmExit) {
        ExitConfirmDialog(
            onStay = { confirmExit = false },
            onExit = { activity?.finish() },
        )
    }
}

@Composable
private fun HomeSidebar(
    catalog: ChannelCatalog,
    selectedCategoryId: String,
    favoriteCount: Int,
    byCategory: Map<String, List<TvChannel>>,
    onSelectCategory: (String) -> Unit,
    onSettings: () -> Unit,
    selectedFocusRequester: FocusRequester,
) {
    Column(
        modifier = Modifier
            .width(228.dp)
            .fillMaxHeight()
            .background(
                Brush.verticalGradient(
                    listOf(TvColors.Sidebar, TvColors.Background),
                ),
            )
            .padding(horizontal = 12.dp, vertical = 16.dp),
    ) {
        ParentsTvLogo(modifier = Modifier.padding(horizontal = 8.dp))
        Spacer(Modifier.height(18.dp))
        SidebarItem(
            label = "我的收藏",
            icon = iconForCategory(FAVORITES_ID),
            selected = selectedCategoryId == FAVORITES_ID,
            badge = favoriteCount,
            focusRequester = if (selectedCategoryId == FAVORITES_ID) selectedFocusRequester else null,
            onClick = { onSelectCategory(FAVORITES_ID) },
        )
        Spacer(Modifier.height(4.dp))
        catalog.categories.forEach { category ->
            SidebarItem(
                label = category.name,
                icon = iconForCategory(category.id),
                selected = selectedCategoryId == category.id,
                badge = byCategory[category.id]?.size,
                focusRequester = if (selectedCategoryId == category.id) selectedFocusRequester else null,
                onClick = { onSelectCategory(category.id) },
            )
            Spacer(Modifier.height(4.dp))
        }
        Spacer(Modifier.weight(1f))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(TvColors.Divider.copy(alpha = 0.6f)),
        )
        Spacer(Modifier.height(8.dp))
        SidebarItem(
            label = "设置",
            icon = Icons.Rounded.Settings,
            selected = false,
            badge = null,
            onClick = onSettings,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text = "数据更新（北京） " + (
                BeijingTime.formatIso(catalog.generatedAt, "yyyy-MM-dd") ?: "未知"
                ),
            fontSize = 11.sp,
            color = TvColors.TextSecondary.copy(alpha = 0.65f),
            modifier = Modifier.padding(horizontal = 10.dp),
        )
    }
}

@Composable
private fun HomeHeader(
    title: String,
    subtitle: String,
    count: Int,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 36.dp, end = 40.dp, top = 22.dp, bottom = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.headlineLarge,
                    color = TvColors.TextPrimary,
                )
                Spacer(Modifier.width(14.dp))
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(10.dp))
                        .background(TvColors.AccentSoft)
                        .padding(horizontal = 10.dp, vertical = 5.dp),
                ) {
                    Text(
                        text = "当前 $count 个",
                        color = TvColors.Accent,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Medium,
                    )
                }
            }
            Spacer(Modifier.height(6.dp))
            Text(
                text = subtitle,
                color = TvColors.TextSecondary,
                fontSize = 15.sp,
            )
        }
        Column(horizontalAlignment = Alignment.End) {
            TvClock()
            Text(
                text = "方向键选择 · OK 播放",
                color = TvColors.TextSecondary.copy(alpha = 0.65f),
                fontSize = 11.sp,
            )
        }
    }
}

@Composable
private fun EmptyState(isFavorites: Boolean) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 90.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(
            imageVector = if (isFavorites) Icons.Rounded.Star else Icons.Rounded.LiveTv,
            contentDescription = null,
            tint = TvColors.Accent.copy(alpha = 0.55f),
            modifier = Modifier.size(56.dp),
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = if (isFavorites) "还没有收藏频道" else "这个分类暂时没有频道",
            fontSize = 24.sp,
            fontWeight = FontWeight.SemiBold,
            color = TvColors.TextPrimary,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = if (isFavorites) {
                "播放频道时，在控制栏点按爱心即可收藏"
            } else {
                "可以试试其他分类或省份"
            },
            fontSize = 16.sp,
            color = TvColors.TextSecondary,
        )
    }
}

@Composable
private fun ExitConfirmDialog(
    onStay: () -> Unit,
    onExit: () -> Unit,
) {
    val stayFocus = remember { FocusRequester() }
    LaunchedEffect(Unit) {
        delay(120)
        runCatching { stayFocus.requestFocus() }
    }
    Dialog(
        onDismissRequest = onStay,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Column(
            modifier = Modifier
                .width(620.dp)
                .clip(RoundedCornerShape(22.dp))
                .background(TvColors.BackgroundAlt)
                .border(1.dp, TvColors.Divider, RoundedCornerShape(22.dp))
                .padding(horizontal = 32.dp, vertical = 30.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = "要退出父母TV吗？",
                color = TvColors.TextPrimary,
                fontSize = 27.sp,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(10.dp))
            Text(
                text = "上次看的频道会记住，下次打开直接回到这里（也可以在设置里关掉）。" +
                    "按遥控器的主页键可以回到电视首页。",
                color = TvColors.TextSecondary,
                fontSize = 15.sp,
                lineHeight = 22.sp,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(24.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                PlayerActionButton(
                    icon = Icons.Rounded.PlayArrow,
                    label = "继续观看",
                    focusRequester = stayFocus,
                    onClick = onStay,
                )
                PlayerActionButton(
                    icon = Icons.Rounded.PowerSettingsNew,
                    label = "退出应用",
                    onClick = onExit,
                )
            }
        }
    }
}
