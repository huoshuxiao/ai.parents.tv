package com.parentstv.live.data

import kotlinx.serialization.Serializable

@Serializable
data class ChannelCatalog(
    val schemaVersion: Int = 1,
    val generatedAt: String = "",
    val sourceNote: String = "",
    // 目录生成时按实测证据判坏的那些**地址**（归一键 -> 对端给过的结论）。
    // 公开源会一遍遍把它们捞回来，只删内置目录挡不住下一次自动更新（10-22 模拟器现读：
    // 一次开机后台更新就让设备缓存比目录多出 151 条判坏的线），所以黑名单要跟目录一起装进 APK。
    val blockedLines: Map<String, String> = emptyMap(),
    val categories: List<CategoryDef> = emptyList(),
    val channels: List<TvChannel> = emptyList(),
) {
    fun playableChannels(): List<TvChannel> = channels.filter { it.streams.isNotEmpty() }

    fun categoryName(id: String): String =
        categories.firstOrNull { it.id == id }?.name ?: id
}

@Serializable
data class CategoryDef(
    val id: String,
    val name: String,
    val subgroups: List<String> = emptyList(),
)

@Serializable
data class TvChannel(
    val id: String,
    val name: String,
    val category: String,
    val subgroup: String = "",
    val logo: String = "",
    val streams: List<String> = emptyList(),
)
