package com.parentstv.live.data

import android.content.Context
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.util.UUID

/**
 * 用户手动「挂」上来的临时线路（目前只有腾讯体育这个来源）。
 *
 * 为什么单独存一份、不写进 channels.json：腾讯体育的地址是按场次签发的，开赛才有、赛后即废。
 * 目录是打包产物，塞进临时地址会让下一个包永远带着一条死线；这里把它放在 files/ 下，
 * 每次加载目录时并进去，过期后自动清掉。
 */
@Serializable
data class ManualLine(
    val id: String,
    val title: String,
    val url: String,
    val addedAt: Long,
    val expiresAt: Long,
) {
    companion object {
        /** 默认挂 6 小时：够一场比赛（含加时/颁奖），过点自动从目录里消失。 */
        const val DEFAULT_TTL_MS = 6L * 60L * 60L * 1000L

        fun create(title: String, url: String, now: Long = System.currentTimeMillis()): ManualLine =
            ManualLine(
                id = UUID.randomUUID().toString().take(8),
                title = title,
                url = url,
                addedAt = now,
                expiresAt = now + DEFAULT_TTL_MS,
            )
    }
}

class ManualLineStore(context: Context) {
    private val file = File(context.filesDir, "manual_lines.json")
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        prettyPrint = true
    }

    /** 读出来时就顺手扔掉过期的：不用等下一次挂线，App 重启就会自己收拾干净。 */
    fun load(now: Long = System.currentTimeMillis()): List<ManualLine> = synchronized(LOCK) {
        val all = runCatching {
            json.decodeFromString<List<ManualLine>>(file.readText())
        }.getOrDefault(emptyList())
        val alive = all.filter { it.expiresAt > now && it.url.isNotBlank() }
        if (alive.size != all.size) saveLocked(alive)
        alive
    }

    fun add(line: ManualLine): List<ManualLine> = synchronized(LOCK) {
        val next = load().filterNot { it.id == line.id } + line
        saveLocked(next)
        next
    }

    fun remove(id: String): List<ManualLine> = synchronized(LOCK) {
        val next = load().filterNot { it.id == id }
        saveLocked(next)
        next
    }

    fun clear() {
        synchronized(LOCK) { saveLocked(emptyList()) }
    }

    /**
     * 把未过期的临时线并进目录：每场一条台，挂在体育组最后一个台后面，
     * 这样台号顺延、也不打乱「体育频道」原有顺序。
     */
    fun merged(catalog: ChannelCatalog, now: Long = System.currentTimeMillis()): ChannelCatalog {
        val lines = load(now)
        if (lines.isEmpty()) return catalog
        val manual = lines.sortedBy { it.addedAt }.map { line ->
            TvChannel(
                id = "manual-${line.id}",
                name = line.title,
                category = "sports",
                subgroup = "腾讯体育",
                logo = "",
                streams = listOf(line.url),
            )
        }
        val channels = catalog.channels.toMutableList()
        val lastSports = channels.indexOfLast { it.category == "sports" }
        val insertAt = if (lastSports >= 0) lastSports + 1 else channels.size
        channels.addAll(insertAt, manual)
        return catalog.copy(channels = channels)
    }

    private fun saveLocked(lines: List<ManualLine>) {
        runCatching {
            val tmp = File(file.parentFile, "${file.name}.tmp")
            tmp.writeText(json.encodeToString(lines))
            if (!tmp.renameTo(file)) {
                file.writeText(tmp.readText())
                tmp.delete()
            }
        }
    }

    private companion object {
        /** 目录加载与设置页各 new 一个，靠这把锁保证不会一边写一边读。 */
        val LOCK = Any()
    }
}
