package com.sun.tv.parents.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

object TvColors {
    val Background = Color(0xFF070B14)
    val BackgroundAlt = Color(0xFF0C1424)
    val Sidebar = Color(0xFF090F1B)
    val Surface = Color(0xFF121B2E)
    val SurfaceFocused = Color(0xFF1D2A44)
    val Accent = Color(0xFFFFB23E)
    val AccentSoft = Color(0x33FFB23E)
    val TextPrimary = Color(0xFFF5F7FA)
    val TextSecondary = Color(0xFF9AA6BC)
    val Divider = Color(0xFF1E2A40)
    val Live = Color(0xFFEF4444)
    val Success = Color(0xFF4ADE80)
}

private val TvColorScheme = darkColorScheme(
    primary = TvColors.Accent,
    onPrimary = Color(0xFF1A1200),
    secondary = Color(0xFF7DD3FC),
    background = TvColors.Background,
    onBackground = TvColors.TextPrimary,
    surface = TvColors.Surface,
    onSurface = TvColors.TextPrimary,
    surfaceVariant = TvColors.SurfaceFocused,
    onSurfaceVariant = TvColors.TextSecondary,
)

private val TvTypography = Typography(
    displayLarge = TextStyle(fontSize = 54.sp, fontWeight = FontWeight.Bold),
    headlineLarge = TextStyle(fontSize = 38.sp, fontWeight = FontWeight.Bold),
    headlineMedium = TextStyle(fontSize = 30.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontSize = 24.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 20.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 18.sp),
    bodyMedium = TextStyle(fontSize = 16.sp),
    labelLarge = TextStyle(fontSize = 16.sp, fontWeight = FontWeight.Medium),
)

@Composable
fun ParentsTvTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = TvColorScheme,
        typography = TvTypography,
        content = content,
    )
}
