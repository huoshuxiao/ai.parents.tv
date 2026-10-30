package com.sun.tv.parents.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * 自动加线的闸（用户 10-28 点档「乙」：自动加线保留，但**新增线必须先过门禁**）。
 *
 * 为什么要有这一层：`CatalogUpdater` 原来把公开源里所有新地址直接并进设备目录，10-28 00:04
 * 那一轮一口气加了 155 条，抽样 7 个主机各 1 条、6 条打不开——目录被脏线撑大，门禁跑的却是
 * 打进 APK 的那份目录，两边对不上。
 *
 * 现在分两半：
 * - **允许进**：地址在打进 APK 的 `line_allowlist.json` 里（由 `tools/vet_line_candidates.py`
 *   探过清单／分片／画面后才写进去）；
 * - **先记账**：其余发现的新地址写进 `files/line_candidates.json`，等下一次过闸。
 */
object LineAllowlist {

    /** 判定只在这里做：白名单按 [StreamUrl.canonical] 比，避免同一地址换大小写/尾斜杠被漏判。 */
    fun admit(candidates: List<String>, allowed: List<String>): Pair<List<String>, List<String>> {
        if (candidates.isEmpty()) return emptyList<String>() to emptyList()
        val allowedKeys = allowed.mapTo(HashSet()) { StreamUrl.canonical(it) }
        val keep = ArrayList<String>(candidates.size)
        val drop = ArrayList<String>()
        for (c in candidates) {
            if (StreamUrl.canonical(c) in allowedKeys) keep += c else drop += c
        }
        return keep to drop
    }

    @Serializable
    private data class AllowlistFile(
        @SerialName("lines") val lines: List<String> = emptyList(),
    )

    /** 读打进 APK 的白名单；文件缺失/坏掉一律当空表（＝暂不自动加线），不炸更新流程。 */
    fun parse(raw: String): List<String> {
        if (raw.isBlank()) return emptyList()
        return runCatching {
            Json { ignoreUnknownKeys = true; isLenient = true }
                .decodeFromString(AllowlistFile.serializer(), raw).lines
        }.getOrDefault(emptyList())
    }
}
