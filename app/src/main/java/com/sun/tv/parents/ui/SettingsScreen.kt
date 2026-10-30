package com.sun.tv.parents.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ArrowBack
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.DeleteSweep
import androidx.compose.material.icons.rounded.Keyboard
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.sun.tv.parents.BuildConfig
import com.sun.tv.parents.data.BackKeyAction
import com.sun.tv.parents.data.BeijingTime
import com.sun.tv.parents.data.ChannelCatalog
import com.sun.tv.parents.data.DirectionKeyAction
import com.sun.tv.parents.data.MenuKeyAction
import com.sun.tv.parents.data.TvPrefs
import kotlinx.coroutines.delay

@Composable
fun SettingsScreen(
    catalog: ChannelCatalog,
    favorites: Set<String>,
    prefs: TvPrefs,
    lastUpdateText: String,
    updateMessage: String?,
    updating: Boolean,
    onRefreshLines: () -> Unit,
    onBack: () -> Unit,
    onClearFavorites: () -> Unit,
    onManualLinesChanged: () -> Unit = {},
) {
    val backFocus = remember { FocusRequester() }
    val keyFocus = remember { FocusRequester() }
    var confirmClear by remember { mutableStateOf(false) }
    var showKeySettings by remember { mutableStateOf(false) }
    var showTencentSports by remember { mutableStateOf(false) }
    var keyDialogOpened by remember { mutableStateOf(false) }
    var rememberLastChannel by remember { mutableStateOf(prefs.rememberLastChannel) }
    var epgEnabled by remember { mutableStateOf(prefs.epgEnabled) }
    var showResolution by remember { mutableStateOf(prefs.showVideoResolution) }
    var lineSwitchSeconds by remember { mutableStateOf(prefs.lineSwitchSeconds) }
    BackHandler {
        if (!showKeySettings && !confirmClear && !showTencentSports) onBack()
    }
    val generatedText = remember(catalog.generatedAt) {
        BeijingTime.formatIso(catalog.generatedAt, "yyyy-MM-dd HH:mm") ?: "未知"
    }

    LaunchedEffect(Unit) {
        delay(120)
        runCatching { backFocus.requestFocus() }
    }
    LaunchedEffect(showKeySettings) {
        if (showKeySettings) {
            keyDialogOpened = true
        } else if (keyDialogOpened) {
            delay(100)
            runCatching { keyFocus.requestFocus() }
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
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 56.dp, vertical = 22.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                PlayerActionButton(
                    icon = Icons.Rounded.ArrowBack,
                    label = "返回",
                    focusRequester = backFocus,
                    onClick = onBack,
                )
                Spacer(Modifier.width(22.dp))
                Column {
                    Text(
                        text = "设置",
                        style = MaterialTheme.typography.headlineMedium,
                        color = TvColors.TextPrimary,
                    )
                    Text(
                        text = "频道数据、遥控器与使用说明",
                        color = TvColors.TextSecondary,
                        fontSize = 14.sp,
                    )
                }
                Spacer(Modifier.weight(1f))
                PlayerActionButton(
                    icon = Icons.Rounded.Keyboard,
                    label = "按键设置",
                    focusRequester = keyFocus,
                    onClick = { showKeySettings = true },
                )
                Spacer(Modifier.width(18.dp))
                ParentsTvLogo(showSubtitle = false)
            }

            Spacer(Modifier.height(14.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                SettingsCard(
                    title = "频道数据",
                    modifier = Modifier.weight(1f),
                ) {
                    StatRow("频道总数", "${catalog.playableChannels().size}")
                    StatRow("一级分类", "${catalog.categories.size} 个")
                    StatRow(
                        "地方省份",
                        "${(catalog.categories.firstOrNull { it.id == "local" }?.subgroups?.size ?: 1) - 1} 个",
                    )
                    StatRow("数据生成（北京）", generatedText)
                }
                SettingsCard(
                    title = "线路自动更新",
                    modifier = Modifier.weight(1f),
                ) {
                    StatRow("上次更新（北京）", lastUpdateText)
                    StatRow("更新周期", "每 12 小时")
                    Text(
                        text = updateMessage
                            ?: "每 12 小时自动补同名频道的新线路；按「立即更新」还会检测央视/卫视/辽宁体育每条线路能不能取到画面。",
                        color = TvColors.TextSecondary,
                        fontSize = 13.sp,
                        lineHeight = 18.sp,
                        maxLines = 4,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Spacer(Modifier.height(10.dp))
                    PlayerActionButton(
                        icon = Icons.Rounded.Refresh,
                        label = if (updating) "正在更新…" else "立即更新",
                        enabled = !updating,
                        onClick = onRefreshLines,
                    )
                }
            }

            Spacer(Modifier.height(14.dp))

            SettingsCard(title = "我的收藏", modifier = Modifier.fillMaxWidth()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = "已收藏 ${favorites.size} 个频道",
                            color = TvColors.TextPrimary,
                            fontSize = 17.sp,
                            fontWeight = FontWeight.Medium,
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            text = "播放频道时，点控制栏的爱心即可收藏；菜单键现在用来选线路。",
                            color = TvColors.TextSecondary,
                            fontSize = 13.sp,
                        )
                    }
                    PlayerActionButton(
                        icon = Icons.Rounded.DeleteSweep,
                        label = "清空收藏",
                        enabled = favorites.isNotEmpty(),
                        onClick = { confirmClear = true },
                    )
                }
            }

            Spacer(Modifier.height(14.dp))

            SettingsCard(title = "腾讯体育（手动挂）", modifier = Modifier.fillMaxWidth()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = "按场次挂一条临时线",
                            color = TvColors.TextPrimary,
                            fontSize = 17.sp,
                            fontWeight = FontWeight.Medium,
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            text = "腾讯体育不是 24 小时频道，地址开赛才有。打开后选一场免费比赛点「挂」，抓到的线 6 小时后自动摘掉；SVIP/用券场次不抓。",
                            color = TvColors.TextSecondary,
                            fontSize = 13.sp,
                        )
                    }
                    PlayerActionButton(
                        icon = Icons.Rounded.Refresh,
                        label = "打开",
                        onClick = { showTencentSports = true },
                    )
                }
            }

            Spacer(Modifier.height(14.dp))

            SettingsCard(title = "播放与界面", modifier = Modifier.fillMaxWidth()) {
                PrefSwitchRow(
                    title = "开机回到上次频道",
                    description = "关掉后每次打开都从分类开头，不回上次看的频道。",
                    selected = if (rememberLastChannel) 0 else 1,
                    firstLabel = "开",
                    secondLabel = "关",
                    onFirst = {
                        rememberLastChannel = true
                        prefs.rememberLastChannel = true
                    },
                    onSecond = {
                        rememberLastChannel = false
                        prefs.rememberLastChannel = false
                    },
                )
                PrefSwitchRow(
                    title = "直播节目单",
                    description = "画面上显示正在播出，并可以打开完整节目单。每 6 小时联网取一次。",
                    selected = if (epgEnabled) 0 else 1,
                    firstLabel = "开",
                    secondLabel = "关",
                    onFirst = {
                        epgEnabled = true
                        prefs.epgEnabled = true
                    },
                    onSecond = {
                        epgEnabled = false
                        prefs.epgEnabled = false
                    },
                )
                PrefSwitchRow(
                    title = "显示画面分辨率",
                    description = "起播后在右上角显示 1920×1080 这样的实际画面尺寸。",
                    selected = if (showResolution) 0 else 1,
                    firstLabel = "开",
                    secondLabel = "关",
                    onFirst = {
                        showResolution = true
                        prefs.showVideoResolution = true
                    },
                    onSecond = {
                        showResolution = false
                        prefs.showVideoResolution = false
                    },
                )
                PrefChipsRow(
                    title = "自动换线等待时间",
                    description = "一条线路卡住这么久就换下一条；已经播过的线路不会因此被换掉。默认 10 秒。",
                    options = LINE_SWITCH_OPTIONS.map { "${it} 秒" },
                    selected = LINE_SWITCH_OPTIONS.indexOf(lineSwitchSeconds),
                    onSelect = { position ->
                        val seconds = LINE_SWITCH_OPTIONS[position]
                        lineSwitchSeconds = seconds
                        prefs.lineSwitchSeconds = seconds
                    },
                )
            }

            Spacer(Modifier.height(18.dp))

            Text(
                text = "提示：控制栏隐藏时 OK 选台 · 左右键换线路 · 上下键换频道 · 菜单键选线路 · 数字键直接输台号",
                color = TvColors.TextSecondary.copy(alpha = 0.7f),
                fontSize = 12.sp,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                text = "频道与线路来自公开 IPTV 列表，仅用于学习与个人测试；本应用不提供、不存储直播流。",
                color = TvColors.TextSecondary.copy(alpha = 0.6f),
                fontSize = 11.sp,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                text = "父母TV  v${BuildConfig.VERSION_NAME}  ·  为家人做的电视直播应用",
                color = TvColors.TextSecondary.copy(alpha = 0.5f),
                fontSize = 12.sp,
            )
        }
    }

    if (confirmClear) {
        AlertDialog(
            onDismissRequest = { confirmClear = false },
            title = { Text("清空收藏？") },
            text = { Text("将删除全部 ${favorites.size} 个收藏频道，此操作无法撤销。") },
            confirmButton = {
                TextButton(
                    onClick = {
                        onClearFavorites()
                        confirmClear = false
                    },
                ) {
                    Text("清空")
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmClear = false }) {
                    Text("取消")
                }
            },
            containerColor = TvColors.Surface,
            titleContentColor = TvColors.TextPrimary,
            textContentColor = TvColors.TextSecondary,
        )
    }

    if (showKeySettings) {
        KeySettingsDialog(
            prefs = prefs,
            onDismiss = { showKeySettings = false },
        )
    }

    if (showTencentSports) {
        TencentSportsDialog(
            onDismiss = { showTencentSports = false },
            onLinesChanged = onManualLinesChanged,
        )
    }
}

@Composable
private fun KeySettingsDialog(
    prefs: TvPrefs,
    onDismiss: () -> Unit,
) {
    var upAction by remember { mutableStateOf(prefs.upKeyAction) }
    var downAction by remember { mutableStateOf(prefs.downKeyAction) }
    var menuAction by remember { mutableStateOf(prefs.menuKeyAction) }
    var backAction by remember { mutableStateOf(prefs.backKeyAction) }
    val firstFocus = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        delay(150)
        runCatching { firstFocus.requestFocus() }
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Column(
            modifier = Modifier
                .width(780.dp)
                .heightIn(max=500.dp)
                .clip(RoundedCornerShape(22.dp))
                .background(TvColors.BackgroundAlt)
                .border(1.dp, TvColors.Divider, RoundedCornerShape(22.dp))
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 30.dp, vertical = 22.dp),
        ) {
            Text(
                text = "遥控器按键",
                color = TvColors.TextPrimary,
                fontSize = 27.sp,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                text = "设置会立即保存，回到播放器后生效。",
                color = TvColors.TextSecondary,
                fontSize = 13.sp,
            )
            Spacer(Modifier.height(16.dp))

            KeySettingHeader(
                title = "上键",
                description = "控制栏隐藏时，遥控器「上」键做什么。",
            )
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                DirectionKeyAction.values().forEachIndexed { position, action ->
                    KeyChoiceChip(
                        label = directionLabel(action),
                        selected = upAction == action,
                        focusRequester = if (position == 0) firstFocus else null,
                        onClick = {
                            upAction = action
                            prefs.upKeyAction = action
                        },
                    )
                }
            }
            Spacer(Modifier.height(16.dp))

            KeySettingHeader(
                title = "下键",
                description = "控制栏隐藏时，遥控器「下」键做什么。",
            )
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                DirectionKeyAction.values().forEach { action ->
                    KeyChoiceChip(
                        label = directionLabel(action),
                        selected = downAction == action,
                        onClick = {
                            downAction = action
                            prefs.downKeyAction = action
                        },
                    )
                }
            }
            Spacer(Modifier.height(16.dp))

            KeySettingHeader(
                title = "菜单键",
                description = "遥控器菜单键触发的动作。收藏改用控制栏的爱心按钮。",
            )
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                MenuKeyAction.values().forEach { action ->
                    KeyChoiceChip(
                        label = when (action) {
                            MenuKeyAction.Lines -> "选择线路"
                            MenuKeyAction.Aspect -> "切换画面"
                            MenuKeyAction.Off -> "关闭"
                        },
                        selected = menuAction == action,
                        onClick = {
                            menuAction = action
                            prefs.menuKeyAction = action
                        },
                    )
                }
            }
            Spacer(Modifier.height(16.dp))

            KeySettingHeader(
                title = "返回键",
                description = "控制栏显示时，返回键的行为。",
            )
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                BackKeyAction.values().forEach { action ->
                    KeyChoiceChip(
                        label = when (action) {
                            BackKeyAction.HideControls -> "先收起控制栏"
                            BackKeyAction.BackToList -> "直接返回列表"
                        },
                        selected = backAction == action,
                        onClick = {
                            backAction = action
                            prefs.backKeyAction = action
                        },
                    )
                }
            }
            Spacer(Modifier.height(12.dp))
            Text(
                text = "CH+ / CH- 键始终用于换台。控制栏隐藏时：OK 键选台、左右键换线路、数字键直接输台号（停手 2.5 秒后跳台）。",
                color = TvColors.TextSecondary.copy(alpha = 0.7f),
                fontSize = 11.sp,
            )
            Spacer(Modifier.height(12.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
            ) {
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
private fun KeySettingHeader(
    title: String,
    description: String,
) {
    Text(
        text = title,
        color = TvColors.TextPrimary,
        fontSize = 17.sp,
        fontWeight = FontWeight.SemiBold,
    )
    Spacer(Modifier.height(2.dp))
    Text(
        text = description,
        color = TvColors.TextSecondary,
        fontSize = 12.sp,
    )
}

@Composable
private fun KeyChoiceChip(
    label: String,
    selected: Boolean,
    focusRequester: FocusRequester? = null,
    onClick: () -> Unit,
) {
    val fallbackFocus = remember { FocusRequester() }
    var focused by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(11.dp)
    Box(
        modifier = Modifier
            .height(38.dp)
            .clip(shape)
            .background(
                when {
                    selected -> TvColors.Accent
                    focused -> TvColors.SurfaceFocused
                    else -> TvColors.Surface
                },
            )
            .border(
                width = 2.dp,
                color = when {
                    focused -> TvColors.Accent
                    selected -> TvColors.Accent.copy(alpha = 0.65f)
                    else -> TvColors.Divider
                },
                shape = shape,
            )
            .focusRequester(focusRequester ?: fallbackFocus)
            .onFocusChanged { focused = it.isFocused }
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = label,
            color = when {
                selected -> Color(0xFF1A1200)
                focused -> TvColors.TextPrimary
                else -> TvColors.TextSecondary
            },
            fontSize = 14.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium,
        )
    }
}

@Composable
private fun PrefSwitchRow(
    title: String,
    description: String,
    selected: Int,
    firstLabel: String,
    secondLabel: String,
    onFirst: () -> Unit,
    onSecond: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                color = TvColors.TextPrimary,
                fontSize = 16.sp,
                fontWeight = FontWeight.Medium,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                text = description,
                color = TvColors.TextSecondary,
                fontSize = 12.sp,
            )
        }
        Spacer(Modifier.width(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            KeyChoiceChip(label = firstLabel, selected = selected == 0, onClick = onFirst)
            KeyChoiceChip(label = secondLabel, selected = selected == 1, onClick = onSecond)
        }
    }
}

/** 一格设置项带多个候选（这里给「自动换线等待时间」用）。 */
@Composable
private fun PrefChipsRow(
    title: String,
    description: String,
    options: List<String>,
    selected: Int,
    onSelect: (Int) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                color = TvColors.TextPrimary,
                fontSize = 16.sp,
                fontWeight = FontWeight.Medium,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                text = description,
                color = TvColors.TextSecondary,
                fontSize = 12.sp,
            )
        }
        Spacer(Modifier.width(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            options.forEachIndexed { position, label ->
                KeyChoiceChip(
                    label = label,
                    selected = position == selected,
                    onClick = { onSelect(position) },
                )
            }
        }
    }
}

private fun directionLabel(action: DirectionKeyAction): String = when (action) {
    DirectionKeyAction.ZapPrevious -> "换到上一个台"
    DirectionKeyAction.ZapNext -> "换到下一个台"
    DirectionKeyAction.MoveFocus -> "只移动焦点"
}

private val LINE_SWITCH_OPTIONS = listOf(5, 10, 15, 20, 30, 50, 120)

@Composable
private fun SettingsCard(
    title: String,
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(18.dp))
            .background(TvColors.Surface)
            .border(1.dp, TvColors.Divider, RoundedCornerShape(18.dp))
            .padding(18.dp),
    ) {
        Text(
            text = title,
            color = TvColors.Accent,
            fontSize = 18.sp,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(8.dp))
        content()
    }
}

@Composable
private fun StatRow(label: String, value: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text = label, color = TvColors.TextSecondary, fontSize = 14.sp)
        Spacer(Modifier.weight(1f))
        Text(
            text = value,
            color = TvColors.TextPrimary,
            fontSize = 15.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}
