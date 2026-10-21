package com.parentstv.live.data

import kotlinx.serialization.Serializable

@Serializable
data class ChannelCatalog(
    val schemaVersion: Int = 1,
    val generatedAt: String = "",
    val sourceNote: String = "",
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
