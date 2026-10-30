package com.sun.tv.parents.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Favorite
import androidx.compose.material.icons.rounded.Language
import androidx.compose.material.icons.rounded.LiveTv
import androidx.compose.material.icons.rounded.LocationCity
import androidx.compose.material.icons.rounded.Movie
import androidx.compose.material.icons.rounded.Public
import androidx.compose.material.icons.rounded.SportsSoccer
import androidx.compose.material.icons.rounded.Star
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.sun.tv.parents.data.BeijingTime
import com.sun.tv.parents.data.TvChannel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun ParentsTvLogo(
    modifier: Modifier = Modifier,
    showSubtitle: Boolean = true,
) {
    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        Box(
            modifier = Modifier
                .size(44.dp)
                .clip(RoundedCornerShape(13.dp))
                .background(
                    Brush.linearGradient(
                        listOf(TvColors.Accent, Color(0xFFFF8A3D)),
                    ),
                ),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = Icons.Rounded.LiveTv,
                contentDescription = null,
                tint = Color(0xFF1A1200),
                modifier = Modifier.size(27.dp),
            )
        }
        Spacer(Modifier.width(10.dp))
        Column {
            Text(
                text = "父母TV",
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = TvColors.TextPrimary,
            )
            if (showSubtitle) {
                Text(
                    text = "直播电视",
                    fontSize = 12.sp,
                    color = TvColors.TextSecondary,
                )
            }
        }
    }
}

@Composable
fun TvClock(modifier: Modifier = Modifier) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            now = System.currentTimeMillis()
            delay(10_000)
        }
    }
    Column(modifier = modifier, horizontalAlignment = Alignment.End) {
        Text(
            text = BeijingTime.format(now, "HH:mm"),
            fontSize = 38.sp,
            fontWeight = FontWeight.Light,
            color = TvColors.TextPrimary,
        )
        Text(
            text = BeijingTime.format(now, "M月d日 EEEE") + " · 北京时间",
            fontSize = 14.sp,
            color = TvColors.TextSecondary,
        )
    }
}

fun iconForCategory(id: String): ImageVector = when (id) {
    "favorites" -> Icons.Rounded.Star
    "cctv" -> Icons.Rounded.LiveTv
    "local" -> Icons.Rounded.LocationCity
    "hktw" -> Icons.Rounded.Public
    "featured" -> Icons.Rounded.Movie
    "sports" -> Icons.Rounded.SportsSoccer
    "tencent" -> Icons.Rounded.SportsSoccer
    "international" -> Icons.Rounded.Language
    else -> Icons.Rounded.LiveTv
}

@Composable
fun ChannelLogo(
    channel: TvChannel,
    size: Dp,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val palette = listOf(
        Color(0xFF243B55),
        Color(0xFF3B2E5A),
        Color(0xFF173A3A),
        Color(0xFF4A2E2E),
        Color(0xFF25324A),
        Color(0xFF3D3320),
    )
    val base = palette[(channel.name.hashCode() and Int.MAX_VALUE) % palette.size]
    val shape = RoundedCornerShape(12.dp)
    Box(
        modifier = modifier
            .size(size)
            .clip(shape)
            .background(
                Brush.linearGradient(
                    listOf(base, base.copy(alpha = 0.65f)),
                ),
            ),
        contentAlignment = Alignment.Center,
    ) {
        val short = when {
            channel.name.startsWith("CCTV-") ->
                "C" + channel.name.removePrefix("CCTV-").take(2)
            channel.name.startsWith("CGTN") -> "CGTN"
            else -> channel.name.replace(" ", "").take(2)
        }
        Text(
            text = short,
            color = Color.White.copy(alpha = 0.92f),
            fontSize = if (short.length > 2) 15.sp else 21.sp,
            fontWeight = FontWeight.Bold,
        )
        if (channel.logo.isNotBlank()) {
            AsyncImage(
                model = ImageRequest.Builder(context)
                    .data(channel.logo)
                    .crossfade(false)
                    .build(),
                contentDescription = channel.name,
                contentScale = ContentScale.Fit,
                modifier = Modifier
                    .fillMaxSize()
                    .padding(5.dp),
            )
        }
    }
}

@Composable
fun ChannelCard(
    channel: TvChannel,
    isFavorite: Boolean,
    focusRequester: FocusRequester?,
    leftFocus: FocusRequester? = null,
    // 用户 10-29 00:5x「台号放首页频道列表中」：编号表由上层按全目录算好传进来，
    // 与播放器里数字键跳台用的是同一张表（ChannelNumbers.table）。
    number: Int? = null,
    onClick: () -> Unit,
) {
    val fallbackFocus = remember { FocusRequester() }
    val bringIntoView = remember { BringIntoViewRequester() }
    val scope = rememberCoroutineScope()
    var focused by remember { mutableStateOf(false) }
    val scale by animateFloatAsState(
        targetValue = if (focused) 1.04f else 1f,
        label = "channelScale",
    )
    val shape = RoundedCornerShape(16.dp)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(116.dp)
            .graphicsLayer {
                scaleX = scale
                scaleY = scale
            }
            .clip(shape)
            .background(if (focused) TvColors.SurfaceFocused else TvColors.Surface)
            .border(
                width = 2.dp,
                color = if (focused) TvColors.Accent else TvColors.Divider.copy(alpha = 0.55f),
                shape = shape,
            )
            .focusRequester(focusRequester ?: fallbackFocus)
            .then(
                if (leftFocus != null) {
                    Modifier.focusProperties { left = leftFocus }
                } else {
                    Modifier
                },
            )
            .bringIntoViewRequester(bringIntoView)
            .onFocusChanged {
                focused = it.isFocused
                if (it.isFocused) {
                    scope.launch { bringIntoView.bringIntoView() }
                }
            }
            .clickable(onClick = onClick)
            .padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ChannelLogo(channel = channel, size = 60.dp)
        Spacer(Modifier.width(12.dp))
        if (number != null) {
            Text(
                text = "$number",
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = TvColors.Accent,
                modifier = Modifier.padding(end = 10.dp),
            )
        }
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = channel.name,
                fontSize = 19.sp,
                lineHeight = 22.sp,
                fontWeight = FontWeight.SemiBold,
                color = TvColors.TextPrimary,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(7.dp))
            Text(
                text = channel.subgroup.ifBlank { "直播" },
                fontSize = 13.sp,
                color = TvColors.TextSecondary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (isFavorite) {
            Icon(
                imageVector = Icons.Rounded.Favorite,
                contentDescription = "已收藏",
                tint = TvColors.Accent,
                modifier = Modifier.size(19.dp),
            )
        }
    }
}

@Composable
fun SubgroupChip(
    label: String,
    selected: Boolean,
    onClick: () -> Unit,
) {
    var focused by remember { mutableStateOf(false) }
    val bringIntoView = remember { BringIntoViewRequester() }
    val scope = rememberCoroutineScope()
    val shape = RoundedCornerShape(22.dp)
    val background = when {
        selected -> TvColors.Accent
        focused -> TvColors.SurfaceFocused
        else -> TvColors.Surface.copy(alpha = 0.8f)
    }
    Box(
        modifier = Modifier
            .height(40.dp)
            .clip(shape)
            .background(background)
            .border(
                width = 1.dp,
                color = if (focused) TvColors.Accent else TvColors.Divider.copy(alpha = 0.6f),
                shape = shape,
            )
            .bringIntoViewRequester(bringIntoView)
            .onFocusChanged {
                focused = it.isFocused
                if (it.isFocused) {
                    scope.launch { bringIntoView.bringIntoView() }
                }
            }
            .clickable(onClick = onClick)
            .padding(horizontal = 18.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = label,
            fontSize = 15.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium,
            color = when {
                selected -> Color(0xFF1A1200)
                focused -> TvColors.TextPrimary
                else -> TvColors.TextSecondary
            },
        )
    }
}

@Composable
fun SidebarItem(
    label: String,
    icon: ImageVector,
    selected: Boolean,
    badge: Int?,
    focusRequester: FocusRequester? = null,
    onClick: () -> Unit,
) {
    val fallbackFocus = remember { FocusRequester() }
    var focused by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(12.dp)
    val background = when {
        selected -> TvColors.AccentSoft
        focused -> TvColors.SurfaceFocused
        else -> Color.Transparent
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(44.dp)
            .clip(shape)
            .background(background)
            .border(
                width = 1.dp,
                color = if (focused) TvColors.Accent.copy(alpha = 0.65f) else Color.Transparent,
                shape = shape,
            )
            .focusRequester(focusRequester ?: fallbackFocus)
            .onFocusChanged { focused = it.isFocused }
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .width(3.dp)
                .height(20.dp)
                .clip(RoundedCornerShape(2.dp))
                .background(if (selected) TvColors.Accent else Color.Transparent),
        )
        Spacer(Modifier.width(9.dp))
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = if (selected || focused) TvColors.Accent else TvColors.TextSecondary,
            modifier = Modifier.size(20.dp),
        )
        Spacer(Modifier.width(9.dp))
        Text(
            text = label,
            fontSize = 16.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium,
            color = if (selected || focused) TvColors.TextPrimary else TvColors.TextSecondary,
        )
        Spacer(Modifier.weight(1f))
        if (badge != null && badge > 0) {
            Text(
                text = badge.toString(),
                fontSize = 12.sp,
                color = if (selected) TvColors.Accent else TvColors.TextSecondary,
            )
        }
    }
}

@Composable
fun PlayerActionButton(
    icon: ImageVector,
    label: String,
    onClick: () -> Unit,
    focusRequester: FocusRequester? = null,
    enabled: Boolean = true,
    accent: Boolean = false,
    onFocusChanged: (Boolean) -> Unit = {},
) {
    val fallbackFocus = remember { FocusRequester() }
    var focused by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(14.dp)
    val background = when {
        !enabled -> TvColors.Surface.copy(alpha = 0.45f)
        focused -> TvColors.Accent.copy(alpha = 0.18f)
        else -> TvColors.Surface.copy(alpha = 0.88f)
    }
    val contentColor = when {
        !enabled -> TvColors.TextSecondary.copy(alpha = 0.55f)
        focused || accent -> TvColors.Accent
        else -> TvColors.TextPrimary
    }
    Row(
        modifier = Modifier
            .height(54.dp)
            .clip(shape)
            .background(background)
            .border(
                width = 2.dp,
                color = if (focused) TvColors.Accent else Color.Transparent,
                shape = shape,
            )
            .focusRequester(focusRequester ?: fallbackFocus)
            .onFocusChanged {
                focused = it.isFocused
                onFocusChanged(it.isFocused)
            }
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = contentColor,
            modifier = Modifier.size(20.dp),
        )
        Spacer(Modifier.width(6.dp))
        Text(
            text = label,
            fontSize = 16.sp,
            fontWeight = FontWeight.Medium,
            color = contentColor,
        )
    }
}
