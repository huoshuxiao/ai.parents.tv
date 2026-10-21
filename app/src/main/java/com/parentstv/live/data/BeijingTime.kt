package com.parentstv.live.data

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

object BeijingTime {
    const val ZONE_ID = "Asia/Shanghai"

    fun format(epochMillis: Long, pattern: String): String =
        SimpleDateFormat(pattern, Locale.CHINA)
            .apply { timeZone = TimeZone.getTimeZone(ZONE_ID) }
            .format(Date(epochMillis))

    fun now(pattern: String = "HH:mm"): String = format(System.currentTimeMillis(), pattern)

    fun formatIso(isoText: String, pattern: String): String? {
        val match = ISO_8601.matchEntire(isoText.trim()) ?: return null
        return runCatching {
            val groups = match.groupValues
            val utc = Calendar.getInstance(TimeZone.getTimeZone("UTC"), Locale.US).apply {
                isLenient = false
                set(
                    groups[1].toInt(),
                    groups[2].toInt() - 1,
                    groups[3].toInt(),
                    groups[4].toInt(),
                    groups[5].toInt(),
                    groups[6].toInt(),
                )
                set(Calendar.MILLISECOND, groups[7].padEnd(3, '0').toInt())
            }
            val offsetText = groups[8]
            val offsetMinutes = if (offsetText == "Z") {
                0
            } else {
                val sign = if (offsetText.startsWith("-")) -1 else 1
                sign * (offsetText.substring(1, 3).toInt() * 60 + offsetText.substring(4, 6).toInt())
            }
            format(utc.timeInMillis - offsetMinutes * 60_000L, pattern)
        }.getOrNull()
    }

    private val ISO_8601 = Regex(
        "^(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2}):(\\d{2})(?:\\.(\\d{1,3}))?(Z|[+-]\\d{2}:\\d{2})$",
    )
}
